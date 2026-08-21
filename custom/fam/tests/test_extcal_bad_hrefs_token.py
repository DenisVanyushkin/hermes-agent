"""A delta entry with no calendar-data: re-fetch it, or hold the token.

Regression test for the 2026-08-20 incident: a booking Invictus created
in the iCloud calendar at 12:34 UTC had still not reached assistant.db
five hours and twenty ticks later. Its `sync-collection` delta entry
arrived with `deleted=False, ics=None`, so `_cal_ext_sync` put the href
into `bad_hrefs` and skipped it -- but nothing stopped that calendar's
sync-token from being persisted, and RFC 6578 servers never repeat a
delta once its token has been acknowledged. The change only surfaced a
day later, on the next `periodic_full`.

Two layers close it, and both are pinned here:
  1. the body is re-read with a targeted GET (`extcal.fetch_resource`),
     capped at `cli._BAD_HREF_REFETCH_LIMIT` per tick;
  2. whatever could not be re-read holds its calendar's sync-token (and
     therefore its `extcal_last_full` watermark) back, so the server is
     asked for the very same delta again on the next tick.

Conventions (fixtures, `_cfg`, `_args`) follow tests/test_extcal_tick.py
-- this is the same tick, exercised through the same seam.
"""
import json
import types

from fam import cli, extcal, gate
from fam import db as famdb


CAL_URL = "https://caldav.icloud.com/1/calendars/invictus/"
HREF = CAL_URL + "evt1.ics"
ICS = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:BADA65CC@icloud.com\r\n"
    "SUMMARY:Групповая тренировка\r\n"
    "DTSTART:20370720T050000Z\r\n"
    "DTEND:20370720T055000Z\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)

TEST_NOW = "2037-07-15T00:00:00+00:00"


def _cfg(**over):
    base = dict(gate.CONFIG_DEFAULTS)
    base.update({
        "extcal_enabled": True,
        "extcal_username": "amina@example.com",
        "extcal_read_calendars": [],
        "extcal_write_calendar": "",
        "extcal_horizon_weeks": 8,
        "extcal_fail_streak_threshold": 1,
    })
    base.update(over)
    return base


def _args(now=TEST_NOW):
    return types.SimpleNamespace(now=now)


def _calendar(sync_token="TOK0"):
    return {"url": CAL_URL, "name": "Invictus", "ctag": "c1",
            "sync_token": sync_token, "supports_sync_token": True,
            "components": ["VEVENT"]}


def _stub_sync(monkeypatch, items, token="TOKEN-2", mode="sync_collection"):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    monkeypatch.setattr(cli.extcal, "discover",
                        lambda cfg, request=None: [_calendar()])
    monkeypatch.setattr(
        cli.extcal, "fetch_changes",
        lambda cfg, calendar, sync_token=None, request=None, force_full=False:
            (items, token, {"mode": mode, "reason": None}))


def _seed_token(db, token="TOKEN-1"):
    famdb.meta_set(db, f"extcal_sync_token:{CAL_URL}", token)
    # A fresh `extcal_last_full` keeps the periodic full-resync gate shut,
    # so these tests exercise the incremental path they are about.
    famdb.meta_set(db, f"extcal_last_full:{CAL_URL}", TEST_NOW)
    db.commit()


def _stored_token(db):
    return famdb.meta_get(db, f"extcal_sync_token:{CAL_URL}")


# ---------------------------------------------------------------------
# layer 1: the body is re-read, and the change lands
# ---------------------------------------------------------------------

def test_body_is_refetched_and_event_lands(db, monkeypatch):
    _seed_token(db)
    _stub_sync(monkeypatch, [{"href": HREF, "deleted": False,
                              "etag": '"e1"', "ics": None}])
    seen = []

    def _fetch(cfg, href, request=None):
        seen.append(href)
        return ICS

    monkeypatch.setattr(cli.extcal, "fetch_resource", _fetch)

    rc = cli.cmd_tick_cal_ext(_args())

    assert seen == [HREF]
    row = db.execute("SELECT * FROM events WHERE title=?",
                     ("Групповая тренировка",)).fetchone()
    assert row is not None, "a re-fetched resource must reach events"
    assert rc == 0, "a successful re-fetch is not an error -- nothing to report"
    # Nothing was lost, so the token may advance normally.
    assert _stored_token(db) == "TOKEN-2"


# ---------------------------------------------------------------------
# layer 2: what could not be re-read holds the token back
# ---------------------------------------------------------------------

