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

Fix-round 1 (Opus review): three Critical findings (C1 empty-eligible
silently reads as "she deleted everything", C2 a sync-token advancing past
a delta this tick failed to fully apply, C3 an unparsed/malformed event
cancelled instead of skipped) plus I1 (partial failure invisible), I4
(dropping a calendar from config silently cancels its imports), I5 (audit
spam on a healthy no-op tick), and minors m1/m3/m4 all get a dedicated test
below, in addition to the pre-existing coverage (some of which had to be
UPDATED for I1's stricter "any error -> exit 1" policy -- see the comments
at each changed test).
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
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
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
# (fix-round I1: this is now true for ANY error, not just a total wipeout
# -- see test_partial_failure_* below for the mixed case)
# ---------------------------------------------------------------------

def test_total_sync_failure_audits_tick_error_and_exits_1(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    monkeypatch.setattr(cli.extcal, "discover",
                         lambda cfg, request=None: [_calendar(CAL_URL, "Calendar")])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         (None, None, {"mode": "error", "reason": "missing_credentials"}))
    calls = []
    monkeypatch.setattr(cli, "_audit_tick_error", lambda where, exc: calls.append((where, exc)))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1
    assert len(calls) == 1
    assert calls[0][0] == "cal-ext"


def test_total_sync_failure_still_audits_cal_ext_sync_with_calendar_error(db, monkeypatch):
    """UPDATED for fix-round finding I5: the first cut of this test
    asserted NO cal.ext.sync audit on a total failure. That was wrong in
    the other direction from the spam problem I5 actually flags -- a
    failing calendar's mode/reason is exactly the kind of thing worth
    telling the audit log about, and I5's "don't spam" rule only ever
    meant "don't log an uneventful, unchanged, healthy tick". `has_error`
    is one of the three independent triggers for writing this audit,
    alongside nonzero counts and a calendar's mode changing since last
    time."""
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    monkeypatch.setattr(cli.extcal, "discover",
                         lambda cfg, request=None: [_calendar(CAL_URL, "Calendar")])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         (None, None, {"mode": "error", "reason": "http_500"}))
    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1

    rows = _audit_rows(db, "cal.ext.sync")
    assert len(rows) == 1
    assert rows[0]["calendars"][0]["mode"] == "error"
    assert rows[0]["calendars"][0]["reason"] == "http_500"


# ---------------------------------------------------------------------
# --dry-run: nothing written to DB or iCloud, and the printed changeset is
# redacted (fix-round finding m3: UID/id/counts only, never her titles or
# raw LOCATION text)
# ---------------------------------------------------------------------

