"""Task 1: CalDAV transport (_request, discover, fetch_changes).

Everything here runs against a fake seam -- no test touches the real
network. Two layers are tested:

  - `_request` itself: the host/scheme anti-SSRF guard, and its
    `_default_open` seam (so we can also exercise its own timeout/error
    handling without hitting urllib for real).
  - `discover`/`fetch_changes`: via the higher-level `request` callable
    they accept, matching how Task 2 will inject a fake CalDAV server.
"""
import socket
import urllib.error
from xml.etree import ElementTree as ET

from fam import extcal


# ---------------------------------------------------------------------
# _request: host/scheme guard (anti-SSRF)
# ---------------------------------------------------------------------

def test_request_rejects_http_scheme_on_allowed_host(monkeypatch):
    def boom(req, timeout):
        raise AssertionError("must not reach the network for a rejected scheme")
    monkeypatch.setattr(extcal, "_default_open", boom)
    assert extcal._request("PROPFIND", "http://caldav.icloud.com/") is None


def test_request_rejects_ftp_scheme_on_allowed_host(monkeypatch):
    # Mirrors geo2gis's known minor (scheme wasn't checked independently
    # of the host allowlist, so ftp:// slipped through) -- must NOT
    # reproduce that here.
    def boom(req, timeout):
        raise AssertionError("must not reach the network for ftp://")
    monkeypatch.setattr(extcal, "_default_open", boom)
    assert extcal._request("PROPFIND", "ftp://caldav.icloud.com/") is None


def test_request_rejects_foreign_host(monkeypatch):
    def boom(req, timeout):
        raise AssertionError("must not reach the network for a foreign host")
    monkeypatch.setattr(extcal, "_default_open", boom)
    assert extcal._request("PROPFIND", "https://evil.example.com/") is None


def test_request_rejects_host_that_merely_contains_icloud_com(monkeypatch):
    # "icloud.com.evil.example" must NOT pass an endswith(".icloud.com")-
    # style check done carelessly; urlsplit().hostname handles this
    # correctly, this test pins that it stays correct.
    def boom(req, timeout):
        raise AssertionError("must not reach the network for a lookalike host")
    monkeypatch.setattr(extcal, "_default_open", boom)
    assert extcal._request("PROPFIND", "https://caldav.icloud.com.evil.example/") is None


def test_request_allows_bare_icloud_com_and_subdomains(monkeypatch):
    seen = []

    def fake_open(req, timeout):
        seen.append(req.full_url)
        return extcal.Response(207, b"<ok/>")

    monkeypatch.setattr(extcal, "_default_open", fake_open)
    assert extcal._request("PROPFIND", "https://icloud.com/") is not None
    assert extcal._request("PROPFIND", "https://p12-caldav.icloud.com/x") is not None
    assert len(seen) == 2


# ---------------------------------------------------------------------
# _request: never raises; timeout/5xx/redirect handling
# ---------------------------------------------------------------------

def test_request_timeout_returns_none_not_exception(monkeypatch, capsys):
    def fake_open(req, timeout):
        raise socket.timeout("timed out")
    monkeypatch.setattr(extcal, "_default_open", fake_open)
    assert extcal._request("PROPFIND", "https://caldav.icloud.com/") is None
    assert capsys.readouterr().err.strip() != ""


def test_request_http_error_returns_response_with_status(monkeypatch):
    def fake_open(req, timeout):
        raise urllib.error.HTTPError(
            "https://caldav.icloud.com/", 500, "Internal Server Error", None, None)
    monkeypatch.setattr(extcal, "_default_open", fake_open)
    resp = extcal._request("PROPFIND", "https://caldav.icloud.com/")
    assert resp is not None
    assert resp.status == 500


def test_request_does_not_follow_redirect_blindly(monkeypatch):
    def fake_open(req, timeout):
        raise extcal._RedirectCaught(301, "https://p12-caldav.icloud.com/next")
    monkeypatch.setattr(extcal, "_default_open", fake_open)
    resp = extcal._request("PROPFIND", "https://caldav.icloud.com/.well-known/caldav")
    assert resp is not None
    assert resp.status == 301
    assert resp.headers["Location"] == "https://p12-caldav.icloud.com/next"


