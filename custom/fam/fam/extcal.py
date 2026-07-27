"""iCloud CalDAV transport + read-only probe + ICS parser/recurrence
expansion (Task 0 Steps 1-2 + Task 1 + Task 2).

Scope of THIS module, as landed so far:
  - `_request`     -- one raw HTTP request over CalDAV verbs (PROPFIND,
                       REPORT), Basic auth, stdlib only (urllib.request).
  - `discover`      -- /.well-known/caldav -> principal -> calendar-home-set
                       -> list of calendar collections.
  - `fetch_changes` -- REPORT sync-collection (incremental, given a
                       sync-token) with a REPORT calendar-query (time-range)
                       fallback when there is no token yet, or the server
                       rejects the token.
  - `probe`         -- read-only diagnostic: walks discover()/fetch_changes()
                       across her calendars and returns coarse counts
                       (timed/all_day/with_rrule/with_valarm/total). Writes
                       NOTHING to the DB and NOTHING to iCloud.
  - `parse_ics`     -- Task 2: the REAL streaming/unfolding stdlib ICS
                       parser (VEVENT only), producing `Component` dicts.
                       `probe`'s own `_tally_ics` stays the crude
                       regex-over-raw-text diagnostic it always was --
                       it only ever needed coarse counts, not correctness,
                       and is left alone per the task boundary.
  - `expand`        -- Task 2: RRULE recurrence expansion (via
                       `dateutil.rrule`, DEFERRED import -- see
                       `_load_rrule_module`) of `Component`s into concrete
                       `Occurrence`s inside a caller-given window, honoring
                       EXDATE and RECURRENCE-ID overrides.

Style/contract mirrors weather.py and geo2gis.py, the two existing network
modules in this package:
  - every public entry point is injectable (a `request`/`_default_open`
    seam) so unit tests never touch the real network;
  - nothing here EVER raises out to the caller -- any network error,
    timeout, HTTP error status, or malformed XML degrades to `None` (or an
    entry in `probe()`'s `errors` list), never an exception. A tick that
    calls into this module later must not be able to crash from it;
  - anti-SSRF host-guard: every actual request only ever goes to `https://`
    URLs on `*.icloud.com` (or the bare host `icloud.com`). Unlike
    geo2gis's known minor (scheme wasn't checked independently of the host
    allowlist, so `ftp://` slipped through), the scheme is checked FIRST,
    unconditionally, before the host is even inspected;
  - the iCloud app-specific password is read ONLY from
    `ICLOUD_APP_PASSWORD` in the environment (never a config key, never
    written to disk by this module). It is folded into a Basic-auth header
    and nowhere else: never returned from any public function, never part
    of a repr, never part of an exception message or a stderr diagnostic.

`python-dateutil` availability: `fam` runs inside the Amina agent's Docker
sandbox, which does NOT have `python-dateutil` installed until the image is
separately rebuilt (a later, production-rollout step -- not this task). So:
  - `dateutil` is NEVER imported at module top level here -- only inside
    `_load_rrule_module()`, called lazily by `expand()` and ONLY when a
    component with an actual RRULE needs expanding. `import extcal` (and
    therefore `cli.py`, and every other `fam` command) works with zero
    recurring events in scope whether or not `dateutil` is installed;
  - if `expand()` DOES need to expand an RRULE and `dateutil` turns out to
    be missing, that is surfaced as an explicit sentinel entry in the
    returned list (`{"error": "dateutil_missing", ...}`, `uid` is None) --
    never a silently-empty/short result indistinguishable from "this
    calendar genuinely has no recurring events".
"""
import base64
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape
from zoneinfo import ZoneInfo

# ---- constants -------------------------------------------------------

# Anti-SSRF allowlist: only this host or a subdomain of it is ever
# actually requested, regardless of what a caller (or a server-supplied
# redirect / href) points at.
ALLOWED_HOST_SUFFIX = "icloud.com"

# Apple's fixed CalDAV discovery bootstrap URL (RFC 6764). There is no
# config key for this -- it is the same for every iCloud account.
WELL_KNOWN_URL = "https://caldav.icloud.com/.well-known/caldav"

_DAV_NS = "DAV:"
_CAL_NS = "urn:ietf:params:xml:ns:caldav"
_CS_NS = "http://calendarserver.org/ns/"

DEFAULT_TIMEOUT = 20


def _empty_counts():
    return {"timed": 0, "all_day": 0, "with_rrule": 0, "with_valarm": 0, "total": 0}


# ---- transport ---------------------------------------------------------

class Response:
    """Minimal stand-in for what `_request` hands back on any completed
    HTTP exchange (including error statuses like 401/403/5xx -- those are
    NOT collapsed to None here, since callers such as fetch_changes need
    to tell "invalid sync-token" (403) apart from other failures). Only
    total transport failure (DNS, timeout, refused connection, disallowed
    host) collapses to None.

    Deliberately holds only the SERVER's response -- never the request we
    sent, so it can never carry the Basic-auth header we built.
    """
    __slots__ = ("status", "headers", "body")

    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self.body = body.encode("utf-8") if isinstance(body, str) else (body or b"")
        self.headers = dict(headers or {})

    @property
    def text(self):
        try:
            return self.body.decode("utf-8")
        except Exception:
            return self.body.decode("utf-8", errors="replace")

    def __repr__(self):
        return f"<extcal.Response status={self.status} len={len(self.body)}>"


class _RedirectCaught(Exception):
    """Raised by _NoRedirect to hand a 3xx back to _request as data,
    instead of letting urllib silently chase it."""

    def __init__(self, code, location):
        self.code = code
        self.location = location


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise _RedirectCaught(code, newurl)


def _scheme_and_host_ok(url):
    """Anti-SSRF guard. Scheme is checked FIRST and independently of the
    host allowlist (the geo2gis module has a known minor where an
    `ftp://` URL on an allowed host slipped past the guard because only
    the host was checked; fixed here by short-circuiting on scheme).
    """
    try:
        parts = urlsplit(url)
    except Exception:
        return False
    if parts.scheme.lower() != "https":
        return False
    host = (parts.hostname or "").lower()
    return host == ALLOWED_HOST_SUFFIX or host.endswith("." + ALLOWED_HOST_SUFFIX)


