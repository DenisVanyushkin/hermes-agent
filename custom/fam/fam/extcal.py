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
    be missing, that is surfaced as an entry in `expand()`'s own SEPARATE
    `errors` list (`expand()` returns `{"occurrences": [...], "errors":
    [...]}`, never a bare list) -- never a silently-empty/short result
    indistinguishable from "this calendar genuinely has no recurring
    events", and never a fake occurrence mixed into `occurrences` itself
    (an earlier design used a `{"error": "dateutil_missing", ...}`
    sentinel dict planted inside the SAME list as real occurrences --
    removed after review flagged it as a poison pill: a caller that
    forgot the `if occ.get("error")` guard would hand a `start_utc=None`
    dict straight to `cal.add()` and crash the whole batch. See
    `expand()`'s own docstring for the current contract.)
"""
import base64
import hashlib
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

# Task 5 (application layer) is the first part of this module that touches
# the DB -- everything above it (discover/fetch_changes/parse_ics/expand/
# plan_changes) is read-only network or pure. These are ordinary, always-
# present fam submodules (unlike `dateutil`, there is no missing-package
# concern here), and none of them import `extcal` back (only cli.py does,
# module-level) -- so a plain top-level import is safe, no deferred-import
# seam needed. `mail` (Task 7, fix-round 2) is the same story: its own
# google-auth/googleapiclient dependency is lazy-imported INSIDE
# send_event_email, never at module level (test_no_google_import.py pins
# this), so importing it here costs nothing and creates no cycle -- `mail`
# does not import `extcal` either.
from fam import audit, cal, mail, places, plans, rem

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


def fetch_changes(cfg, calendar, sync_token=None, request=None, force_full=False):
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
      - "periodic_full": `force_full=True` was passed AND a `sync_token`
        WAS supplied (fix-round 3, Critical finding C1 -- the rolling-
        horizon gap: a calendar that only ever sees `REPORT sync-
        collection` deltas never re-materializes an occurrence whose
        resource simply hasn't changed since the window last moved past
        it, so a rolling `expand()` window can silently stop inserting
        NEW occurrences of an untouched recurring series forever). The
        caller (Task 6's tick) decides WHEN this is due (a config-
        driven interval, tracked in `meta`) and skips offering the
        stored token at all THIS round -- `sync_token` here is only
        used to distinguish this mode from "initial_full" (no token
        ever existed) in the report; `_sync_collection` is never even
        attempted, exactly the "initial_full" code path (`_calendar_
        query` over the horizon window, same disappearance-sweep-
        eligible exhaustive listing) is reused verbatim, just labelled
        differently for observability. `sync_info["reason"]` is None,
        same as "initial_full" -- this is a scheduled, not a failure,
        full read.
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
    the WHOLE collection, unbounded by the horizon window. `force_full=
    True` with a `sync_token` present: also goes straight to calendar-
    query (the stored token is never offered to `_sync_collection`),
    but reports as "periodic_full", not "initial_full" -- see above.

    Never raises. On total failure returns `(None, None, {"mode":
    "error", "reason": "..."})`.

    `items` is a list of `{href, deleted, etag, ics}` dicts. `ics` is
    None for a tombstoned (deleted=True) item -- but a LIVE item
    (deleted=False) can ALSO carry `ics=None` (fix-round 3, finding R2:
    this docstring previously claimed otherwise, and a caller that
    believed it treated that case as "nothing to do" rather than "we
    could not actually read this one"): `_parse_multistatus_items`
    returns `ics=None` for ANY `<response>` whose status is not 404 --
    including a per-resource 403/500/507 on an otherwise-200 multistatus
    -- and also when the response IS 200 but its `<C:calendar-data>`
    element is missing or empty. A caller must not treat `deleted=False,
    ics=None` as equivalent to a real deletion.
    """
    cfg = cfg or {}
    request = request or _request
    calendar_url = calendar["url"] if isinstance(calendar, dict) else calendar

    if _auth_header(cfg) is None:
        return None, None, {"mode": "error", "reason": "missing_credentials"}

    fallback_reason = None
    # Fix-round 3 (Critical finding C1): `force_full` skips offering the
    # stored token to `_sync_collection` at all -- a periodic, SCHEDULED
    # full re-baseline (the caller's own decision, based on `meta`), not
    # a failure fallback. Falls straight through to the SAME `_calendar_
    # query` call below that "initial_full"/"fallback_full" already use;
    # only the reported mode differs (see this function's own docstring).
    if sync_token and not force_full:
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
    if force_full and sync_token:
        return items, None, {"mode": "periodic_full", "reason": None}
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


def _parse_ics_components(text):
    """Single shared implementation behind BOTH `parse_ics` and
    `parse_ics_with_count` (fix-round 4, Task 6): this is the ONE place in
    the codebase that decides "this line is where a VEVENT block begins"
    -- the exact same decision `_finalize_component` acts on. Returns
    `(components, vevent_count)`, where `vevent_count` is incremented at
    the SAME site `cur = _new_component()` is created, i.e. once per
    top-level `BEGIN:VEVENT` this loop recognized as the start of a
    component attempt.

    `vevent_count > len(components)` means this call dropped at least one
    VEVENT block: either `_finalize_component` rejected it (no usable
    UID/DTSTART), or the resource was truncated (a `BEGIN:VEVENT` with no
    matching `END:VEVENT` before the text ends). Both are real,
    observable data loss a caller may need to react to -- see
    `parse_ics_with_count`'s own docstring.

    A `BEGIN:VEVENT` line folded into the middle of another property's
    value (e.g. a DESCRIPTION whose continuation line, once unfolded,
    happens to read "...BEGIN:VEVENT...") is NOT counted here, structurally,
    not by a second pass over the text: `_unfold` already joins a folded
    continuation onto its parent line before this loop ever sees it, so
    `_split_property_line` reports the parent property's own NAME (e.g.
    "DESCRIPTION"), never "BEGIN" -- there is no separate line to
    misinterpret in the first place.

    Never raises: garbage input (not ICS, truncated, undecodable-as-text)
    yields `([], 0)`; a single malformed VEVENT inside an otherwise-good
    feed is dropped (see `_finalize_component`) without losing its
    siblings, and IS reflected in the count mismatch above.
    """
    if not text:
        return [], 0
    try:
        text = text.lstrip("﻿")  # tolerate a leading UTF-8 BOM
        lines = _unfold(text)
        components = []
        vevent_count = 0
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
                        vevent_count += 1
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
        return components, vevent_count
    except Exception:
        return [], 0


def parse_ics(text):
    """Raw VCALENDAR text -> `list[Component]` (VEVENTs only -- VTODO/
    VJOURNAL/VTIMEZONE are out of scope, matching the design doc). A
    nested VALARM's own properties are skipped entirely (only its
    PRESENCE, as `has_alarm`, is recorded); any other nested sub-component
    is skipped the same defensive way.

    Never raises: garbage input (not ICS, truncated, undecodable-as-text)
    yields `[]`; a single malformed VEVENT inside an otherwise-good feed
    is dropped (see `_finalize_component`) without losing its siblings.

    Thin wrapper over `_parse_ics_components` (fix-round 4) -- see
    `parse_ics_with_count` for the sibling entry point a caller that needs
    to know whether anything was silently dropped should use instead. Kept
    as a separate function, rather than requiring every existing caller to
    unpack a tuple, precisely so none of them (this module's own tests
    included) had to change for this task.
    """
    return _parse_ics_components(text)[0]


def parse_ics_with_count(text):
    """`parse_ics`, plus the raw `BEGIN:VEVENT` block count `_parse_ics_
    components` computed in the SAME pass (fix-round 4, Task 6). Returns
    `(components, vevent_count)`.

    Exists for a caller (`cli.py`'s per-resource "did the parser silently
    drop a component" guard) that must detect a shortfall between "VEVENT
    blocks present" and "components successfully parsed" WITHOUT
    re-implementing "what counts as the start of a VEVENT block" as a
    second, independent piece of code -- three fix rounds in a row
    (task-6-report.md) reintroduced exactly that drift: an external
    line-counter that disagreed with `parse_ics`'s own notion of a VEVENT
    boundary on case, line-ending convention, or whitespace-around-colon,
    each time silencing the very guard it was supposed to be. Both
    `parse_ics` and this function read the result of the SAME single
    decision (`_parse_ics_components`), made once per call -- there is
    structurally nothing left for a second implementation to drift from.

    `vevent_count > len(components)` means this resource lost at least
    one VEVENT block this call: caller should treat the resource as
    untrustworthy this round (exclude it from any disappearance sweep,
    record an error) rather than silently tombstoning whatever local row
    it can no longer see.
    """
    return _parse_ics_components(text)


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
    """One RRULE-bearing master Component -> `(occurrence_tuples, error)`,
    where `occurrence_tuples` is a list of `(start_utc, end_utc)` inside
    [w_start_utc, w_end_utc] with EXDATE already subtracted, and `error` is
    `None` on success or a short diagnostic string on failure. The RRULE is
    generated in Asia/Almaty LOCAL time (not raw UTC) and only converted to
    UTC per-result -- Almaty has a fixed UTC+5 offset with no DST (see
    meds.py's own `_ALMATY = timezone(timedelta(hours=5))`), so this is not
    a DST-correctness fix, it's a day-ROLLOVER fix: evaluating a
    BYMONTHDAY/BYDAY rule directly against a UTC-shifted dtstart could land
    on the wrong calendar day for an early-morning local event (e.g. 01:00
    Almaty is still 20:00 UTC the PREVIOUS day). Generating in local time
    and converting each result back to UTC avoids that entirely.

    Never raises: an unparseable/unsupported RRULE string (a real Apple
    quirk `dateutil.rrulestr` doesn't accept -- non-standard BYSETPOS, an
    unusual WKST, a floating-time UNTIL instead of UTC, ...) returns `([],
    "<reason>")` -- the caller surfaces that reason in `expand()`'s
    `errors` list (review finding I2: a silently-vanishing series is the
    same failure mode as the dateutil-missing case, just from the other
    end, and gets the same treatment here, not a bare `except: return []`).
    """
    dtstart_utc = master["dtstart_utc"]
    # dtend_utc is guaranteed non-None by _finalize_component (missing
    # DTEND already defaulted to +1h there) -- no fallback needed here.
    duration = master["dtend_utc"] - dtstart_utc
    dtstart_local = dtstart_utc.astimezone(ALMATY)
    try:
        rule = rrule_mod.rrulestr(f"RRULE:{master['rrule']}", dtstart=dtstart_local)
        starts_local = rule.between(w_start_utc.astimezone(ALMATY),
                                     w_end_utc.astimezone(ALMATY), inc=True)
    except Exception as e:
        return [], (f"RRULE {master.get('rrule')!r} for uid={master.get('uid')!r} "
                     f"could not be parsed/evaluated ({type(e).__name__}: {e})")
    exdates_utc = master.get("exdates_utc") or []
    out = []
    for start_local in starts_local:
        start_utc = start_local.astimezone(timezone.utc)
        if any(abs((start_utc - ex).total_seconds()) < 1 for ex in exdates_utc):
            continue
        out.append((start_utc, start_utc + duration))
    return out, None


def expand(components, window_start, window_end):
    """`list[Component]` -> `{"occurrences": list[Occurrence], "errors":
    list[str]}` inside [window_start, window_end] (inclusive at both ends;
    either bound may be an aware/naive `datetime` or an ISO-8601 string).
    `Occurrence` = `{uid, recurrence_id, title, start_utc, end_utc,
    all_day, location, status, seq, has_alarm}` -- `start_utc`/`end_utc`/
    `recurrence_id` are ISO-8601 UTC strings (matching how `cal.py`
    stores/exchanges events).

    This return shape -- a dict with a SEPARATE `errors` list, the same
    convention `probe()` already uses in this module -- is deliberate
    (coordinator review finding C1): every entry in `occurrences` is a
    real, fully-populated Occurrence, so an error can never be mistaken
    for data by a caller that forgets to special-case it. The earlier
    design (one shape, with a `{"error": ...}` sentinel dict mixed into
    the same list as real occurrences) made a caller's `if occ.get
    ("error")` check optional-by-construction -- a single skipped guard
    anywhere in a Task 4 pipeline would hand a `start_utc=None` dict to
    `cal.add()` and crash the whole tick batch, including every correctly
    expanded occurrence alongside it. With this shape, "treat every item
    in `occurrences` as real" requires no discipline from the caller at
    all -- there is structurally nowhere for a fake occurrence to hide.

    Never raises (top-level try/except backstop, matching `parse_ics`'s
    own style, on top of defensive checks at every step): a malformed
    window bound, an empty/None `components`, or a non-dict entry inside
    `components` all degrade to a `errors` entry rather than an
    exception -- this docstring's "never raises" claim is backed by a
    dedicated garbage-input test, the same standard `parse_ics` is held to.

    RRULE handling:
      - an RRULE-bearing component with no RECURRENCE-ID is a "master" --
        expanded via `dateutil.rrule` (deferred import) into individual
        occurrences across the window, EXDATE removed, each carrying its
        own `recurrence_id` (its ORIGINAL, un-overridden start) so a
        caller can key any single instance by (uid, recurrence_id) --
        matching the design doc's "ключ вхождения" convention. An RRULE
        `dateutil` can't parse/evaluate contributes NO occurrences from
        that master AND one entry in `errors` naming the uid and reason
        (finding I2) -- never a silent gap indistinguishable from "this
        series produced zero instances in this window".
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
    importable, one entry is appended to `errors` (`"python-dateutil not
    installed; ..."`) -- `occurrences` for those masters is simply empty,
    which is now safe to be "just empty" precisely because the error
    lives in a channel a real occurrence can never appear in. Every
    non-recurring component (singles, overrides) is still expanded
    normally in this case; only the RRULE masters are skipped.
    """
    errors = []
    try:
        w_start = _coerce_utc_dt(window_start)
        w_end = _coerce_utc_dt(window_end)
        if w_start is None or w_end is None:
            errors.append("expand(): invalid/unparseable window_start or window_end")
            return {"occurrences": [], "errors": errors}
        if not components:
            return {"occurrences": [], "errors": errors}

        masters, overrides_by_uid, singles = [], {}, []
        for comp in components:
            if not isinstance(comp, dict):
                errors.append(f"expand(): skipped a non-dict component ({type(comp).__name__})")
                continue
            if not comp.get("uid") or comp.get("dtstart_utc") is None:
                continue
            if comp.get("recurrence_id_utc") is not None:
                overrides_by_uid.setdefault(comp["uid"], []).append(comp)
            elif comp.get("rrule"):
                masters.append(comp)
            else:
                singles.append(comp)

        occurrences = []
        rrule_mod = _load_rrule_module() if masters else None
        if masters and rrule_mod is None:
            errors.append(
                "python-dateutil not installed; RRULE recurrences could not "
                "be expanded (non-recurring components were still processed)")

        for master in masters:
            if rrule_mod is None:
                continue
            overrides = {ov["recurrence_id_utc"]: ov
                         for ov in overrides_by_uid.get(master["uid"], [])}
            expanded, err = _expand_master(master, w_start, w_end, rrule_mod)
            if err:
                errors.append(f"expand(): {err}")
            for start_utc, end_utc in expanded:
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

        occurrences.sort(key=lambda o: o.get("start_utc") or "")
        return {"occurrences": occurrences, "errors": errors}
    except Exception as e:
        errors.append(f"expand(): unexpected failure ({type(e).__name__})")
        return {"occurrences": [], "errors": errors}


# ---- reconciliation (Task 4) -----------------------------------------------
#
# `plan_changes` is the pure decision layer between what `expand()` reports
# as currently live on her iCloud calendars (`remote_occurrences`, a
# `list[Occurrence]`) and what Hermes already has on file (`local_snapshot`).
# Like every other function in this module it NEVER touches the DB, the
# network, or the system clock -- "now" is the caller's `now_utc` (a
# `datetime` or ISO-8601 string, same forgiving coercion as `expand()`'s own
# window bounds), never `datetime.now()`. Applying a returned `Changeset` to
# the DB is a LATER task (T5) -- nothing here writes anything anywhere.
#
# Contract -- `local_snapshot`:
#   {"events": [EventRow, ...], "plans": [PlanRow, ...]}
#     EventRow = {id, owner, external_uid, external_seq, status, title,
#                 start_utc, end_utc, external_location}
#     PlanRow  = {id, owner, external_uid, status, title, deadline,
#                 external_location}
#
# `external_location` is schema v12's column of the same name, read
# VERBATIM off the row -- a plain `dict(row)` of `SELECT * FROM events/
# plans` already satisfies this contract with no mapping work at all (that
# is the point of the column; see the fix-round-4 note further down). It
# is the raw iCloud LOCATION text `apply_changes` last wrote, which is
# exactly what `_event_diff`/`_plan_diff` compare the incoming
# occurrence's LOCATION against. NEVER substitute the row's `notes` (human
# text -- see the note below) or its resolved place's `name` (Hermes's own
# canonical spelling, not necessarily byte-identical to her phone's free
# text) for it: either one risks a spurious "changed" on every tick
# forever, or a real edit on the phone silently never landing.
#
# `owner` is 'hermes' or 'iphone' (schema v12's CHECK). `status` is the
# row's own status column ('active'/'cancelled'/'done' for events,
# 'open'/'dropped'/'done' for plans) -- tombstoned (cancelled/dropped) rows
# MUST still be included so a re-appearing remote item can be recognized as
# "already tombstoned, do not resurrect" rather than looking new.
#
# Schema v12 gives `events`/`plans` exactly ONE `external_uid TEXT` column
# (partial UNIQUE: one local row per value) and no separate recurrence-id
# column of its own, so a recurring series' individual occurrences are told
# apart by encoding BOTH the ICS UID and the RECURRENCE-ID into that single
# column -- see `_occurrence_key` below for the exact, LENGTH-PREFIXED
# encoding (`f"u{len(uid)}:{uid}"`, or `f"u{len(uid)}:{uid}r{len(rid)}:{rid}"`
# when there is a recurrence_id). This is a REQUIRED CONTRACT for T5 (the DB
# writer) and T6 (the tick): `external_uid` MUST be persisted using this
# exact same `_occurrence_key(uid, recurrence_id)` encoding, or (a) a moved
# recurring instance will look like a brand-new occurrence on the next sync
# instead of an update, and (b) if a caller ever needs to recover the plain
# `uid`/`recurrence_id` back out of a stored `external_uid` (e.g. to look up
# a fetch_changes item's href/etag by uid), a naive `"::"`.split()-style
# un-join would be ambiguous -- RFC 5545 does not forbid a literal `"::"`
# inside a UID, so `uid="A::B"` with no recurrence_id and `uid="A"` with
# `recurrence_id="B"` would collide under a bare separator-join. The
# length-prefix makes every key unambiguously re-parseable regardless of
# what characters the uid/recurrence_id themselves contain.
#
# `local_snapshot` is expected to hold every CANDIDATE row for this sync:
# every owner='iphone' row (any status -- tombstones must be visible) whose
# start_utc/deadline falls inside the SAME horizon window `remote_occurrences`
# was fetched for, plus every owner='hermes' row with status active/open
# (candidates for fuzzy-collision detection only, see guard below). Rows
# outside that window are the CALLER's concern to exclude; this function has
# no window bounds of its own to filter by.
#
# Contract -- `Changeset`:
#   {"events": {"insert": [...], "update": [...], "cancel": [...]},
#    "plans":  {"insert": [...], "update": [...], "drop": [...]},
#    "collisions": [...]}
#
#   - insert entries (events): {title, start_utc, end_utc, location,
#     external_uid, external_seq, owner:"iphone"}.
#   - insert entries (plans): {title, deadline, location, external_uid,
#     owner:"iphone"}.
#     NEITHER carries external_href/external_etag: those live per-HREF on
#     the raw `fetch_changes` item, not per-Occurrence (one recurring
#     master's single ICS resource expands into MANY occurrences sharing one
#     href/etag) -- T5/T6 must attach them itself, keyed by `uid`, from the
#     `fetch_changes` items it already has alongside `remote_occurrences`.
#   - update entries: {"id": <local row id>, "changes": {field: (was, now)}}
#     -- same shape as `seed.diff()`'s `Diff.updates`, deliberately (task
#     brief: reuse seed.py's style). Compared fields: title, start_utc,
#     end_utc, location (events); title, deadline, location (plans).
#   - cancel/drop entries: {"id": <local row id>, "external_uid": <key>}.
#   - collisions entries: {"branch": "events"|"plans", "local_id": <hermes
#     row id>, "remote_uid", "recurrence_id", "title", and "start_utc"
#     (events) or "deadline" (plans)} -- a fuzzy match against an
#     owner='hermes' row (design doc rule #2: she added the same thing in
#     both places). Her iPhone copy is NOT inserted (would duplicate the
#     alarm-owning Hermes row) and the Hermes row is NOT updated from it
#     either (its VALARM/ownership stays untouched) -- this is purely a
#     reporting channel for T6's audit/nightly-summary.
#
# Guard (rule #3, the most important one, tested explicitly): owner='hermes'
# rows are NEVER placed in insert/update/cancel/drop for either branch. They
# only ever appear as fuzzy-match CANDIDATES (read-only lookups) -- ingest
# has no path that can mutate what Hermes itself created.
#
# Tombstone semantics: a local row already at a terminal status
# ('cancelled'/'dropped') is left alone even if the SAME uid/recurrence_id
# reappears in `remote_occurrences` looking "active" again (a sync-token
# replay, or Apple re-sending an unchanged resource) -- it is neither
# updated nor re-inserted. This is a one-way ratchet: once cancelled/dropped
# locally, a row only leaves that state via an explicit, separate action
# (not implemented by this function), never by this reconciliation pass.

def _occurrence_key(uid, recurrence_id):
    """The composite occurrence-identity convention stored in the schema's
    single `external_uid` column (see the module note above): a
    LENGTH-PREFIXED encoding, `f"u{len(uid)}:{uid}"` for a non-recurring
    item (or a recurring series' own un-keyed identity), or
    `f"u{len(uid)}:{uid}r{len(recurrence_id)}:{recurrence_id}"` for one
    specific occurrence of a recurring series. `recurrence_id` here is
    already the ISO-8601 UTC string `expand()` puts on an Occurrence -- the
    ORIGINAL slot's time, not the current (possibly moved) one -- so this
    key stays stable across a moved occurrence, which is exactly what lets a
    later sync recognize "same occurrence, new time" as an UPDATE rather
    than a new row.

    Length-prefixed rather than a bare `f"{uid}::{recurrence_id}"` join
    (review finding I3): RFC 5545 does not forbid a literal separator
    sequence inside a UID, so a plain-join key is ambiguous on its face --
    `uid="A::B"` with no recurrence_id and `uid="A"` with
    `recurrence_id="B"` would produce the exact same string. Prefixing each
    part with its own exact length makes the encoding unambiguous to
    re-parse regardless of what characters `uid`/`recurrence_id` contain --
    THIS is the contract T5/T6 must follow when persisting/reading
    `external_uid` (see the module note above).
    """
    if not recurrence_id:
        return f"u{len(uid)}:{uid}"
    return f"u{len(uid)}:{uid}r{len(recurrence_id)}:{recurrence_id}"


def _pc_norm_text(v):
    return (v or "").strip()


def _deadline_from_occurrence(occ):
    """All-day Occurrence -> local (Asia/Almaty) 'YYYY-MM-DD' deadline date.
    Design doc rule: single-day -> that day; multi-day -> the END date.

    RFC 5545 quirk this has to work around: `parse_ics`/`expand` (Task 2,
    already landed, out of scope to edit here) do NOT adjust for VALUE=DATE's
    end-EXCLUSIVE convention -- an all-day DTEND is officially the day AFTER
    the last real day, but the parser treats DTSTART/DTEND identically. A
    component with no real DTEND at all falls back (in `_finalize_component`)
    to a 1-HOUR default duration, not RFC 5545's own 1-day all-day default --
    so a single-day all-day Occurrence's `end_utc` is only ~1h after
    `start_utc`, while a genuine multi-day DTEND always differs from DTSTART
    by a whole number of 24h days. That duration gap is exactly what tells
    the two cases apart here: <=1h duration means "no real DTEND" (single
    day, use the start date); a longer duration means a real (exclusive)
    DTEND, so its local date is stepped back by one day to land on the
    actual last day of the event.
    """
    start_utc = _coerce_utc_dt(occ.get("start_utc"))
    if start_utc is None:
        return None
    start_local_date = start_utc.astimezone(ALMATY).date()
    end_utc = _coerce_utc_dt(occ.get("end_utc"))
    if end_utc is None or (end_utc - start_utc) <= timedelta(hours=1):
        return start_local_date.isoformat()
    end_local_date = (end_utc.astimezone(ALMATY) - timedelta(days=1)).date()
    if end_local_date < start_local_date:
        return start_local_date.isoformat()
    return end_local_date.isoformat()


def _event_diff(existing, occ):
    """{field: (was, now)} for whatever differs between a matched local
    EventRow and the remote Occurrence it was linked to -- title, start_utc,
    end_utc, location. Datetimes compare by PARSED value (via
    `_coerce_utc_dt`), never by raw string, so a harmless formatting
    difference (e.g. `+00:00` vs `Z`, or a dropped/added `:00` seconds
    field) between what `local_snapshot` happens to store and what
    `expand()` emits is never mistaken for a real change.

    The local side of the location comparison is the row's
    `external_location` COLUMN (fix-round 4) -- the raw iCloud text as
    stored by `apply_changes`, read straight off the row with no parsing
    of any kind. The emitted change key stays `"location"` (that is the
    Changeset's own field name, consumed by `_apply_event_update`); only
    the source of the "was" value is the dedicated column.
    """
    changes = {}
    if _pc_norm_text(existing.get("title")) != _pc_norm_text(occ.get("title")):
        changes["title"] = (existing.get("title"), occ.get("title"))
    new_start = _coerce_utc_dt(occ.get("start_utc"))
    old_start = _coerce_utc_dt(existing.get("start_utc"))
    if _iso(new_start) != _iso(old_start):
        changes["start_utc"] = (existing.get("start_utc"), _iso(new_start))
    new_end = _coerce_utc_dt(occ.get("end_utc"))
    old_end = _coerce_utc_dt(existing.get("end_utc"))
    if _iso(new_end) != _iso(old_end):
        changes["end_utc"] = (existing.get("end_utc"), _iso(new_end))
    old_location = existing.get("external_location")
    if _pc_norm_text(old_location) != _pc_norm_text(occ.get("location")):
        changes["location"] = (old_location, occ.get("location"))
    return changes


def _plan_diff(existing, occ, deadline):
    """Same idea as `_event_diff` for the plans branch: title, deadline
    (already resolved by `_deadline_from_occurrence`), location -- the
    latter compared against the row's `external_location` column, same as
    `_event_diff` (fix-round 4)."""
    changes = {}
    if _pc_norm_text(existing.get("title")) != _pc_norm_text(occ.get("title")):
        changes["title"] = (existing.get("title"), occ.get("title"))
    if (existing.get("deadline") or None) != (deadline or None):
        changes["deadline"] = (existing.get("deadline"), deadline)
    old_location = existing.get("external_location")
    if _pc_norm_text(old_location) != _pc_norm_text(occ.get("location")):
        changes["location"] = (old_location, occ.get("location"))
    return changes


_FUZZY_WINDOW_SECONDS = 15 * 60


def _fuzzy_match_event(occ, hermes_rows, claimed):
    """Design doc rule #2's fuzzy link for the events branch: an
    owner='hermes' row whose start is within +-15 minutes of `occ` AND whose
    title matches casefold-insensitively (Cyrillic-safe -- SQLite's NOCASE
    collation does not fold Cyrillic case, so this comparison is done here,
    in Python, via `str.casefold()`, never left to a caller's SQL). Only
    'active' (or status-less, for a lenient snapshot) Hermes rows are
    candidates -- a cancelled/done Hermes row is not a live duplicate to
    protect. `claimed` prevents one Hermes row from being fuzzy-matched to
    two different remote occurrences in the same pass.
    """
    occ_start = _coerce_utc_dt(occ.get("start_utc"))
    occ_title = _pc_norm_text(occ.get("title")).casefold()
    if occ_start is None or not occ_title:
        return None
    for row in hermes_rows:
        if not isinstance(row, dict):
            continue
        rid = row.get("id")
        if rid is None or rid in claimed:
            continue
        if row.get("status") not in (None, "active"):
            continue
        row_start = _coerce_utc_dt(row.get("start_utc"))
        if row_start is None:
            continue
        if abs((occ_start - row_start).total_seconds()) > _FUZZY_WINDOW_SECONDS:
            continue
        if _pc_norm_text(row.get("title")).casefold() != occ_title:
            continue
        claimed.add(rid)
        return row
    return None


def _fuzzy_match_plan(occ, hermes_rows, deadline, claimed):
    """Plans-branch analogue of `_fuzzy_match_event`: same deadline date
    (there is no "time" to compare +-15 minutes on an all-day item) and a
    casefold-insensitive title match, against 'open' (or status-less)
    owner='hermes' plan rows only.
    """
    occ_title = _pc_norm_text(occ.get("title")).casefold()
    if not occ_title or not deadline:
        return None
    for row in hermes_rows:
        if not isinstance(row, dict):
            continue
        rid = row.get("id")
        if rid is None or rid in claimed:
            continue
        if row.get("status") not in (None, "open"):
            continue
        if row.get("deadline") != deadline:
            continue
        if _pc_norm_text(row.get("title")).casefold() != occ_title:
            continue
        claimed.add(rid)
        return row
    return None


def _disappearance_in_scope(existing, field, now_dt):
    """Whether a local row that VANISHED from this sync's remote batch is
    close enough to "now" for its absence to be trustworthy evidence of a
    real deletion on the phone, rather than having simply aged out of the
    read window's own floor (today-1 day, see `_time_range`) on its own --
    without this, an event that started more than a day ago would look
    "cancelled" on every sync forever after it naturally scrolls out of the
    fetch window, even though nobody touched it on the phone.

    Fails CLOSED (returns False, i.e. SUPPRESS the cancel/drop) whenever the
    relevant date can't be determined at all -- matching this module's
    overall "never guess into a destructive action" posture (mirrors how
    `_request` degrades to `None` rather than half-acting on a botched
    response): an uncertain `now_utc` or an uncertain local row date must
    never itself be the reason something gets tombstoned.
    """
    if now_dt is None:
        return False
    dt = _coerce_utc_dt(existing.get(field))
    if dt is None:
        return False
    return dt >= (now_dt - timedelta(days=1))


def plan_changes(remote_occurrences, local_snapshot, now_utc):
    """`(list[Occurrence], local_snapshot, now_utc) -> Changeset` -- the pure
    reconciliation decision layer (see the module note above for the full
    `local_snapshot`/`Changeset` contract). Never raises: malformed input
    anywhere (a non-list `remote_occurrences`, a non-dict `local_snapshot`,
    garbage rows/occurrences mixed in with good ones, an unparseable
    `now_utc`) degrades to "skip that one item" or "treat that one guard as
    closed", never an exception -- a single bad occurrence must not sink
    reconciliation for every OTHER occurrence in the same batch, matching
    `parse_ics`'s own per-component defensiveness.

    Linking, per occurrence: primary path is by `_occurrence_key` (ICS
    UID(+RECURRENCE-ID)) against the matching branch's owner='iphone' rows.
    On a miss, an EXACT `external_uid` match against an owner='hermes' row
    (fix-round 2, finding N4 -- see the module note above `hermes_events_
    by_uid`/`hermes_plans_by_uid`) is a quiet no-op: this is the SAME
    occurrence, already adopted, not a coincidence and not new data. Only
    past that is fuzzy-link against owner='hermes' rows tried at all
    (rule #2) -- a hit there is a `collisions` entry, NOT an insert/update.
    A miss on all three is a plain insert (unless the occurrence itself
    carries STATUS:CANCELLED, in which case there is nothing to insert OR
    cancel -- it never existed locally to begin with).

    Guard (rule #3): owner='hermes' rows never appear in insert/update/
    cancel/drop for either branch -- `events_by_key`/`plans_by_key` below are
    built from owner='iphone' rows ONLY, so a hermes row is structurally
    unreachable as a write target; it can only ever be a fuzzy-match
    candidate.

    Branch-crossing edge case: if the SAME key was previously imported into
    the OTHER branch (an item flipped between all-day and timed between two
    syncs), the stale other-branch row is cancelled/dropped and this
    occurrence is then processed fresh on its NEW branch (insert, or a fuzzy
    check) -- it is never left stranded as a phantom in the wrong table.

    Intra-batch dedup (review finding I2): if `remote_occurrences` itself
    contains two occurrences with the SAME `_occurrence_key` for the SAME
    branch -- e.g. she is visible to the same underlying iCloud event
    through two of her subscribed calendars at once (`extcal_read_calendars
    == []` reads all of them) -- only the FIRST one seen is processed; every
    later repeat is silently skipped via the same `seen_events`/`seen_plans`
    sets used for disappearance tracking. Without this, two identical
    occurrences would both see `existing=None` and both queue an insert
    carrying the same `external_uid`, an internally-inconsistent `Changeset`
    that would only fail later, at T5's write time, against schema v12's
    partial UNIQUE index.

    Disappearance: any owner='iphone' row not seen in this batch (by key) is
    cancel/drop-worthy UNLESS it is already tombstoned, or falls outside
    `_disappearance_in_scope`'s window-floor safety check.
    """
    changeset = {
        "events": {"insert": [], "update": [], "cancel": []},
        "plans": {"insert": [], "update": [], "drop": []},
        "collisions": [],
    }
    try:
        remote_occurrences = list(remote_occurrences or [])
        local_snapshot = local_snapshot if isinstance(local_snapshot, dict) else {}
        local_events = local_snapshot.get("events") or []
        local_plans = local_snapshot.get("plans") or []
        now_dt = _coerce_utc_dt(now_utc)

        events_by_key, hermes_events = {}, []
        for row in local_events:
            if not isinstance(row, dict):
                continue
            owner = row.get("owner")
            if owner == "iphone":
                key = row.get("external_uid")
                if key:
                    events_by_key[key] = row
            elif owner == "hermes":
                hermes_events.append(row)

        plans_by_key, hermes_plans = {}, []
        for row in local_plans:
            if not isinstance(row, dict):
                continue
            owner = row.get("owner")
            if owner == "iphone":
                key = row.get("external_uid")
                if key:
                    plans_by_key[key] = row
            elif owner == "hermes":
                hermes_plans.append(row)

        seen_events, seen_plans = set(), set()
        # SEPARATE claimed-sets per branch (review finding C1): events and
        # plans have independent autoincrement PKs, so id=3 in `events` and
        # id=3 in `plans` are two unrelated rows that both routinely exist
        # at once. A single shared claimed-set keyed on the bare `id` would
        # let a fuzzy-claim on a hermes EVENT with id=3 wrongly suppress a
        # fuzzy-claim on an entirely different hermes PLAN that also happens
        # to have id=3 -- silently turning what should be a `collisions`
        # entry into a plain (duplicate) insert. Keeping the two branches'
        # claimed-sets independent removes the cross-table id collision
        # entirely, without needing a synthetic (branch, id) tuple key.
        claimed_hermes_events, claimed_hermes_plans = set(), set()

        # Fix-round 2, finding N4: an already-ADOPTED occurrence (`fam
        # cal adopt`) keeps its own `external_uid` after its owner flips
        # to 'hermes' -- it is no longer reachable through `events_by_
        # key`/`plans_by_key` (rule #3: those are 'iphone'-only), so it
        # falls through to the rule #2 fuzzy (title+time / title+deadline)
        # path below on every later resync of the SAME occurrence. Fuzzy
        # matching there is a HEURISTIC for two independently-created
        # things that merely look alike; here the incoming occurrence
        # and the local row are, by construction, the exact same CalDAV
        # occurrence (identical uid+recurrence_id key) -- not a
        # coincidence to report as a `collisions` entry, and not new
        # data to insert (a second row for the same external_uid would
        # collide with the partial UNIQUE index anyway). These dicts let
        # the per-occurrence loop recognize that case by EXACT key
        # before ever reaching the fuzzy heuristic, so it can be a quiet
        # no-op instead of a `collisions` entry that would otherwise
        # repeat, unfixably, on every single tick for as long as the
        # adopted occurrence exists.
        hermes_events_by_uid = {r["external_uid"]: r for r in hermes_events
                                 if r.get("external_uid")}
        hermes_plans_by_uid = {r["external_uid"]: r for r in hermes_plans
                                if r.get("external_uid")}

        for occ in remote_occurrences:
            try:
                if not isinstance(occ, dict):
                    continue
                uid = occ.get("uid")
                if not uid or _coerce_utc_dt(occ.get("start_utc")) is None:
                    continue  # garbage occurrence -- nothing usable to link on
                key = _occurrence_key(uid, occ.get("recurrence_id"))
                is_cancelled = _pc_norm_text(occ.get("status")).upper() == "CANCELLED"
                all_day = bool(occ.get("all_day"))

                if all_day:
                    if key in seen_plans:
                        # Same occurrence reported twice in this very batch
                        # (review finding I2) -- e.g. she reads the SAME
                        # iCloud event through two of her subscribed
                        # calendars at once (extcal_read_calendars=[] reads
                        # all of them). Without this guard, both copies
                        # would see existing=None and both queue an insert
                        # with the identical external_uid, which downstream
                        # collides with schema v12's partial UNIQUE index.
                        # Keep the FIRST occurrence seen for this key in the
                        # batch, silently skip exact repeats.
                        continue
                    seen_plans.add(key)
                    # Branch-crossing: this key used to be a timed event.
                    stale = events_by_key.get(key)
                    if stale is not None:
                        seen_events.add(key)
                        if stale.get("status") != "cancelled":
                            changeset["events"]["cancel"].append(
                                {"id": stale["id"], "external_uid": key})

                    deadline = _deadline_from_occurrence(occ)
                    existing = plans_by_key.get(key)
                    if existing is None:
                        if key in hermes_plans_by_uid:
                            # N4: same occurrence, already adopted -- see
                            # the module note above. Quiet no-op, not a
                            # collision, not an insert.
                            continue
                        fuzzy = _fuzzy_match_plan(occ, hermes_plans, deadline, claimed_hermes_plans)
                        if fuzzy is not None:
                            changeset["collisions"].append({
                                "branch": "plans", "local_id": fuzzy["id"],
                                "remote_uid": uid, "recurrence_id": occ.get("recurrence_id"),
                                "title": occ.get("title"), "deadline": deadline,
                            })
                            continue
                        if is_cancelled:
                            continue  # never existed locally -- nothing to insert
                        changeset["plans"]["insert"].append({
                            "title": occ.get("title") or "", "deadline": deadline,
                            "location": occ.get("location") or "",
                            "external_uid": key, "owner": "iphone",
                        })
                        continue

                    if is_cancelled:
                        if existing.get("status") != "dropped":
                            changeset["plans"]["drop"].append(
                                {"id": existing["id"], "external_uid": key})
                        continue
                    if existing.get("status") == "dropped":
                        continue  # tombstone -- never resurrect
                    changes = _plan_diff(existing, occ, deadline)
                    if changes:
                        changeset["plans"]["update"].append(
                            {"id": existing["id"], "changes": changes})
                else:
                    if key in seen_events:
                        continue  # duplicate occurrence within this batch (I2, see above)
                    seen_events.add(key)
                    # Branch-crossing: this key used to be an all-day plan.
                    stale = plans_by_key.get(key)
                    if stale is not None:
                        seen_plans.add(key)
                        if stale.get("status") != "dropped":
                            changeset["plans"]["drop"].append(
                                {"id": stale["id"], "external_uid": key})

                    existing = events_by_key.get(key)
                    if existing is None:
                        if key in hermes_events_by_uid:
                            # N4: same occurrence, already adopted -- see
                            # the module note above. Quiet no-op, not a
                            # collision, not an insert.
                            continue
                        fuzzy = _fuzzy_match_event(occ, hermes_events, claimed_hermes_events)
                        if fuzzy is not None:
                            changeset["collisions"].append({
                                "branch": "events", "local_id": fuzzy["id"],
                                "remote_uid": uid, "recurrence_id": occ.get("recurrence_id"),
                                "title": occ.get("title"), "start_utc": occ.get("start_utc"),
                            })
                            continue
                        if is_cancelled:
                            continue
                        changeset["events"]["insert"].append({
                            "title": occ.get("title") or "", "start_utc": occ.get("start_utc"),
                            "end_utc": occ.get("end_utc"), "location": occ.get("location") or "",
                            "external_uid": key, "external_seq": occ.get("seq") or 0,
                            "owner": "iphone",
                        })
                        continue

                    if is_cancelled:
                        if existing.get("status") != "cancelled":
                            changeset["events"]["cancel"].append(
                                {"id": existing["id"], "external_uid": key})
                        continue
                    if existing.get("status") == "cancelled":
                        continue  # tombstone -- never resurrect
                    changes = _event_diff(existing, occ)
                    if changes:
                        changeset["events"]["update"].append(
                            {"id": existing["id"], "changes": changes})
            except Exception:
                continue  # one malformed occurrence must not sink the batch

        for key, row in events_by_key.items():
            if key in seen_events or row.get("status") == "cancelled":
                continue
            if _disappearance_in_scope(row, "start_utc", now_dt):
                changeset["events"]["cancel"].append({"id": row["id"], "external_uid": key})

        for key, row in plans_by_key.items():
            if key in seen_plans or row.get("status") == "dropped":
                continue
            if _disappearance_in_scope(row, "deadline", now_dt):
                changeset["plans"]["drop"].append({"id": row["id"], "external_uid": key})

        return changeset
    except Exception:
        return changeset


# ---- application (Task 5) --------------------------------------------------
#
# `apply_changes(conn, changeset, cfg)` is the ONLY place in this module that
# touches the DB. Everything above it (discover/fetch_changes/parse_ics/
# expand/plan_changes) is read-only or pure; this is where a `Changeset`
# (plan_changes' output) actually gets written -- exclusively through
# `cal.*`/`plans.*` wherever those modules expose an entry point, so every
# such write still inherits `audit_log` and `rem.regenerate`'s reminder-chain
# recompute, same as any hand-typed `fam cal add`/`fam plan add`.
#
# THREE narrow, deliberate, documented exceptions to "only cal.*/plans.*"
# below -- all direct DB reads/writes, all scoped to things neither module's
# CURRENT surface (this task's boundary excludes touching cal.py/plans.py)
# exposes an entry point for:
#   1. `owner`/`external_uid`/`external_href`/`external_etag`/`external_seq`/
#      `external_location` on `events`/`plans` -- schema v12 added these
#      columns, but `cal.add`/`cal.update`/`plans.add` have no kwarg for any
#      of them, so there is no cal.*/plans.* entry point that can set them at
#      all. (Reading them back out needs no change to either module: both
#      `cal.get` and `plans.get` are `dict(row)` over `SELECT *`, so every
#      one of these columns is already part of what they return.)
#   2. A plans-branch "update" (title/deadline edit on an EXISTING imported
#      plan) -- `plans.py` has `add`/`mark`/`attach` but, unlike
#      `cal.update` on the events side, no generic `update()` verb -- an
#      in-place plan edit has nowhere to go through `plans.*` either.
#   3. A `SELECT owner, ... FROM events/plans WHERE id=?` read-only ownership
#      guard (`_require_iphone_owned`, fix-round finding I2) before ANY
#      mutation of an EXISTING row -- see that function's own docstring.
# All three gaps are a real, load-bearing observation for whoever scopes
# cal.py's/plans.py's next revision (T5's report flags this explicitly), not
# something this task is scoped to fix. Every OTHER mutation below (insert,
# cancel, drop, and the title/start_utc/end_utc fields of an event update)
# goes through cal.*/plans.* exactly as documented.
#
# Fix-round finding C1 (Denis's decision, refined after the first fix
# round): an imported event/plan's `place` is resolved via `places.
# resolve()` -- the SAME resolver `cal.add`/`plans.add` already use
# internally -- when the free-text iCloud `LOCATION` happens to match a
# known `places` name/alias ("Точное совпадение с places по-прежнему
# используем, когда оно есть"). When it does NOT match (the common case --
# her free-text locations like "Стоматология, Абая 150" essentially never
# match an existing `places` entry), `place` is simply left None/NULL --
# NEVER `cal.UnknownRefError`/raise. Either way, the raw (normalized) text
# is ALSO stored verbatim in the `external_location` COLUMN (fix-round 4 --
# see the note on that column below), so nothing she typed is ever lost on
# a resolution miss, and the next sync has something byte-stable to diff
# the incoming LOCATION against.
#
# Rationale (Denis): place carries no OPERATIONAL weight for an
# owner='iphone' row -- her phone rings for it, and neither `leave_at` nor
# road/`по пути` are ever computed on the strength of it -- so there is
# nothing to lose by leaving it unresolved when it doesn't match, and
# nothing forced about resolving it when it happens to: a known place is
# used for free (lets `fam cal day` show a proper place card, keeps `по
# пути` matching available for her events too, no cost either way), an
# unknown one is no longer a hard import failure like the first cut of
# this fix made it (`UnknownRefError` -> per-row guard swallow -> retried
# every 15 minutes forever, plus a nightly `cal.ext.apply_error` summary
# entry for something that will never resolve itself).
#
# **Contract for Task 6** (finding I4): when Task 6 builds `local_snapshot`
# for `plan_changes()`, an `owner='iphone'` `EventRow`/`PlanRow` must carry
# its `external_location` COLUMN verbatim -- `dict(row)` over `SELECT *`
# already does this, no mapping needed. `_event_diff`/`_plan_diff` compare
# that value against the remote occurrence's raw `LOCATION` via
# `_pc_norm_text` on both sides, and `apply_changes` writes that same
# normalized text back into the same column, so the round trip is exact
# and a re-applied, unchanged occurrence produces no update forever.


def _require_iphone_owned(conn, table, row_id):
    """Read-only ownership guard (fix-round finding I2) -- called as the
    FIRST thing inside every `_apply_*` helper that mutates an EXISTING row
    (update/cancel/drop; insert has nothing to guard, it only ever creates a
    brand-new row). `table` is always one of the two literal strings
    "events"/"plans" from this module's own `_APPLY_STEPS` wiring below,
    never caller-supplied data, so the f-string is safe.

    Race this closes: `plan_changes()`'s own "never touch owner='hermes'"
    guard is a snapshot-time decision -- it only knows what `owner` a row
    had at the moment Task 6's tick took its `local_snapshot`. A live
    CalDAV `fetch_changes()` round-trip is real wall-clock time (seconds to
    minutes); in that window, `fam cal adopt <id>` (design doc's
    "адоптирование по явной просьбе Амины") can flip that SAME row's
    `owner` to 'hermes'. Without this guard, a stale `cancel`/`update`/`drop`
    entry built from the pre-adopt snapshot would reach `cal.cancel()`/
    `cal.update()`/`plans.mark()` and silently act on what is, by the time
    `apply_changes()` actually runs, a Hermes-owned row -- undoing the
    adoption (and its freshly-regenerated reminder chain) the operator
    asked for, with no visible error. Raising here instead routes that race
    through `_apply_one`'s ordinary per-row guard: the entry is skipped,
    logged as `cal.ext.apply_error`, and every OTHER entry in the batch
    still applies.

    Returns the full existing row as a dict (used by `_apply_plan_update`
    for its `attached_event_id` cascade check, finding I1) when the row
    exists AND is `owner='iphone'`; raises `LookupError` otherwise (unknown
    id, or a real ownership mismatch) -- both cases are just "this entry no
    longer applies", indistinguishable to the caller and both handled the
    same way by `_apply_one`.
    """
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
    if row is None:
        raise LookupError(f"{table} id={row_id}: no longer exists")
    d = dict(row)
    if d.get("owner") != "iphone":
        raise LookupError(
            f"{table} id={row_id}: owner={d.get('owner')!r}, expected "
            f"'iphone' -- refusing to apply an extcal mutation (I2: likely "
            f"a race with `cal adopt`/`disown` between fetch and apply)")
    return d


_AUDIT_REDACT_KEYS = ("title", "location")


def _audit_safe_changes(changes):
    """`changes` (an update entry's `{field: (was, now)}` dict) with free
    TEXT fields' VALUES replaced by a fixed redaction marker before it goes
    into `audit_log` -- design doc privacy requirement: "тела VEVENT целиком
    не логируются, только UID и счётчики" (fix-round Minor finding, VEVENT
    SUMMARY/LOCATION content must not land in the audit trail, which several
    other things on this VM besides Denis routinely read). `title`/
    `location` are the two fields that can carry her free-text event/plan
    content; `start_utc`/`end_utc`/`deadline` are timestamps -- WHEN
    something moved, not WHAT it is -- and are kept as-is (operationally
    useful for a human skimming `audit_log`, and not the "body" the design
    doc's rule is about).
    """
    safe = {}
    for key, pair in (changes or {}).items():
        if key in _AUDIT_REDACT_KEYS:
            safe[key] = "<redacted>"
        else:
            safe[key] = pair
    return safe


def _resolve_place_ref(conn, raw_text):
    """Free iCloud `LOCATION` text -> a `places.*`-usable ref (the matched
    place's `id`) or None -- fix-round finding C1 (Denis's refined
    decision, see the module note above): resolved via `places.resolve()`,
    the SAME resolver `cal.add`/`plans.add` use internally, so a known
    place is picked up for free; an unmatched text (the common case for her
    free-text locations) is simply None, NEVER a raised `UnknownRefError`
    -- callers pass this straight through as `place=`, which both `cal.add`/
    `cal.update` and `plans.add` treat identically to "no place given".
    """
    text = _pc_norm_text(raw_text)
    if not text:
        return None
    resolved = places.resolve(conn, text)
    return resolved["id"] if resolved else None


# ---- external_location: the machine's own column ------------------------
#
# Fix-round 4 (finding N1, third and final redesign -- this time by
# REMOVING the mechanism rather than re-encoding it). Rounds 1-3 all kept
# the raw iCloud LOCATION text inside `notes`, and each round only traded
# one failure mode for the next:
#
#   round 1 overwrote `notes` wholesale on every location change, silently
#     destroying any human note attached to the row ("взяла паспорт" against
#     her dentist appointment) with no trace anywhere -- `_audit_safe_
#     changes` redacts location/title VALUES from the audit trail by design,
#     so it was not recoverable from `audit_log` either;
#   round 2 fenced the machine's half off with a READABLE marker pair,
#     `[extcal:location]...[/extcal:location]` -- and the reviewer
#     reproduced, live, a human note containing that same literal marker
#     pair (quoting visible text is completely ordinary behavior) being
#     parsed as the machine's own block and destroyed;
#   round 3 replaced the marker with invisible Unicode format characters --
#     which closed that narrow case but not the class: `notes` is ALSO
#     edited by `fam cal update --notes`, which REPLACES the whole column,
#     and its caller is an LLM agent. An agent rewriting a note it read
#     will not reliably carry five invisible code points through; drop one
#     marker and the block stops being recognized forever, so the next sync
#     appends a second one beside it -- silent, accumulating duplicates.
#
# The common root was never the delimiter: it was that machine data lived
# in a column a human (and an agent acting for her) owns and rewrites
# wholesale. Schema v12 therefore carries a dedicated `external_location
# TEXT` on both `events` and `plans`, and this module writes the raw
# LOCATION text there and NOWHERE else:
#
#   * `notes` is now purely human. `extcal` never reads it, never writes
#     it, never parses it. Whatever Amina (or Hermes on her behalf) puts
#     there survives any number of syncs, any change of location, and the
#     location being deleted on the phone entirely -- not because a
#     delimiter protects it, but because nothing here touches the column.
#   * `external_location` is purely machine. Nothing in `fam` writes it
#     except `apply_changes`; `cal.update`'s `_UPDATE_FIELDS` does not
#     include it, so `fam cal update` cannot reach it even by accident.
#   * There is no marker, no parsing, no merge step, and therefore no
#     collision to reason about at all. That whole class is gone rather
#     than re-encoded -- which is the only reason to believe a fourth
#     round of this same finding is not coming.
#
# Amina can still see the address: both `cal.get` and `plans.get` are
# `dict(row)` over `SELECT *`, so `external_location` is already part of
# every event/plan dict they return (and of `fam cal show --json` /
# `fam cal day --json` / `fam plan list --json` on top of them), which is
# how the agent answers "а по какому адресу стоматология?".


def _external_location_value(raw_text):
    """Raw iCloud `LOCATION` text -> what goes in the `external_location`
    column: the same `_pc_norm_text` normalization `_event_diff`/
    `_plan_diff` apply to BOTH sides of their comparison (so a re-applied,
    unchanged occurrence can never diff against its own stored value), and
    NULL rather than an empty string when the location was deleted on the
    phone -- "no location" is one state in the DB, not two.
    """
    return _pc_norm_text(raw_text) or None


def _existing_id_by_external_uid(conn, table, external_uid):
    """SELECT id FROM {table} WHERE external_uid=? -- `table` is always one
    of this module's own two literal strings ("events"/"plans"), never
    caller-supplied data, so the f-string is safe. Shared by both insert
    paths' idempotency guard (fix-round finding I3, and its fix-round-2
    extension to the events branch, finding N3 -- see `_apply_event_insert`/
    `_apply_plan_insert`).
    """
    if not external_uid:
        return None
    row = conn.execute(
        f"SELECT id FROM {table} WHERE external_uid=?", (external_uid,)
    ).fetchone()
    return row["id"] if row else None


def _series_already_adopted(conn, href):
    """True when some OTHER row of the SAME CalDAV resource (`external_
    href`) has already been adopted (`owner='hermes'`, via `fam cal
    adopt`). Used by `_apply_event_insert` (final-review blocker 1,
    fix-round 2 finding N1 -- the horizon-rollover gap): a recurring
    series arrives as ONE CalDAV resource, but `expand()` only
    materializes the occurrences that currently fall inside the sync
    window; when the horizon later rolls forward and a BRAND-NEW
    occurrence of that same resource slides into view for the first
    time, it must inherit `owner='hermes'` too, complete with its own
    reminder chain -- her phone's VALARM for the whole resource was
    already permanently stripped by the earlier `adopt` (`drop_valarm`
    acts on the resource as a whole; there is no such thing as a
    per-occurrence VALARM on the wire), so an `owner='iphone'` new
    occurrence would silently never remind at all, forever, one freshly
    silent occurrence every time the window advances.

    No new state needs to be stored for this: an already-adopted sibling
    row already answers the question on its own, since adopted rows are
    never cleaned up or reset (`owner='hermes'` rows stay exactly where
    `fam cal adopt` left them). `href` with no rows at all (a brand-new
    series, or a non-recurring event) -> False, same as any other row's
    href simply having no siblings yet.
    """
    if not href:
        return False
    row = conn.execute(
        "SELECT 1 FROM events WHERE external_href=? AND owner='hermes' LIMIT 1",
        (href,),
    ).fetchone()
    return row is not None


_SKIPPED = object()


def _apply_event_insert(conn, entry):
    """One `events.insert` Changeset entry -> a new owner='iphone' event.

    `cal.add()` inserts the row with schema v12's DEFAULT owner='hermes'
    (it has no `owner` kwarg) and, as its very last step, calls
    `rem.regenerate()` on that still-'hermes' row -- which, under today's
    rules (nothing yet knows this event is hers), WOULD build a full
    reminder chain for it. The raw UPDATE below flips owner (and attaches
    the external_* identity) BEFORE this function's own explicit
    `rem.regenerate()` call runs a second time -- THAT second call is what
    actually deletes the transient chain, via the early-exit Task 5 adds to
    rem.regenerate (see rem.py). apply_changes' caller (`_apply_one`)
    commits per-entry, never mid-entry, so the transient 'hermes'-shaped
    chain this function's own `cal.add()` call built is never visible to
    any other connection -- only ever present-then-retracted inside this
    one function's own uncommitted work.

    `place` is resolved via `_resolve_place_ref` (fix-round finding C1 --
    see the module note above): a known `places` match is used, an
    unmatched free-text location leaves `place_id` None/NULL. Either way
    the raw text ALSO goes into the `external_location` column, via the
    same raw UPDATE that attaches the rest of the external identity
    (fix-round 4). `notes` is never passed at all -- a fresh imported row
    starts with an EMPTY, entirely human-owned notes column.

    Fix-round 5 (review finding I2, T6): `cal.add()` is called with
    `place=None` UNCONDITIONALLY here, even when `entry["location"]`
    resolves to a known `places` row with coordinates -- `place_id` is
    set SEPARATELY, by the SAME raw UPDATE that already attaches
    owner/external_*/external_location below, never through `cal.add`'s
    own `place` kwarg. This matters because `cal.add()` calls
    `recompute_road()` as an unconditional step of its own (BEFORE this
    function's raw UPDATE ever runs) -- with a real place attached at
    add-time, that hook would fire a live TomTom call for every single
    imported timed event, competing with Hermes-owned trips for the same
    shared 100/day budget on the very first full import (dozens of
    events at once). An `owner='iphone'` event never needs Hermes'
    road/leave_at figure in the first place (the same reasoning behind
    `tick.road_recompute`'s own `owner='hermes'` guard) -- so skipping
    `recompute_road` entirely for the insert path, by never giving
    `cal.add()` a place to compute FROM, is strictly correct, not just
    cheaper. `place_id` itself is still fully resolved and stored (needed
    for display / future fuzzy-matching), only the road hook is avoided.

    Idempotency guard (fix-round finding I3 for plans; extended here to
    events in fix-round 2, finding N3): `events.external_uid` already has
    a partial UNIQUE index (db.py), so a duplicate insert here would
    previously have gone all the way through `cal.add()` -- INSERT,
    `recompute_road`, `rem.regenerate`, `cal.add`'s own audit entry --
    only to fail on the UNIQUE constraint and get fully rolled back by
    `_apply_one`: correct, but an expensive and noisy way to be idempotent
    (a real `cal.ext.apply_error` entry for something that isn't actually
    an error, just a re-applied Changeset). The same cheap `SELECT`-before-
    insert check `_apply_plan_insert` already uses for its own
    no-index-of-its-own case is used here too -- a hit is a clean,
    audited no-op (`_SKIPPED`), not a wasted insert-then-rollback. The
    UNIQUE index remains as the TOCTOU backstop for both branches, same
    dual-layer pattern `tick.py::meds_gen` uses for its own SELECT-guarded
    inserts.

    Owner inheritance (final-review blocker 1, fix-round 2 finding N1):
    a brand-new occurrence whose `external_href` already has an adopted
    (`owner='hermes'`) sibling row inherits `owner='hermes'` instead of
    the usual `'iphone'` -- see `_series_already_adopted`'s own
    docstring for why. `rem.regenerate()` below is unconditional either
    way: for the ordinary 'iphone' case it retracts the transient
    'hermes'-shaped chain `cal.add()` just built (as always); for the
    inherited-'hermes' case it instead BUILDS the real chain this new
    occurrence needs from day one, exactly like `cal adopt` itself does
    for the occurrences that existed at adopt-time.
    """
    external_uid = entry.get("external_uid")
    existing_id = _existing_id_by_external_uid(conn, "events", external_uid)
    if existing_id is not None:
        audit.log(conn, "cal.ext.apply", {
            "branch": "events", "action": "insert_skipped_duplicate",
            "id": existing_id, "external_uid": external_uid,
        })
        return _SKIPPED

    href = entry.get("external_href")
    inherit_hermes = _series_already_adopted(conn, href)
    place_id = _resolve_place_ref(conn, entry.get("location"))
    added = cal.add(conn, entry.get("title") or "", entry["start_utc"],
                     end_utc=entry.get("end_utc"), place=None)
    event_id = added["id"]
    owner = "hermes" if inherit_hermes else "iphone"
    conn.execute(
        "UPDATE events SET owner=?, external_uid=?, external_href=?, "
        "external_etag=?, external_seq=?, external_location=?, place_id=? "
        "WHERE id=?",
        (owner, entry.get("external_uid"), href,
         entry.get("external_etag"), entry.get("external_seq"),
         _external_location_value(entry.get("location")), place_id, event_id),
    )
    created = rem.regenerate(conn, event_id)
    audit_payload = {
        "branch": "events", "action": "insert", "id": event_id,
        "external_uid": entry.get("external_uid"),
    }
    if inherit_hermes:
        # Visible in the audit trail (not just a silent side effect) --
        # this is the one insert-time path that deviates from the
        # ordinary "owner='iphone', reminder-free" outcome the rest of
        # this function's docstring/tests describe.
        audit_payload["owner"] = "hermes"
        audit_payload["reminders_created"] = created
    audit.log(conn, "cal.ext.apply", audit_payload)
    return None


def _apply_event_update(conn, entry):
    """One `events.update` Changeset entry -> `cal.update()` for whatever
    user-visible fields changed. `changes` is never empty for a real update
    entry (`plan_changes` only ever appends one after `_event_diff` found
    something), so this always calls `cal.update()` at least once.

    A `location` change maps to `cal.update`'s `place` kwarg plus a direct
    write of the `external_location` column -- and touches `notes` not at
    all (fix-round 4: `notes` is human-owned, this module neither reads nor
    writes it, so a human note on an imported row survives every location
    change, and the location being deleted, for free). `place` is
    re-resolved via `_resolve_place_ref` (fix-round finding C1, see the
    module note above); passing it explicitly (even as `None`) makes
    `cal.update` clear a previously-set place when the new location text no
    longer matches anything, exactly as it should -- `cal.update`'s own
    `place_given = "place" in fields` check treats `place=None` as "clear
    it", not "leave unchanged". `external_location` is set unconditionally
    whenever `"location"` is among the changes, INCLUDING to NULL when she
    deleted the location on her phone (`_external_location_value`), so a
    stale address can never linger past the edit that removed it.

    Also attaches external_href/external_etag/external_seq when the CALLER
    put them on this entry (Task 6's job -- attaching them by uid from
    fetch_changes' raw items is not yet wired up anywhere as of this task;
    absent here today, so this is a no-op `COALESCE` until T6 exists) --
    `COALESCE(?, external_<col>)` so a caller that only ever supplies
    `changes` (no href/etag/seq keys at all) can never accidentally clobber
    an already-stored value back to NULL. The `AND owner='iphone'` guard
    (fix-round finding I2) is redundant with `_require_iphone_owned` below
    within this SAME connection/transaction (nothing else can be racing it
    here), but costs nothing and keeps this UPDATE self-defending even if a
    future refactor ever calls it from a different context.

    Never calls `rem.regenerate()` itself: `cal.update()` already does,
    but ONLY when start_utc (or travel_min/place/participants/prep_min) is
    among the changed fields -- exactly the same trigger any other
    `cal.update()` caller gets, no special-casing needed here. `owner`
    stays 'iphone' throughout (this function never touches that column),
    so `rem.regenerate`'s early exit (invariant #2) keeps this event
    reminder-free across the update the same as right after insert.
    """
    event_id = entry["id"]
    _require_iphone_owned(conn, "events", event_id)

    changes = entry.get("changes") or {}
    fields = {}
    if "title" in changes:
        fields["title"] = changes["title"][1]
    if "start_utc" in changes:
        fields["start_utc"] = changes["start_utc"][1]
    if "end_utc" in changes:
        fields["end_utc"] = changes["end_utc"][1]
    if "location" in changes:
        fields["place"] = _resolve_place_ref(conn, changes["location"][1])
    if fields:
        cal.update(conn, event_id, **fields)

    if "location" in changes:
        # The machine's own column -- see the fix-round-4 note above. Set
        # unconditionally (not COALESCE'd like href/etag/seq below): a
        # deleted iCloud LOCATION must actually clear it, and only a real
        # `"location"` entry in `changes` gets here at all, so this can
        # never blank a value the changeset simply didn't mention.
        conn.execute(
            "UPDATE events SET external_location=? "
            "WHERE id=? AND owner='iphone'",
            (_external_location_value(changes["location"][1]), event_id),
        )

    href = entry.get("external_href")
    etag = entry.get("external_etag")
    seq = entry.get("external_seq")
    if href is not None or etag is not None or seq is not None:
        conn.execute(
            "UPDATE events SET "
            "external_href=COALESCE(?, external_href), "
            "external_etag=COALESCE(?, external_etag), "
            "external_seq=COALESCE(?, external_seq) "
            "WHERE id=? AND owner='iphone'",
            (href, etag, seq, event_id),
        )

    audit.log(conn, "cal.ext.apply", {
        "branch": "events", "action": "update", "id": event_id,
        "changes": _audit_safe_changes(changes),
    })


def _apply_event_cancel(conn, entry):
    """One `events.cancel` Changeset entry -> `cal.cancel()` -- marks the
    event cancelled, cancels its pending reminder chain (`rem.cancel_
    chain`), and drops any open prep-plans tied to it -- exactly the same
    call any other cancellation path in `fam` uses. Guarded by
    `_require_iphone_owned` (fix-round finding I2) first, same race as
    `_apply_event_update`'s own guard.
    """
    event_id = entry["id"]
    _require_iphone_owned(conn, "events", event_id)
    cal.cancel(conn, event_id)
    audit.log(conn, "cal.ext.apply", {
        "branch": "events", "action": "cancel", "id": event_id,
        "external_uid": entry.get("external_uid"),
    })


def _apply_plan_insert(conn, entry):
    """One `plans.insert` Changeset entry -> a new owner='iphone' plan via
    `plans.add()`, then the same owner/external_* raw-UPDATE pattern
    `_apply_event_insert` uses (see the module note above -- `plans.add`
    has no kwarg for any of these columns either). Plans carry no reminder
    chain of their own (only events do, via `reminders`), so there is no
    second `rem.regenerate()` call needed here.

    `place` is resolved via `_resolve_place_ref`, same as
    `_apply_event_insert` (fix-round finding C1); the raw `location` text
    ALSO goes into the `external_location` column via the same raw UPDATE
    that attaches the external identity (fix-round 4). `notes` is not
    passed at all -- the column stays empty and entirely human-owned.

    Idempotency guard (fix-round finding I3, and this same pattern
    extended to the events branch in fix-round 2's N3): unlike `events.
    external_uid` (a partial UNIQUE index in db.py from the start of this
    task), `plans.external_uid` had NO index at all until fix-round 1's
    own I3 fix added one (still v12, prod is not yet migrated). A
    re-applied Changeset (a retried tick after a mid-batch crash, or --
    theoretically -- two overlapping tick runs) would otherwise insert a
    SECOND plans row for the exact same iCloud occurrence, and it would
    stay a permanent, undeletable phantom "горящий план" in her digest:
    `plan_changes()`'s own `plans_by_key[key] = row` dict construction
    (one entry per key) means only ONE of the two duplicate rows is ever
    visible again as an update/drop target, so nothing downstream can ever
    clean up the other one. Closed the same way `tick.py::meds_gen` closes
    its own no-unique-index-backstop case: a `SELECT ... WHERE
    external_uid=?` existence check immediately before the insert (shared
    with the events branch via `_existing_id_by_external_uid`). A hit is
    treated as a no-op success (returns the `_SKIPPED` sentinel, which
    `_apply_one` recognizes and does NOT count as a fresh insert) -- the
    row already exists, which is exactly the intended end state. The
    UNIQUE index remains the TOCTOU backstop underneath this check.
    """
    external_uid = entry.get("external_uid")
    existing_id = _existing_id_by_external_uid(conn, "plans", external_uid)
    if existing_id is not None:
        audit.log(conn, "cal.ext.apply", {
            "branch": "plans", "action": "insert_skipped_duplicate",
            "id": existing_id, "external_uid": external_uid,
        })
        return _SKIPPED

    plan_id = plans.add(conn, entry.get("title") or "",
                         place=_resolve_place_ref(conn, entry.get("location")),
                         deadline=entry.get("deadline"))
    conn.execute(
        "UPDATE plans SET owner='iphone', external_uid=?, external_href=?, "
        "external_etag=?, external_location=? WHERE id=?",
        (entry.get("external_uid"), entry.get("external_href"),
         entry.get("external_etag"),
         _external_location_value(entry.get("location")), plan_id),
    )
    audit.log(conn, "cal.ext.apply", {
        "branch": "plans", "action": "insert", "id": plan_id,
        "external_uid": entry.get("external_uid"),
    })
    return None


def _apply_plan_update(conn, entry):
    """One `plans.update` Changeset entry -> a direct UPDATE of `plans`'
    title/deadline. `plans.py` has no generic `update()` verb at all
    (see the module note above) -- this is the one genuinely unavoidable
    raw-SQL mutation of plan CONTENT (not just bookkeeping columns) in this
    module. A `location` change maps to `external_location` (the machine's
    own column, fix-round 4 -- `notes` is never read or written here) AND
    `place_id` (re-resolved via `_resolve_place_ref`, same as events --
    fix-round finding C1): a location edit that stops matching a
    previously-resolved place clears `place_id` back to NULL, same
    "explicit None clears it" semantics `cal.update` gives events, and a
    location deleted on the phone clears `external_location` to NULL too.

    Guarded by `_require_iphone_owned` FIRST (fix-round finding I2) -- its
    return value is also this function's only source for `attached_event_id`
    (and the PRE-update `place_id`, for the cascade gate below) so there is
    no second row-fetch needed. `AND owner='iphone'` on both raw UPDATEs
    (redundant with the guard within this same transaction, kept for the
    same self-defense reason `_apply_event_update` keeps it).

    Cascade (fix-round finding I1, gate narrowed in fix-round 2's N2):
    `plans.mark`/`plans.attach` both recompute the attached event's road
    figure and reminder chain (`cal.recompute_road` then `rem.regenerate`)
    whenever an attached plan's state changes; this raw UPDATE bypassed
    that entirely for a place-affecting edit. Reproduced here, same order,
    but gated on `place_id` HAVING ACTUALLY CHANGED (`new_place_id !=
    existing["place_id"]`) rather than on "any field changed" -- the
    first-round fix gated on any `set_clauses` at all, which fired
    `cal.recompute_road` (a potentially LIVE TomTom call) on something as
    inconsequential as a title-only edit; only a location edit can ever
    move `place_id`, and only when it actually resolves to something
    different than before is there anything for the attached event's route
    to recompute. This also keeps the daily TomTom budget (100 calls) from
    being spent on edits that can't possibly change the route -- a
    separate, related finding (her whole calendar import competing for
    that same budget) is T6's to address, not this narrower one.
    """
    plan_id = entry["id"]
    existing = _require_iphone_owned(conn, "plans", plan_id)

    changes = entry.get("changes") or {}
    set_clauses, params = [], []
    place_changed = False
    if "title" in changes:
        set_clauses.append("title=?")
        params.append(changes["title"][1])
    if "deadline" in changes:
        new_deadline = changes["deadline"][1]
        # `plans.add`'s own `_validate_deadline` runs before every insert;
        # this raw UPDATE has no equivalent gate of its own by construction
        # (it deliberately doesn't go through `plans.add`), so a malformed
        # deadline would otherwise land straight in the DB unvalidated --
        # `plans.py`'s own docstring says exactly why that's a real risk,
        # not a theoretical one: "tick._burning_plans parses deadline with
        # date.fromisoformat and would otherwise crash the daily digest on
        # a bad value" (plans.py, Final review Finding 1). Reusing
        # `plans._validate_deadline` here (a read, not an edit of
        # plans.py) keeps this raw UPDATE honoring the exact same contract
        # `plans.add` already guarantees, rather than quietly being a
        # weaker, unvalidated back door to the same column.
        plans._validate_deadline(new_deadline)
        set_clauses.append("deadline=?")
        params.append(new_deadline)
    if "location" in changes:
        raw_location = changes["location"][1]
        set_clauses.append("external_location=?")
        params.append(_external_location_value(raw_location))
        new_place_id = _resolve_place_ref(conn, raw_location)
        set_clauses.append("place_id=?")
        params.append(new_place_id)
        place_changed = new_place_id != existing.get("place_id")
    if set_clauses:
        params.append(plan_id)
        conn.execute(
            f"UPDATE plans SET {', '.join(set_clauses)} "
            f"WHERE id=? AND owner='iphone'", params)

    href = entry.get("external_href")
    etag = entry.get("external_etag")
    if href is not None or etag is not None:
        conn.execute(
            "UPDATE plans SET external_href=COALESCE(?, external_href), "
            "external_etag=COALESCE(?, external_etag) "
            "WHERE id=? AND owner='iphone'",
            (href, etag, plan_id),
        )

    attached_event_id = existing.get("attached_event_id")
    if place_changed and attached_event_id is not None:
        cal.recompute_road(conn, attached_event_id)
        rem.regenerate(conn, attached_event_id)

    audit.log(conn, "cal.ext.apply", {
        "branch": "plans", "action": "update", "id": plan_id,
        "changes": _audit_safe_changes(changes),
    })


def _apply_plan_drop(conn, entry):
    """One `plans.drop` Changeset entry -> `plans.mark(conn, plan_id,
    'dropped')` -- exactly the same call any other plan-dropping path in
    `fam` uses, including its own attached_event_id recompute/regenerate
    cascade if this plan happened to be attached to an event. Guarded by
    `_require_iphone_owned` first (fix-round finding I2), same race as the
    events-branch guards above.
    """
    plan_id = entry["id"]
    _require_iphone_owned(conn, "plans", plan_id)
    plans.mark(conn, plan_id, "dropped")
    audit.log(conn, "cal.ext.apply", {
        "branch": "plans", "action": "drop", "id": plan_id,
        "external_uid": entry.get("external_uid"),
    })


_APPLY_STEPS = (
    ("events", "insert", _apply_event_insert, "events_inserted"),
    ("events", "update", _apply_event_update, "events_updated"),
    ("events", "cancel", _apply_event_cancel, "events_cancelled"),
    ("plans", "insert", _apply_plan_insert, "plans_inserted"),
    ("plans", "update", _apply_plan_update, "plans_updated"),
    ("plans", "drop", _apply_plan_drop, "plans_dropped"),
)


def _apply_one(conn, branch, action, fn, entry, counts, count_key):
    """Per-row guard (task brief hard requirement: one bad row must not
    sink the batch) -- same pattern as `tick.py::_meds_series`'s own
    per-row try/except/rollback/commit. Each entry gets its OWN commit: a
    failure rolls back ONLY this entry's own uncommitted writes (its
    cal.*/plans.* call, the owner/external_* UPDATE, and/or its own
    rem.regenerate) -- never the previous entries from this same
    `apply_changes()` call, which are already committed by the time this
    one runs.

    `fn` may return the `_SKIPPED` sentinel (currently only
    `_apply_plan_insert`'s idempotency guard, finding I3) to mean "handled,
    but nothing new was actually created" -- `count_key` is then left
    untouched rather than incremented, so `counts` reflects real inserts,
    not idempotent no-ops. Any other return value (including plain `None`,
    every other `_apply_*` helper's implicit return) counts as a normal
    success.

    The failure branch is ITSELF wrapped in a nested try/except (fix-round
    Minor finding b): `conn.rollback()`/`audit.log()`/`conn.commit()` are
    ordinary DB operations against the SAME file the minute-tick reminders
    timer and other tick jobs also touch, so "database is locked" (or any
    other transient failure) is a real possibility here, not a
    theoretical one. `counts["errors"].append(...)` happens FIRST and is
    pure in-memory bookkeeping that cannot itself fail, so this row's
    failure is recorded in the returned counts no matter what happens
    next; if the rollback/audit/commit sequence itself then fails, that
    second failure is swallowed here rather than propagating out of
    `_apply_one` and aborting every REMAINING entry in the batch -- one bad
    row (even a doubly-unlucky one) must never take down the rest.
    """
    try:
        result = fn(conn, entry)
        conn.commit()
        if result is not _SKIPPED:
            counts[count_key] += 1
    except Exception as e:
        ref = entry.get("external_uid")
        row_id = entry.get("id")
        error = f"{type(e).__name__}: {e}"[:300]
        counts["errors"].append({
            "branch": branch, "action": action, "id": row_id,
            "external_uid": ref, "error": error,
        })
        try:
            conn.rollback()
            audit.log(conn, "cal.ext.apply_error", {
                "branch": branch, "action": action, "id": row_id,
                "external_uid": ref, "error": error,
            })
            conn.commit()
        except Exception:
            pass  # see docstring: the failure is already in counts["errors"]


def apply_changes(conn, changeset, cfg=None):
    """Write one `plan_changes()` Changeset to the DB. Returns a counts
    dict: `{events_inserted, events_updated, events_cancelled,
    plans_inserted, plans_updated, plans_dropped, collisions, errors}`.

    `errors` is a list of `{branch, action, id, external_uid, error}` for
    every entry that raised (see `_apply_one`) -- every OTHER entry in the
    same batch still gets applied.

    `collisions` is simply `len(changeset["collisions"])`, passed through
    for Task 6's tick to fold into its own `audit cal.ext.sync` counts
    without recomputing it -- this function does not itself write anything
    for collisions: `plan_changes`' own docstring assigns that reporting
    job to Task 6 ("purely a reporting channel for T6's audit/nightly-
    summary"), so logging it here too would be a second, redundant writer.

    Every actual mutation goes through `cal.*`/`plans.*` wherever those
    modules expose an entry point for it (see the module note above for
    the three narrow, documented raw-SQL/raw-SELECT exceptions this still
    requires, given cal.py/plans.py's CURRENT surface) -- audit_log and
    rem.regenerate's reminder-chain recompute are inherited for free from
    those calls wherever they're used.

    `cfg` is accepted for interface symmetry with the rest of this
    module's cfg-taking public entry points (discover/fetch_changes/
    probe); nothing here currently branches on it.

    **Transaction contract (fix-round Minor finding e):** this function
    commits and rolls back `conn` itself, per entry (see `_apply_one`) --
    it must only ever be called on a connection with NO caller-pending
    uncommitted work of its own; anything the caller had staged but not
    yet committed before calling this will be committed (or rolled back)
    as a side effect of processing the FIRST Changeset entry, not on the
    caller's own terms. This mirrors `tick.py::_meds_series`'s identical
    contract (that module's own docstring: ticks own their transaction
    boundary; `cal.py`/`plans.py`/`rem.py` themselves never commit and
    leave that decision to whoever calls them).

    Never raises: a malformed `changeset` (not a dict, missing/wrong-typed
    branches or slots, a non-dict entry inside a branch's list) degrades to
    "nothing to apply for that slot" (counts stay 0 for anything malformed
    away, including a non-list `collisions`); a single bad (but
    well-formed) entry inside a branch is caught by `_apply_one`'s per-row
    guard and recorded in `errors`, never sinking the rest of the batch.
    """
    cfg = cfg or {}
    changeset = changeset if isinstance(changeset, dict) else {}
    raw_events = changeset.get("events")
    raw_plans = changeset.get("plans")
    by_branch = {
        "events": raw_events if isinstance(raw_events, dict) else {},
        "plans": raw_plans if isinstance(raw_plans, dict) else {},
    }
    raw_collisions = changeset.get("collisions")

    counts = {
        "events_inserted": 0, "events_updated": 0, "events_cancelled": 0,
        "plans_inserted": 0, "plans_updated": 0, "plans_dropped": 0,
        "collisions": len(raw_collisions) if isinstance(raw_collisions, list) else 0,
        "errors": [],
    }

    for branch, action, fn, count_key in _APPLY_STEPS:
        entries = by_branch.get(branch, {}).get(action)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            _apply_one(conn, branch, action, fn, entry, counts, count_key)

    return counts


# ---- export (Task 7) --------------------------------------------------
#
# `export_own(conn, cfg, request=None, now_utc=None)` is the reverse
# direction of `apply_changes` above -- it is the OTHER (and last) place in
# this module that writes anything: instead of iCloud -> local DB, this
# writes owner='hermes' events -> the "Гермес" collection in her iCloud
# (`extcal_write_calendar`), plus the `ext_exports` bookkeeping row that
# lets the NEXT tick tell "unchanged" apart from "needs a fresh PUT"
# without re-touching the network for events nothing happened to.
#
# Design doc Sec.5's central requirement: this is what makes a Hermes-
# created event VISIBLE on her phone without making her phone RING for it
# -- the VEVENT this writes carries NO VALARM, ever. The alarm for a
# owner='hermes' event stays exactly where it already is: Hermes' own
# `reminders`/`rem.regenerate` chain. This module's own anti-echo belts
# (module docstring; `_HERMES_UID_RE` above and cli.py's `_EXTCAL_ECHO_
# UID_RE`) are written to recognize precisely the UID convention used
# here (`_export_uid`, `fam-<event_id>@hermes-home` -- verbatim the same
# convention `mail.py::build_ics`/`send_event_email` already use for the
# .ics Denis gets by email, so both representations of the same Hermes
# event always carry the identical UID) -- so nothing this function PUTs
# can ever be read back in as a NEW "her" occurrence by the import side
# (T6), regardless of whether `extcal_write_calendar` happens to be
# configured correctly at read time (belt 1) or not (belt 2 alone still
# saves it). See test_extcal_export.py's own anti-echo tests, which
# exercise both belts against exactly what this function produces/writes
# to, rather than re-asserting the belts' logic in the abstract.
#
# Scope is deliberately EVENTS only, never `plans` -- design doc Sec.5 and
# the DoD both only ever mention "события Гермеса" for reverse-write;
# an all-day `plans` row has no VEVENT-shaped time of its own to PUT
# (it is deadline-only), and nothing in the brief asks for it.

_EXPORT_UID_TMPL = "fam-{id}@hermes-home"


def _export_uid(event_id):
    """The exact same UID convention `mail.py::build_ics` already uses
    (`fam-<event_id>@hermes-home`) -- reused verbatim, not re-derived, so
    an event's outbound iCloud export and its emailed .ics always carry
    byte-identical UIDs. This is also EXACTLY the pattern this module's
    own `_HERMES_UID_RE` (anti-echo belt 2) is written to recognize."""
    return _EXPORT_UID_TMPL.format(id=event_id)


def _export_escape_text(value):
    """RFC5545 TEXT escaping -- the same rules as mail.py's
    `_escape_ics_text` (backslash first, then comma/semicolon, then
    newlines -> literal `\\n`), kept as an independent copy here rather
    than an import: this task's boundary is "read mail.py's UID
    convention", not "import mail.py's private helpers" (mail.py also
    lazy-imports google-auth/googleapiclient on a different code path,
    and `test_no_google_import.py` pins that fam.cli/fam.mail import
    stays google-free at module level -- extcal.py owes that same
    guarantee independently, not by relying on mail.py's own care)."""
    value = value or ""
    return (
        value.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


_EXPORT_LINE_LIMIT = 75


def _export_fold_line(line, limit=_EXPORT_LINE_LIMIT):
    """Byte-safe RFC5545 3.1 line folding -- same algorithm as mail.py's
    `_fold_ics_line` (independent copy, see `_export_escape_text`'s own
    docstring for why): counts UTF-8 octets, never splits a multi-byte
    character across a fold boundary, continuation lines reserve 1 octet
    for their leading space."""
    encoded = line.encode("utf-8")
    if len(encoded) <= limit:
        return line
    chunks = []
    start = 0
    n = len(encoded)
    first = True
    while start < n:
        budget = limit if first else limit - 1
        end = min(start + budget, n)
        while end > start and end < n and (encoded[end] & 0xC0) == 0x80:
            end -= 1
        chunks.append(encoded[start:end].decode("utf-8"))
        start = end
        first = False
    return "\r\n ".join(chunks)


def _export_to_ics_utc(value):
    """An aware/naive datetime OR ISO-8601 string -> RFC5545 UTC
    DATE-TIME (`YYYYMMDDTHHMMSSZ`), via the same forgiving `_coerce_utc_dt`
    every other window/diff helper in this module already uses. None on
    anything unparseable (never raises)."""
    dt = _coerce_utc_dt(value)
    return dt.strftime("%Y%m%dT%H%M%SZ") if dt is not None else None


def _export_body_hash(event, location, participants):
    """sha256 over EXACTLY (title, start_utc, end_utc, location,
    participant names) -- the fields that matter to what her phone
    actually displays. Text is normalized via `_pc_norm_text` and
    datetimes via `_coerce_utc_dt`/`_iso` (the SAME normalization
    `_event_diff` applies to both sides of its own comparison), so a
    harmless formatting difference in the STORED start_utc/end_utc string
    (`+00:00` vs `Z`, a dropped `:00` seconds field, ...) is never
    mistaken for a real change -- exactly the "лишний PUT" this hash
    exists to gate (requirement #4).

    Participants (fix-round 1, finding N1) are folded in via
    `mail.participant_names` -- fix-round 2, finding R1: this is now the
    ONLY place that join is implemented (this module used to keep its own
    copy of the identical one-liner; see `mail.participant_names`'s own
    docstring for why that was worth removing rather than leaving as a
    harmless-looking duplicate). `_export_participants`' own query is
    already `ORDER BY name COLLATE NOCASE`, so the join is stable across
    ticks regardless of insert order, and a participant-set edit (add/
    remove/rename) changes this hash and therefore DOES trigger a fresh
    PUT -- unlike a location edit that resolves to the same place name,
    there is no "same meaning, different spelling" case to normalize away
    here beyond the ordering already handled by the query itself.

    Deliberately EXCLUDED from the hash:
      - `id`/the UID: constant per event for as long as it is exported at
        all -- it is already the `ext_exports` row's own primary key, not
        something that needs to also be inside the hash of its BODY;
      - DTSTAMP: a fresh wall-clock value every single time this function
        would be called to build a NEW body -- including it would force a
        PUT on literally every tick, defeating the whole point of this
        hash;
      - status: only `status='active'` events ever reach this function at
        all (`export_own`'s own eligibility query) -- a transition to
        cancelled/done routes through the DELETE path instead (see
        `_export_delete_event`), never through a PUT whose hash this
        gates, so status never actually varies among the rows that call
        this.
    """
    start_dt = _coerce_utc_dt(event.get("start_utc"))
    end_dt = _coerce_utc_dt(event.get("end_utc"))
    fields = (
        _pc_norm_text(event.get("title")),
        _iso(start_dt) or "",
        _iso(end_dt) or "",
        _pc_norm_text(location),
        mail.participant_names(participants),
    )
    raw = "\x1f".join(fields)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_export_vevent(event, location, participants, now_dt):
    """title/start/end/location/participants -> a full PUT-ready
    VCALENDAR/VEVENT text.

    NO VALARM, ever -- this is the module's central requirement for this
    whole section (design doc Sec.5): an owner='hermes' event's alarm
    stays Hermes' own reminder chain (`reminders`/`rem.regenerate`); her
    phone must only ever SHOW this event (so she can see her day is full
    at 10:00), never ring for it a second time. There is structurally no
    VALARM-emitting code anywhere in this function -- not a filter that
    strips one out, there is simply nothing here that could ever add one.

    UID is `_export_uid(event["id"])` -- the SAME `fam-<id>@hermes-home`
    convention `mail.py::build_ics` uses for the emailed .ics of the same
    event. DTEND falls back to DTSTART+1h when the event carries no
    end_utc (fam's own convention, matching both `mail.build_ics` and
    `extcal._finalize_component`'s identical no-DTEND default on the
    import side).

    DESCRIPTION (fix-round 1, finding N1; join centralized in fix-round 2,
    finding R1): when `participants` is non-empty, a `DESCRIPTION:
    Участники: <names>` line is appended -- the EXACT same property name,
    prefix text, and join `mail.py::build_ics` already uses for the
    emailed .ics of the same event, via the ONE shared
    `mail.participant_names` (`event.get("participants")` there,
    `_export_participants` here -- same underlying query, see that
    function's own docstring). Before fix-round 1, the SAME `UID` carried
    different content in the two representations (the emailed .ics had
    participants, the iCloud copy never did) -- now both agree, and now
    (fix-round 2) there is exactly one join computing the shared string,
    not two independently-typed copies of it.
    """
    start_utc = event["start_utc"]
    end_dt = _coerce_utc_dt(event.get("end_utc"))
    if end_dt is None:
        end_dt = _coerce_utc_dt(start_utc) + timedelta(hours=1)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//hermes-agent//fam//RU",
        "BEGIN:VEVENT",
        f"UID:{_export_uid(event['id'])}",
        f"DTSTAMP:{_export_to_ics_utc(now_dt)}",
        f"DTSTART:{_export_to_ics_utc(start_utc)}",
        f"DTEND:{_export_to_ics_utc(end_dt)}",
        f"SUMMARY:{_export_escape_text(event.get('title') or '')}",
    ]
    if location:
        lines.append(f"LOCATION:{_export_escape_text(location)}")
    names = mail.participant_names(participants)
    if names:
        lines.append(f"DESCRIPTION:{_export_escape_text('Участники: ' + names)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    folded = [_export_fold_line(l) for l in lines]
    return "\r\n".join(folded) + "\r\n"


def _export_href(write_url, event_id):
    """Where a BRAND-NEW export PUTs its resource: `<uid>.ics` under the
    write-target collection -- an ordinary CalDAV convention (Apple's own
    servers accept a client-chosen resource name on initial PUT). An
    UPDATE never calls this: it reuses the href `ext_exports` already
    recorded from the original insert."""
    base = (write_url or "").rstrip("/") + "/"
    return urljoin(base, f"{_export_uid(event_id)}.ics")


def _export_headers(cfg, extra=None):
    headers = {}
    auth = _auth_header(cfg)
    if auth:
        headers.update(auth)
    if extra:
        headers.update(extra)
    return headers


def _export_put(cfg, url, body, etag, request):
    """One PUT attempt. Returns `(ok, new_etag, status, conflict)` --
    `conflict` is True ONLY on an HTTP 412 (etag precondition failed, RFC
    4791/RFC 7232) -- the ONE status `_export_put_event` retries, exactly
    once (requirement #5). `new_etag` is read off the response when
    present (iCloud returns it on both 201 Created and 204/200); it is
    None when absent (some servers only return it on a follow-up GET) --
    the caller handles a None etag exactly like any other value (a later
    update simply omits If-Match, an unconditional overwrite of OUR OWN
    resource, never a risk to her data since this collection holds nothing
    but this module's own writes).

    Never raises: `request(...)` (the injected seam, `_request` by
    default) already never raises; a None response (network/timeout/
    host-guard) is reported as `(False, None, None, False)`, indistinguishable
    from the caller's point of view from ordinary HTTP failure paths --
    only the (status is None) detail differs, folded into the same
    `_ExportFailure` message either way.
    """
    headers = _export_headers(cfg, {"Content-Type": "text/calendar; charset=utf-8"})
    if etag:
        headers["If-Match"] = etag
    resp = request("PUT", url, headers=headers, body=body, timeout=DEFAULT_TIMEOUT)
    if resp is None:
        return False, None, None, False
    if resp.status == 412:
        return False, None, 412, True
    if resp.status not in (200, 201, 204):
        return False, None, resp.status, False
    new_etag = resp.headers.get("ETag") or resp.headers.get("Etag") or resp.headers.get("etag")
    return True, new_etag, resp.status, False


def _export_reread_etag(cfg, url, request):
    """Requirement #5's "перечитать" half of "перечитать и повторить один
    раз": a plain GET of the resource we just failed to PUT (412), to pick
    up whatever etag it actually holds right now. Returns None (never
    raises) on any failure -- the caller (`_export_put_event`) treats a
    None fresh etag as "retry unconditionally, no If-Match at all", which
    is safe here specifically because this collection holds nothing but
    our own prior writes -- there is no THIRD PARTY's concurrent edit this
    unconditional retry could ever clobber."""
    resp = request("GET", url, headers=_export_headers(cfg), timeout=DEFAULT_TIMEOUT)
    if resp is None or resp.status not in (200, 207):
        return None
    return resp.headers.get("ETag") or resp.headers.get("Etag") or resp.headers.get("etag")


def fetch_resource(cfg, href, request=None):
    """GET one calendar resource and return its raw ICS text, or None.

    Exists for the one case `REPORT sync-collection` leaves us blind in:
    a delta entry that is NOT a tombstone yet carries no
    `<C:calendar-data>` (a per-resource 403/500/507 inside an otherwise-200
    multistatus, or a 200 whose calendar-data element is missing/empty).
    Before this, such an entry was skipped for the round while its
    sync-token was still persisted -- so the change it represented became
    invisible until the next `periodic_full`, up to a full day later
    (observed 2026-08-20: an event created at 12:34 UTC was still missing
    five hours and twenty ticks later).

    Same contract as every other transport-touching function here: never
    raises, degrades to None. Reuses `_export_headers` for auth so there
    is exactly one place that builds the Basic-auth header, and re-checks
    `_scheme_and_host_ok` itself: `href` is SERVER-supplied (it comes
    straight out of a multistatus `<D:href>`), and unlike the real
    `_request` an injected transport carries no host guard of its own.
    """
    if not href or not _scheme_and_host_ok(href):
        return None
    if _auth_header(cfg) is None:
        return None
    request = request or _request
    try:
        resp = request("GET", href, headers=_export_headers(cfg),
                       timeout=DEFAULT_TIMEOUT)
    except Exception as e:
        # `_request` itself never raises; an INJECTED transport (tests,
        # future callers) might, and this function's callers -- the tick's
        # per-resource loop -- must not learn to expect exceptions from a
        # read that is, by design, best-effort. Type only, never str(e):
        # same reasoning as `_request`'s own except-clause comment.
        print(f"extcal: resource fetch failed ({type(e).__name__})", file=sys.stderr)
        return None
    if resp is None or not (200 <= getattr(resp, "status", 0) < 300):
        return None
    text = resp.text or ""
    # Whitespace-only is "no body" here, exactly as it is in the tick's
    # own `not (ics_text or "").strip()` guard (cli.py, fix-round 4) --
    # returning "\n  " would just re-create the bug one layer up.
    return text if text.strip() else None


def _export_delete(cfg, url, etag, request):
    """One DELETE attempt. `(ok, status)`. A 404/410 (already gone on the
    server -- e.g. a previous tick's DELETE actually succeeded but this
    module never got to record that before crashing) is treated as
    SUCCESS, not a failure: either way, the end state ("nothing there")
    is exactly what this call wanted. Never raises."""
    headers = _export_headers(cfg)
    if etag:
        headers["If-Match"] = etag
    resp = request("DELETE", url, headers=headers, timeout=DEFAULT_TIMEOUT)
    if resp is None:
        return False, None
    if resp.status in (200, 202, 204, 404, 410):
        return True, resp.status
    return False, resp.status


def _export_location(conn, event):
    """An owner='hermes' event's LOCATION text for the exported VEVENT:
    its resolved `places` row's name, or "" when it has no place_id at
    all. Unlike the import side's `external_location` column (free text
    that may not match any known place), a Hermes event's place is always
    either a real `places` row or nothing -- there is no free-text
    location concept on this side to fall back to."""
    place_id = event.get("place_id")
    if place_id is None:
        return ""
    place = places.get(conn, place_id)
    return place["name"] if place else ""


def _export_participants(conn, event_id):
    """An owner='hermes' event's participants, as `[{"name": ...}, ...]`,
    ORDER BY name COLLATE NOCASE.

    Fix-round 2, finding R2: this used to run its own `conn.execute` of a
    hand-copied version of `cal.get()`'s own `event_participants JOIN
    people ... ORDER BY pe.name COLLATE NOCASE` query -- textually
    identical today, but a second, independent copy of one decision with
    nothing forcing it to stay that way (this project already paid for
    exactly that class of drift once, Task 6, four fix rounds). `cal` is
    already imported by this module; calling `cal.get()` (which this
    module's own `apply_changes` section already reads, e.g.
    `cal.recompute_road`/`cal.cancel`) reuses its ENTIRE participants
    query instead of re-typing it, at the cost of also re-fetching the
    event row and resolving its place/start_local/end_local inside
    `cal.get()` -- work this call doesn't otherwise need. That extra cost
    is one cheap, indexed `SELECT ... WHERE id=?` plus a `places.get()`
    per eligible hermes event per 15-minute tick; the event counts in
    scope here (an active window of `[today-1d, +extcal_horizon_weeks]`
    on a personal calendar) are small enough that this is not worth a
    special-cased leaner path -- reuse over reinventing, per default.
    Returns `[]` for an unknown/deleted event_id rather than raising
    (`cal.get()` itself returns `None` for that case)."""
    fetched = cal.get(conn, event_id)
    return fetched["participants"] if fetched else []


def _export_record(conn, event_id, href, etag, body_hash, synced_at):
    """INSERT-or-UPDATE the one `ext_exports` row for `event_id` (its
    schema is `event_id INTEGER PRIMARY KEY`, one row per exported event,
    see db.py's v12 migration). Plain SELECT-then-branch rather than an
    `ON CONFLICT` upsert -- matches this module's own established style
    elsewhere (`_existing_id_by_external_uid`'s SELECT-before-write, the
    same reasoning: explicit and portable rather than relying on a SQLite
    upsert-syntax version floor)."""
    existing = conn.execute(
        "SELECT event_id FROM ext_exports WHERE event_id=?", (event_id,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE ext_exports SET href=?, etag=?, body_hash=?, synced_at=? "
            "WHERE event_id=?",
            (href, etag, body_hash, synced_at, event_id))
    else:
        conn.execute(
            "INSERT INTO ext_exports(event_id, href, etag, body_hash, synced_at) "
            "VALUES(?,?,?,?,?)",
            (event_id, href, etag, body_hash, synced_at))


class _ExportFailure(Exception):
    """Raised by `_export_put_event`/`_export_delete_event` for a
    recognized (non-crash) PUT/DELETE failure -- every attempt (including
    the one 412 retry, for PUT) came back a non-2xx/non-404 status, or no
    response at all. Caught by `_export_commit_one` exactly like any other
    exception (same per-row isolation contract as `apply_changes`' own
    `_apply_one`), but carries a clean, pre-formatted message instead of a
    bare exception type name."""


def _export_put_event(conn, cfg, request, event, exp, location, participants, new_hash, now_dt):
    """One event -> PUT (fresh insert when `exp` is None, update -- same
    href/etag -- otherwise). Raises `_ExportFailure` on any non-2xx
    outcome after the one 412 retry (requirement #5); writes the
    `ext_exports` bookkeeping row itself on success, but does NOT commit
    (that is `_export_commit_one`'s job, same contract as `apply_changes`'
    own `_apply_*` helpers)."""
    is_update = exp is not None
    href = exp["href"] if is_update else _export_href(
        cfg.get("extcal_write_calendar"), event["id"])
    etag = exp.get("etag") if is_update else None
    body = _build_export_vevent(event, location, participants, now_dt)

    ok, new_etag, status, conflict = _export_put(cfg, href, body, etag, request)
    if conflict:
        # Requirement #5: exactly one re-read-and-retry on a 412 ETag
        # conflict -- never a second retry, regardless of THIS attempt's
        # own outcome.
        fresh_etag = _export_reread_etag(cfg, href, request)
        ok, new_etag, status, _conflict2 = _export_put(cfg, href, body, fresh_etag, request)
    if not ok:
        raise _ExportFailure(f"PUT {href} failed (status={status})")
    _export_record(conn, event["id"], href, new_etag, new_hash, _iso(now_dt))


def _export_delete_event(conn, cfg, request, event_id, exp):
    """One previously-exported event that is no longer eligible (design
    doc requirement #6: cancelled/deleted -- and, by this function's own
    scoping in `export_own`, also 'done', re-owned away from 'hermes', or
    scrolled outside the window, which are exactly as wrong to leave
    behind on her phone as a plain cancellation) -> DELETE its iCloud
    resource and drop its `ext_exports` row. Raises `_ExportFailure` on a
    non-2xx/non-404 DELETE outcome; does NOT commit (see
    `_export_put_event`'s own docstring for the shared contract).

    `exp.get("href")` missing (should not happen in practice -- see
    `_export_record`, which always writes href together with body_hash --
    but defensive regardless) skips the network call entirely: there is
    nothing on the server this row could even point at, so the only
    correct action is dropping the local bookkeeping row.
    """
    href = exp.get("href")
    if href:
        ok, status = _export_delete(cfg, href, exp.get("etag"), request)
        if not ok:
            raise _ExportFailure(f"DELETE {href} failed (status={status})")
    conn.execute("DELETE FROM ext_exports WHERE event_id=?", (event_id,))


def _export_commit_one(conn, event_id, action, count_key, counts, fn):
    """Per-event commit/rollback isolation -- the export-side analogue of
    `apply_changes`' own `_apply_one` (same reasoning: one event's
    transport hiccup, malformed row, or unexpected exception must not sink
    the rest of THIS tick's export batch, and each event gets its own
    commit so a failure only rolls back its own uncommitted work).
    `fn` performs the actual PUT/DELETE + `ext_exports` write and raises
    (`_ExportFailure` or anything else) on failure; it never commits
    itself."""
    try:
        fn()
    except Exception as e:
        # Final review blocker 3: the `_ExportFailure` branch used to skip
        # the `[:300]` bound entirely (only the OTHER branch had it) --
        # `_ExportFailure`'s own messages (`f"PUT {href} failed ..."`,
        # `f"DELETE {href} failed ..."`) carry an absolute CalDAV resource
        # href, so an unbounded `str(e)` here was the one channel in this
        # function inconsistent with its sibling. Same cap either way now.
        error = (str(e) if isinstance(e, _ExportFailure)
                 else f"{type(e).__name__}: {e}")[:300]
        counts["errors"].append({"event_id": event_id, "action": action, "error": error})
        try:
            conn.rollback()
            audit.log(conn, "cal.ext.export_error", {
                "event_id": event_id, "action": action, "error": error})
            conn.commit()
        except Exception:
            pass  # see _apply_one's identical reasoning: already in counts["errors"]
        return
    counts[count_key] += 1
    audit.log(conn, "cal.ext.export", {"event_id": event_id, "action": action})
    conn.commit()


def export_own(conn, cfg, request=None, now_utc=None):
    """Reverse write (Task 7): PUT every `owner='hermes'` event inside
    `[today-1d, today+extcal_horizon_weeks]` (recurring series' individual
    occurrences included automatically -- they are ordinary materialized
    `events` rows with `owner='hermes'` by default, same query, no special
    casing needed) into the "Гермес" collection (`extcal_write_calendar`),
    without VALARM -- so her iPhone shows them without ringing for them.
    `owner='iphone'` rows are NEVER touched here (the query's own `WHERE
    owner='hermes'` makes them structurally unreachable, the same
    guarantee `plan_changes`' rule #3 gives the import direction).

    Adopted rows excluded (final review, finding N3): an `owner='hermes'`
    row with a non-NULL `external_uid` was imported from HER OWN iCloud
    calendar and later adopted (`fam cal adopt`) -- it already lives in
    her calendar under its original CalDAV resource; that resource is
    untouched by adoption (only its VALARM is stripped, via `drop_valarm`,
    a completely separate write path below). Exporting it here as well
    would PUT a second copy into the "Гермес" collection, so she would
    see every adopted occurrence twice on her phone: once under its
    original calendar, once more under "Гермес". The eligibility query's
    own `AND external_uid IS NULL` excludes exactly these rows -- a plain
    Hermes-native event (never touched by extcal's import direction at
    all) always has `external_uid IS NULL`, so this changes nothing for
    the common case this function existed for before adoption existed.

    Returns counts: `{exported, updated, unchanged, deleted, errors}`.
      - `exported`: a brand-new PUT (no prior `ext_exports` row).
      - `updated`: a PUT for an event whose `body_hash` changed since its
        last export (time/title/location edit).
      - `unchanged`: `body_hash` matched -- ZERO network calls for this
        event (requirement #4).
      - `deleted`: a DELETE for a previously-exported event that is no
        longer eligible (cancelled, done, deleted outright, re-owned away
        from 'hermes', or aged out of the window either direction).
      - `errors`: a list of `{event_id, action, error}` dicts, one per
        event whose PUT/DELETE ultimately failed -- mirrors
        `apply_changes`' own `errors` shape closely enough that a caller
        (T7's `cli.py` tick wiring) can fold both into one `tick.error`
        message the same way (requirement #10).

    `extcal_write_calendar` unset/blank -> hard no-op (requirement #8):
    returns the all-zero counts above IMMEDIATELY -- this is the ONLY
    early-return branch in this function, and it comes before even a
    single `conn.execute()`, let alone a network call. This matters for a
    fresh install: T10 (a separate, later task) is what actually creates
    the "Гермес" collection in her iCloud and fills in this config key;
    until then, this function must be provably inert.

    Never calls `gate.deliver` (invariant #1, same as every other extcal
    entry point) -- this module does not even import `gate`, so there is
    structurally nothing on any path here that could reach it.

    Never raises: every per-event PUT/DELETE goes through
    `_export_commit_one`'s per-row try/except (same isolation contract as
    `apply_changes`' own `_apply_one`) -- one event's transport failure,
    malformed row, or unexpected exception is recorded in `errors` and
    every OTHER event in this same batch still gets processed.
    """
    cfg = cfg or {}
    write_url = (cfg.get("extcal_write_calendar") or "").strip()
    counts = {"exported": 0, "updated": 0, "unchanged": 0, "deleted": 0, "errors": []}
    if not write_url:
        return counts
    request = request or _request

    now_dt = _coerce_utc_dt(now_utc) or datetime.now(timezone.utc)
    horizon_weeks = cfg.get("extcal_horizon_weeks", 8)
    window_start = _iso(now_dt - timedelta(days=1))
    window_end = _iso(now_dt + timedelta(weeks=horizon_weeks))

    eligible_rows = conn.execute(
        "SELECT * FROM events WHERE owner='hermes' AND status='active' "
        "AND external_uid IS NULL "
        "AND start_utc >= ? AND start_utc <= ?",
        (window_start, window_end)).fetchall()
    eligible = {r["id"]: dict(r) for r in eligible_rows}

    exported_rows = conn.execute("SELECT * FROM ext_exports").fetchall()
    exported = {r["event_id"]: dict(r) for r in exported_rows}

    # Removal pass FIRST (same ordering `apply_changes` uses across its own
    # branches -- insert/update before cancel/drop is irrelevant there since
    # every entry targets a DIFFERENT row; here it is similarly harmless,
    # kept simply because "clean up what's gone" reads naturally before
    # "write what's current"): anything previously exported that is no
    # longer among THIS round's eligible candidates -- cancelled, done,
    # deleted outright, re-owned away from 'hermes', or aged out of the
    # window in either direction -- gets DELETEd from iCloud and dropped
    # from `ext_exports`. Deliberately broader than "cancelled" alone
    # (requirement #6's literal wording): every one of those other cases is
    # exactly as wrong to leave visible on her phone.
    for event_id, exp in exported.items():
        if event_id in eligible:
            continue
        _export_commit_one(
            conn, event_id, "delete", "deleted", counts,
            lambda exp=exp, event_id=event_id:
                _export_delete_event(conn, cfg, request, event_id, exp))

    # Export pass: PUT every eligible event whose content actually changed
    # since its last export. `body_hash` match -> zero network calls at all
    # for that event (requirement #4).
    for event_id, event in eligible.items():
        exp = exported.get(event_id)
        location = _export_location(conn, event)
        participants = _export_participants(conn, event_id)
        new_hash = _export_body_hash(event, location, participants)
        if exp is not None and exp.get("body_hash") == new_hash:
            counts["unchanged"] += 1
            continue
        is_update = exp is not None
        action = "update" if is_update else "insert"
        count_key = "updated" if is_update else "exported"
        _export_commit_one(
            conn, event_id, action, count_key, counts,
            lambda event=event, exp=exp, location=location, participants=participants, new_hash=new_hash:
                _export_put_event(conn, cfg, request, event, exp, location, participants, new_hash, now_dt))

    return counts


# ---- adopt: strip VALARM from HER OWN copy (Task 9) ------------------
#
# `drop_valarm` is the one sanctioned write this module ever makes OUTSIDE
# the "Гермес" write-target collection `export_own` owns: `fam cal adopt`
# (cli.py) calls it against an owner='iphone' event's own `external_href`
# -- her personal calendar's resource, not ours -- specifically and ONLY
# because she herself asked Hermes to take over reminding her about it.
# Every other property of that resource (SUMMARY, ORGANIZER, ATTENDEE, any
# X- property, ...) is left byte-for-byte untouched by `_strip_valarm_ics`:
# this is a surgical removal on the raw text, never a rebuild from a parsed
# `Component` (parse_ics's own declared field list would silently drop
# anything it doesn't track, e.g. ATTENDEE -- unacceptable for a write to
# HER data).
#
# Reuses this module's own existing PUT/retry primitives verbatim
# (`_export_put`, `_export_reread_etag`, `_export_headers`) -- the same
# one-412-retry contract `_export_put_event` already implements for the
# "Гермес" collection, not a second copy of it for this collection.

def _strip_valarm_ics(text):
    """Raw VCALENDAR text -> the same text with every VALARM sub-component
    (`BEGIN:VALARM` ... `END:VALARM`) removed, everything else preserved --
    or `None` if `text` is too malformed/truncated to safely write back
    (fix-round 1, finding I3: a resource that is NOT provably intact must
    never be re-PUT as a truncated stand-in for the rest of her event).

    Reuses `_unfold` (the same RFC 5545 unfolding `parse_ics` itself uses)
    so a VALARM boundary line that happens to be folded across a line
    break is still recognized, and `_export_fold_line` (the same 75-octet
    refold `_build_export_vevent` uses) to keep the result a valid CalDAV
    resource regardless of the server's original folding choices. A
    resource with no VALARM at all round-trips with the same parsed
    meaning (fold POINTS may differ -- RFC-legal, and `parse_ics` treats
    both forms identically).

    Integrity checks (fix-round 1, finding I3) -- ANY of these makes this
    function return `None` instead of a (possibly truncated) string:
      - an unclosed `BEGIN:VALARM` (no matching `END:VALARM` before the
        text ends) -- the ORIGINAL code let its own `in_alarm` flag stay
        True to the end of the text, silently dropping EVERYTHING after
        it (`END:VEVENT`, `END:VCALENDAR`, any later property) as if it
        had been part of the alarm. That is exactly the truncation this
        rewrite refuses to produce.
      - an unbalanced `BEGIN:`/`END:` line count anywhere in the resource
        (not just inside VALARM) -- the cheapest general proxy for "this
        GET came back truncated" (e.g. a network hiccup mid-read), since
        a genuinely complete VCALENDAR/VEVENT/VALARM resource always
        closes everything it opens.
      - a missing `END:VEVENT` or a missing `END:VCALENDAR` -- the two
        closing lines a real, complete single-event resource must always
        carry.
    None of these checks second-guess VALARM detection itself (still one
    case-insensitive `BEGIN:VALARM`/`END:VALARM` line pair, same as
    before, still correct for multiple back-to-back VALARM blocks and for
    `BEGIN:valarm`-style casing -- review confirmed both already work and
    asked that they be left alone).

    Never raises: garbage/truncated input degrades to `None`, never a
    partial string; empty/falsy input also returns `None` (there is
    nothing here to safely write back either).
    """
    if not text:
        return None
    lines = _unfold(text)
    out = []
    in_alarm = False
    begin_count = 0
    end_count = 0
    saw_end_vevent = False
    saw_end_vcalendar = False
    for line in lines:
        upper = line.strip().upper()
        if upper.startswith("BEGIN:"):
            begin_count += 1
        elif upper.startswith("END:"):
            end_count += 1
            if upper == "END:VEVENT":
                saw_end_vevent = True
            elif upper == "END:VCALENDAR":
                saw_end_vcalendar = True
        if upper == "BEGIN:VALARM":
            in_alarm = True
            continue
        if upper == "END:VALARM":
            in_alarm = False
            continue
        if in_alarm:
            continue
        out.append(line)
    if in_alarm:
        return None  # unclosed VALARM -- refuse rather than truncate
    if begin_count != end_count:
        return None  # unbalanced BEGIN/END -- looks truncated
    if not (saw_end_vevent and saw_end_vcalendar):
        return None  # missing a required closing component
    if not out:
        return None
    folded = [_export_fold_line(l) for l in out]
    return "\r\n".join(folded) + "\r\n"


def drop_valarm(cfg, href, etag, request=None):
    """Strip every VALARM from HER OWN iCloud copy of an event at `href`
    (`fam cal adopt`'s one sanctioned write outside the "Гермес" write-
    target -- see module note above). GET the current resource, strip its
    VALARM block(s) (`_strip_valarm_ics`), PUT the result back with
    `If-Match: etag` (the caller's stored `events.external_etag`) via
    `_export_put` -- exactly one re-read-and-retry on a 412 conflict, via
    `_export_reread_etag`, the SAME contract `_export_put_event` already
    implements for the other collection (never a second implementation of
    either).

    Two refusal cases added in fix-round 1, BOTH before any PUT is
    attempted:
      - finding I3: `_strip_valarm_ics` returned `None` (malformed or
        truncated GET response) -- writing back a resource that isn't
        provably intact would risk clobbering the rest of her event.
      - finding I2: no etag to write with, EITHER on the way in (`etag`
        arg empty -- a real, if rare, gap: the sibling update path a few
        hundred lines up this same file already has to `COALESCE` around
        exactly this) or after a 412's re-read (`_export_reread_etag`
        returned nothing). `_export_put`'s own docstring says writing
        with no `If-Match` is fine ONLY because ITS collection holds
        nothing but this module's own prior writes -- that reasoning
        does NOT carry over here: this collection is HERS, and she may
        have edited the very same resource (on her phone) between import
        and this `adopt` call. An unconditional PUT here would silently
        overwrite whatever she just wrote with the copy this function
        read moments earlier. Refusing is strictly safer than a 412 retry
        that isn't actually conditional on anything.

    Returns `(ok, new_etag, detail)`:
      - `ok=True`: the PUT succeeded (with or without the one retry);
        `new_etag` is whatever the server returned (may be None -- some
        servers only hand back ETag on a follow-up GET, same caveat
        `_export_put` already documents for the other collection).
      - `ok=False`: the initial GET failed, the resource didn't pass the
        integrity check above, no etag was available to write with
        safely, or the PUT itself (including after the one retry)
        failed; `detail` is a short human-readable reason (status code or
        a plain description -- never the auth header, never the response
        body).

    Never raises: `request(...)` (the injected seam, `_request` by
    default) already never raises; any failure degrades to
    `(False, None, detail)`. The caller (`cli.cmd_cal_adopt`) is expected
    to treat a `False` here as "adoption still stands, but her phone may
    still ring once more for this event" -- NOT as a reason to undo the
    ownership flip that already happened (design decision, task 9 brief):
    she asked Hermes to remind her, so silence from Hermes because of a
    network hiccup (or a refused unsafe write) on the OTHER phone's copy
    would be the worse failure mode of the two.
    """
    request = request or _request
    resp = request("GET", href, headers=_export_headers(cfg), timeout=DEFAULT_TIMEOUT)
    if resp is None or resp.status not in (200, 207):
        status = resp.status if resp is not None else None
        return False, None, f"GET {href} failed (status={status})"

    stripped = _strip_valarm_ics(resp.text)
    if stripped is None:
        return False, None, (
            f"GET {href} returned a malformed or truncated ICS resource "
            f"-- refusing to write it back"
        )

    if not etag:
        return False, None, (
            "no external_etag on record for this resource -- refusing an "
            "unconditional PUT into her own collection (unlike the "
            "\"Гермес\" write-target, this collection can hold her own "
            "concurrent edits)"
        )

    ok, new_etag, status, conflict = _export_put(cfg, href, stripped, etag, request)
    if conflict:
        # Requirement (task 9 brief): exactly one re-read-and-retry on a
        # 412 ETag conflict -- never a second retry, regardless of THIS
        # attempt's own outcome. Same helper `_export_put_event` already
        # uses for its own 412 path.
        fresh_etag = _export_reread_etag(cfg, href, request)
        if not fresh_etag:
            # Fix-round 1, finding I2: a retry with no etag at all would
            # be an unconditional overwrite of a resource that is NOT
            # ours -- refuse instead (see this function's own docstring).
            return False, None, (
                f"412 conflict on {href}, and the re-read found no fresh "
                f"etag -- refusing an unconditional retry PUT"
            )
        ok, new_etag, status, _conflict2 = _export_put(cfg, href, stripped, fresh_etag, request)
    if not ok:
        return False, None, f"PUT {href} failed (status={status})"
    return True, new_etag, None