def test_dry_run_writes_nothing(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    item = {"href": CAL_URL + "evt1.ics", "deleted": False, "etag": "e1",
            "ics": _ics_vevent("evt1@icloud.com", "Йога",
                                "20370720T130000Z", "20370720T140000Z")}
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
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


def test_dry_run_changeset_is_redacted(db, monkeypatch, capsys):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    item = {"href": CAL_URL + "evt1.ics", "deleted": False, "etag": "e1",
            "ics": _ics_vevent("evt1@icloud.com", "Секретный визит к врачу",
                                "20370720T130000Z", "20370720T140000Z",
                                location="Тайная клиника, ул. Скрытая 1")}
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         ([item], None, {"mode": "initial_full", "reason": None}))

    rc = cli.cmd_tick_cal_ext(_args(dry_run=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Секретный визит" not in out
    assert "Тайная клиника" not in out
    assert "Скрытая" not in out

    printed = json.loads(out)
    cs = printed["changeset"]
    assert cs["counts"]["events_insert"] == 1
    assert len(cs["events"]["insert"]) == 1
    assert set(cs["events"]["insert"][0].keys()) == {"external_uid"}


def test_dry_run_sync_errors_redact_hrefs(db, monkeypatch, capsys):
    """Fix-round 2, minor #4: `sync_errors` messages embed a full CalDAV
    RESOURCE href (the specific event's own path under her account, e.g.
    `.../home/broken.ics`) -- `--dry-run`'s printed output must not leak
    THAT in plain text, same redaction principle as the changeset itself
    (m3). This is narrower than "no URL anywhere in the output" -- the
    `calendars` list legitimately still shows each CALENDAR's own
    collection URL (that's not a specific resource path, and is already
    shown in every `audit cal.ext.sync` entry regardless of dry-run)."""
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    resource_href = CAL_URL + "broken.ics"
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    item = {"href": resource_href, "deleted": False, "etag": "e1",
            "ics": "this is not ICS at all\r\n"}
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         ([item], None, {"mode": "initial_full", "reason": None}))

    rc = cli.cmd_tick_cal_ext(_args(dry_run=True))
    assert rc == 0  # dry-run always exits 0 (m4), even with sync_errors present
    out = capsys.readouterr().out
    assert "broken.ics" not in out
    assert resource_href not in out

    printed = json.loads(out)
    assert printed["sync_errors"]
    assert any("<href>" in e for e in printed["sync_errors"])
    assert not any("broken.ics" in e for e in printed["sync_errors"])


# ---------------------------------------------------------------------
# Final review, blocker 3 (Important, privacy): the SAME redaction must
# apply on the REAL (non-dry-run) path too -- the boевая `cal.ext.sync`
# audit row, and the `tick.error` message built from the identical
# `sync_errors` list (which is what `maint.problem_summary` copies
# VERBATIM into the nightly message to Denis for `kind='tick.error'`).
# ---------------------------------------------------------------------

def test_prod_cal_ext_sync_sync_errors_redact_href(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    resource_href = CAL_URL + "broken.ics"
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    item = {"href": resource_href, "deleted": False, "etag": "e1",
            "ics": "this is not ICS at all\r\n"}
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         ([item], None, {"mode": "initial_full", "reason": None}))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1  # a real (non-dry-run) run with sync_errors present fails the tick

    sync_rows = _audit_rows(db, "cal.ext.sync")
    assert len(sync_rows) == 1
    assert sync_rows[0]["sync_errors"]
    assert any("<href>" in e for e in sync_rows[0]["sync_errors"])
    assert not any(resource_href in e for e in sync_rows[0]["sync_errors"])
    assert not any("broken.ics" in e for e in sync_rows[0]["sync_errors"])

    tick_error_rows = _audit_rows(db, "tick.error")
    assert len(tick_error_rows) == 1
    assert resource_href not in tick_error_rows[0]["error"]
    assert "broken.ics" not in tick_error_rows[0]["error"]


def test_prod_cal_ext_sync_sync_errors_redact_raw_rrule(db, monkeypatch):
    """A master VEVENT with an RRULE `dateutil.rrulestr` can't parse
    surfaces the raw RRULE VALUE in `expand()`'s own error text
    (extcal._expand_master: `f"RRULE {master['rrule']!r} for uid=..."`)
    -- that raw value must not reach the production `cal.ext.sync` audit
    row or the `tick.error` message either."""
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    bad_rrule = "NOT_A_VALID_RRULE_AT_ALL"
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
           "UID:series1@icloud.com\r\nSUMMARY:Тренировка\r\n"
           "DTSTART:20370720T130000Z\r\nDTEND:20370720T140000Z\r\n"
           f"RRULE:{bad_rrule}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    item = {"href": CAL_URL + "series1.ics", "deleted": False, "etag": "e1",
            "ics": ics}
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         ([item], None, {"mode": "initial_full", "reason": None}))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1

    sync_rows = _audit_rows(db, "cal.ext.sync")
    assert len(sync_rows) == 1
    assert sync_rows[0]["sync_errors"]
    assert any("RRULE <redacted>" in e for e in sync_rows[0]["sync_errors"])
    assert not any(bad_rrule in e for e in sync_rows[0]["sync_errors"])

    tick_error_rows = _audit_rows(db, "tick.error")
    assert len(tick_error_rows) == 1
    assert bad_rrule not in tick_error_rows[0]["error"]


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
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
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

    def _fetch_first(cfg, calendar, sync_token=None, request=None, force_full=False):
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

    def _fetch_second(cfg, calendar, sync_token=None, request=None, force_full=False):
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
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
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
                         lambda conn, event, cfg, now_utc=None, **kw: (None, "none"))
    hermes_evt = cal.add(db, "Тренировка (Гермес)", start, place="Invictus")
    iphone_evt = cal.add(db, "Йога (iPhone)", start, place="Invictus")
    db.execute("UPDATE events SET owner='iphone' WHERE id=?", (iphone_evt["id"],))
    db.commit()

    recomputed_ids = []
    monkeypatch.setattr(tick.road, "compute_travel_min",
                         lambda conn, event, cfg, now_utc=None, **kw:
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
# partial failure: one calendar down, the other live -- no data loss, but
# (fix-round I1) the tick now DOES flag it: exit 1 + tick.error, not a
# silent exit 0. The live calendar's own progress (data applied, its OWN
# token advanced) is unaffected -- only the exit code/alerting changed.
# ---------------------------------------------------------------------

def test_partial_failure_applies_live_calendar_but_flags_the_tick(db, monkeypatch):
    """UPDATED for fix-round finding I1: the first cut of this test
    asserted exit 0 on a partial failure -- that was exactly the
    "частичный отказ никого не будит" gap the review flagged. A calendar
    stuck on a permanently-403ing app-password must eventually wake
    someone up, the same way cmd_tick_offsite already treats ANY non-empty
    errors list as a failing run even when SOME backups succeeded."""
    url_live = "https://caldav.icloud.com/1/calendars/live/"
    url_down = "https://caldav.icloud.com/1/calendars/down/"
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    monkeypatch.setattr(cli.extcal, "discover",
                         lambda cfg, request=None: [
                             _calendar(url_live, "Calendar", sync_token="T1"),
                             _calendar(url_down, "Invictus", sync_token="T2"),
                         ])

    def _fetch(cfg, calendar, sync_token=None, request=None, force_full=False):
        if calendar["url"] == url_down:
            return (None, None, {"mode": "error", "reason": "no_response"})
        item = {"href": url_live + "evt1.ics", "deleted": False, "etag": "e1",
                "ics": _ics_vevent("evt-live@icloud.com", "Стоматолог",
                                    "20370721T090000Z", "20370721T100000Z")}
        return ([item], None, {"mode": "initial_full", "reason": None})

    monkeypatch.setattr(cli.extcal, "fetch_changes", _fetch)

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1  # fix-round I1: partial failure now DOES flag the tick

    # ... but the live calendar's data still landed.
    row = db.execute("SELECT * FROM events WHERE owner='iphone'").fetchone()
    assert row is not None and row["title"] == "Стоматолог"

    rows = _audit_rows(db, "cal.ext.sync")
    modes = {c["url"]: c["mode"] for c in rows[0]["calendars"]}
    assert modes[url_live] == "initial_full"
    assert modes[url_down] == "error"

    # the down calendar's token must NOT have been touched/seeded ...
    assert famdb.meta_get(db, f"extcal_sync_token:{url_down}") is None
    # ... but the LIVE calendar's own progress is not held hostage by the
    # unrelated calendar being down (fix-round C2 is scoped to APPLY
    # errors specifically, not to a sibling calendar's fetch failure).
    assert famdb.meta_get(db, f"extcal_sync_token:{url_live}") == "T1"


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

    def _fetch(cfg, calendar, sync_token=None, request=None, force_full=False):
        if calendar["url"] == url_b:
            return (None, None, {"mode": "error", "reason": "http_500"})
        # Calendar A: nothing changed since the last sync (steady-state
        # incremental delta is empty) -- existing row must survive.
        return ([], "TA-NEXT", {"mode": "sync_collection", "reason": None})

    monkeypatch.setattr(cli.extcal, "fetch_changes", _fetch)

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1  # fix-round I1: calendar B's failure still flags the tick

    row = db.execute("SELECT * FROM events WHERE id=?", (existing["id"],)).fetchone()
    assert row["status"] == "active"  # NOT cancelled

    # Calendar A itself was clean (no apply errors anywhere this tick) --
    # its own incremental progress still advances.
    assert famdb.meta_get(db, f"extcal_sync_token:{url_a}") == "TA-NEXT"


# ---------------------------------------------------------------------
# C2: a calendar's token never advances past a round with an apply error
# -- ANYWHERE this tick, not just for the affected calendar (the simpler,
# safer blanket the review explicitly asked for).
# ---------------------------------------------------------------------

def test_apply_error_blocks_every_calendars_token_this_tick(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    item = {"href": CAL_URL + "evt1.ics", "deleted": False, "etag": "e1",
            "ics": _ics_vevent("evt1@icloud.com", "Йога",
                                "20370720T130000Z", "20370720T140000Z")}
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         ([item], "NEXT-TOKEN", {"mode": "sync_collection", "reason": None}))

    # Simulate apply_changes reporting a real per-row failure (e.g. "database
    # is locked", per extcal._apply_one's own docstring) -- everything else
    # about the plumbing (plan_changes/apply_changes contract) is exercised
    # for real elsewhere; here only the CLI's own reaction to a nonzero
    # counts["errors"] is under test, so apply_changes itself is stubbed.
    monkeypatch.setattr(cli.extcal, "apply_changes", lambda conn, changeset, cfg:
                         {"events_inserted": 0, "events_updated": 0,
                          "events_cancelled": 0, "plans_inserted": 0,
                          "plans_updated": 0, "plans_dropped": 0, "collisions": 0,
                          "errors": [{"branch": "events", "action": "insert",
                                      "id": None, "external_uid": "u1:x",
                                      "error": "OperationalError: database is locked"}]})

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1
    assert famdb.meta_get(db, f"extcal_sync_token:{CAL_URL}") is None
    assert famdb.meta_get(db, "extcal_last_ok") is None  # not a full success either


# ---------------------------------------------------------------------
# C1(a): extcal_read_calendars configured but nothing eligible survived
# -- treated as a real error, not "nothing to do".
# ---------------------------------------------------------------------

def test_empty_eligible_with_configured_filter_is_an_error(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg(
        extcal_read_calendars=["Cycle"]))
    # discover() itself degrades to [] on ANY failure (missing
    # credentials, timeout, 5xx, malformed XML) -- indistinguishable, at
    # this layer, from "her calendar really was renamed"; either way, a
    # configured filter matching literally nothing is suspicious.
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [])
    fetch_called = []
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda *a, **k: fetch_called.append(1) or ([], None, {"mode": "error"}))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1
    assert fetch_called == []  # nothing to fetch -- the error is purely "0 eligible"

    rows = _audit_rows(db, "cal.ext.sync")
    assert len(rows) == 1
    assert any("extcal_read_calendars" in e for e in rows[0]["sync_errors"])


