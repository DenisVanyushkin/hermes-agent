"""iCloud CalDAV transport + read-only probe (Task 0 Steps 1-2 + Task 1).

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

ICS parsing here is deliberately crude (regex over raw VEVENT blocks) --
the real streaming/unfolding parser is Task 2. `probe` only needs feature
COUNTS, not correct values.

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
# practical risk here. It does still expand INTERNAL entities without a
# built-in nesting limit ("billion laughs"). This module is stdlib-only
# by explicit project constraint (no defusedxml/lxml dependency for this
# task), so as a cheap, dependency-free mitigation, any response body
# past this size is treated as a parse failure BEFORE it reaches
# ET.fromstring -- a legitimate PROPFIND/REPORT response for one
# person's calendar over an 8-week horizon is nowhere near this size.
# Residual risk is further bounded by the host-guard: only TLS
# (`https://`) responses from `*.icloud.com` are ever parsed at all, not
# arbitrary/attacker-supplied XML.
MAX_XML_BYTES = 8 * 1024 * 1024


def _parse_xml(text):
    """ET.fromstring wrapper enforcing MAX_XML_BYTES. Raises ET.ParseError
    (same as a real malformed-XML failure) when the body is oversized, so
    every existing try/except ET.ParseError call site handles it for
    free."""
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
        if not location or not _scheme_and_host_ok(location):
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
    body = _SYNC_BODY_TMPL.format(token=_xml_escape(sync_token))
    resp = request("REPORT", calendar_url, headers=_dav_headers(cfg, "1"),
                    body=body, timeout=DEFAULT_TIMEOUT)
    if resp is None or resp.status not in (200, 207):
        return None, None, False
    try:
        items, new_token = _parse_multistatus_items(resp.text)
    except Exception:
        return None, None, False
    return items, new_token, True


def _calendar_query(cfg, calendar_url, horizon_weeks, request):
    start, end = _time_range(horizon_weeks)
    body = _QUERY_BODY_TMPL.format(start=start, end=end)
    resp = request("REPORT", calendar_url, headers=_dav_headers(cfg, "1"),
                    body=body, timeout=DEFAULT_TIMEOUT)
    if resp is None or resp.status not in (200, 207):
        return None, None
    try:
        items, _token = _parse_multistatus_items(resp.text)
    except Exception:
        return None, None
    # calendar-query is not incremental -- it never yields a usable
    # sync-token for the next round.
    return items, None


def fetch_changes(cfg, calendar, sync_token=None, request=None):
    """One page of changes for `calendar` (a Calendar dict from
    discover(), or a bare url string).

    - sync_token given: REPORT sync-collection. If the server accepts it
      (2xx/207 with parseable XML), returns (items, new_token).
    - sync_token given but the server rejects it (any non-2xx/207 status,
      e.g. 403 "invalid sync-token", or a 5xx, or malformed XML): falls
      back to REPORT calendar-query over the horizon time-range.
    - sync_token is None (first sync for this calendar): goes straight to
      the calendar-query fallback -- an unfiltered sync-collection would
      return the WHOLE collection, unbounded by the horizon window.

    Never raises. On total failure (missing credentials, network error,
    every attempted REPORT failing, malformed XML) returns (None, None).

    `items` is a list of `{href, deleted, etag, ics}` dicts -- `ics` is
    the raw VCALENDAR text for a live item, None for a tombstoned
    (deleted=True) one.
    """
    cfg = cfg or {}
    request = request or _request
    calendar_url = calendar["url"] if isinstance(calendar, dict) else calendar

    if _auth_header(cfg) is None:
        return None, None

    if sync_token:
        items, new_token, ok = _sync_collection(cfg, calendar_url, sync_token, request)
        if ok:
            return items, new_token
        # fall through to calendar-query below

    horizon_weeks = cfg.get("extcal_horizon_weeks", 8)
    return _calendar_query(cfg, calendar_url, horizon_weeks, request)


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

    for calendar in calendars:
        # Anti-echo belt 1 (design doc invariant #4): never read back our
        # own write-target collection as if it were "her" data.
        if write_url and _same_calendar(calendar["url"], write_url):
            continue
        if read_filter and calendar["url"] not in read_filter and calendar["name"] not in read_filter:
            continue

        calendars_out.append({
            "url": calendar["url"],
            "name": calendar["name"],
            "ctag": calendar.get("ctag"),
            "supports_sync_token": calendar.get("supports_sync_token", False),
        })

        items, _new_token = fetch_changes(cfg, calendar, sync_token=None, request=request)
        if items is None:
            errors.append(f"fetch failed for calendar: {calendar['name']}")
            continue
        for item in items:
            if item.get("deleted"):
                continue
            _tally_ics(counts, item.get("ics"))

    return {"calendars": calendars_out, "counts": counts, "errors": errors}