def _default_open(req, timeout):
    """The real network call, isolated in its own function so tests can
    monkeypatch it without touching urllib globally. Never called for a
    disallowed host/scheme -- `_request` filters before reaching here.
    """
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=timeout) as resp:
        status = getattr(resp, "status", 200)
        headers = dict(resp.headers.items()) if resp.headers else {}
        return Response(status, resp.read(), headers)


def _request(method, url, headers=None, body=None, timeout=DEFAULT_TIMEOUT):
    """One CalDAV HTTP request (PROPFIND/REPORT/PUT/DELETE/...).

    Never raises. Returns:
      - a Response (possibly carrying an error status, e.g. 401/403/5xx,
        or a 3xx with a Location header) on any completed HTTP exchange;
      - None on a disallowed host/scheme, or on total transport failure
        (timeout, DNS, connection refused, ...).

    Redirects are NEVER auto-followed (a custom opener catches them and
    surfaces the 3xx + Location as a normal Response) -- the caller
    (discover()) decides whether to re-request the Location, and MUST
    re-check the host guard on it before doing so: an iCloud server
    redirecting to an attacker-controlled host is exactly the case this
    guard exists for.

    `headers` is caller-supplied (e.g. the Basic-auth header built by
    `_auth_header`) and is never echoed back into the returned Response,
    never logged, and never appears in any exception message below.
    """
    if not _scheme_and_host_ok(url):
        print("extcal: refused request to disallowed host/scheme", file=sys.stderr)
        return None
    data = body.encode("utf-8") if isinstance(body, str) else body
    try:
        req = urllib.request.Request(url, data=data, method=method,
                                      headers=dict(headers or {}))
    except Exception as e:
        print(f"extcal: could not build request: {type(e).__name__}", file=sys.stderr)
        return None
    try:
        return _default_open(req, timeout)
    except _RedirectCaught as e:
        return Response(e.code, b"", {"Location": e.location})
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read()
        except Exception:
            err_body = b""
        err_headers = dict(e.headers.items()) if getattr(e, "headers", None) else {}
        return Response(e.code, err_body, err_headers)
    except Exception as e:
        # URLError, socket.timeout, ValueError (bad URL), etc. None of
        # these exception messages can contain the Authorization header
        # (it was never part of the exception-raising path) -- but we
        # still only print the exception TYPE, not str(e), as an extra
        # margin against a future urllib version embedding request state
        # in an error message.
        print(f"extcal: request failed ({type(e).__name__})", file=sys.stderr)
        return None


def _auth_header(cfg):
    """Basic-auth header dict, or None if credentials are incomplete.
    The password NEVER comes from cfg -- only from ICLOUD_APP_PASSWORD in
    the environment (controller decision: it must never land in
    fam-config.json, audit rows, or test fixtures).
    """
    username = ((cfg or {}).get("extcal_username") or "").strip()
    password = os.environ.get("ICLOUD_APP_PASSWORD", "")
    if not username or not password:
        return None
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _dav_headers(cfg, depth):
    headers = {"Content-Type": "application/xml; charset=utf-8", "Depth": depth}
    auth = _auth_header(cfg)
    if auth:
        headers.update(auth)
    return headers


# ---- XML request bodies -------------------------------------------------

_PRINCIPAL_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:">'
    '<d:prop><d:current-user-principal/></d:prop>'
    '</d:propfind>'
)

_HOME_SET_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    '<d:prop><c:calendar-home-set/></d:prop>'
    '</d:propfind>'
)

_COLLECTIONS_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" '
    'xmlns:cs="http://calendarserver.org/ns/">'
    '<d:prop>'
    '<d:displayname/>'
    '<d:resourcetype/>'
    '<c:supported-calendar-component-set/>'
    '<cs:getctag/>'
    '<d:sync-token/>'
    '</d:prop>'
    '</d:propfind>'
)

_SYNC_BODY_TMPL = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:sync-collection xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    '<d:sync-token>{token}</d:sync-token>'
    '<d:sync-level>1</d:sync-level>'
    '<d:prop><d:getetag/><c:calendar-data/></d:prop>'
    '</d:sync-collection>'
)

_QUERY_BODY_TMPL = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    '<d:prop><d:getetag/><c:calendar-data/></d:prop>'
    '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
    '<c:time-range start="{start}" end="{end}"/>'
    '</c:comp-filter></c:comp-filter></c:filter>'
    '</c:calendar-query>'
)


