"""Task 6: `fam tick cal-ext` -- the periodic glue that wires extcal.py's
already-landed, independently-tested layers (discover/fetch_changes/
parse_ics/expand/plan_changes/apply_changes) into one sync per run
(`cli.cmd_tick_cal_ext` / `cli._cal_ext_sync`).

Only `extcal.discover`/`extcal.fetch_changes` are monkeypatched here (the
task's own injectable seam -- "весь ввод-вывод через инъектируемый seam");
`parse_ics`/`expand`/`plan_changes`/`apply_changes` run for REAL against the
`db` fixture's tmp sqlite file, so these are integration tests of the whole
pipeline, not just cli.py's own glue in isolation. No test here ever touches
the real network.
"""
import json
import types

import pytest

from fam import cal, cli, extcal, gate
from fam import db as famdb


# ---------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------

def _ics_vevent(uid, summary, dtstart, dtend=None, location="", all_day=False,
                seq=0):
    """A minimal single-VEVENT ICS resource body, RFC 5545 basic-format
    timestamps (matching test_extcal_parse.py's own fixture convention --
    `parse_ics` is the real, unmocked parser here)."""
    if all_day:
        start_line = f"DTSTART;VALUE=DATE:{dtstart}"
        end_line = f"DTEND;VALUE=DATE:{dtend}" if dtend else None
    else:
        start_line = f"DTSTART:{dtstart}"
        end_line = f"DTEND:{dtend}" if dtend else None
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT",
              f"UID:{uid}", f"SUMMARY:{summary}", start_line]
    if end_line:
        lines.append(end_line)
    if location:
        lines.append(f"LOCATION:{location}")
    if seq:
        lines.append(f"SEQUENCE:{seq}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def _calendar(url, name, sync_token=None):
    return {"url": url, "name": name, "ctag": "c1", "sync_token": sync_token,
            "supports_sync_token": True, "components": ["VEVENT"]}


def _cfg(**over):
    base = dict(gate.CONFIG_DEFAULTS)
    base.update({
        "extcal_enabled": True,
        "extcal_username": "amina@example.com",
        "extcal_read_calendars": [],
        "extcal_write_calendar": "",
        "extcal_horizon_weeks": 8,
    })
    base.update(over)
    return base


TEST_NOW = "2037-07-15T00:00:00+00:00"


def _args(now=TEST_NOW, dry_run=False, json_out=False):
    ns = types.SimpleNamespace(now=now)
    if dry_run:
        ns.dry_run = True
    if json_out:
        ns.json = True
    return ns


def _audit_rows(conn, kind):
    rows = conn.execute(
        "SELECT payload FROM audit_log WHERE kind=? ORDER BY id", (kind,)
    ).fetchall()
    return [json.loads(r["payload"]) for r in rows]


CAL_URL = "https://caldav.icloud.com/1/calendars/home/"


# ---------------------------------------------------------------------
# invariant: gate.deliver is never called on any cal-ext path
# ---------------------------------------------------------------------

def test_gate_deliver_never_called(db, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("gate.deliver must never be called by cal-ext")
    monkeypatch.setattr(cli.gate, "deliver", _boom)
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())

    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    item = {"href": CAL_URL + "evt1.ics", "deleted": False, "etag": "e1",
            "ics": _ics_vevent("evt1@icloud.com", "Йога",
                                "20370720T130000Z", "20370720T140000Z")}

    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None:
                         ([item], None, {"mode": "initial_full", "reason": None}))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0

    # Also exercise --dry-run through the same gate.deliver tripwire.
    rc2 = cli.cmd_tick_cal_ext(_args(dry_run=True))
    assert rc2 == 0


# ---------------------------------------------------------------------
# extcal_enabled=false -> zero actions, exit 0
# ---------------------------------------------------------------------

def test_disabled_is_zero_action_noop(db, monkeypatch, capsys):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg(extcal_enabled=False))
    called = {"n": 0}
    monkeypatch.setattr(cli.extcal, "discover",
                         lambda *a, **k: called.__setitem__("n", called["n"] + 1) or [])
    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0
    assert called["n"] == 0  # never even reaches the network layer


# ---------------------------------------------------------------------
# sync error -> audit tick.error{where:'cal-ext'} + exit 1
# ---------------------------------------------------------------------

def test_total_sync_failure_audits_tick_error_and_exits_1(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    monkeypatch.setattr(cli.extcal, "discover",
                         lambda cfg, request=None: [_calendar(CAL_URL, "Calendar")])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None:
                         (None, None, {"mode": "error", "reason": "missing_credentials"}))
    calls = []
    monkeypatch.setattr(cli, "_audit_tick_error", lambda where, exc: calls.append((where, exc)))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1
    assert len(calls) == 1
    assert calls[0][0] == "cal-ext"


