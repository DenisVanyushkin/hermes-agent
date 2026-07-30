"""Task 0 (Steps 1-2 only): `extcal.probe` on a fake CalDAV seam.

Step 3 (live run against her real iCloud calendar) is explicitly OUT of
scope here -- there is no app-specific password yet. Every test below
drives probe() through the injectable `request` callable; none touches
the network.
"""
import json

from fam import cli, extcal


def _cfg(**overrides):
    cfg = {
        "extcal_username": "amina@icloud.com",
        "extcal_read_calendars": [],
        "extcal_write_calendar": "",
        "extcal_horizon_weeks": 8,
        "extcal_stale_hours": 6,
    }
    cfg.update(overrides)
    return cfg


_TIMED_WITH_VALARM = (
    "BEGIN:VEVENT\r\nUID:evt-timed@example.com\r\nSUMMARY:Yoga\r\n"
    "DTSTART;TZID=Asia/Almaty:20260728T180000\r\nDTEND;TZID=Asia/Almaty:20260728T190000\r\n"
    "BEGIN:VALARM\r\nACTION:DISPLAY\r\nEND:VALARM\r\nEND:VEVENT\r\n"
)
_ALL_DAY = (
    "BEGIN:VEVENT\r\nUID:evt-allday@example.com\r\nSUMMARY:Birthday\r\n"
    "DTSTART;VALUE=DATE:20260801\r\nDTEND;VALUE=DATE:20260802\r\nEND:VEVENT\r\n"
)
_RECURRING = (
    "BEGIN:VEVENT\r\nUID:evt-recur@example.com\r\nSUMMARY:Gym\r\n"
    "DTSTART;TZID=Asia/Almaty:20260729T070000\r\nRRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR\r\n"
    "END:VEVENT\r\n"
)
_HERMES_ECHO = (
    "BEGIN:VEVENT\r\nUID:fam-42@hermes-home\r\nSUMMARY:Should never be counted\r\n"
    "DTSTART;TZID=Asia/Almaty:20260730T100000\r\nEND:VEVENT\r\n"
)


def _multistatus(*ics_blocks, extra_response=""):
    responses = "".join(
        f"<d:response><d:href>/123/cal/personal/e{i}.ics</d:href>"
        f"<d:propstat><d:prop><d:getetag>\"e{i}\"</d:getetag>"
        f"<c:calendar-data>{block}</c:calendar-data>"
        f"</d:prop></d:propstat></d:response>"
        for i, block in enumerate(ics_blocks)
    )
    return (
        '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" '
        'xmlns:c="urn:ietf:params:xml:ns:caldav">'
        f"{responses}{extra_response}</d:multistatus>"
    )


def _collections_xml(name="Personal", href="/123/cal/personal/", sync_token="tok-1"):
    token_xml = f"<d:sync-token>{sync_token}</d:sync-token>" if sync_token else ""
    return (
        '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" '
        'xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:cs="http://calendarserver.org/ns/">'
        "<d:response><d:href>/123/cal/</d:href><d:propstat><d:prop>"
        "<d:displayname>home</d:displayname><d:resourcetype><d:collection/></d:resourcetype>"
        "</d:prop></d:propstat></d:response>"
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>"
        f"<d:displayname>{name}</d:displayname>"
        "<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>"
        '<c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>'
        f"<cs:getctag>ctag-xyz</cs:getctag>{token_xml}"
        "</d:prop></d:propstat></d:response></d:multistatus>"
    )