def _time_range(horizon_weeks):
    """[today-1d, today+horizon_weeks] as CalDAV UTC basic-format
    timestamps, matching the design's read/write window."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=1)).strftime("%Y%m%dT000000Z")
    end = (now + timedelta(weeks=horizon_weeks)).strftime("%Y%m%dT000000Z")
    return start, end


# ---- XML response parsing -----------------------------------------------

# xml.etree.ElementTree (stdlib, via pyexpat) does not resolve EXTERNAL
# entities by default, so classic XXE (file/URL disclosure) is not a
# practical risk here. It IS officially listed as vulnerable to "billion
# laughs" and "quadratic blowup" -- a small document whose DTD declares
# nested <!ENTITY> definitions only expands to something huge DURING
# ET.fromstring itself, so a pre-parse BYTE-COUNT check alone does NOT
# close this off: the dangerous growth happens strictly inside the parse
# call, after a size check on the small input would already have passed.
# This module is stdlib-only by explicit project constraint (no
# defusedxml/lxml dependency for this task), so the actual mitigation
# here is a CONTENT check, not a size check: a legitimate CalDAV
# PROPFIND/REPORT multistatus response never contains a <!DOCTYPE or
# <!ENTITY declaration (CalDAV has no legitimate use for either), so any
# response body containing one (case-insensitive) is rejected as a parse
# failure BEFORE it ever reaches ET.fromstring -- this closes off
# entity-expansion attacks entirely, regardless of nesting depth, unlike
# a size cap. MAX_XML_BYTES below is a SEPARATE, orthogonal guard against
# a simply oversized-but-DTD-free response -- it is NOT what defeats
# billion laughs. Residual risk is further bounded by the host-guard:
# only TLS (`https://`) responses from `*.icloud.com` are ever parsed at
# all, not arbitrary/attacker-supplied XML.
MAX_XML_BYTES = 8 * 1024 * 1024

_DTD_MARKERS = ("<!doctype", "<!entity")


def _parse_xml(text):
    """ET.fromstring wrapper. Raises ET.ParseError (same failure mode as
    genuinely malformed XML, so every existing try/except ET.ParseError
    call site handles both for free) when:
      - the body contains a `<!DOCTYPE` or `<!ENTITY` marker
        (case-insensitive) -- the billion-laughs/quadratic-blowup
        mitigation, see the module comment above;
      - the body exceeds MAX_XML_BYTES -- a separate, plain size guard.
    """
    lowered = text.lower()
    if any(marker in lowered for marker in _DTD_MARKERS):
        raise ET.ParseError(
            "response body contains a DOCTYPE/ENTITY declaration, refusing to parse")
    if len(text.encode("utf-8")) > MAX_XML_BYTES:
        raise ET.ParseError(f"response body exceeds {MAX_XML_BYTES} bytes, refusing to parse")
    return ET.fromstring(text)


def _first_href(root, ns, local):
    el = root.find(f".//{{{ns}}}{local}/{{{_DAV_NS}}}href")
    if el is not None and el.text:
        return el.text.strip()
    return None


def _parse_current_user_principal(text):
    try:
        root = _parse_xml(text)
    except ET.ParseError:
        return None
    return _first_href(root, _DAV_NS, "current-user-principal")


def _parse_calendar_home_set(text):
    try:
        root = _parse_xml(text)
    except ET.ParseError:
        return None
    return _first_href(root, _CAL_NS, "calendar-home-set")


def _parse_collections(text, home_url):
    try:
        root = _parse_xml(text)
    except ET.ParseError:
        return []
    calendars = []
    for resp in root.findall(f"{{{_DAV_NS}}}response"):
        href_el = resp.find(f"{{{_DAV_NS}}}href")
        if href_el is None or not href_el.text:
            continue
        href = href_el.text.strip()

        resourcetype = resp.find(f".//{{{_DAV_NS}}}resourcetype")
        is_calendar = (resourcetype is not None
                       and resourcetype.find(f"{{{_CAL_NS}}}calendar") is not None)
        if not is_calendar:
            continue  # the home collection itself, or a non-calendar child

        name_el = resp.find(f".//{{{_DAV_NS}}}displayname")
        name = name_el.text.strip() if name_el is not None and name_el.text else href

        ctag_el = resp.find(f".//{{{_CS_NS}}}getctag")
        ctag = ctag_el.text.strip() if ctag_el is not None and ctag_el.text else None

        token_el = resp.find(f".//{{{_DAV_NS}}}sync-token")
        sync_token = token_el.text.strip() if token_el is not None and token_el.text else None

        comp_set = resp.find(f".//{{{_CAL_NS}}}supported-calendar-component-set")
        components = []
        if comp_set is not None:
            for c in comp_set.findall(f"{{{_CAL_NS}}}comp"):
                n = c.get("name")
                if n:
                    components.append(n)

        calendars.append({
            "url": urljoin(home_url, href),
            "name": name,
            "ctag": ctag,
            "sync_token": sync_token,
            "supports_sync_token": bool(sync_token),
            "components": components,
        })
    return calendars


def _parse_multistatus_items(text):
    """Parse a sync-collection/calendar-query multistatus REPORT response.
    Raises ET.ParseError on malformed XML -- callers wrap this in
    try/except so a broken payload degrades to None, never an exception
    reaching probe()/fetch_changes()'s caller.
    """
    root = _parse_xml(text)
    token_el = root.find(f"{{{_DAV_NS}}}sync-token")
    new_token = token_el.text.strip() if token_el is not None and token_el.text else None

    items = []
    for resp in root.findall(f"{{{_DAV_NS}}}response"):
        href_el = resp.find(f"{{{_DAV_NS}}}href")
        href = href_el.text.strip() if href_el is not None and href_el.text else None

        status_el = resp.find(f"{{{_DAV_NS}}}status")
        status_text = status_el.text or "" if status_el is not None else ""
        if " 404 " in status_text:
            # sync-collection's way of saying "this href was deleted".
            items.append({"href": href, "deleted": True, "etag": None, "ics": None})
            continue

        etag_el = resp.find(f".//{{{_DAV_NS}}}getetag")
        etag = etag_el.text.strip() if etag_el is not None and etag_el.text else None
        data_el = resp.find(f".//{{{_CAL_NS}}}calendar-data")
        ics = data_el.text if data_el is not None else None
        items.append({"href": href, "deleted": False, "etag": etag, "ics": ics})
    return items, new_token


# ---- discover ------------------------------------------------------------

def _discover(cfg, request):
    """Full discovery walk, returning (calendars, errors) -- the private
    variant with rich diagnostics, used by probe(). The public `discover`
    below is a thin wrapper matching the plain `-> list[Calendar]`
    contract Task 2/4/6 will call.
    """
    errors = []
    cfg = cfg or {}

    if _auth_header(cfg) is None:
        errors.append("missing iCloud credentials "
                       "(ICLOUD_APP_PASSWORD not set, or extcal_username empty)")
        return [], errors

    resp = request("PROPFIND", WELL_KNOWN_URL, headers=_dav_headers(cfg, "0"),
                    body=_PRINCIPAL_BODY, timeout=DEFAULT_TIMEOUT)
    if resp is None:
        errors.append("well-known lookup failed (network/timeout/host-guard)")
        return [], errors

    base = WELL_KNOWN_URL
    if resp.status in (301, 302, 303, 307, 308):
        location = resp.headers.get("Location") or resp.headers.get("location")
        if not location:
            errors.append("well-known redirect had no Location header")
            return [], errors
        # Resolve BEFORE the host-guard check (same order as
        # principal_href/home_href below) -- a relative Location (e.g.
        # "/next") has urlsplit().hostname == "" and would otherwise be
        # rejected with a misleading "disallowed host" reason instead of
        # being correctly resolved against `base` first.
        location = urljoin(base, location)
        if not _scheme_and_host_ok(location):
            errors.append("well-known redirected to a disallowed host")
            return [], errors
        base = location
        resp = request("PROPFIND", location, headers=_dav_headers(cfg, "0"),
                        body=_PRINCIPAL_BODY, timeout=DEFAULT_TIMEOUT)
        if resp is None:
            errors.append("principal lookup failed after well-known redirect")
            return [], errors

    if resp.status not in (200, 207):
        errors.append(f"principal lookup returned HTTP {resp.status}")
        return [], errors

    principal_href = _parse_current_user_principal(resp.text)
    if not principal_href:
        errors.append("no current-user-principal in PROPFIND response")
        return [], errors
    principal_url = urljoin(base, principal_href)
    if not _scheme_and_host_ok(principal_url):
        errors.append("current-user-principal resolved to a disallowed host")
        return [], errors

    resp = request("PROPFIND", principal_url, headers=_dav_headers(cfg, "0"),
                    body=_HOME_SET_BODY, timeout=DEFAULT_TIMEOUT)
    if resp is None or resp.status not in (200, 207):
        errors.append("calendar-home-set lookup failed")
        return [], errors

    home_href = _parse_calendar_home_set(resp.text)
    if not home_href:
        errors.append("no calendar-home-set in PROPFIND response")
        return [], errors
    home_url = urljoin(principal_url, home_href)
    if not _scheme_and_host_ok(home_url):
        errors.append("calendar-home-set resolved to a disallowed host")
        return [], errors

    resp = request("PROPFIND", home_url, headers=_dav_headers(cfg, "1"),
                    body=_COLLECTIONS_BODY, timeout=DEFAULT_TIMEOUT)
    if resp is None or resp.status not in (200, 207):
        errors.append("calendar collection listing failed")
        return [], errors

    calendars = _parse_collections(resp.text, home_url)
    return calendars, errors


def discover(cfg, request=None):
    """`/.well-known/caldav` -> principal -> calendar-home-set -> list of
    calendar collections: `{url, name, ctag, sync_token,
    supports_sync_token, components}`.

    Never raises; on any failure (missing credentials, network error,
    disallowed redirect target, malformed XML) returns `[]`. Callers that
    need to know WHY it came back empty should use `probe()`, which
    surfaces the same walk's diagnostics in its `errors` list.
    """
    calendars, _errors = _discover(cfg, request or _request)
    return calendars


# ---- fetch_changes ---------------------------------------------------

def _sync_collection(cfg, calendar_url, sync_token, request):
    """Returns (items, new_token, ok, reason). `reason` is only
    meaningful when ok is False -- a short machine-matchable string
    ("no_response", "http_<status>", "malformed_xml") a caller/audit log
    can key off, distinct from a free-text message."""
    body = _SYNC_BODY_TMPL.format(token=_xml_escape(sync_token))
    resp = request("REPORT", calendar_url, headers=_dav_headers(cfg, "1"),
                    body=body, timeout=DEFAULT_TIMEOUT)
    if resp is None:
        return None, None, False, "no_response"
    if resp.status not in (200, 207):
        return None, None, False, f"http_{resp.status}"
    try:
        items, new_token = _parse_multistatus_items(resp.text)
    except Exception:
        return None, None, False, "malformed_xml"
    return items, new_token, True, None


def _calendar_query(cfg, calendar_url, horizon_weeks, request):
    """Returns (items, reason). `reason` is only meaningful when items is
    None -- same short machine-matchable vocabulary as _sync_collection."""
    start, end = _time_range(horizon_weeks)
    body = _QUERY_BODY_TMPL.format(start=start, end=end)
    resp = request("REPORT", calendar_url, headers=_dav_headers(cfg, "1"),
                    body=body, timeout=DEFAULT_TIMEOUT)
    if resp is None:
        return None, "no_response"
    if resp.status not in (200, 207):
        return None, f"http_{resp.status}"
    try:
        items, _token = _parse_multistatus_items(resp.text)
    except Exception:
        return None, "malformed_xml"
    # calendar-query is not incremental -- it never yields a usable
    # sync-token for the next round.
    return items, None


def fetch_changes(cfg, calendar, sync_token=None, request=None):
    """One page of changes for `calendar` (a Calendar dict from
    discover(), or a bare url string).

    Returns `(items, new_token, sync_info)`. `sync_info` is
    `{"mode": ..., "reason": ...}` -- the diagnostic channel this
    function's callers (Task 6's tick, in particular) need to tell a
    routine "first sync, no token yet" apart from "sync-collection was
    attempted and REJECTED, we silently fell back to a full re-scan"
    (Important finding I1): without it, both cases return byte-identical
    `(items, None)` and a broken sync-token exchange would look healthy
    forever, doing a full re-scan every 15 minutes with nothing to show
    for it.

    `sync_info["mode"]` is one of:
      - "sync_collection": REPORT sync-collection succeeded; `new_token`
        holds the fresh token to persist for next time.
      - "initial_full": `sync_token` was None going in (no prior state
        for this calendar) -- calendar-query was used BY DESIGN, not as
        a failure fallback. `sync_info["reason"]` is None.
      - "fallback_full": `sync_token` WAS supplied but sync-collection
        failed, so calendar-query rescued the read. `sync_info["reason"]`
        explains why sync-collection failed ("no_response",
        "http_<status>", or "malformed_xml") -- a caller (or Task 6's
        `audit cal.ext.sync`) that keeps seeing this mode on every tick,
        instead of the token eventually settling into steady-state
        "sync_collection", has a real, previously-invisible signal that
        the sync-token exchange itself is broken.
      - "error": every attempt failed outright (missing credentials, or
        both sync-collection AND calendar-query failed, or the only
        attempted call -- calendar-query, when there was no token to try
        sync-collection with -- failed). `items`/`new_token` are None;
        `sync_info["reason"]` describes what failed.

    sync_token given but the server rejects it (any non-2xx/207 status,
    e.g. 403 "invalid sync-token", a 5xx, a dropped connection, or
    malformed XML): falls back to REPORT calendar-query over the horizon
    time-range -- mode "fallback_full". sync_token is None (first sync
    for this calendar): goes straight to calendar-query -- mode
    "initial_full" -- since an unfiltered sync-collection would return
    the WHOLE collection, unbounded by the horizon window.

    Never raises. On total failure returns `(None, None, {"mode":
    "error", "reason": "..."})`.

    `items` is a list of `{href, deleted, etag, ics}` dicts -- `ics` is
    the raw VCALENDAR text for a live item, None for a tombstoned
    (deleted=True) one.
    """
    cfg = cfg or {}
    request = request or _request
    calendar_url = calendar["url"] if isinstance(calendar, dict) else calendar

    if _auth_header(cfg) is None:
        return None, None, {"mode": "error", "reason": "missing_credentials"}

    fallback_reason = None
    if sync_token:
        items, new_token, ok, reason = _sync_collection(cfg, calendar_url, sync_token, request)
        if ok:
            return items, new_token, {"mode": "sync_collection", "reason": None}
        # sync-collection failed -- remember why, then attempt the
        # calendar-query fallback below. This IS the case I1 is about.
        fallback_reason = reason

    horizon_weeks = cfg.get("extcal_horizon_weeks", 8)
    items, cq_reason = _calendar_query(cfg, calendar_url, horizon_weeks, request)

    if items is None:
        if fallback_reason:
            reason = f"sync_collection_failed:{fallback_reason};calendar_query_failed:{cq_reason}"
        else:
            reason = f"calendar_query_failed:{cq_reason}"
        return None, None, {"mode": "error", "reason": reason}

    if fallback_reason is not None:
        return items, None, {"mode": "fallback_full", "reason": fallback_reason}
    return items, None, {"mode": "initial_full", "reason": None}


# ---- probe (read-only diagnostic, Task 0) --------------------------------

_VEVENT_RE = re.compile(r"BEGIN:VEVENT.*?END:VEVENT", re.S)
_DTSTART_RE = re.compile(r"^DTSTART([^:\r\n]*):", re.M)
_RRULE_RE = re.compile(r"^RRULE:", re.M)
_UID_RE = re.compile(r"^UID:([^\r\n]+)", re.M)

# Anti-echo belt 2 (design doc invariant #4): an event whose UID matches
# this convention was written BY hermes into her calendar in the first
# place (mail.send_event_ics's existing `fam-<event_id>@hermes-home`
# convention) -- never count it as "her" data, even if the write-target
# URL in config is stale or wrong (belt 1, below).
_HERMES_UID_RE = re.compile(r"^fam-.*@hermes-home$")


def _tally_ics(counts, ics_text):
    if not ics_text:
        return
    for block in _VEVENT_RE.findall(ics_text):
        m = _UID_RE.search(block)
        if m and _HERMES_UID_RE.match(m.group(1).strip()):
            continue  # our own echoed-back write, never hers to count
        counts["total"] += 1
        if _RRULE_RE.search(block):
            counts["with_rrule"] += 1
        if "BEGIN:VALARM" in block:
            counts["with_valarm"] += 1
        dm = _DTSTART_RE.search(block)
        if dm and "VALUE=DATE" in dm.group(1).upper():
            counts["all_day"] += 1
        else:
            counts["timed"] += 1


def _same_calendar(url_a, url_b):
    if not url_a or not url_b:
        return False
    return url_a.rstrip("/") == url_b.rstrip("/")


def probe(cfg, request=None):
    """Read-only reconnaissance across her iCloud calendars: what
    collections exist, whether each supports incremental sync, and a
    coarse feature count of their VEVENTs (timed/all_day/with_rrule/
    with_valarm/total). Writes NOTHING to the DB and NOTHING to iCloud --
    it only calls discover()/fetch_changes(), both pure network reads.

    Returns `{calendars: [{url, name, ctag, supports_sync_token}, ...],
    counts: {timed, all_day, with_rrule, with_valarm, total}, errors: []}`.

    Never raises: a missing ICLOUD_APP_PASSWORD, a network failure, a
    disallowed redirect target, or malformed XML all land in `errors`
    (calendars/counts then reflect whatever was salvaged, often nothing)
    instead of propagating an exception. Nothing here ever puts the
    password -- or any event content beyond UID/flags -- into the
    returned dict.

    Important finding I2 fix: if `extcal_read_calendars` is non-empty but
    matches NONE of the calendars discover() actually found (typo, case
    mismatch, unicode-normalization mismatch in a display name, ...),
    that is reported as an `errors` entry ("matched 0 of N") rather than
    silently looking identical to "she genuinely has zero calendars".
    """
    cfg = cfg or {}
    request = request or _request
    errors = []
    counts = _empty_counts()
    calendars_out = []

    calendars, discover_errors = _discover(cfg, request)
    errors.extend(discover_errors)

    write_url = cfg.get("extcal_write_calendar") or ""
    read_filter = set(cfg.get("extcal_read_calendars") or [])
    # eligible: calendars that aren't the write-target echo collection --
    # counted separately from calendars_out so a read_filter that matches
    # nothing can be told apart from "the only calendar found was the
    # write target" (a different, non-error situation).
    eligible = 0

    for calendar in calendars:
        # Anti-echo belt 1 (design doc invariant #4): never read back our
        # own write-target collection as if it were "her" data.
        if write_url and _same_calendar(calendar["url"], write_url):
            continue
        eligible += 1
        if read_filter and calendar["url"] not in read_filter and calendar["name"] not in read_filter:
            continue

        calendars_out.append({
            "url": calendar["url"],
            "name": calendar["name"],
            "ctag": calendar.get("ctag"),
            "supports_sync_token": calendar.get("supports_sync_token", False),
        })

        items, _new_token, sync_info = fetch_changes(
            cfg, calendar, sync_token=None, request=request)
        if items is None:
            errors.append(f"fetch failed for calendar: {calendar['name']} "
                           f"({sync_info.get('reason')})")
            continue
        for item in items:
            if item.get("deleted"):
                continue
            _tally_ics(counts, item.get("ics"))

    if read_filter and eligible > 0 and not calendars_out:
        errors.append(
            f"extcal_read_calendars matched 0 of {eligible} discovered "
            f"calendar(s) by url/name -- check spelling/case in config")

    return {"calendars": calendars_out, "counts": counts, "errors": errors}


# ---- ICS parsing (Task 2) -------------------------------------------------
#
# Stdlib-only, single-pass over unfolded content lines. A Component is a
# plain dict (not a class), so it round-trips freely through tests and
# other modules without importing a private type:
#
#   {uid, summary, location, status, seq, has_alarm,
#    dtstart_utc, dtend_utc, all_day,
#    recurrence_id_utc, exdates_utc, rrule}
#
# Every datetime field on a Component is an AWARE UTC `datetime` object
# (never naive, never a string) -- the datetime-vs-ISO-string boundary is
# `expand()`'s OUTPUT only (Occurrence), matching how `cal.py` itself works
# with aware datetimes internally but stores/exchanges start_utc/end_utc as
# ISO strings.
#
# `parse_ics` never raises: input that isn't ICS at all, a truncated
# BEGIN with no matching END, or any other garbage degrades to `[]`; a
# single malformed VEVENT inside an otherwise-good feed is dropped (see
# `_finalize_component`) without losing its siblings.

ALMATY = ZoneInfo("Asia/Almaty")


def _unfold(text):
    """RFC 5545 line unfolding: a line break immediately followed by a
    single SPACE or TAB is a FOLD, not a real line break -- the SPACE/TAB
    is removed and the two physical lines rejoin into one logical content
    line. Real-world feeds are inconsistent about CRLF vs bare LF/CR, so
    all three are normalized to `\\n` first.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for line in text.split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _split_property_line(line):
    """One unfolded content line -> (NAME, {PARAM: value}, raw_value), or
    None if there is no unquoted top-level colon at all (not a property
    line -- e.g. stray garbage). Param NAMES are upper-cased for matching;
    param VALUES keep their original case (TZID is case-sensitive, e.g.
    `Asia/Almaty`), with surrounding quotes stripped if present.
    """
    in_quotes = False
    split_at = None
    for i, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ":" and not in_quotes:
            split_at = i
            break
    if split_at is None:
        return None
    head, value = line[:split_at], line[split_at + 1:]
    parts = head.split(";")
    name = parts[0].strip().upper()
    if not name:
        return None
    params = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        pname, _, pval = part.partition("=")
        pval = pval.strip()
        if len(pval) >= 2 and pval[0] == '"' and pval[-1] == '"':
            pval = pval[1:-1]
        params[pname.strip().upper()] = pval
    return name, params, value