def test_empty_eligible_without_a_configured_filter_is_not_an_error(db, monkeypatch):
    """The narrower counterpart: if NO filter is configured at all (read
    everything), a genuinely-empty discover() result is treated as
    "nothing to do yet" (e.g. before the app-specific password is even
    configured) -- not flagged, matching the pre-fix-round no-op
    behavior for this specific sub-case, which the review did not ask to
    change."""
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg(
        extcal_read_calendars=[]))
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [])

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0
    assert _audit_rows(db, "cal.ext.sync") == []


# ---------------------------------------------------------------------
# C1(b) / I4: a row whose calendar is no longer eligible this round
# (dropped from extcal_read_calendars, or its calendar wasn't rediscovered
# at all) is EXCLUDED from the snapshot, not defaulted in -- it survives
# untouched rather than being read as "she deleted it".
# ---------------------------------------------------------------------

def test_row_from_a_now_ineligible_calendar_is_not_cancelled(db, monkeypatch):
    url_dropped = "https://caldav.icloud.com/1/calendars/dropped-from-config/"
    existing = cal.add(db, "Импортировано раньше", "2037-07-20T08:00:00+00:00",
                        end_utc="2037-07-20T09:00:00+00:00")
    db.execute(
        "UPDATE events SET owner='iphone', external_uid=?, external_href=? "
        "WHERE id=?",
        (extcal._occurrence_key("evt-old@icloud.com", None), url_dropped + "evt-old.ics",
         existing["id"]),
    )
    db.commit()

    # This round's config no longer includes that calendar at all -- e.g.
    # Denis narrowed extcal_read_calendars, or discover() simply didn't
    # return it this time. `eligible` below is built from a discover()
    # result that omits it entirely.
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [])

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0

    row = db.execute("SELECT * FROM events WHERE id=?", (existing["id"],)).fetchone()
    assert row["status"] == "active"  # NOT cancelled just because we can't see its calendar


# ---------------------------------------------------------------------
# C3 / N1 / N2: an unparsed/malformed event is excluded from the
# disappearance sweep BY HREF, not cancelled outright and not by taking
# its whole calendar down with it.
# ---------------------------------------------------------------------

def test_broken_ics_excludes_its_href_from_disappearance_instead_of_cancelling(db, monkeypatch):
    existing = cal.add(db, "Уже была", "2037-07-20T08:00:00+00:00",
                        end_utc="2037-07-20T09:00:00+00:00")
    db.execute(
        "UPDATE events SET owner='iphone', external_uid=?, external_href=? "
        "WHERE id=?",
        (extcal._occurrence_key("evt-broken@icloud.com", None), CAL_URL + "evt-broken.ics",
         existing["id"]),
    )
    db.commit()

    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    # A genuinely-full listing (fallback_full) that returns this href
    # with GARBAGE ics text -- parse_ics() degrades to [] on this per its
    # own "never raises" contract, NOT a real "the event is gone" signal.
    item = {"href": CAL_URL + "evt-broken.ics", "deleted": False, "etag": "e9",
            "ics": "this is not ICS at all\r\nno BEGIN:VEVENT anywhere\r\n"}
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         ([item], None, {"mode": "fallback_full", "reason": "http_403"}))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1  # the parse problem itself is a real error (I1)

    row = db.execute("SELECT * FROM events WHERE id=?", (existing["id"],)).fetchone()
    assert row["status"] == "active"  # NOT cancelled -- excluded from the sweep instead

    rows = _audit_rows(db, "cal.ext.sync")
    assert any("VEVENT block" in e for e in rows[0]["sync_errors"])