def _fake_server(collections_xml, calendar_ics_by_href):
    """A fake `request` seam wiring together the full discover() walk
    (well-known -> principal -> home-set -> collections) plus one
    calendar-query REPORT per discovered calendar href, returning the
    given canned XML bodies."""

    def fake(method, url, headers=None, body=None, timeout=20):
        if url == extcal.WELL_KNOWN_URL:
            return extcal.Response(301, b"", {"Location": "https://p1-caldav.icloud.com/"})
        if url == "https://p1-caldav.icloud.com/" and "current-user-principal" in (body or ""):
            xml = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
                   '<d:response><d:href>/123/principal/</d:href>'
                   '<d:propstat><d:prop><d:current-user-principal>'
                   '<d:href>/123/principal/</d:href></d:current-user-principal>'
                   '</d:prop></d:propstat></d:response></d:multistatus>')
            return extcal.Response(207, xml.encode())
        if url == "https://p1-caldav.icloud.com/123/principal/":
            xml = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" '
                   'xmlns:c="urn:ietf:params:xml:ns:caldav">'
                   '<d:response><d:href>/123/principal/</d:href>'
                   '<d:propstat><d:prop><c:calendar-home-set>'
                   '<d:href>/123/cal/</d:href></c:calendar-home-set>'
                   '</d:prop></d:propstat></d:response></d:multistatus>')
            return extcal.Response(207, xml.encode())
        if url == "https://p1-caldav.icloud.com/123/cal/":
            return extcal.Response(207, collections_xml.encode())
        for href, ics_xml in calendar_ics_by_href.items():
            if url == f"https://p1-caldav.icloud.com{href}":
                return extcal.Response(207, ics_xml.encode())
        return extcal.Response(404, b"")

    return fake


# ---------------------------------------------------------------------
# missing credentials -> error, no exception, no network
# ---------------------------------------------------------------------

def test_probe_missing_password_records_error_not_exception(monkeypatch):
    monkeypatch.delenv("ICLOUD_APP_PASSWORD", raising=False)

    def boom(method, url, headers=None, body=None, timeout=20):
        raise AssertionError("must not touch the network without a password")

    result = extcal.probe(_cfg(), request=boom)
    assert result["calendars"] == []
    assert result["counts"] == extcal._empty_counts()
    assert result["errors"] != []
    assert any("password" in e.lower() or "credential" in e.lower() for e in result["errors"])


# ---------------------------------------------------------------------
# full happy path: counts + anti-echo
# ---------------------------------------------------------------------