def _unescape_text(value):
    """RFC 5545 TEXT unescaping: `\\\\` -> `\\`, `\\;` -> `;`, `\\,` -> `,`,
    `\\n`/`\\N` -> newline. An unrecognized escape is left exactly as
    written (lenient -- a third-party feed's minor quirk here must not
    lose the whole event).
    """
    if not value:
        return ""
    out = []
    i, n = 0, len(value)
    while i < n:
        c = value[i]
        if c == "\\" and i + 1 < n:
            nxt = value[i + 1]
            if nxt == "\\":
                out.append("\\"); i += 2; continue
            if nxt == ";":
                out.append(";"); i += 2; continue
            if nxt == ",":
                out.append(","); i += 2; continue
            if nxt in ("n", "N"):
                out.append("\n"); i += 2; continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse_dt_value(raw, params):
    """One DTSTART/DTEND/RECURRENCE-ID/EXDATE value -> (aware UTC
    datetime, all_day) -- or (None, False) if unparseable. Never raises.

      - `VALUE=DATE` (or a bare 8-digit value): all-day -- interpreted as
        an Asia/Almaty calendar date at local midnight, converted to UTC
        (documented assumption -- see task-2-report.md's Apple-format
        assumptions list).
      - a value ending in `Z`: already UTC, used as-is.
      - `TZID=<iana-name>`: localized via `zoneinfo.ZoneInfo`, then
        converted to UTC. An unresolvable TZID (unknown name, or no
        system tzdata for it) falls back to Asia/Almaty rather than
        failing the whole component.
      - no `Z`, no `TZID`, no `VALUE=DATE` ("floating" time): assumed
        Asia/Almaty local (documented assumption -- her devices/locale).
    """
    if not raw:
        return None, False
    raw = raw.strip()
    value_type = (params.get("VALUE") or "").upper()
    tzid = params.get("TZID")
    try:
        if value_type == "DATE" or (len(raw) == 8 and raw.isdigit()):
            d = datetime.strptime(raw, "%Y%m%d")
            local_midnight = d.replace(tzinfo=ALMATY)
            return local_midnight.astimezone(timezone.utc), True
        is_utc = raw.endswith("Z") or raw.endswith("z")
        core = raw[:-1] if is_utc else raw
        naive = datetime.strptime(core, "%Y%m%dT%H%M%S")
        if is_utc:
            return naive.replace(tzinfo=timezone.utc), False
        tz = ALMATY
        if tzid:
            try:
                tz = ZoneInfo(tzid)
            except Exception:
                tz = ALMATY  # unresolvable TZID -- fallback, not a failure
        return naive.replace(tzinfo=tz).astimezone(timezone.utc), False
    except Exception:
        return None, False


