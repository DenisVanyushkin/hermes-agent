"""S6 anti-echo ingest boundary tests."""

from fam import extcal


HERMES = "https://caldav.icloud.com/1/calendars/hermes/"
TAYA = "https://caldav.icloud.com/1/calendars/taya/"


def test_both_write_urls_are_removed_before_displayname_matching(monkeypatch):
    calendars = [
        {"url": HERMES, "name": "Personal"},
        {"url": TAYA, "name": "Personal"},
        {"url": "https://caldav.icloud.com/1/calendars/real/", "name": "Personal"},
    ]
    monkeypatch.setattr(extcal, "_discover", lambda cfg, request: (calendars, []))
    monkeypatch.setattr(
        extcal,
        "fetch_changes",
        lambda *args, **kwargs: (
            [],
            "token",
            {"mode": "sync_collection", "reason": None},
        ),
    )
    result = extcal.probe(
        {
            "extcal_write_calendar": HERMES,
            "extcal_taya_calendar": TAYA,
            "extcal_read_calendars": ["Personal"],
        },
        request=lambda *args, **kwargs: None,
    )
    assert [row["url"] for row in result["calendars"]] == [
        "https://caldav.icloud.com/1/calendars/real/"
    ]