def test_component_count_mismatch_excludes_only_that_href_not_the_whole_calendar(db, monkeypatch):
    """Fix-round 2, finding N1: the REAL silent-loss scenario is a single
    ICS RESOURCE holding a recurring master together with its
    RECURRENCE-ID override, where the override's own DTSTART is broken/
    missing -- parse_ics/_finalize_component drop that ONE component
    while the master survives, with no error of its own. This is only
    observable from the OUTSIDE as a component-count mismatch against the
    resource's own raw BEGIN:VEVENT blocks. A pre-existing local row tied
    to the SAME href must be excluded from disappearance (N1), but a
    HEALTHY event from a DIFFERENT resource in the SAME calendar must
    still be inserted -- the granularity is per-HREF, not per-calendar
    (N2)."""
    broken_href = CAL_URL + "recurring-with-broken-override.ics"
    existing = cal.add(db, "Перенос был здесь", "2037-07-27T13:00:00+00:00",
                        end_utc="2037-07-27T14:00:00+00:00")
    db.execute(
        "UPDATE events SET owner='iphone', external_uid=?, external_href=? "
        "WHERE id=?",
        (extcal._occurrence_key("recur@icloud.com", "2037-07-27T13:00:00+00:00"),
         broken_href, existing["id"]),
    )
    db.commit()

    # ONE resource, TWO BEGIN:VEVENT blocks: a healthy master (no RRULE,
    # for simplicity -- what matters here is the COUNT, not recurrence)
    # and an override with NO DTSTART at all -- _finalize_component drops
    # the second one silently; parse_ics returns only 1 component for 2
    # raw blocks.
    broken_resource_ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        "BEGIN:VEVENT\r\nUID:recur@icloud.com\r\nSUMMARY:Мастер\r\n"
        "DTSTART:20370720T130000Z\r\nDTEND:20370720T140000Z\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:recur@icloud.com\r\n"
        "RECURRENCE-ID:20370727T130000Z\r\nSUMMARY:Сломанный перенос\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    healthy_item = {"href": CAL_URL + "healthy.ics", "deleted": False, "etag": "eH",
                    "ics": _ics_vevent("evt-healthy@icloud.com", "Здоровое событие",
                                       "20370721T090000Z", "20370721T100000Z")}
    broken_item = {"href": broken_href, "deleted": False, "etag": "eB",
                   "ics": broken_resource_ics}

    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         ([healthy_item, broken_item], None,
                          {"mode": "fallback_full", "reason": "http_403"}))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1  # the count mismatch is a real, flagged error (I1)

    # The pre-existing row tied to the BROKEN resource's href survives --
    # excluded from disappearance, not cancelled.
    row = db.execute("SELECT * FROM events WHERE id=?", (existing["id"],)).fetchone()
    assert row["status"] == "active"

    # The HEALTHY resource -- a DIFFERENT href in the SAME calendar --
    # was NOT held hostage by its sibling's problem: it still landed.
    healthy_row = db.execute(
        "SELECT * FROM events WHERE title=?", ("Здоровое событие",)).fetchone()
    assert healthy_row is not None

    rows = _audit_rows(db, "cal.ext.sync")
    assert any("VEVENT block" in e and "dropped 1" in e
               for e in rows[0]["sync_errors"])


# ---------------------------------------------------------------------
# R1 (fix-round 3): the component-count detector must not be silenced by
# case or by \r-only line endings.
#
# Fix-round 4: this detector no longer lives in cli.py at all -- three
# rounds in a row (fix-round 2's bare regex, fix-round 3's own
# case/CR fix, fix-round 3's second attempt) each reintroduced the SAME
# class of bug on a new input, because each was a second, independent
# reimplementation of "what counts as a VEVENT boundary" that inevitably
# drifted from parse_ics's own answer to that question. `extcal.
# parse_ics_with_count` now returns the block count computed by the SAME
# parsing pass that decides component boundaries in the first place (see
# its own docstring and test_extcal_parse.py's dedicated unit tests) --
# this end-to-end test is kept as-is (same scenario, same assertions) to
# prove the case/CR-tolerance property still holds all the way through
# `cli.cmd_tick_cal_ext`, not just at the parser's own unit-test layer.
# ---------------------------------------------------------------------

def test_count_mismatch_detector_is_case_insensitive_and_cr_tolerant(db, monkeypatch):
    """Fix-round 2's first cut used a bare `re.compile(r"^BEGIN:VEVENT",
    re.M)` -- no `re.I`, and `re.M`'s `^` only anchors after an actual
    `\\n`. RFC 5545 property names are case-insensitive (parse_ics's own
    `_split_property_line` upper-cases before comparing) and a feed using
    lone `\\r` line endings is exactly what `extcal._unfold` normalizes
    for parse_ics itself -- either mismatch would have silently counted
    `begin_count=0` and turned the WHOLE guard off, the exact class of
    failure this test exists to catch. `extcal.parse_ics_with_count`
    (fix-round 4) closes this structurally: see test_extcal_parse.py's
    `test_parse_ics_with_count_is_case_insensitive_for_begin_and_end` /
    `..._is_tolerant_of_cr_only_line_endings` for the parser-level proof;
    this test proves the SAME property survives through the whole tick."""
    broken_href = CAL_URL + "cr-and-case.ics"
    existing = cal.add(db, "Перенос был здесь", "2037-07-27T13:00:00+00:00",
                        end_utc="2037-07-27T14:00:00+00:00")
    db.execute(
        "UPDATE events SET owner='iphone', external_uid=?, external_href=? "
        "WHERE id=?",
        (extcal._occurrence_key("recur2@icloud.com", "2037-07-27T13:00:00+00:00"),
         broken_href, existing["id"]),
    )
    db.commit()

    # Mixed-case "Begin:VEvent"/"End:VEvent", and CR-ONLY (\r, no \n at
    # all) line endings throughout.
    broken_resource_ics = (
        "Begin:VCalendar\rVERSION:2.0\r"
        "Begin:VEvent\rUID:recur2@icloud.com\rSUMMARY:Мастер\r"
        "DTSTART:20370720T130000Z\rDTEND:20370720T140000Z\rEnd:VEvent\r"
        "Begin:VEvent\rUID:recur2@icloud.com\r"
        "RECURRENCE-ID:20370727T130000Z\rSUMMARY:Сломанный перенос\r"
        "End:VEvent\r"
        "End:VCalendar\r"
    )
    item = {"href": broken_href, "deleted": False, "etag": "eC",
            "ics": broken_resource_ics}

    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         ([item], None, {"mode": "fallback_full", "reason": "http_403"}))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1  # the mismatch IS detected (would be 0 with the old bug)

    row = db.execute("SELECT * FROM events WHERE id=?", (existing["id"],)).fetchone()
    assert row["status"] == "active"  # NOT cancelled

    rows = _audit_rows(db, "cal.ext.sync")
    assert any("VEVENT block" in e and "dropped 1" in e
               for e in rows[0]["sync_errors"])