def _new_component():
    return {
        "uid": None, "summary": "", "location": "", "status": None,
        "seq": 0, "has_alarm": False,
        "dtstart_raw": None, "dtend_raw": None, "recurrence_id_raw": None,
        "exdate_raws": [], "rrule": None,
    }


def _apply_property(cur, name, params, value):
    if name == "UID":
        cur["uid"] = _unescape_text(value.strip()) or None
    elif name == "SUMMARY":
        cur["summary"] = _unescape_text(value)
    elif name == "LOCATION":
        cur["location"] = _unescape_text(value)
    elif name == "STATUS":
        cur["status"] = value.strip().upper() or None
    elif name == "SEQUENCE":
        try:
            cur["seq"] = int(value.strip())
        except (ValueError, AttributeError):
            cur["seq"] = 0
    elif name == "DTSTART":
        cur["dtstart_raw"] = (value, dict(params))
    elif name == "DTEND":
        cur["dtend_raw"] = (value, dict(params))
    elif name == "RECURRENCE-ID":
        cur["recurrence_id_raw"] = (value, dict(params))
    elif name == "EXDATE":
        for v in value.split(","):
            v = v.strip()
            if v:
                cur["exdate_raws"].append((v, dict(params)))
    elif name == "RRULE":
        cur["rrule"] = value.strip() or None
    # Everything else (DESCRIPTION, ORGANIZER, ATTENDEE, TRANSP, ...) is
    # outside this module's declared field list -- silently ignored.