def test_probe_counts_and_calendar_list(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    collections_xml = _collections_xml()
    events_xml = _multistatus(_TIMED_WITH_VALARM, _ALL_DAY, _RECURRING, _HERMES_ECHO)
    fake = _fake_server(collections_xml, {"/123/cal/personal/": events_xml})

    result = extcal.probe(_cfg(), request=fake)

    assert result["errors"] == []
    assert len(result["calendars"]) == 1
    cal = result["calendars"][0]
    assert cal["name"] == "Personal"
    assert cal["ctag"] == "ctag-xyz"
    assert cal["supports_sync_token"] is True

    counts = result["counts"]
    # the fam-*@hermes-home echo must never be counted (anti-echo belt 2)
    assert counts["total"] == 3
    assert counts["timed"] == 2       # yoga + gym
    assert counts["all_day"] == 1     # birthday
    assert counts["with_rrule"] == 1  # gym
    assert counts["with_valarm"] == 1  # yoga


def test_probe_skips_write_target_calendar_by_url(monkeypatch):
    # Anti-echo belt 1: the write-target collection is never read back as
    # if it were "her" data, even if it happens to contain events.
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    collections_xml = _collections_xml(name="Hermes", href="/123/cal/hermes/")
    events_xml = _multistatus(_TIMED_WITH_VALARM)
    fake = _fake_server(collections_xml, {"/123/cal/hermes/": events_xml})

    cfg = _cfg(extcal_write_calendar="https://p1-caldav.icloud.com/123/cal/hermes/")
    result = extcal.probe(cfg, request=fake)

    assert result["calendars"] == []
    assert result["counts"]["total"] == 0
    assert result["errors"] == []


# ---------------------------------------------------------------------
# I2: a non-empty extcal_read_calendars that matches NOTHING must be
# distinguishable from "she genuinely has zero calendars" -- both used
# to come back as an identical empty/silent result.
# ---------------------------------------------------------------------

def test_probe_read_filter_matching_nothing_reports_error(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    collections_xml = _collections_xml()  # one real calendar: "Personal"
    events_xml = _multistatus(_TIMED_WITH_VALARM)
    fake = _fake_server(collections_xml, {"/123/cal/personal/": events_xml})

    # Typo'd/mismatched filter: discover() DID find a calendar, but it
    # matches neither by url nor by (case-sensitive) name.
    cfg = _cfg(extcal_read_calendars=["personal"])  # actual name is "Personal"
    result = extcal.probe(cfg, request=fake)

    assert result["calendars"] == []
    assert result["counts"] == extcal._empty_counts()
    assert result["errors"] != []
    assert any("extcal_read_calendars" in e and "0" in e for e in result["errors"])


def test_probe_read_filter_matching_something_is_silent(monkeypatch):
    # Sanity companion to the above: when the filter DOES match, no
    # spurious "matched 0 of N" error should appear.
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    collections_xml = _collections_xml()
    events_xml = _multistatus(_TIMED_WITH_VALARM)
    fake = _fake_server(collections_xml, {"/123/cal/personal/": events_xml})

    cfg = _cfg(extcal_read_calendars=["Personal"])
    result = extcal.probe(cfg, request=fake)

    assert len(result["calendars"]) == 1
    assert result["errors"] == []


def test_probe_empty_read_filter_with_zero_real_calendars_has_no_spurious_error(monkeypatch):
    # The write-target-only scenario (belt 1) must NOT trip the new I2
    # error -- that's a different, expected situation, not a filter typo.
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    collections_xml = _collections_xml(name="Hermes", href="/123/cal/hermes/")
    fake = _fake_server(collections_xml, {})
    cfg = _cfg(extcal_write_calendar="https://p1-caldav.icloud.com/123/cal/hermes/")

    result = extcal.probe(cfg, request=fake)
    assert result["calendars"] == []
    assert result["errors"] == []


# ---------------------------------------------------------------------
# failure modes: never raise
# ---------------------------------------------------------------------

def test_probe_network_error_records_error_not_exception(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def always_none(method, url, headers=None, body=None, timeout=20):
        return None  # total transport failure at every step

    result = extcal.probe(_cfg(), request=always_none)
    assert result["calendars"] == []
    assert result["counts"] == extcal._empty_counts()
    assert result["errors"] != []


def test_probe_5xx_records_error_not_exception(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def always_500(method, url, headers=None, body=None, timeout=20):
        return extcal.Response(500, b"internal error")

    result = extcal.probe(_cfg(), request=always_500)
    assert result["calendars"] == []
    assert result["errors"] != []


def test_probe_malformed_xml_records_error_not_exception(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")

    def fake(method, url, headers=None, body=None, timeout=20):
        if url == extcal.WELL_KNOWN_URL:
            return extcal.Response(207, b"<this is not < valid xml")
        raise AssertionError("unreachable")

    result = extcal.probe(_cfg(), request=fake)
    assert result["calendars"] == []
    assert result["errors"] != []


def test_probe_dtd_bearing_response_records_error_not_exception(monkeypatch):
    # I3: a DOCTYPE/ENTITY-bearing response must degrade the same way a
    # malformed one does -- error in `errors`, no exception, no attempt
    # to actually expand any entity.
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    bomb = (b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x "y">]>'
            b'<d:multistatus xmlns:d="DAV:"/>')

    def fake(method, url, headers=None, body=None, timeout=20):
        if url == extcal.WELL_KNOWN_URL:
            return extcal.Response(207, bomb)
        raise AssertionError("unreachable")

    result = extcal.probe(_cfg(), request=fake)
    assert result["calendars"] == []
    assert result["errors"] != []


def test_probe_per_calendar_fetch_failure_is_recorded_but_does_not_abort(monkeypatch):
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    collections_xml = _collections_xml()

    def fake(method, url, headers=None, body=None, timeout=20):
        if url == extcal.WELL_KNOWN_URL:
            return extcal.Response(301, b"", {"Location": "https://p1-caldav.icloud.com/"})
        if url == "https://p1-caldav.icloud.com/" and "current-user-principal" in (body or ""):
            xml = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
                   '<d:response><d:href>/123/principal/</d:href>'
                   '<d:propstat><d:prop><d:current-user-principal>'
                   '<d:href>/123/principal/</d:href></d:current-user-principal>'
                   '</d:prop></d:propstat></d:response></d:multistatus>')
            return extcal.Response(207, xml.encode())
        if url == "https://p1-caldav.icloud.com/123/principal/":
            xml = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" '
                   'xmlns:c="urn:ietf:params:xml:ns:caldav">'
                   '<d:response><d:href>/123/principal/</d:href>'
                   '<d:propstat><d:prop><c:calendar-home-set>'
                   '<d:href>/123/cal/</d:href></c:calendar-home-set>'
                   '</d:prop></d:propstat></d:response></d:multistatus>')
            return extcal.Response(207, xml.encode())
        if url == "https://p1-caldav.icloud.com/123/cal/":
            return extcal.Response(207, collections_xml.encode())
        if url == "https://p1-caldav.icloud.com/123/cal/personal/":
            return extcal.Response(500, b"boom")  # calendar-query fails
        return extcal.Response(404, b"")

    result = extcal.probe(_cfg(), request=fake)
    assert len(result["calendars"]) == 1  # discovery still succeeded
    assert result["counts"]["total"] == 0
    assert any("fetch failed" in e for e in result["errors"])


# ---------------------------------------------------------------------
# the password never appears anywhere in probe()'s output
# ---------------------------------------------------------------------

def test_probe_password_never_leaks_into_output(monkeypatch):
    secret = "correct-horse-battery-staple-999"
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", secret)
    collections_xml = _collections_xml()
    events_xml = _multistatus(_TIMED_WITH_VALARM, _ALL_DAY)
    fake = _fake_server(collections_xml, {"/123/cal/personal/": events_xml})

    result = extcal.probe(_cfg(), request=fake)
    dumped = json.dumps(result, ensure_ascii=False)
    assert secret not in dumped
    assert secret not in repr(result)


def test_probe_summary_examples_are_uid_and_flags_only(monkeypatch):
    # The brief is explicit that any per-event detail probe() surfaces
    # must be limited to UID + boolean flags -- never SUMMARY/LOCATION/
    # free text. probe()'s declared return shape (calendars/counts/
    # errors) never includes per-event content at all, which trivially
    # satisfies this; this test pins that SUMMARY text specifically never
    # leaks into the result even though it's present in the raw ICS feed.
    monkeypatch.setenv("ICLOUD_APP_PASSWORD", "pw")
    collections_xml = _collections_xml()
    events_xml = _multistatus(_TIMED_WITH_VALARM)
    fake = _fake_server(collections_xml, {"/123/cal/personal/": events_xml})

    result = extcal.probe(_cfg(), request=fake)
    dumped = json.dumps(result, ensure_ascii=False)
    assert "Yoga" not in dumped


# ---------------------------------------------------------------------
# CLI wiring: `fam cal-ext probe [--json]`
# ---------------------------------------------------------------------

def test_cli_cal_ext_probe_json(monkeypatch, capsys):
    monkeypatch.setattr(
        extcal, "probe",
        lambda cfg, request=None: {
            "calendars": [{"url": "https://x/", "name": "Personal",
                           "ctag": "c1", "supports_sync_token": True}],
            "counts": {"timed": 1, "all_day": 0, "with_rrule": 0,
                       "with_valarm": 0, "total": 1},
            "errors": [],
        })
    rc = cli.main(["cal-ext", "probe", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["counts"]["total"] == 1
    assert out["calendars"][0]["name"] == "Personal"


def test_cli_cal_ext_probe_text_mode_reports_errors(monkeypatch, capsys):
    monkeypatch.setattr(
        extcal, "probe",
        lambda cfg, request=None: {
            "calendars": [],
            "counts": extcal._empty_counts(),
            "errors": ["ICLOUD_APP_PASSWORD not set in environment"],
        })
    rc = cli.main(["cal-ext", "probe"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ICLOUD_APP_PASSWORD" in out