def test_request_forwards_timeout(monkeypatch):
    seen = {}

    def fake_open(req, timeout):
        seen["timeout"] = timeout
        return extcal.Response(207, b"<ok/>")

    monkeypatch.setattr(extcal, "_default_open", fake_open)
    extcal._request("PROPFIND", "https://caldav.icloud.com/", timeout=7)
    assert seen["timeout"] == 7


# ---------------------------------------------------------------------
# _request: Basic-auth header is built but never leaks
# ---------------------------------------------------------------------

def test_auth_header_built_from_env_password(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "s3kr3t-app-pw")
    headers = extcal._auth_header({"extcal_username": "amina@icloud.com"})
    assert headers is not None
    assert headers["Authorization"].startswith("Basic ")
    import base64
    decoded = base64.b64decode(headers["Authorization"].split(" ", 1)[1]).decode()
    assert decoded == "amina@icloud.com:s3kr3t-app-pw"


def test_auth_header_none_without_password(monkeypatch):
    monkeypatch.delenv("ICLOUD_APP_PASSWORD", raising=False)
    assert extcal._auth_header({"extcal_username": "amina@icloud.com"}) is None


def test_auth_header_none_without_username(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "s3kr3t-app-pw")
    assert extcal._auth_header({"extcal_username": ""}) is None


def test_password_never_appears_in_request_repr_or_stderr(monkeypatch, capsys):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "very-secret-value-123")
    cfg = {"extcal_username": "amina@icloud.com"}
    headers = extcal._dav_headers(cfg, "0")

    def fake_open(req, timeout):
        # simulate a failure that goes through the generic except branch
        raise RuntimeError("boom")

    monkeypatch.setattr(extcal, "_default_open", fake_open)
    resp = extcal._request("PROPFIND", "https://caldav.icloud.com/", headers=headers)
    assert resp is None
    err = capsys.readouterr().err
    assert "very-secret-value-123" not in err
    assert repr(headers) is not None  # headers dict itself legitimately holds it
    # but nothing this module prints/returns downstream should:
    assert "very-secret-value-123" not in repr(extcal.Response(200, b"ok"))


# ---------------------------------------------------------------------
# fake CalDAV server helper for discover()/fetch_changes()
# ---------------------------------------------------------------------

_COLLECTIONS_XML = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"
               xmlns:cs="http://calendarserver.org/ns/">
  <d:response>
    <d:href>/123/calendars/home/</d:href>
    <d:propstat><d:prop>
      <d:displayname>calendars-home</d:displayname>
      <d:resourcetype><d:collection/></d:resourcetype>
    </d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/123/calendars/home/personal/</d:href>
    <d:propstat><d:prop>
      <d:displayname>Personal</d:displayname>
      <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
      <c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>
      <cs:getctag>ctag-abc</cs:getctag>
      <d:sync-token>sync-token-1</d:sync-token>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>