def _finalize_component(cur):
    """Raw-value dict -> resolved Component, or None if it lacks a UID or
    a usable DTSTART (unusable as an occurrence either way -- this is
    where a single malformed VEVENT inside an otherwise-good feed gets
    dropped without losing its siblings).
    """
    if not cur["uid"]:
        return None
    dtstart_utc, all_day = (None, False)
    if cur["dtstart_raw"]:
        dtstart_utc, all_day = _parse_dt_value(*cur["dtstart_raw"])
    if dtstart_utc is None:
        return None
    dtend_utc = None
    if cur["dtend_raw"]:
        dtend_utc, _ = _parse_dt_value(*cur["dtend_raw"])
    if dtend_utc is None:
        # Design doc edge case #4: no DTEND -> 1 hour default duration,
        # regardless of all_day -- this is the literal fam convention
        # (NOT RFC 5545's own "1 day" all-day default).
        dtend_utc = dtstart_utc + timedelta(hours=1)
    recurrence_id_utc = None
    if cur["recurrence_id_raw"]:
        recurrence_id_utc, _ = _parse_dt_value(*cur["recurrence_id_raw"])
    exdates_utc = []
    for raw, params in cur["exdate_raws"]:
        dt, _ = _parse_dt_value(raw, params)
        if dt is not None:
            exdates_utc.append(dt)
    return {
        "uid": cur["uid"], "summary": cur["summary"], "location": cur["location"],
        "status": cur["status"], "seq": cur["seq"], "has_alarm": cur["has_alarm"],
        "dtstart_utc": dtstart_utc, "dtend_utc": dtend_utc, "all_day": all_day,
        "recurrence_id_utc": recurrence_id_utc, "exdates_utc": exdates_utc,
        "rrule": cur["rrule"],
    }


