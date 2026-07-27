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

    items, new_token = extcal.fetch_changes(_cfg(), _CALENDAR,
                                             sync_token="sync-token-old", request=fake)
    assert new_token == "sync-token-2"
    assert len(items) == 1
    assert items[0]["href"].endswith("evt-1.ics")
    assert items[0]["etag"] == '"etag-1"'
    assert "Yoga" in items[0]["ics"]
    assert seen["method"] == "REPORT"


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

    items, new_token = extcal.fetch_changes(_cfg(), _CALENDAR,
                                             sync_token="stale-token", request=fake)
    assert new_token is None  # calendar-query never yields an incremental token
    assert len(items) == 1
    assert len(calls) == 2  # sync-collection attempt, then calendar-query fallback


def test_fetch_changes_no_token_goes_straight_to_calendar_query(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake(method, url, headers=None, body=None, timeout=20):
        assert "calendar-query" in body
        return extcal.Response(207, _QUERY_RESPONSE_XML.encode())

    items, new_token = extcal.fetch_changes(_cfg(), _CALENDAR, sync_token=None, request=fake)
    assert new_token is None
    assert len(items) == 1


def test_fetch_changes_timeout_returns_none_no_exception(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake(method, url, headers=None, body=None, timeout=20):
        return None

    items, new_token = extcal.fetch_changes(_cfg(), _CALENDAR, sync_token=None, request=fake)
    assert items is None and new_token is None


def test_fetch_changes_5xx_returns_none_no_exception(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake(method, url, headers=None, body=None, timeout=20):
        return extcal.Response(500, b"server error")

    items, new_token = extcal.fetch_changes(_cfg(), _CALENDAR, sync_token="tok", request=fake)
    assert items is None and new_token is None


def test_fetch_changes_malformed_xml_returns_none_no_exception(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake(method, url, headers=None, body=None, timeout=20):
        return extcal.Response(207, b"<broken xml")

    items, new_token = extcal.fetch_changes(_cfg(), _CALENDAR, sync_token=None, request=fake)
    assert items is None and new_token is None


def test_fetch_changes_missing_password_returns_none(monkeypatch):
    monkeypatch.delenv("ICLOUD_APP_PASSWORD", raising=False)

    def boom(method, url, headers=None, body=None, timeout=20):
        raise AssertionError("must not touch the network without credentials")

    items, new_token = extcal.fetch_changes(_cfg(), _CALENDAR, sync_token=None, request=boom)
    assert items is None and new_token is None


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

    items, new_token = extcal.fetch_changes(_cfg(), _CALENDAR, sync_token="tok", request=fake)
    assert new_token == "sync-token-3"
    assert len(items) == 1
    assert items[0]["deleted"] is True
    assert items[0]["ics"] is None