def test_total_sync_failure_writes_no_cal_ext_sync_audit(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    monkeypatch.setattr(cli.extcal, "discover",
                         lambda cfg, request=None: [_calendar(CAL_URL, "Calendar")])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None:
                         (None, None, {"mode": "error", "reason": "http_500"}))
    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1
    assert _audit_rows(db, "cal.ext.sync") == []


# ---------------------------------------------------------------------
# --dry-run: nothing written to DB or iCloud
# ---------------------------------------------------------------------

def test_dry_run_writes_nothing(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    item = {"href": CAL_URL + "evt1.ics", "deleted": False, "etag": "e1",
            "ics": _ics_vevent("evt1@icloud.com", "Йога",
                                "20370720T130000Z", "20370720T140000Z")}
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None:
                         ([item], None, {"mode": "initial_full", "reason": None}))

    def _boom(*a, **k):
        raise AssertionError("apply_changes must not run on --dry-run")
    monkeypatch.setattr(cli.extcal, "apply_changes", _boom)
    monkeypatch.setattr(cli.famdb, "meta_set",
                         lambda *a, **k: (_ for _ in ()).throw(
                             AssertionError("meta_set must not run on --dry-run")))

    rc = cli.cmd_tick_cal_ext(_args(dry_run=True))
    assert rc == 0
    assert db.execute("SELECT COUNT(*) AS n FROM events WHERE owner='iphone'").fetchone()["n"] == 0
    assert _audit_rows(db, "cal.ext.sync") == []


# ---------------------------------------------------------------------
# success: audit cal.ext.sync with counts AND sync_info.mode
# ---------------------------------------------------------------------

def test_success_audits_counts_and_sync_mode(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    item = {"href": CAL_URL + "evt1.ics", "deleted": False, "etag": "e1",
            "ics": _ics_vevent("evt1@icloud.com", "Йога",
                                "20370720T130000Z", "20370720T140000Z",
                                location="Invictus")}
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None:
                         ([item], None, {"mode": "initial_full", "reason": None}))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0

    rows = _audit_rows(db, "cal.ext.sync")
    assert len(rows) == 1
    payload = rows[0]
    assert payload["events_inserted"] == 1
    calendars = payload["calendars"]
    assert len(calendars) == 1
    assert calendars[0]["mode"] == "initial_full"
    assert calendars[0]["url"] == CAL_URL

    row = db.execute("SELECT * FROM events WHERE owner='iphone'").fetchone()
    assert row is not None
    assert row["external_location"] == "Invictus"


# ---------------------------------------------------------------------
# sync_token: seeded from discover() BEFORE the full pass, reused next tick
# ---------------------------------------------------------------------

def test_sync_token_seeded_before_full_pass_then_reused(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="SEEDED-TOKEN")

    fetch_calls = []

    def _fetch_first(cfg, calendar, sync_token=None, request=None):
        fetch_calls.append(sync_token)
        item = {"href": CAL_URL + "evt1.ics", "deleted": False, "etag": "e1",
                "ics": _ics_vevent("evt1@icloud.com", "Йога",
                                    "20370720T130000Z", "20370720T140000Z")}
        # First-ever sync for this calendar: no stored token yet ->
        # calendar-query is used, mode=initial_full, new_token EMPTY (the
        # live-probe finding this task's report documents).
        return ([item], None, {"mode": "initial_full", "reason": None})

    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes", _fetch_first)

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0
    assert fetch_calls == [None]  # no stored token on the first-ever tick

    stored = famdb.meta_get(db, f"extcal_sync_token:{CAL_URL}")
    # Seeded from discover()'s OWN sync_token, captured BEFORE the full
    # pass -- NOT the (empty) new_token initial_full returned.
    assert stored == "SEEDED-TOKEN"

    def _fetch_second(cfg, calendar, sync_token=None, request=None):
        fetch_calls.append(sync_token)
        return ([], "NEXT-TOKEN", {"mode": "sync_collection", "reason": None})

    monkeypatch.setattr(cli.extcal, "fetch_changes", _fetch_second)
    rc2 = cli.cmd_tick_cal_ext(_args())
    assert rc2 == 0
    assert fetch_calls[-1] == "SEEDED-TOKEN"  # the seeded token was reused

    stored2 = famdb.meta_get(db, f"extcal_sync_token:{CAL_URL}")
    assert stored2 == "NEXT-TOKEN"  # steady-state: persists its OWN new_token

    rows = _audit_rows(db, "cal.ext.sync")
    assert rows[-1]["calendars"][0]["mode"] == "sync_collection"