"""


def _fake_full_discover_server(collections_xml=_COLLECTIONS_XML,
                                redirect_host="p12-caldav.icloud.com"):
    calls = []

    def fake(method, url, headers=None, body=None, timeout=20):
        calls.append((method, url, body))
        if url == extcal.WELL_KNOWN_URL:
            return extcal.Response(301, b"", {"Location": f"https://{redirect_host}/"})
        if url == f"https://{redirect_host}/" and "current-user-principal" in (body or ""):
            xml = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
                   '<d:response><d:href>/123/principal/</d:href>'
                   '<d:propstat><d:prop><d:current-user-principal>'
                   '<d:href>/123/principal/</d:href>'
                   '</d:current-user-principal></d:prop></d:propstat></d:response>'
                   '</d:multistatus>')
            return extcal.Response(207, xml.encode())
        if url == f"https://{redirect_host}/123/principal/":
            xml = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" '
                   'xmlns:c="urn:ietf:params:xml:ns:caldav">'
                   '<d:response><d:href>/123/principal/</d:href>'
                   '<d:propstat><d:prop><c:calendar-home-set>'
                   '<d:href>/123/calendars/home/</d:href>'
                   '</c:calendar-home-set></d:prop></d:propstat></d:response>'
                   '</d:multistatus>')
            return extcal.Response(207, xml.encode())
        if url == f"https://{redirect_host}/123/calendars/home/":
            return extcal.Response(207, collections_xml.encode())
        return extcal.Response(404, b"")

    return fake, calls


# ---------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------

def test_discover_full_walk_returns_calendar(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    cfg = {"extcal_username": "amina@icloud.com"}
    fake, calls = _fake_full_discover_server()

    result = extcal.discover(cfg, request=fake)
    assert len(result) == 1
    cal = result[0]
    assert cal["url"] == "https://p12-caldav.icloud.com/123/calendars/home/personal/"
    assert cal["name"] == "Personal"
    assert cal["ctag"] == "ctag-abc"
    assert cal["sync_token"] == "sync-token-1"
    assert cal["supports_sync_token"] is True
    assert cal["components"] == ["VEVENT"]
    assert len(calls) == 4  # well-known, principal, home-set, collections


def test_discover_missing_password_returns_empty_no_network(monkeypatch):
    monkeypatch.delenv("ICLOUD_APP_PASSWORD", raising=False)

    def boom(method, url, headers=None, body=None, timeout=20):
        raise AssertionError("must not touch the network without credentials")

    assert extcal.discover({"extcal_username": "amina@icloud.com"}, request=boom) == []


def test_discover_rejects_redirect_to_foreign_host(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake(method, url, headers=None, body=None, timeout=20):
        if url == extcal.WELL_KNOWN_URL:
            return extcal.Response(301, b"", {"Location": "https://evil.example.com/"})
        raise AssertionError("must not follow a redirect to a disallowed host")

    result = extcal.discover({"extcal_username": "amina@icloud.com"}, request=fake)
    assert result == []


# ---- M1: well-known's Location must be resolved (urljoin) BEFORE the
# host-guard check, same order as principal_href/home_href below it --
# otherwise a relative Location is rejected for the wrong reason
# (urlsplit().hostname == "" reads as "disallowed host", not "relative").

def test_discover_well_known_relative_redirect_is_resolved_before_guard(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    # WELL_KNOWN_URL is https://caldav.icloud.com/.well-known/caldav --
    # a relative Location must resolve against THAT host, which IS
    # allowed, so discovery should proceed rather than bail out.
    calls = []

    def fake(method, url, headers=None, body=None, timeout=20):
        calls.append(url)
        if url == extcal.WELL_KNOWN_URL:
            return extcal.Response(301, b"", {"Location": "/next"})
        if url == "https://caldav.icloud.com/next":
            # reached only if the relative Location was correctly
            # resolved to an allowed host instead of being rejected
            xml = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
                   '<d:response><d:href>/123/principal/</d:href>'
                   '<d:propstat><d:prop><d:current-user-principal>'
                   '<d:href>/123/principal/</d:href></d:current-user-principal>'
                   '</d:prop></d:propstat></d:response></d:multistatus>')
            return extcal.Response(207, xml.encode())
        return extcal.Response(404, b"")

    extcal.discover({"extcal_username": "amina@icloud.com"}, request=fake)
    assert "https://caldav.icloud.com/next" in calls


def test_discover_timeout_returns_empty_no_exception(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake(method, url, headers=None, body=None, timeout=20):
        return None  # simulates _request's own timeout->None collapse

    result = extcal.discover({"extcal_username": "amina@icloud.com"}, request=fake)
    assert result == []


def test_discover_malformed_xml_returns_empty_no_exception(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake(method, url, headers=None, body=None, timeout=20):
        if url == extcal.WELL_KNOWN_URL:
            return extcal.Response(207, b"<not><valid xml at all")
        raise AssertionError("unreachable")

    result = extcal.discover({"extcal_username": "amina@icloud.com"}, request=fake)
    assert result == []


# ---------------------------------------------------------------------
# I3: _parse_xml rejects any body carrying a DTD/entity declaration
# (billion-laughs/quadratic-blowup mitigation), not just oversized ones.
# ---------------------------------------------------------------------

def test_parse_xml_rejects_doctype_with_entity_bomb():
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">]>'
            '<d:multistatus xmlns:d="DAV:">&lol2;</d:multistatus>')
    try:
        extcal._parse_xml(bomb)
        assert False, "expected ET.ParseError"
    except ET.ParseError:
        pass


def test_parse_xml_rejects_doctype_case_insensitively():
    bomb = ('<?xml version="1.0"?><!doctype lolz [<!entity lol "lol">]>'
            '<d:multistatus xmlns:d="DAV:">&lol;</d:multistatus>')
    try:
        extcal._parse_xml(bomb)
        assert False, "expected ET.ParseError"
    except ET.ParseError:
        pass


def test_parse_xml_accepts_normal_multistatus_without_doctype():
    ok = '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"><d:response/></d:multistatus>'
    root = extcal._parse_xml(ok)
    assert root is not None


def test_discover_rejects_dtd_bearing_response_no_exception(monkeypatch):
    # Even if a compromised/misbehaving server (or a broken fake in a
    # future test) sends a DOCTYPE-bearing body, discover() must degrade
    # to [] + an error, never raise and never actually expand entities.
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    bomb = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x "y">]>'
            '<d:multistatus xmlns:d="DAV:"/>').encode()

    def fake(method, url, headers=None, body=None, timeout=20):
        if url == extcal.WELL_KNOWN_URL:
            return extcal.Response(207, bomb)
        raise AssertionError("unreachable")

    result = extcal.discover({"extcal_username": "amina@icloud.com"}, request=fake)
    assert result == []


# ---------------------------------------------------------------------
# fetch_changes()
# ---------------------------------------------------------------------

_ICS_TIMED = (
    "BEGIN:VEVENT\r\nUID:evt-1@example.com\r\nSUMMARY:Yoga\r\n"
    "DTSTART;TZID=Asia/Almaty:20260728T180000\r\nEND:VEVENT\r\n"
)

_SYNC_RESPONSE_XML = f"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:sync-token>sync-token-2</d:sync-token>
  <d:response>
    <d:href>/123/calendars/home/personal/evt-1.ics</d:href>
    <d:propstat><d:prop>
      <d:getetag>"etag-1"</d:getetag>
      <c:calendar-data>{_ICS_TIMED}</c:calendar-data>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>
"""