# ---------------------------------------------------------------------
# R2 (fix-round 3): a live item with no calendar-data is untrustworthy,
# not "nothing to do" and not a deletion.
# ---------------------------------------------------------------------

def test_live_item_with_no_calendar_data_is_not_treated_as_deleted(db, monkeypatch):
    """extcal._parse_multistatus_items returns `ics=None` for ANY
    non-404 response -- including a per-resource 403/500/507 on an
    otherwise-200 multistatus, or a 200 whose <C:calendar-data> is
    missing/empty -- NOT only for a real deletion (`deleted=True`). The
    fix-round 2 cut's `if not ics_text: continue` silently treated this
    exactly like "nothing changed," letting a pre-existing row fall
    straight into plan_changes' disappearance sweep with zero record of
    the read failure."""
    href = CAL_URL + "unreadable.ics"
    existing = cal.add(db, "Была раньше", "2037-07-20T08:00:00+00:00",
                        end_utc="2037-07-20T09:00:00+00:00")
    db.execute(
        "UPDATE events SET owner='iphone', external_uid=?, external_href=? "
        "WHERE id=?",
        (extcal._occurrence_key("unreadable@icloud.com", None), href, existing["id"]),
    )
    db.commit()

    # deleted=False (NOT a tombstone) but ics=None -- exactly what a
    # per-resource 403/500/507 or an empty <C:calendar-data> produces.
    item = {"href": href, "deleted": False, "etag": "eU", "ics": None}

    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         ([item], None, {"mode": "fallback_full", "reason": "http_403"}))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1  # a real, flagged error -- not a silent no-op

    row = db.execute("SELECT * FROM events WHERE id=?", (existing["id"],)).fetchone()
    assert row["status"] == "active"  # NOT cancelled -- excluded, not tombstoned

    rows = _audit_rows(db, "cal.ext.sync")
    assert any("no calendar-data" in e for e in rows[0]["sync_errors"])


def test_live_item_with_whitespace_only_calendar_data_is_not_treated_as_deleted(db, monkeypatch):
    """Fix-round 4: `if not ics_text:` alone does not catch a
    WHITESPACE-ONLY string -- exactly the shape a pretty-printed
    multistatus XML response's `<C:calendar-data>\\n   </C:calendar-data>`
    produces when its actual content is empty. That string is truthy, so
    it used to sail past the fix-round 3 `if not ics_text:` guard
    straight into `parse_ics_with_count`, which (correctly, per its own
    contract) returns `([], 0)` for it -- 0 == 0, no mismatch detected --
    and in `sync_collection` mode (the tick's own NORMAL running mode
    once the sync-token exchange settles in) a 0-VEVENT result is
    otherwise unremarkable (cheap fix #1: a changed VTODO/VJOURNAL is
    normal there) -- so this exact shape reached `plan_changes`'
    disappearance sweep completely uncontested, with the href already
    sitting in `batch_hrefs`. Round 3's own regression test for this bug
    class (`test_live_item_with_no_calendar_data_is_not_treated_as_
    deleted`, above) only used `ics=None`, which the bare truthiness
    check already caught -- whitespace-only needed its own test, since
    it is exactly the input the OLD check silently let through."""
    href = CAL_URL + "whitespace-only.ics"
    existing = cal.add(db, "Была раньше", "2037-07-20T08:00:00+00:00",
                        end_utc="2037-07-20T09:00:00+00:00")
    db.execute(
        "UPDATE events SET owner='iphone', external_uid=?, external_href=? "
        "WHERE id=?",
        (extcal._occurrence_key("whitespace@icloud.com", None), href, existing["id"]),
    )
    db.commit()

    # Whitespace-only -- truthy in Python, but no real calendar-data.
    item = {"href": href, "deleted": False, "etag": "eW", "ics": "\n   "}

    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    # sync_collection -- the tick's own normal running mode, where cheap
    # fix #1 otherwise waves a 0-VEVENT result through unremarked.
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         ([item], "NEXT-TOK", {"mode": "sync_collection", "reason": None}))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1  # a real, flagged error -- not a silent no-op

    row = db.execute("SELECT * FROM events WHERE id=?", (existing["id"],)).fetchone()
    assert row["status"] == "active"  # NOT cancelled -- excluded, not tombstoned

    rows = _audit_rows(db, "cal.ext.sync")
    assert any("no calendar-data" in e for e in rows[0]["sync_errors"])


# ---------------------------------------------------------------------
# Cheap fix #1 (fix-round 3): a 0-VEVENT item in an incremental
# (sync_collection) delta is unremarkable -- no server-side comp-filter
# there, unlike a FULL-mode calendar-query.
# ---------------------------------------------------------------------