def test_unfetchable_body_holds_the_token(db, monkeypatch):
    _seed_token(db)
    _stub_sync(monkeypatch, [{"href": HREF, "deleted": False,
                              "etag": '"e1"', "ics": None}])
    monkeypatch.setattr(cli.extcal, "fetch_resource",
                        lambda cfg, href, request=None: None)

    cli.cmd_tick_cal_ext(_args())

    # The token did NOT advance -- the next tick asks for the same delta.
    assert _stored_token(db) == "TOKEN-1"


def test_unfetchable_body_does_not_advance_last_full(db, monkeypatch):
    """`extcal_last_full` rides the same gate as the sync-token
    (cli.py's `for url in result["full_mode_urls"]: if url in
    result["tokens"]`), so holding the token back must hold the
    full-resync watermark back too -- otherwise the very mechanism that
    rescued this event on 2026-08-20 would be postponed by a day."""
    famdb.meta_set(db, f"extcal_sync_token:{CAL_URL}", "TOKEN-1")
    famdb.meta_set(db, f"extcal_last_full:{CAL_URL}", "2026-08-19T19:15:48+00:00")
    db.commit()
    _stub_sync(monkeypatch, [{"href": HREF, "deleted": False,
                              "etag": '"e1"', "ics": None}],
               token=None, mode="periodic_full")
    monkeypatch.setattr(cli.extcal, "fetch_resource",
                        lambda cfg, href, request=None: None)

    cli.cmd_tick_cal_ext(_args())

    assert famdb.meta_get(db, f"extcal_last_full:{CAL_URL}") == \
        "2026-08-19T19:15:48+00:00"


def test_unfetchable_body_is_still_reported(db, monkeypatch):
    _seed_token(db)
    _stub_sync(monkeypatch, [{"href": HREF, "deleted": False,
                              "etag": '"e1"', "ics": None}])
    monkeypatch.setattr(cli.extcal, "fetch_resource",
                        lambda cfg, href, request=None: None)

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1

    rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='cal.ext.sync' ORDER BY id"
    ).fetchall()
    errs = json.loads(rows[0]["payload"])["sync_errors"]
    assert any("no calendar-data" in e for e in errs)
    # Redaction still applies: no absolute iCloud href in the audit row.
    assert not any(HREF in e for e in errs)
    assert not any("evt1.ics" in e for e in errs)


# ---------------------------------------------------------------------
# the healthy paths must be untouched
# ---------------------------------------------------------------------

def test_healthy_calendar_still_advances_its_token(db, monkeypatch):
    _seed_token(db)
    _stub_sync(monkeypatch, [{"href": HREF, "deleted": False,
                              "etag": '"e1"', "ics": ICS}])
    called = []
    monkeypatch.setattr(cli.extcal, "fetch_resource",
                        lambda cfg, href, request=None: called.append(href))

    rc = cli.cmd_tick_cal_ext(_args())

    assert rc == 0
    assert called == [], "a healthy resource has nothing to re-fetch"
    assert _stored_token(db) == "TOKEN-2"


def test_tombstone_is_not_refetched(db, monkeypatch):
    _seed_token(db)
    _stub_sync(monkeypatch, [{"href": HREF, "deleted": True,
                              "etag": None, "ics": None}])
    called = []
    monkeypatch.setattr(cli.extcal, "fetch_resource",
                        lambda cfg, href, request=None: called.append(href))

    cli.cmd_tick_cal_ext(_args())

    assert called == [], "a tombstone IS the deletion -- nothing to re-read"
    assert _stored_token(db) == "TOKEN-2"


# ---------------------------------------------------------------------
# the cap, and what it costs
# ---------------------------------------------------------------------

def test_refetch_is_capped_per_tick(db, monkeypatch):
    """A collection where every resource is unreadable must not turn each
    15-minute tick into a request storm. Whatever the cap leaves over is
    not lost: it holds the token back, so the next tick sees it again."""
    _seed_token(db)
    items = [{"href": f"{CAL_URL}evt{i}.ics", "deleted": False,
              "etag": '"e"', "ics": None} for i in range(50)]
    _stub_sync(monkeypatch, items)
    called = []
    monkeypatch.setattr(cli.extcal, "fetch_resource",
                        lambda cfg, href, request=None: called.append(href))

    cli.cmd_tick_cal_ext(_args())

    assert len(called) == cli._BAD_HREF_REFETCH_LIMIT
    assert _stored_token(db) == "TOKEN-1"

    # "we could not read this resource" and "this tick ran out of its
    # re-fetch budget" hold the token back for the same reason but mean
    # very different things to whoever reads the audit row -- only the
    # first is a reason to go look at the server.
    errs = json.loads(db.execute(
        "SELECT payload FROM audit_log WHERE kind='cal.ext.sync' ORDER BY id"
    ).fetchone()["payload"])["sync_errors"]
    assert any("re-fetch failed too" in e for e in errs)
    assert any("cap of 20 reached" in e for e in errs)