# ---------------------------------------------------------------------
# local_snapshot: built from owner='iphone' rows, external_location carried
# ---------------------------------------------------------------------

def test_snapshot_uses_owner_iphone_rows_with_external_location(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    # Pre-seed a previously-imported iPhone event with a stored
    # external_location -- a phone-side location EDIT must be detected by
    # comparing against THIS column, never `notes` or a resolved place name
    # (extcal.py's own module note on local_snapshot's contract).
    added = cal.add(db, "Йога", "2037-07-20T13:00:00+00:00",
                     end_utc="2037-07-20T14:00:00+00:00")
    db.execute(
        "UPDATE events SET owner='iphone', external_uid=?, external_href=?, "
        "external_location=? WHERE id=?",
        (extcal._occurrence_key("evt1@icloud.com", None), CAL_URL + "evt1.ics",
         "Old Address", added["id"]),
    )
    db.commit()

    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    item = {"href": CAL_URL + "evt1.ics", "deleted": False, "etag": "e2",
            "ics": _ics_vevent("evt1@icloud.com", "Йога",
                                "20370720T130000Z", "20370720T140000Z",
                                location="New Address")}
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None:
                         ([item], None, {"mode": "fallback_full", "reason": "http_403"}))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0

    row = db.execute("SELECT * FROM events WHERE id=?", (added["id"],)).fetchone()
    assert row["external_location"] == "New Address"

    rows = _audit_rows(db, "cal.ext.sync")
    assert rows[0]["events_updated"] == 1


# ---------------------------------------------------------------------
# road_recompute never picks owner='iphone' candidates
# ---------------------------------------------------------------------

def test_road_recompute_excludes_owner_iphone(db, monkeypatch):
    from fam import tick, places

    places.add(db, "Invictus", lat=43.2298, lon=76.8823)
    db.commit()

    now = "2026-07-20T04:30:00+00:00"
    start = "2026-07-20T06:29:00+00:00"  # NOW + 119 min -- inside the T-120 window

    # Neutralize cal.add()'s own add-time road hook (it reads the REAL
    # on-disk config via gate.load_config(), unrelated to the cfg this test
    # passes to road_recompute() below -- same pattern test_tick.py's own
    # _add_event_neutral_road helper uses) so both events start at
    # travel_min_road=None regardless of the host's live fam-config.json.
    monkeypatch.setattr(tick.road, "compute_travel_min",
                         lambda conn, event, cfg, now_utc=None: (None, "none"))
    hermes_evt = cal.add(db, "Тренировка (Гермес)", start, place="Invictus")
    iphone_evt = cal.add(db, "Йога (iPhone)", start, place="Invictus")
    db.execute("UPDATE events SET owner='iphone' WHERE id=?", (iphone_evt["id"],))
    db.commit()

    recomputed_ids = []
    monkeypatch.setattr(tick.road, "compute_travel_min",
                         lambda conn, event, cfg, now_utc=None:
                         recomputed_ids.append(event["id"]) or (5, "tomtom"))

    cfg = dict(gate.CONFIG_DEFAULTS)
    cfg.update(road_home_lat=43.2220, road_home_lon=76.8512, road_coef=1.4,
               road_speed_kmh=30, road_daily_cap=100, road_timeout_sec=10,
               road_recompute_min=[120, 60])

    touched = tick.road_recompute(db, now_utc=now, cfg=cfg)

    # The iPhone-owned event must never reach road.compute_travel_min from
    # this sweep at all -- her phone rings for it, and the shared TomTom
    # daily budget belongs to Hermes-owned trips only (Task 6 tick.py
    # edit). The Hermes-owned neighbor IS still a candidate (the guard is
    # scoped to owner, not a global road_recompute no-op).
    assert recomputed_ids == [hermes_evt["id"]]
    assert touched == 1
    iphone_row = cal.get(db, iphone_evt["id"])
    assert iphone_row.get("travel_min_road") is None
    hermes_row = cal.get(db, hermes_evt["id"])
    assert hermes_row.get("travel_min_road") == 5


# ---------------------------------------------------------------------
# partial failure: one calendar down, the other live -- no data loss
# ---------------------------------------------------------------------