def test_zero_vevent_item_in_sync_collection_mode_is_not_an_error(db, monkeypatch):
    """REPORT sync-collection has NO comp-filter (RFC 6578 returns every
    changed resource in the collection regardless of component type) --
    a changed VTODO sitting in the same subscribed calendar is completely
    normal there. The fix-round 2 cut flagged ANY 0-VEVENT resource
    unconditionally, which would have made a to-do edit in her calendar
    fail the WHOLE cal-ext tick every time."""
    vtodo_only = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        "BEGIN:VTODO\r\nUID:todo1@icloud.com\r\nSUMMARY:Купить молоко\r\n"
        "END:VTODO\r\n"
        "END:VCALENDAR\r\n"
    )
    item = {"href": CAL_URL + "todo1.ics", "deleted": False, "etag": "eT",
            "ics": vtodo_only}

    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         ([item], "NEXT-TOK", {"mode": "sync_collection", "reason": None}))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0  # NOT an error -- a VTODO with 0 VEVENT is unremarkable here

    rows = _audit_rows(db, "cal.ext.sync")
    # No sync_errors mentioning this resource at all.
    all_errors = rows[0].get("sync_errors", []) if rows else []
    assert not any("todo1.ics" in e for e in all_errors)


def test_zero_vevent_item_in_full_mode_is_still_flagged(db, monkeypatch):
    """The mirror case: calendar-query (FULL mode) DOES server-side
    filter by comp-filter name="VEVENT" -- a 0-VEVENT result there means
    something that used to match no longer parses, and stays a real,
    flagged concern."""
    garbage = "this is not ICS at all\r\nno BEGIN:VEVENT anywhere\r\n"
    item = {"href": CAL_URL + "garbage.ics", "deleted": False, "etag": "eG",
            "ics": garbage}

    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         ([item], None, {"mode": "initial_full", "reason": None}))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1  # FULL mode: 0 VEVENT is still suspicious

    rows = _audit_rows(db, "cal.ext.sync")
    assert any("VEVENT block" in e for e in rows[0]["sync_errors"])


# ---------------------------------------------------------------------
# Cheap fix #2 (fix-round 3): a healthy occurrence sharing a bad resource
# with a lost sibling stays a normal update candidate -- not silently
# excluded (and re-queued as insert_skipped_duplicate every tick).
# ---------------------------------------------------------------------

def test_healthy_master_alongside_lost_override_still_gets_updated(db, monkeypatch):
    """Fix-round 2's own bad_hrefs exclusion was scoped to the whole
    HREF: an existing row for the healthy master sharing a resource with
    a lost RECURRENCE-ID override would ALSO be excluded from the
    snapshot -- its own title/time edits would stop applying for as long
    as the resource stays broken, and plan_changes would re-queue it as
    a fresh insert that apply_changes' own idempotency guard silently
    absorbs as insert_skipped_duplicate every single tick. Narrowed
    (cheap fix #2) to only exclude the occurrence(s) NOT reproduced this
    round."""
    broken_href = CAL_URL + "master-plus-lost-override.ics"
    existing = cal.add(db, "Старое название", "2037-07-20T13:00:00+00:00",
                        end_utc="2037-07-20T14:00:00+00:00")
    db.execute(
        "UPDATE events SET owner='iphone', external_uid=?, external_href=? "
        "WHERE id=?",
        (extcal._occurrence_key("master3@icloud.com", None), broken_href, existing["id"]),
    )
    db.commit()

    # Same resource: the master (matches the EXISTING row's own key,
    # title CHANGED) plus an override with no DTSTART (dropped).
    broken_resource_ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        "BEGIN:VEVENT\r\nUID:master3@icloud.com\r\nSUMMARY:Новое название\r\n"
        "DTSTART:20370720T130000Z\r\nDTEND:20370720T140000Z\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:master3@icloud.com\r\n"
        "RECURRENCE-ID:20370727T130000Z\r\nSUMMARY:Сломанный перенос\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    item = {"href": broken_href, "deleted": False, "etag": "eM",
            "ics": broken_resource_ics}

    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         ([item], None, {"mode": "fallback_full", "reason": "http_403"}))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1  # the count mismatch is still a real, flagged error

    # The healthy master's EXISTING row was updated in place -- not
    # excluded, not re-inserted as a duplicate.
    row = db.execute("SELECT * FROM events WHERE id=?", (existing["id"],)).fetchone()
    assert row["title"] == "Новое название"
    assert db.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] == 1

    rows = _audit_rows(db, "cal.ext.sync")
    assert rows[0]["events_updated"] == 1
    assert rows[0]["events_inserted"] == 0


# ---------------------------------------------------------------------
# I5: a truly steady-state tick (mode unchanged, zero counts) does not
# spam a fresh cal.ext.sync audit row every single run.
# ---------------------------------------------------------------------

def test_steady_state_noop_tick_does_not_spam_the_audit_log(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         ([], "SAME-MODE-TOKEN", {"mode": "sync_collection", "reason": None}))

    rc1 = cli.cmd_tick_cal_ext(_args())
    assert rc1 == 0
    first_rows = _audit_rows(db, "cal.ext.sync")
    assert len(first_rows) == 1  # first-ever observation of this mode -- logged

    rc2 = cli.cmd_tick_cal_ext(_args())
    assert rc2 == 0
    second_rows = _audit_rows(db, "cal.ext.sync")
    # Same mode, zero counts, no errors -- steady state, no second row.
    assert len(second_rows) == 1

    # extcal_last_ok still advances on every clean run regardless (the
    # heartbeat this audit's own silence relies on).
    assert famdb.meta_get(db, "extcal_last_ok") is not None


# ---------------------------------------------------------------------
# m1: write-URL and read-filter matching both go through the SAME
# normalized (trailing-slash-insensitive) comparison.
# ---------------------------------------------------------------------

def test_read_filter_trailing_slash_mismatch_still_matches(db, monkeypatch):
    url_allowed = "https://caldav.icloud.com/1/calendars/allowed/"
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg(
        # Config has the URL WITHOUT the trailing slash the discovered
        # calendar itself carries -- must still match (fix-round m1).
        extcal_read_calendars=[url_allowed.rstrip("/")]))
    monkeypatch.setattr(cli.extcal, "discover",
                         lambda cfg, request=None: [_calendar(url_allowed, "Calendar")])
    fetched = []
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         (fetched.append(calendar["url"]) or ([], None,
                          {"mode": "initial_full", "reason": None})))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0
    assert fetched == [url_allowed]


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

    def _fetch(cfg, calendar, sync_token=None, request=None, force_full=False):
        fetched_urls.append(calendar["url"])
        return ([], None, {"mode": "initial_full", "reason": None})

    monkeypatch.setattr(cli.extcal, "fetch_changes", _fetch)

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0
    assert fetched_urls == [url_allowed]  # neither the blocked nor write-target