_QUERY_RESPONSE_XML = f"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/123/calendars/home/personal/evt-1.ics</d:href>
    <d:propstat><d:prop>
      <d:getetag>"etag-1"</d:getetag>
      <c:calendar-data>{_ICS_TIMED}</c:calendar-data>
    </d:prop></d:propstat>
  </d:response>
</d:multistatus>
"""

_CALENDAR = {"url": "https://p12-caldav.icloud.com/123/calendars/home/personal/"}


def _cfg():
    return {"extcal_username": "amina@icloud.com", "extcal_horizon_weeks": 8}


def test_fetch_changes_sync_collection_with_token(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    seen = {}

    def fake(method, url, headers=None, body=None, timeout=20):
        seen["method"] = method
        seen["body"] = body
        assert method == "REPORT"
        assert "sync-collection" in body
        assert "sync-token-old" in body
        return extcal.Response(207, _SYNC_RESPONSE_XML.encode())

    items, new_token, sync_info = extcal.fetch_changes(
        _cfg(), _CALENDAR, sync_token="sync-token-old", request=fake)
    assert new_token == "sync-token-2"
    assert len(items) == 1
    assert items[0]["href"].endswith("evt-1.ics")
    assert items[0]["etag"] == '"etag-1"'
    assert "Yoga" in items[0]["ics"]
    assert seen["method"] == "REPORT"
    # I1: steady-state incremental sync is unambiguously reported.
    assert sync_info == {"mode": "sync_collection", "reason": None}


def test_fetch_changes_falls_back_to_calendar_query_on_error(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    calls = []

    def fake(method, url, headers=None, body=None, timeout=20):
        calls.append(body)
        if "sync-collection" in body:
            return extcal.Response(403, b"<error/>")  # invalid sync-token
        assert "calendar-query" in body
        assert "time-range" in body
        return extcal.Response(207, _QUERY_RESPONSE_XML.encode())

    items, new_token, sync_info = extcal.fetch_changes(
        _cfg(), _CALENDAR, sync_token="stale-token", request=fake)
    assert new_token is None  # calendar-query never yields an incremental token
    assert len(items) == 1
    assert len(calls) == 2  # sync-collection attempt, then calendar-query fallback
    # I1: this IS the case the finding is about -- a token was offered,
    # sync-collection was rejected, and a full re-scan silently happened.
    # That must be visible and distinguishable from "no token yet".
    assert sync_info == {"mode": "fallback_full", "reason": "http_403"}


def test_fetch_changes_no_token_goes_straight_to_calendar_query(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake(method, url, headers=None, body=None, timeout=20):
        assert "calendar-query" in body
        return extcal.Response(207, _QUERY_RESPONSE_XML.encode())

    items, new_token, sync_info = extcal.fetch_changes(
        _cfg(), _CALENDAR, sync_token=None, request=fake)
    assert new_token is None
    assert len(items) == 1
    # I1: NOT a fallback -- no token was ever offered, so this is the
    # expected initial full sync, not a broken incremental exchange.
    assert sync_info == {"mode": "initial_full", "reason": None}


def test_fetch_changes_distinguishes_initial_full_from_fallback_full(monkeypatch):
    """I1's core requirement: a caller (Task 6's tick) must be able to
    tell "first sync, no token yet" apart from "sync-collection was
    attempted and REJECTED, we silently fell back to a full re-scan" --
    even though both paths return the exact same `items`/`new_token`
    shape. Only `sync_info["mode"]` carries the distinction.
    """
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake_initial(method, url, headers=None, body=None, timeout=20):
        return extcal.Response(207, _QUERY_RESPONSE_XML.encode())

    def fake_fallback(method, url, headers=None, body=None, timeout=20):
        if "sync-collection" in body:
            return extcal.Response(403, b"<error/>")
        return extcal.Response(207, _QUERY_RESPONSE_XML.encode())

    _items1, _tok1, info_initial = extcal.fetch_changes(
        _cfg(), _CALENDAR, sync_token=None, request=fake_initial)
    _items2, _tok2, info_fallback = extcal.fetch_changes(
        _cfg(), _CALENDAR, sync_token="some-token", request=fake_fallback)

    assert info_initial["mode"] == "initial_full"
    assert info_fallback["mode"] == "fallback_full"
    assert info_initial["mode"] != info_fallback["mode"]
    assert info_fallback["reason"] is not None
    assert info_initial["reason"] is None


# --- Fix-round 3, Critical finding C1: `force_full` -- the periodic
# rolling-horizon re-baseline (Task 6's tick decides WHEN, based on
# `meta`; this module only needs to honor the flag by skipping
# sync-collection and reusing the SAME calendar-query path already used
# for initial_full/fallback_full, under a distinct "periodic_full" mode
# label).

def test_fetch_changes_force_full_skips_sync_collection_entirely(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    calls = []

    def fake(method, url, headers=None, body=None, timeout=20):
        calls.append(body)
        # sync-collection must never even be attempted when force_full
        # is set -- if it were, this fake would 200 it just like a
        # healthy server, and the test below would fail to catch a
        # regression that silently re-enables it.
        assert "sync-collection" not in body
        assert "calendar-query" in body
        return extcal.Response(207, _QUERY_RESPONSE_XML.encode())

    items, new_token, sync_info = extcal.fetch_changes(
        _cfg(), _CALENDAR, sync_token="a-perfectly-valid-token",
        request=fake, force_full=True)
    assert len(calls) == 1  # exactly one request -- no wasted sync-collection round-trip
    assert new_token is None  # calendar-query never yields an incremental token
    assert len(items) == 1
    assert sync_info == {"mode": "periodic_full", "reason": None}


def test_fetch_changes_force_full_without_a_token_is_still_initial_full(monkeypatch):
    # force_full with NO sync_token at all is indistinguishable from a
    # plain first-ever sync -- there was never a token to skip offering,
    # so this stays "initial_full", not a new, redundant third label.
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake(method, url, headers=None, body=None, timeout=20):
        assert "calendar-query" in body
        return extcal.Response(207, _QUERY_RESPONSE_XML.encode())

    items, new_token, sync_info = extcal.fetch_changes(
        _cfg(), _CALENDAR, sync_token=None, request=fake, force_full=True)
    assert sync_info == {"mode": "initial_full", "reason": None}


def test_fetch_changes_force_full_defaults_to_false(monkeypatch):
    # Regression guard: omitting force_full entirely must behave exactly
    # as before this finding -- a valid token still goes through
    # sync-collection first.
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake(method, url, headers=None, body=None, timeout=20):
        assert "sync-collection" in body
        return extcal.Response(207, _SYNC_RESPONSE_XML.encode())

    items, new_token, sync_info = extcal.fetch_changes(
        _cfg(), _CALENDAR, sync_token="tok", request=fake)
    assert sync_info["mode"] == "sync_collection"


def test_fetch_changes_timeout_returns_none_no_exception(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake(method, url, headers=None, body=None, timeout=20):
        return None

    items, new_token, sync_info = extcal.fetch_changes(
        _cfg(), _CALENDAR, sync_token=None, request=fake)
    assert items is None and new_token is None
    assert sync_info["mode"] == "error"
    assert "no_response" in sync_info["reason"]


def test_fetch_changes_5xx_returns_none_no_exception(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake(method, url, headers=None, body=None, timeout=20):
        return extcal.Response(500, b"server error")

    items, new_token, sync_info = extcal.fetch_changes(
        _cfg(), _CALENDAR, sync_token="tok", request=fake)
    assert items is None and new_token is None
    assert sync_info["mode"] == "error"
    assert "http_500" in sync_info["reason"]


def test_fetch_changes_reports_both_failures_when_fallback_also_fails(monkeypatch):
    # sync_token given, sync-collection fails (403), the calendar-query
    # fallback ALSO fails (500) -- error reason must surface both, not
    # just whichever failed last.
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake(method, url, headers=None, body=None, timeout=20):
        if "sync-collection" in body:
            return extcal.Response(403, b"<error/>")
        return extcal.Response(500, b"boom")

    items, new_token, sync_info = extcal.fetch_changes(
        _cfg(), _CALENDAR, sync_token="tok", request=fake)
    assert items is None and new_token is None
    assert sync_info["mode"] == "error"
    assert "http_403" in sync_info["reason"]
    assert "http_500" in sync_info["reason"]


def test_fetch_changes_malformed_xml_returns_none_no_exception(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake(method, url, headers=None, body=None, timeout=20):
        return extcal.Response(207, b"<broken xml")

    items, new_token, sync_info = extcal.fetch_changes(
        _cfg(), _CALENDAR, sync_token=None, request=fake)
    assert items is None and new_token is None
    assert sync_info["mode"] == "error"
    assert "malformed_xml" in sync_info["reason"]


def test_fetch_changes_missing_password_returns_none(monkeypatch):
    monkeypatch.delenv("ICLOUD_APP_PASSWORD", raising=False)

    def boom(method, url, headers=None, body=None, timeout=20):
        raise AssertionError("must not touch the network without credentials")

    items, new_token, sync_info = extcal.fetch_changes(
        _cfg(), _CALENDAR, sync_token=None, request=boom)
    assert items is None and new_token is None
    assert sync_info == {"mode": "error", "reason": "missing_credentials"}


def test_fetch_changes_reports_deleted_items(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    xml = """<?xml version="1.0"?>
    <d:multistatus xmlns:d="DAV:">
      <d:sync-token>sync-token-3</d:sync-token>
      <d:response>
        <d:href>/123/calendars/home/personal/gone.ics</d:href>
        <d:status>HTTP/1.1 404 Not Found</d:status>
      </d:response>
    </d:multistatus>
    """

    def fake(method, url, headers=None, body=None, timeout=20):
        return extcal.Response(207, xml.encode())

    items, new_token, sync_info = extcal.fetch_changes(
        _cfg(), _CALENDAR, sync_token="tok", request=fake)
    assert new_token == "sync-token-3"
    assert len(items) == 1
    assert items[0]["deleted"] is True
    assert items[0]["ics"] is None
    assert sync_info["mode"] == "sync_collection"