def parse_ics(text):
    """Raw VCALENDAR text -> `list[Component]` (VEVENTs only -- VTODO/
    VJOURNAL/VTIMEZONE are out of scope, matching the design doc). A
    nested VALARM's own properties are skipped entirely (only its
    PRESENCE, as `has_alarm`, is recorded); any other nested sub-component
    is skipped the same defensive way.

    Never raises: garbage input (not ICS, truncated, undecodable-as-text)
    yields `[]`; a single malformed VEVENT inside an otherwise-good feed
    is dropped (see `_finalize_component`) without losing its siblings.
    """
    if not text:
        return []
    try:
        text = text.lstrip("﻿")  # tolerate a leading UTF-8 BOM
        lines = _unfold(text)
        components = []
        cur = None
        nested_depth = 0
        for raw_line in lines:
            if not raw_line:
                continue
            parsed = _split_property_line(raw_line)
            if parsed is None:
                continue
            name, params, value = parsed

            if name == "BEGIN":
                kind = value.strip().upper()
                if cur is None:
                    if kind == "VEVENT":
                        cur = _new_component()
                    continue  # BEGIN:VCALENDAR/VTIMEZONE/... at top level
                nested_depth += 1
                if kind == "VALARM":
                    cur["has_alarm"] = True
                continue

            if name == "END":
                kind = value.strip().upper()
                if cur is not None and nested_depth == 0 and kind == "VEVENT":
                    finalized = _finalize_component(cur)
                    if finalized is not None:
                        components.append(finalized)
                    cur = None
                elif cur is not None and nested_depth > 0:
                    nested_depth -= 1
                continue

            if cur is None or nested_depth > 0:
                continue  # property outside any tracked VEVENT, or inside VALARM
            _apply_property(cur, name, params, value)
        return components
    except Exception:
        return []


# ---- recurrence expansion (Task 2) ----------------------------------------
#
# `dateutil.rrule` is NEVER imported at module level -- see
# `_load_rrule_module`'s docstring and the module docstring above. This
# keeps `import extcal` (and therefore `cli.py`, and every other `fam`
# command) working whether or not python-dateutil is installed.

def _load_rrule_module():
    """Deferred import -- the ONLY place in this module (or, transitively,
    anywhere in `fam`'s import graph via cli.py) that touches `dateutil`.
    Returns the `dateutil.rrule` module, or None if it isn't installed.
    Never raises.
    """
    try:
        from dateutil import rrule
        return rrule
    except ImportError:
        return None


def _coerce_utc_dt(value):
    """Accept an aware/naive `datetime` OR an ISO-8601 string for a window
    bound; a naive value is treated as already-UTC (the same forgiving
    rule as `gate._parse_utc`), so a caller can pass either a `datetime`
    or a plain ISO string produced elsewhere in `fam`. Returns None (never
    raises) on anything unparseable.
    """
    if value is None:
        return None
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _iso(dt):
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _occurrence_from(comp, start_utc, end_utc, recurrence_id_utc):
    return {
        "uid": comp["uid"],
        "recurrence_id": _iso(recurrence_id_utc),
        "title": comp.get("summary") or "",
        "start_utc": _iso(start_utc),
        "end_utc": _iso(end_utc),
        "all_day": bool(comp.get("all_day")),
        "location": comp.get("location") or "",
        "status": comp.get("status"),
        "seq": comp.get("seq") or 0,
        "has_alarm": bool(comp.get("has_alarm")),
    }


def _expand_master(master, w_start_utc, w_end_utc, rrule_mod):
    """One RRULE-bearing master Component -> list of (start_utc, end_utc)
    tuples inside [w_start_utc, w_end_utc], with EXDATE already
    subtracted. The RRULE is generated in Asia/Almaty LOCAL time (not raw
    UTC) and only converted to UTC per-result -- Almaty has a fixed UTC+5
    offset with no DST (see meds.py's own
    `_ALMATY = timezone(timedelta(hours=5))`), so this is not a
    DST-correctness fix, it's a day-ROLLOVER fix: evaluating a
    BYMONTHDAY/BYDAY rule directly against a UTC-shifted dtstart could
    land on the wrong calendar day for an early-morning local event (e.g.
    01:00 Almaty is still 20:00 UTC the PREVIOUS day). Generating in local
    time and converting each result back to UTC avoids that entirely.

    Never raises: an unparseable/unsupported RRULE string yields `[]`
    (that master silently contributes nothing rather than aborting the
    whole `expand()` call).
    """
    dtstart_utc = master["dtstart_utc"]
    duration = (master["dtend_utc"] - dtstart_utc) if master["dtend_utc"] else timedelta(hours=1)
    dtstart_local = dtstart_utc.astimezone(ALMATY)
    try:
        rule = rrule_mod.rrulestr(f"RRULE:{master['rrule']}", dtstart=dtstart_local)
        starts_local = rule.between(w_start_utc.astimezone(ALMATY),
                                     w_end_utc.astimezone(ALMATY), inc=True)
    except Exception:
        return []
    exdates_utc = master.get("exdates_utc") or []
    out = []
    for start_local in starts_local:
        start_utc = start_local.astimezone(timezone.utc)
        if any(abs((start_utc - ex).total_seconds()) < 1 for ex in exdates_utc):
            continue
        out.append((start_utc, start_utc + duration))
    return out