# ---------------------------------------------------------------------
# m5 (fix-round 2): tick.error text does not duplicate the same calendar
# reason twice.
# ---------------------------------------------------------------------

def test_tick_error_message_does_not_duplicate_calendar_reason(db, monkeypatch):
    """The first cut of fix-round 1 appended a calendar's own fetch
    error TWICE -- once via `sync_errors` (built inside `_cal_ext_sync`),
    once more via a separate `calendar_errors` pass in `cmd_tick_cal_ext`
    -- wasting roughly half of `_audit_tick_error`'s own 200-char slice
    on a verbatim repeat (fix-round 2, minor #5)."""
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    monkeypatch.setattr(cli.extcal, "discover",
                         lambda cfg, request=None: [_calendar(CAL_URL, "Calendar")])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         (None, None, {"mode": "error",
                                        "reason": "a_very_specific_reason_xyz"}))
    calls = []
    monkeypatch.setattr(cli, "_audit_tick_error", lambda where, exc: calls.append(exc))

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 1
    assert len(calls) == 1
    assert calls[0].count("a_very_specific_reason_xyz") == 1


# ---------------------------------------------------------------------
# m6 (fix-round 2): extcal_last_mode is only WRITTEN when it changes.
# ---------------------------------------------------------------------

def test_last_mode_meta_only_written_when_it_changes(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="TOK0")
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None, force_full=False:
                         ([], "SAME-TOKEN", {"mode": "sync_collection", "reason": None}))

    # First tick: mode transitions from "no prior record" -- writes once.
    cli.cmd_tick_cal_ext(_args())

    last_mode_writes = []
    real_meta_set = famdb.meta_set

    def _spy(conn, key, value):
        if key.startswith("extcal_last_mode:"):
            last_mode_writes.append(value)
        return real_meta_set(conn, key, value)
    monkeypatch.setattr(cli.famdb, "meta_set", _spy)

    # Second tick: SAME mode as last time -- must NOT write again (this
    # meta key lands in the same WAL sqlite file fam-reminders touches
    # every minute; an unconditional write here would add 96/day to the
    # very contention m6's own RandomizedDelaySec was trying to reduce).
    cli.cmd_tick_cal_ext(_args())
    assert last_mode_writes == []


# ---------------------------------------------------------------------
# Fix-round 3, Critical finding C1: the rolling-horizon gap. Steady-state
# `REPORT sync-collection` deltas only ever mention a resource that
# actually changed -- an untouched recurring series never reappears in a
# delta, so a rolling `expand()` window silently stops inserting its NEW
# occurrences forever once they scroll into view. `extcal_full_resync_
# days` (default 1, gate.py) plus the per-calendar `meta["extcal_last_
# full:<url>"]` watermark force a periodic full re-baseline through the
# SAME `calendar-query` path (and therefore the SAME disappearance sweep
# and bad_hrefs/degraded_urls/apply-error guards) already used for
# initial_full/fallback_full.
# ---------------------------------------------------------------------

_ROLLING_SERIES_HREF = CAL_URL + "training-series.ics"
_ROLLING_SERIES_ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
    "UID:training-series@icloud.com\r\nSUMMARY:Тренировка\r\n"
    "DTSTART:20370601T090000Z\r\nDTEND:20370601T100000Z\r\n"
    "RRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)


def test_periodic_full_resync_materializes_occurrences_a_delta_would_never_see(
        db, monkeypatch):
    """The core C1 scenario: a weekly series nobody has edited since
    2037-06-01 stopped changing long ago, so a plain incremental delta
    for its resource legitimately comes back EMPTY (nothing changed
    since the stored token) -- proven by the second half of this test.
    With the `extcal_last_full` watermark already 5 days stale (past the
    default 1-day interval), THIS tick must force a full `calendar-
    query` pass (`force_full=True`, mode "periodic_full") instead, which
    re-lists the whole resource and lets `expand()` materialize every
    occurrence currently inside the window -- occurrences a delta round
    would never have surfaced."""
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="SEEDED-TOKEN")
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])

    famdb.meta_set(db, f"extcal_sync_token:{CAL_URL}", "OLD-TOKEN")
    famdb.meta_set(db, f"extcal_last_full:{CAL_URL}", "2037-07-10T00:00:00+00:00")
    db.commit()

    seen_force_full = []

    def _fetch_full(cfg, calendar, sync_token=None, request=None, force_full=False):
        seen_force_full.append(force_full)
        item = {"href": _ROLLING_SERIES_HREF, "deleted": False, "etag": "e1",
                "ics": _ROLLING_SERIES_ICS}
        return ([item], None, {"mode": "periodic_full", "reason": None})
    monkeypatch.setattr(cli.extcal, "fetch_changes", _fetch_full)

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0
    assert seen_force_full == [True]  # the stale watermark forced a full pass

    rows = db.execute("SELECT * FROM events WHERE external_href=?",
                       (_ROLLING_SERIES_HREF,)).fetchall()
    # The weekly series expands to several occurrences inside the
    # [now-1d, now+8w] window -- every one of them just got inserted in
    # ONE full pass.
    assert len(rows) > 1
    assert all(r["owner"] == "iphone" for r in rows)

    stored_full = famdb.meta_get(db, f"extcal_last_full:{CAL_URL}")
    assert stored_full == TEST_NOW  # watermark advanced to this tick's "now"

    # Second half: the SAME resource, still unedited, through a plain
    # incremental delta -- sync-collection legitimately returns an EMPTY
    # items list (nothing changed since the token). This IS the silence
    # C1 is about -- without the watermark forcing a periodic full pass,
    # a tick would look exactly like this forever.
    seen_force_full.clear()

    def _fetch_delta(cfg, calendar, sync_token=None, request=None, force_full=False):
        seen_force_full.append(force_full)
        return ([], "NEXT-TOKEN", {"mode": "sync_collection", "reason": None})
    monkeypatch.setattr(cli.extcal, "fetch_changes", _fetch_delta)

    rc2 = cli.cmd_tick_cal_ext(_args())
    assert rc2 == 0
    assert seen_force_full == [False]  # watermark just advanced -- stays incremental


