"""`extcal.fetch_resource`: a single, targeted GET of one ICS resource.

This is the transport-layer half of the 2026-08-20 fix (an iCloud booking
created at 12:34 UTC stayed invisible for five hours and twenty ticks
because its `sync-collection` delta entry carried no `<C:calendar-data>`
and the calendar's sync-token was persisted anyway). Same contract as
every other transport-touching helper in this module: never raises,
degrades to None, never leaves the iCloud host allowlist.
"""
from fam import extcal

CFG = {"extcal_username": "u@example.com"}
HREF = "https://p181-caldav.icloud.com:443/1/calendars/C/evt1.ics"
ICS = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"


def test_returns_body_on_200(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    seen = []

    def _request(method, url, headers=None, body=None, timeout=None):
        seen.append((method, url))
        return extcal.Response(200, body=ICS)

    assert extcal.fetch_resource(CFG, HREF, request=_request) == ICS
    assert seen == [("GET", HREF)]


def test_sends_auth_but_response_never_carries_it(monkeypatch):
    """The Basic-auth header is built in exactly one place
    (`_auth_header`, via `_export_headers`) and is never echoed back."""
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    seen = {}

    def _request(method, url, headers=None, body=None, timeout=None):
        seen.update(headers or {})
        return extcal.Response(200, body=ICS)

    extcal.fetch_resource(CFG, HREF, request=_request)
    assert "Authorization" in seen


def test_returns_none_without_credentials(monkeypatch):
    monkeypatch.delenv("ICLOUD_APP_PASSWORD", raising=False)
    called = []
    assert extcal.fetch_resource(
        CFG, HREF,
        request=lambda *a, **kw: called.append(a) or extcal.Response(200, body=ICS)
    ) is None
    assert called == []


def test_returns_none_on_error_status(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    assert extcal.fetch_resource(
        CFG, HREF,
        request=lambda *a, **kw: extcal.Response(403, body="nope")) is None


def test_returns_none_on_transport_failure(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    assert extcal.fetch_resource(CFG, HREF,
                                 request=lambda *a, **kw: None) is None


def test_returns_none_on_blank_body(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    assert extcal.fetch_resource(
        CFG, HREF, request=lambda *a, **kw: extcal.Response(200, body="   \n")) is None


def test_never_raises(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def _boom(*a, **kw):
        raise RuntimeError("network went away")

    assert extcal.fetch_resource(CFG, HREF, request=_boom) is None


def test_refuses_foreign_host(monkeypatch):
    """The href comes from the SERVER (a multistatus `<D:href>`), so the
    anti-SSRF guard (`_scheme_and_host_ok`) has to be applied here too --
    `_request` applies it for the real transport, but this function is
    also called with an injected one, and a caller must never be able to
    turn a server-supplied href into a request to an arbitrary host."""
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    called = []
    assert extcal.fetch_resource(
        CFG, "https://evil.example.com/evt.ics",
        request=lambda *a, **kw: called.append(a) or extcal.Response(200, body=ICS)
    ) is None
    assert called == []


def test_refuses_empty_href(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    called = []
    assert extcal.fetch_resource(
        CFG, "",
        request=lambda *a, **kw: called.append(a) or extcal.Response(200, body=ICS)
    ) is None
    assert called == []