# Sentinel returned (as one EXTRA list entry, never in place of real
# occurrences) when `expand()` needed to expand at least one RRULE master
# and `python-dateutil` was not importable -- task brief hard requirement
# #3: absence of the library must never look like "zero recurrences".
# Every normal Occurrence key is present (so a caller that merely does
# `occ.get("title")` etc. without special-casing this never KeyErrors),
# but `uid` is None -- which a real occurrence (parse_ics drops anything
# without a UID) can never be -- and the extra `"error"`/`"detail"` keys
# make it unambiguous on inspection.
_DATEUTIL_MISSING_OCCURRENCE = {
    "uid": None, "recurrence_id": None, "title": None,
    "start_utc": None, "end_utc": None, "all_day": None,
    "location": None, "status": "error", "seq": None, "has_alarm": None,
}


def expand(components, window_start, window_end):
    """`list[Component]` -> `list[Occurrence]` inside [window_start,
    window_end] (inclusive at both ends; either bound may be an
    aware/naive `datetime` or an ISO-8601 string). `Occurrence` =
    `{uid, recurrence_id, title, start_utc, end_utc, all_day, location,
    status, seq, has_alarm}` -- `start_utc`/`end_utc`/`recurrence_id` are
    ISO-8601 UTC strings (matching how `cal.py` stores/exchanges events),
    or None only in the dateutil-missing sentinel described below.

    Never raises. RRULE handling:
      - an RRULE-bearing component with no RECURRENCE-ID is a "master" --
        expanded via `dateutil.rrule` (deferred import) into individual
        occurrences across the window, EXDATE removed, each carrying its
        own `recurrence_id` (its ORIGINAL, un-overridden start) so a
        caller can key any single instance by (uid, recurrence_id) --
        matching the design doc's "ключ вхождения" convention.
      - a component WITH a RECURRENCE-ID is an override: it REPLACES the
        master's generated occurrence at that original slot (never
        emitted alongside it), and its OWN (possibly moved) start/end
        decides window visibility -- an instance moved INTO the window
        appears even if its original slot was outside it, and vice versa.
      - a component with neither RRULE nor RECURRENCE-ID is a plain
        single occurrence, included iff its own start falls in the window.
      - `STATUS:CANCELLED` is carried through as the occurrence's
        `status`, never used to drop it from the result here -- deciding
        cancel-vs-drop is `plan_changes`' job (a later task), not this
        one's.

    dateutil-missing guard (task brief hard requirement #3): if ANY
    RRULE-bearing master needed expanding and `python-dateutil` is not
    importable, this does NOT silently return an empty/partial list as if
    there were simply no recurrences -- exactly one extra dict is
    appended (see `_DATEUTIL_MISSING_OCCURRENCE`), distinguishable from a
    real occurrence by `uid is None` or by the presence of an `"error"`
    key. Every non-recurring component (singles, overrides) is still
    expanded normally in this case; only the RRULE masters are skipped.
    """
    w_start = _coerce_utc_dt(window_start)
    w_end = _coerce_utc_dt(window_end)
    if w_start is None or w_end is None or not components:
        return []

    masters, overrides_by_uid, singles = [], {}, []
    for comp in components:
        if not comp or not comp.get("uid") or comp.get("dtstart_utc") is None:
            continue
        if comp.get("recurrence_id_utc") is not None:
            overrides_by_uid.setdefault(comp["uid"], []).append(comp)
        elif comp.get("rrule"):
            masters.append(comp)
        else:
            singles.append(comp)

    occurrences = []
    dateutil_missing = False
    rrule_mod = _load_rrule_module() if masters else None
    if masters and rrule_mod is None:
        dateutil_missing = True

    for master in masters:
        if rrule_mod is None:
            continue
        overrides = {ov["recurrence_id_utc"]: ov
                     for ov in overrides_by_uid.get(master["uid"], [])}
        for start_utc, end_utc in _expand_master(master, w_start, w_end, rrule_mod):
            if any(abs((rid - start_utc).total_seconds()) < 1 for rid in overrides):
                continue  # replaced by an override, emitted separately below
            occurrences.append(_occurrence_from(master, start_utc, end_utc,
                                                 recurrence_id_utc=start_utc))

    for ovs in overrides_by_uid.values():
        for ov in ovs:
            start_utc = ov["dtstart_utc"]
            end_utc = ov["dtend_utc"] or (start_utc + timedelta(hours=1))
            if not (w_start <= start_utc <= w_end):
                continue
            occurrences.append(_occurrence_from(
                ov, start_utc, end_utc, recurrence_id_utc=ov["recurrence_id_utc"]))

    for single in singles:
        start_utc = single["dtstart_utc"]
        end_utc = single["dtend_utc"] or (start_utc + timedelta(hours=1))
        if not (w_start <= start_utc <= w_end):
            continue
        occurrences.append(_occurrence_from(single, start_utc, end_utc,
                                             recurrence_id_utc=None))

    if dateutil_missing:
        sentinel = dict(_DATEUTIL_MISSING_OCCURRENCE)
        sentinel["error"] = "dateutil_missing"
        sentinel["detail"] = (
            "python-dateutil not installed; RRULE recurrences could not "
            "be expanded (non-recurring components were still processed)")
        occurrences.append(sentinel)

    occurrences.sort(key=lambda o: o.get("start_utc") or "")
    return occurrences