def test_partial_failure_keeps_live_calendars_data(db, monkeypatch):
    url_live = "https://caldav.icloud.com/1/calendars/live/"
    url_down = "https://caldav.icloud.com/1/calendars/down/"
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    monkeypatch.setattr(cli.extcal, "discover",
                         lambda cfg, request=None: [
                             _calendar(url_live, "Calendar", sync_token="T1"),
                             _calendar(url_down, "Invictus", sync_token="T2"),
                         ])

    def _fetch(cfg, calendar, sync_token=None, request=None):
        if calendar["url"] == url_down:
            return (None, None, {"mode": "error", "reason": "no_response"})
        item = {"href": url_live + "evt1.ics", "deleted": False, "etag": "e1",
                "ics": _ics_vevent("evt-live@icloud.com", "Стоматолог",
                                    "20370721T090000Z", "20370721T100000Z")}
        return ([item], None, {"mode": "initial_full", "reason": None})

    monkeypatch.setattr(cli.extcal, "fetch_changes", _fetch)

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0  # partial failure is NOT a total failure -- exit 0

    row = db.execute("SELECT * FROM events WHERE owner='iphone'").fetchone()
    assert row is not None and row["title"] == "Стоматолог"

    rows = _audit_rows(db, "cal.ext.sync")
    modes = {c["url"]: c["mode"] for c in rows[0]["calendars"]}
    assert modes[url_live] == "initial_full"
    assert modes[url_down] == "error"

    # the down calendar's token must NOT have been touched/seeded
    assert famdb.meta_get(db, f"extcal_sync_token:{url_down}") is None


def test_partial_failure_does_not_spuriously_cancel_unrelated_synced_rows(db, monkeypatch):
    """A steady-state incremental tick where calendar B errors this round
    must not touch calendar A's already-imported rows just because they
    are not part of THIS round's (empty, erroring) batch for B, and must
    not touch calendar A's rows via a bogus disappearance sweep either."""
    url_a = "https://caldav.icloud.com/1/calendars/a/"
    url_b = "https://caldav.icloud.com/1/calendars/b/"

    existing = cal.add(db, "Уже была импортирована", "2037-07-22T08:00:00+00:00",
                        end_utc="2037-07-22T09:00:00+00:00")
    db.execute(
        "UPDATE events SET owner='iphone', external_uid=?, external_href=? "
        "WHERE id=?",
        (extcal._occurrence_key("evt-a@icloud.com", None), url_a + "evt-a.ics",
         existing["id"]),
    )
    db.commit()

    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    monkeypatch.setattr(cli.extcal, "discover",
                         lambda cfg, request=None: [
                             _calendar(url_a, "Calendar", sync_token="TA"),
                             _calendar(url_b, "Invictus", sync_token="TB"),
                         ])

    def _fetch(cfg, calendar, sync_token=None, request=None):
        if calendar["url"] == url_b:
            return (None, None, {"mode": "error", "reason": "http_500"})
        # Calendar A: nothing changed since the last sync (steady-state
        # incremental delta is empty) -- existing row must survive.
        return ([], "TA-NEXT", {"mode": "sync_collection", "reason": None})

    monkeypatch.setattr(cli.extcal, "fetch_changes", _fetch)

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0

    row = db.execute("SELECT * FROM events WHERE id=?", (existing["id"],)).fetchone()
    assert row["status"] == "active"  # NOT cancelled


# ---------------------------------------------------------------------
# extcal_read_calendars filter is honored
# ---------------------------------------------------------------------

def test_read_calendars_filter_is_honored(db, monkeypatch):
    url_allowed = "https://caldav.icloud.com/1/calendars/allowed/"
    url_blocked = "https://caldav.icloud.com/1/calendars/blocked/"
    url_write = "https://caldav.icloud.com/1/calendars/hermes-write/"

    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg(
        extcal_read_calendars=["Calendar"], extcal_write_calendar=url_write))
    monkeypatch.setattr(cli.extcal, "discover",
                         lambda cfg, request=None: [
                             _calendar(url_allowed, "Calendar", sync_token="T1"),
                             _calendar(url_blocked, "Cycle", sync_token="T2"),
                             _calendar(url_write, "Гермес", sync_token="T3"),
                         ])

    fetched_urls = []

    def _fetch(cfg, calendar, sync_token=None, request=None):
        fetched_urls.append(calendar["url"])
        return ([], None, {"mode": "initial_full", "reason": None})

    monkeypatch.setattr(cli.extcal, "fetch_changes", _fetch)

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0
    assert fetched_urls == [url_allowed]  # neither the blocked nor write-target


# ---------------------------------------------------------------------
# tick.error where= exact value, and errors do not raise out of the CLI
# ---------------------------------------------------------------------

def test_unexpected_exception_marks_tick_error_and_exits_1(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())

    def _boom(cfg, request=None):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(cli.extcal, "discover", _boom)

    calls = []
    monkeypatch.setattr(cli, "_audit_tick_error", lambda where, exc: calls.append(where))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1
    assert calls == ["cal-ext"]