def test_full_resync_not_forced_before_interval_elapses(db, monkeypatch):
    """Regression guard, the other half of C1: a calendar whose
    `extcal_last_full` watermark is still FRESH (inside the configured
    interval) must not be forced through a full pass every tick -- that
    would defeat the entire point of `REPORT sync-collection` deltas
    (one full `calendar-query` per calendar per day is meant to be cheap
    exactly BECAUSE it is rare)."""
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="SEEDED-TOKEN")
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])

    famdb.meta_set(db, f"extcal_sync_token:{CAL_URL}", "OLD-TOKEN")
    # One hour stale -- well inside the default 1-day interval.
    famdb.meta_set(db, f"extcal_last_full:{CAL_URL}", "2037-07-14T23:00:00+00:00")
    db.commit()

    seen_force_full = []

    def _fetch(cfg, calendar, sync_token=None, request=None, force_full=False):
        seen_force_full.append(force_full)
        return ([], "NEXT-TOKEN", {"mode": "sync_collection", "reason": None})
    monkeypatch.setattr(cli.extcal, "fetch_changes", _fetch)

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0
    assert seen_force_full == [False]

    # A calendar that stayed incremental this round does not touch its
    # own full watermark -- it should stay exactly where it was.
    assert famdb.meta_get(db, f"extcal_last_full:{CAL_URL}") == "2037-07-14T23:00:00+00:00"


def test_missing_full_watermark_forces_full_then_gates_the_next_tick(db, monkeypatch):
    """A calendar with an EXISTING stored sync-token but NO recorded
    `extcal_last_full` yet (e.g. right after this fix first deploys) is
    treated as overdue -- self-healing, not a special case: the safest
    assumption about an unknown-age token is that it might already be
    stale. Once this tick's full pass completes, the watermark persists
    in `meta` (the same table `extcal_sync_token` and `extcal_last_ok`
    already survive a process restart through -- nothing new to prove
    there) and correctly gates the very next tick back to incremental."""
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="SEEDED-TOKEN")
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])

    famdb.meta_set(db, f"extcal_sync_token:{CAL_URL}", "OLD-TOKEN")
    db.commit()
    assert famdb.meta_get(db, f"extcal_last_full:{CAL_URL}") is None

    seen = []

    def _fetch_full(cfg, calendar, sync_token=None, request=None, force_full=False):
        seen.append(force_full)
        return ([], None, {"mode": "periodic_full", "reason": None})
    monkeypatch.setattr(cli.extcal, "fetch_changes", _fetch_full)

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0
    assert seen == [True]
    assert famdb.meta_get(db, f"extcal_last_full:{CAL_URL}") == TEST_NOW

    # Next tick: watermark is now fresh -- stays incremental. This is the
    # persistence check -- a fresh `cmd_tick_cal_ext` call (this project's
    # own equivalent of "a new process after a restart", since `meta`
    # lives in the sqlite file, not in memory) reads back exactly what
    # the previous call wrote.
    seen.clear()

    def _fetch_delta(cfg, calendar, sync_token=None, request=None, force_full=False):
        seen.append(force_full)
        return ([], "NEXT-TOKEN", {"mode": "sync_collection", "reason": None})
    monkeypatch.setattr(cli.extcal, "fetch_changes", _fetch_delta)

    rc2 = cli.cmd_tick_cal_ext(_args())
    assert rc2 == 0
    assert seen == [False]


def test_periodic_full_still_runs_the_disappearance_sweep_like_any_other_full_mode(
        db, monkeypatch):
    """C1's fix must not invent a side door around the existing full-mode
    guards (the task's own explicit warning: this project has already
    burned four review rounds on irreversible-cancellation regressions
    here) -- `periodic_full` has to be exactly as exhaustive-listing-
    trustworthy as `initial_full`/`fallback_full` already are, so a row
    that genuinely vanished from her calendar is still cancelled by
    `plan_changes`' disappearance sweep, same as before this finding."""
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    cal_a = _calendar(CAL_URL, "Calendar", sync_token="SEEDED-TOKEN")
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [cal_a])

    existing = cal.add(db, "Уже не будет", "2037-07-20T08:00:00+00:00",
                        end_utc="2037-07-20T09:00:00+00:00")
    db.execute(
        "UPDATE events SET owner='iphone', external_uid=?, external_href=? "
        "WHERE id=?",
        (extcal._occurrence_key("evt-gone@icloud.com", None),
         CAL_URL + "evt-gone.ics", existing["id"]),
    )
    famdb.meta_set(db, f"extcal_sync_token:{CAL_URL}", "OLD-TOKEN")
    famdb.meta_set(db, f"extcal_last_full:{CAL_URL}", "2037-07-10T00:00:00+00:00")
    db.commit()

    def _fetch(cfg, calendar, sync_token=None, request=None, force_full=False):
        assert force_full is True
        # Exhaustive listing that no longer mentions evt-gone.ics at all
        # -- a genuine phone-side deletion, not a fetch problem.
        return ([], None, {"mode": "periodic_full", "reason": None})
    monkeypatch.setattr(cli.extcal, "fetch_changes", _fetch)

    rc = cli.cmd_tick_cal_ext(_args())
    assert rc == 0

    row = db.execute("SELECT * FROM events WHERE id=?", (existing["id"],)).fetchone()
    assert row["status"] == "cancelled"


# ---------------------------------------------------------------------
# m4: a malformed --now is a graceful tick.error, not an uncaught traceback
# ---------------------------------------------------------------------

def test_invalid_now_argument_is_a_graceful_error(db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: _cfg())
    calls = []
    monkeypatch.setattr(cli, "_audit_tick_error", lambda where, exc: calls.append(where))

    rc = cli.cmd_tick_cal_ext(_args(now="not-a-real-timestamp"))
    assert rc == 1
    assert calls == ["cal-ext"]


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
