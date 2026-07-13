import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from fam import audit, cal, gate, people, places, rem, tick

CFG = {
    "target": "whatsapp:+77782110625",
    "quiet_start": "21:30",
    "quiet_end": "07:30",
    "daily_budget": 8,
    "gate_model": "gpt-5.4-mini",
    "gate_provider": "openai-codex",
    "max_len_reminder": 300,
    "max_len_digest": 900,
    "reminder_max_age_min": 120,
}

NOW = "2026-07-20T04:30:00+00:00"
# 10 min before NOW: always "due" relative to NOW, and -- since Fix 1's
# stale-age guard cancels anything older than cfg["reminder_max_age_min"]
# (120 min default) -- fresh enough to survive it and reach gate.deliver,
# unlike the year-2000 timestamp this used to be.
PAST = "2026-07-20T04:20:00+00:00"
FUTURE = "2099-01-01T00:00:00+00:00"  # never "due" relative to NOW


def _insert_reminder(db, event_id, label="напоминание", fire_at=PAST,
                      status="pending", anchor="start", created_at=PAST):
    cur = db.execute(
        "INSERT INTO reminders(event_id, label, anchor, fire_at_utc, "
        "status, created_at) VALUES (?,?,?,?,?,?)",
        (event_id, label, anchor, fire_at, status, created_at),
    )
    return cur.lastrowid


class FakeDeliver:
    """Records every gate.deliver() call and returns canned statuses
    consumed in order -- tick.py tests never touch subprocess/hermes;
    gate.py's own subprocess contract is exercised in test_gate.py."""

    def __init__(self):
        self.calls = []
        self.responses = []

    def __call__(self, conn, kind, raw, human_fallback, cfg, force=False,
                 now_utc=None):
        self.calls.append({
            "kind": kind, "raw": raw, "human_fallback": human_fallback,
            "force": force, "now_utc": now_utc,
        })
        return self.responses.pop(0)


@pytest.fixture()
def fake_deliver(monkeypatch):
    fd = FakeDeliver()
    monkeypatch.setattr(gate, "deliver", fd)
    return fd


def _event(db, title="Событие", start="2026-07-20T05:00:00+00:00", **kw):
    return cal.add(db, title, start, **kw)


# ---- due selection by time ----

def test_due_selection_only_past_fire_at_is_processed(db, fake_deliver):
    e = _event(db)
    db.commit()
    due_id = _insert_reminder(db, e["id"], fire_at=PAST)
    _insert_reminder(db, e["id"], fire_at=FUTURE)
    db.commit()
    fake_deliver.responses = ["sent"]

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts == {"due": 1, "sent": 1, "quiet": 0, "budget": 0,
                       "error": 0, "cancelled": 0, "stale": 0,
                       "error_capped": 0, "road_recomputed": 0}
    assert len(fake_deliver.calls) == 1
    statuses = {r["id"]: r["status"] for r in db.execute(
        "SELECT id, status FROM reminders WHERE event_id=?", (e["id"],))}
    assert statuses[due_id] == "sent"


# ---- sent updates status + sent_at ----

def test_sent_updates_status_and_sent_at(db, fake_deliver):
    e = _event(db)
    db.commit()
    rid = _insert_reminder(db, e["id"])
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    row = db.execute(
        "SELECT status, sent_at FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "sent"
    assert row["sent_at"] == NOW


# ---- raw/fallback payload shape passed to gate.deliver ----

def test_raw_and_fallback_shape(db, fake_deliver):
    people.add(db, "Тая", slug="taya")
    places.add(db, "Клиника")
    db.commit()
    e = _event(db, title="Врач", place="Клиника", participants=["Тая"])
    db.commit()
    _insert_reminder(db, e["id"], label="пора выходить")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    call = fake_deliver.calls[0]
    assert call["kind"] == "reminder"
    assert call["raw"] == {
        "label": "пора выходить",
        "event_id": e["id"],
        "title": "Врач",
        "start_local": e["start_local"],
        "participants": ["Тая"],
        "place_name": "Клиника",
        # NOW = 2026-07-20T04:30:00+00:00 UTC == 09:30:00+05:00 Almaty.
        "sent_now_local": "2026-07-20T09:30:00+05:00",
    }
    assert call["human_fallback"] == (
        f"пора выходить: Врач — {e['start_local']}"
    )
    assert call["now_utc"] == NOW

    # Phase 2c, task 7: a second tick for the SAME event, after an
    # earlier send today, carries raw["prior_texts"] with that earlier
    # send's final text -- the variation-rule input (see
    # GATE_REMINDER_TIME_SEMANTICS_INSTRUCTION). gate.deliver is mocked
    # here (fake_deliver never writes a real gate.sent audit row), so
    # the earlier send is seeded directly as an audit row, same shape
    # gate.deliver's own payload uses.
    db.execute(
        "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
        (NOW, "gate.sent", "test", json.dumps(
            {"kind": "reminder", "raw": {"event_id": e["id"]},
             "final": "Пора выходить к врачу."},
            ensure_ascii=False,
        )),
    )
    db.commit()
    _insert_reminder(db, e["id"], label="уже пора")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    second_call = fake_deliver.calls[-1]
    assert second_call["raw"]["prior_texts"] == ["Пора выходить к врачу."]


def test_raw_omits_prior_texts_when_no_prior_sends_today(db, fake_deliver):
    e = _event(db)
    db.commit()
    _insert_reminder(db, e["id"])
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert "prior_texts" not in fake_deliver.calls[0]["raw"]


def test_raw_omits_place_name_when_no_place(db, fake_deliver):
    e = _event(db)
    db.commit()
    _insert_reminder(db, e["id"])
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert "place_name" not in fake_deliver.calls[0]["raw"]
    assert fake_deliver.calls[0]["raw"]["participants"] == []


# ---- sent_now_local: send-time anchor, distinct from the event's own
# start_local (Task 16 live bug -- "В 13:00 Тае пора собираться": the
# rewrite bound the label's action to the event's start_local instead of
# the actual send time) ----

def test_raw_sent_now_local_is_almaty_iso_of_now(db, fake_deliver):
    e = _event(db)
    db.commit()
    _insert_reminder(db, e["id"])
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    # NOW = 2026-07-20T04:30:00+00:00 UTC == 09:30:00+05:00 Asia/Almaty.
    raw = fake_deliver.calls[0]["raw"]
    assert raw["sent_now_local"] == "2026-07-20T09:30:00+05:00"


def test_raw_sent_now_local_differs_from_event_start_local(db, fake_deliver):
    # Mirrors the live-found bug's shape: a reminder (e.g. the "Тае пора
    # собираться" leave_at-45min stage) fires well before its event's own
    # start -- sent_now_local and start_local must never be conflated.
    e = _event(db, start="2026-07-20T08:00:00+00:00")  # 13:00 Almaty
    db.commit()
    _insert_reminder(db, e["id"], label="Тае пора собираться")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)  # 09:30 Almaty

    raw = fake_deliver.calls[0]["raw"]
    assert raw["start_local"] == e["start_local"] == "2026-07-20T13:00:00+05:00"
    assert raw["sent_now_local"] == "2026-07-20T09:30:00+05:00"
    assert raw["sent_now_local"] != raw["start_local"]


# ---- quiet / budget / error leave the reminder pending ----

@pytest.mark.parametrize("gate_status", ["quiet", "budget", "error"])
def test_non_sent_outcomes_leave_reminder_pending(db, fake_deliver, gate_status):
    e = _event(db)
    db.commit()
    rid = _insert_reminder(db, e["id"])
    db.commit()
    fake_deliver.responses = [gate_status]

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts["due"] == 1
    assert counts[gate_status] == 1
    assert counts["sent"] == 0
    row = db.execute(
        "SELECT status, sent_at FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "pending"
    assert row["sent_at"] is None


# ---- cancelled/inactive event cancels the reminder, no send attempted ----

@pytest.mark.parametrize("event_status", ["cancelled", "done"])
def test_inactive_event_cancels_reminder_without_delivering(db, fake_deliver,
                                                              event_status):
    e = _event(db)
    db.commit()
    # Flip status directly via SQL (not cal.cancel()/cal.done()) so this
    # test pins the tick's OWN defensive check, not cal.py's existing
    # cancel_chain hook -- mirrors test_rem.py's
    # test_regenerate_on_cancelled_event_clears_pending pattern.
    db.execute("UPDATE events SET status=? WHERE id=?", (event_status, e["id"]))
    rid = _insert_reminder(db, e["id"])
    db.commit()
    fake_deliver.responses = []  # must not be called

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts == {"due": 1, "sent": 0, "quiet": 0, "budget": 0,
                       "error": 0, "cancelled": 1, "stale": 0,
                       "error_capped": 0, "road_recomputed": 0}
    assert fake_deliver.calls == []
    row = db.execute(
        "SELECT status FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "cancelled"

    audit_rows = audit.query(db, since_utc=None, kind_prefix="rem.cancel_stale",
                              grep=None, limit=10)
    assert len(audit_rows) == 1
    assert audit_rows[0]["payload"] == {"reminder_id": rid, "event_id": e["id"]}


# ---- stale-age guard: parked-too-long reminders are cancelled, not sent
# (Fix 1, pre-live guards review round) ----

def test_stale_pending_reminder_8h_old_is_cancelled_not_delivered(db, fake_deliver):
    # Models a reminder repeatedly parked by quiet hours until it's 8h
    # old -- well past reminder_max_age_min (120 min default) -- which
    # must now be cancelled as stale rather than delivered late.
    e = _event(db)  # start 2026-07-20T05:00:00+00:00, 30 min after NOW
    db.commit()
    old_fire_at = "2026-07-19T20:30:00+00:00"  # 8h before NOW
    rid = _insert_reminder(db, e["id"], fire_at=old_fire_at)
    db.commit()
    fake_deliver.responses = []  # must not be called

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts["due"] == 1
    assert counts["stale"] == 1
    assert counts["sent"] == 0
    assert fake_deliver.calls == []
    row = db.execute(
        "SELECT status FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "cancelled"

    audit_rows = audit.query(db, since_utc=None, kind_prefix="rem.cancel_stale_age",
                              grep=None, limit=10)
    assert len(audit_rows) == 1
    assert audit_rows[0]["payload"] == {
        "reminder_id": rid, "event_id": e["id"],
        "fire_at_utc": old_fire_at, "age_min": 480,
    }


def test_fresh_reminder_10_min_late_is_delivered_normally(db, fake_deliver):
    # Age < reminder_max_age_min (120): delivered as normal, not stale.
    e = _event(db)
    db.commit()
    rid = _insert_reminder(db, e["id"], fire_at=PAST)  # 10 min before NOW
    db.commit()
    fake_deliver.responses = ["sent"]

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts["due"] == 1
    assert counts["stale"] == 0
    assert counts["sent"] == 1
    assert len(fake_deliver.calls) == 1
    row = db.execute(
        "SELECT status FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "sent"


def test_stale_via_event_start_far_in_past_even_with_fresh_fire_at(db, fake_deliver):
    # The OR branch: a fresh fire_at (age 0) is still stale if the
    # event's own start_utc is already more than max_age in the past --
    # e.g. a reminder regenerated late for an event that already happened.
    e = _event(db, start="2026-07-20T02:00:00+00:00")  # 2h30m before NOW
    db.commit()
    rid = _insert_reminder(db, e["id"], fire_at=NOW)
    db.commit()
    fake_deliver.responses = []  # must not be called

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts["stale"] == 1
    assert counts["sent"] == 0
    assert fake_deliver.calls == []
    row = db.execute(
        "SELECT status FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "cancelled"


# ---- retry cap: repeated delivery errors eventually cancel the reminder
# (Fix 2, pre-live guards review round) ----

def test_two_errors_keep_reminder_pending_with_incrementing_error_count(db, fake_deliver):
    e = _event(db)
    db.commit()
    rid = _insert_reminder(db, e["id"])
    db.commit()

    fake_deliver.responses = ["error"]
    first = tick.reminders(db, now_utc=NOW, cfg=CFG)
    assert first["error"] == 1
    assert first["error_capped"] == 0

    fake_deliver.responses = ["error"]
    second = tick.reminders(db, now_utc=NOW, cfg=CFG)
    assert second["error"] == 1
    assert second["error_capped"] == 0

    row = db.execute(
        "SELECT status, error_count FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "pending"
    assert row["error_count"] == 2


def test_third_error_hits_cap_and_cancels(db, fake_deliver):
    e = _event(db)
    db.commit()
    rid = _insert_reminder(db, e["id"])
    db.commit()

    for _ in range(2):
        fake_deliver.responses = ["error"]
        tick.reminders(db, now_utc=NOW, cfg=CFG)

    fake_deliver.responses = ["error"]
    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts["error"] == 0
    assert counts["error_capped"] == 1
    row = db.execute(
        "SELECT status, error_count FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "cancelled"
    assert row["error_count"] == 3

    audit_rows = audit.query(db, since_utc=None, kind_prefix="rem.cancel_error_cap",
                              grep=None, limit=10)
    assert len(audit_rows) == 1
    assert audit_rows[0]["payload"] == {"reminder_id": rid, "errors": 3}


# ---- zero-run always audits ----

def test_zero_due_run_still_audits_tick_reminders(db, fake_deliver):
    fake_deliver.responses = []

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts == {"due": 0, "sent": 0, "quiet": 0, "budget": 0,
                       "error": 0, "cancelled": 0, "stale": 0,
                       "error_capped": 0, "road_recomputed": 0}
    rows = audit.query(db, since_utc=None, kind_prefix="tick.reminders",
                        grep=None, limit=10)
    assert len(rows) == 1
    assert rows[0]["payload"] == counts


def test_tick_reminders_audit_payload_matches_counts_on_mixed_run(db, fake_deliver):
    e1 = _event(db, title="A")
    e2 = _event(db, title="B", start="2026-07-21T05:00:00+00:00")
    db.commit()
    _insert_reminder(db, e1["id"])
    _insert_reminder(db, e2["id"])
    db.commit()
    fake_deliver.responses = ["sent", "quiet"]

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    rows = audit.query(db, since_utc=None, kind_prefix="tick.reminders",
                        grep=None, limit=10)
    assert len(rows) == 1
    assert rows[0]["payload"] == counts
    assert counts["due"] == 2 and counts["sent"] == 1 and counts["quiet"] == 1


# ---- idempotence: immediate re-run sees due=0 ----

def test_idempotent_rerun_sees_zero_due(db, fake_deliver):
    e = _event(db)
    db.commit()
    _insert_reminder(db, e["id"])
    db.commit()
    fake_deliver.responses = ["sent"]

    first = tick.reminders(db, now_utc=NOW, cfg=CFG)
    assert first["due"] == 1 and first["sent"] == 1

    fake_deliver.responses = []  # would raise IndexError if called again
    second = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert second == {"due": 0, "sent": 0, "quiet": 0, "budget": 0,
                       "error": 0, "cancelled": 0, "stale": 0,
                       "error_capped": 0, "road_recomputed": 0}
    assert len(fake_deliver.calls) == 1  # only the first run's call


def test_idempotent_rerun_after_quiet_leaves_reminder_due_again(db, fake_deliver):
    # A "quiet" outcome leaves the reminder pending, so a later tick
    # (once the quiet window has passed) must see it as due again --
    # this is the whole point of NOT advancing status on quiet/budget.
    e = _event(db)
    db.commit()
    _insert_reminder(db, e["id"])
    db.commit()
    fake_deliver.responses = ["quiet"]

    first = tick.reminders(db, now_utc=NOW, cfg=CFG)
    assert first["due"] == 1 and first["quiet"] == 1

    fake_deliver.responses = ["sent"]
    second = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert second["due"] == 1 and second["sent"] == 1


# ---- default now_utc / default cfg wiring (no explicit override) ----

def test_reminders_uses_real_now_when_not_given(db, fake_deliver):
    # This pins that omitting now_utc doesn't raise and drives
    # due-selection off the real wall clock. Both the event start and the
    # reminder's fire_at are built relative to the real current time
    # (rather than the fixed NOW/PAST constants) so they stay inside
    # reminder_max_age_min of whenever this test actually runs -- an
    # old fixed timestamp would now be judged stale by Fix 1's guard
    # before ever reaching gate.deliver.
    real_now = datetime.now(timezone.utc)
    start = (real_now + timedelta(hours=1)).isoformat(timespec="seconds")
    fire_at = (real_now - timedelta(minutes=5)).isoformat(timespec="seconds")
    e = _event(db, start=start)
    db.commit()
    _insert_reminder(db, e["id"], fire_at=fire_at)
    db.commit()
    fake_deliver.responses = ["sent"]

    counts = tick.reminders(db, cfg=CFG)

    assert counts["due"] == 1 and counts["sent"] == 1


def test_reminders_loads_config_when_not_given(db, fake_deliver, monkeypatch, tmp_path):
    example = tmp_path / "fam-config.example.json"
    example.write_text(json.dumps(CFG, ensure_ascii=False), encoding="utf-8")
    target = tmp_path / "fam-config.json"
    monkeypatch.setattr(gate, "CONFIG_PATH", target)
    monkeypatch.setattr(gate, "CONFIG_EXAMPLE_PATH", example)
    fake_deliver.responses = []

    counts = tick.reminders(db, now_utc=NOW)

    assert counts["due"] == 0
    assert target.exists()


# ==== Task 4: threshold road recompute (T-120/T-60) ====

ROAD_CFG = dict(CFG, road_home_lat=43.2220, road_home_lon=76.8512,
                 road_coef=1.4, road_speed_kmh=30, road_daily_cap=100,
                 road_timeout_sec=10, road_recompute_min=[120, 60])

NO_HOME_ROAD_CFG = dict(ROAD_CFG, road_home_lat=None, road_home_lon=None)


def _road_place(db, name="Клиника"):
    places.add(db, name, lat=43.2298, lon=76.8823)
    db.commit()
    return name


def _add_event_neutral_road(db, monkeypatch, place, start="2026-07-20T06:29:00+00:00",
                             **kw):
    """cal.add() (Task 3) runs its own road hook at add-time, reading the
    REAL on-disk config via gate.load_config() -- unrelated to whatever
    cfg a test later passes to tick.reminders(). On a host whose live
    fam-config.json already has home coordinates configured (as this
    one's does), that hook would fire for real and pre-seed
    travel_min_road with a value these tests don't control, breaking
    both the "old" value in road.recompute's audit payload and the
    minutes-to-leave arithmetic the threshold-window tests rely on.
    Neutralizing compute_travel_min to ("none") for the add() call only
    keeps events created this way at travel_min_road=None, exactly like
    a from-scratch test DB -- the caller then sets its own
    compute_travel_min stub for the tick-level recompute under test.
    """
    monkeypatch.setattr(tick.road, "compute_travel_min",
                         lambda conn, event, cfg, now_utc=None: (None, "none"))
    e = cal.add(db, "Врач", start, place=place, **kw)
    db.commit()
    return e


def test_road_recompute_at_119min_happens_once_per_window(db, fake_deliver, monkeypatch):
    place = _road_place(db)
    e = _add_event_neutral_road(db, monkeypatch, place)
    # start = NOW + 119min, travel 0 initially -> leave_at = start

    monkeypatch.setattr(tick.road, "compute_travel_min",
                         lambda conn, event, cfg, now_utc=None: (5, "tomtom"))
    fake_deliver.responses = []

    counts = tick.reminders(db, now_utc=NOW, cfg=ROAD_CFG)
    assert counts["road_recomputed"] == 1
    got = cal.get(db, e["id"])
    assert got["travel_min_road"] == 5
    assert got["road_checked_at"] == NOW  # stamped from this tick's own now
    assert audit.query(db, None, "road.recompute", None)

    # Second tick, same NOW: road_checked_at (== NOW) already covers the
    # T-120 window that just opened (leave_at shifted to NOW+114min after
    # the first recompute, but window_open == leave_at-120 predates NOW),
    # so no second call.
    counts2 = tick.reminders(db, now_utc=NOW, cfg=ROAD_CFG)
    assert counts2["road_recomputed"] == 0


def test_road_recompute_second_threshold_window_after_first(db, fake_deliver, monkeypatch):
    place = _road_place(db)
    # start = NOW + 119min; a constant travel figure (10) keeps leave_at
    # fixed at NOW+109min once first recomputed, so the T-60 window's
    # boundary can be reasoned about precisely on the second tick.
    e = _add_event_neutral_road(db, monkeypatch, place)
    monkeypatch.setattr(tick.road, "compute_travel_min",
                         lambda conn, event, cfg, now_utc=None: (10, "tomtom"))
    fake_deliver.responses = []

    first = tick.reminders(db, now_utc=NOW, cfg=ROAD_CFG)
    assert first["road_recomputed"] == 1  # T-120 window, travel_min_road None->10

    now2 = (datetime.fromisoformat(NOW) + timedelta(minutes=50)).isoformat(
        timespec="seconds")
    # leave_at is now NOW+109min; at now2 (NOW+50min) that's 59 min away
    # -- a fresh T-60 window the earlier road_checked_at (== NOW) doesn't
    # cover (window_open == leave_at-60 == NOW+49min > NOW).
    second = tick.reminders(db, now_utc=now2, cfg=ROAD_CFG)
    assert second["road_recomputed"] == 1


def test_road_recompute_changed_minutes_updates_and_regenerates_chain(
        db, fake_deliver, monkeypatch):
    rem.seed_default_rules(db)
    db.commit()
    place = _road_place(db)
    e = _add_event_neutral_road(db, monkeypatch, place)

    # Seed one already-sent reminder for this event: must survive the
    # regenerate untouched (rem.regenerate only ever deletes 'pending').
    sent_id = _insert_reminder(db, e["id"], label="старое", fire_at=PAST,
                                status="sent")
    db.commit()
    pending_before = db.execute(
        "SELECT fire_at_utc FROM reminders WHERE event_id=? AND status='pending' "
        "ORDER BY fire_at_utc LIMIT 1", (e["id"],)).fetchone()

    monkeypatch.setattr(tick.road, "compute_travel_min",
                         lambda conn, event, cfg, now_utc=None: (45, "tomtom"))
    fake_deliver.responses = []
    counts = tick.reminders(db, now_utc=NOW, cfg=ROAD_CFG)

    assert counts["road_recomputed"] == 1
    got = cal.get(db, e["id"])
    assert got["travel_min_road"] == 45
    rows = audit.query(db, None, "road.recompute", None)
    assert rows and rows[0]["payload"] == {
        "event_id": e["id"], "old": None, "new": 45, "source": "tomtom"}
    assert audit.query(db, None, "rem.regenerate", None)

    sent_row = db.execute(
        "SELECT status, fire_at_utc FROM reminders WHERE id=?", (sent_id,)
    ).fetchone()
    assert sent_row["status"] == "sent"    # untouched by regen
    assert sent_row["fire_at_utc"] == PAST  # fire time not rewritten either

    pending_after = db.execute(
        "SELECT fire_at_utc FROM reminders WHERE event_id=? AND status='pending' "
        "ORDER BY fire_at_utc LIMIT 1", (e["id"],)).fetchone()
    if pending_before and pending_after:
        assert pending_after["fire_at_utc"] != pending_before["fire_at_utc"]


def test_road_recompute_unchanged_minutes_only_bumps_checked_at(
        db, fake_deliver, monkeypatch):
    place = _road_place(db)
    e = _add_event_neutral_road(db, monkeypatch, place, travel_min=7)
    # Pre-set travel_min_road to the same value compute_travel_min will
    # return, and road_checked_at to something clearly outside any
    # threshold window, so this tick's recompute finds "unchanged".
    db.execute(
        "UPDATE events SET travel_min_road=7, road_checked_at=? WHERE id=?",
        ("2026-07-19T00:00:00+00:00", e["id"]))
    db.commit()

    regen_before = len(audit.query(db, None, "rem.regenerate", None))

    monkeypatch.setattr(tick.road, "compute_travel_min",
                         lambda conn, event, cfg, now_utc=None: (7, "tomtom"))
    fake_deliver.responses = []
    counts = tick.reminders(db, now_utc=NOW, cfg=ROAD_CFG)

    assert counts["road_recomputed"] == 1
    assert not audit.query(db, None, "road.recompute", None)
    # cal.add() itself already logged one rem.regenerate at add-time --
    # only assert road_recompute (unchanged minutes) didn't log another.
    assert len(audit.query(db, None, "rem.regenerate", None)) == regen_before
    got = cal.get(db, e["id"])
    assert got["travel_min_road"] == 7
    assert got["road_checked_at"] == NOW


class RecordingRoad:
    """compute_travel_min stub that records every call's now_utc
    (depart_at anchor) and returns canned (minutes, source) results
    consumed in order (last one repeats)."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, conn, event, cfg, now_utc=None):
        self.calls.append({"now_utc": now_utc})
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]


def test_road_recompute_big_shift_forces_followup_at_corrected_anchor(
        db, fake_deliver, monkeypatch):
    # Anchor re-check rule: a recompute whose delta > 10 min was anchored
    # at the now-wrong OLD leave -- checked_at is left NULL so the next
    # tick recomputes once more, with depart_at == the NEW leave.
    place = _road_place(db)
    # start = NOW + 90min (06:00Z); prior computed travel 20 -> leave at
    # 05:40Z, 70 min away: inside T-120, outside T-60.
    e = _add_event_neutral_road(db, monkeypatch, place,
                                 start="2026-07-20T06:00:00+00:00")
    db.execute(
        "UPDATE events SET travel_min_road=20, road_checked_at=? WHERE id=?",
        ("2026-07-19T00:00:00+00:00", e["id"]))
    db.commit()

    stub = RecordingRoad([(45, "tomtom")])  # 20 -> 45: big shift (25 > 10)
    monkeypatch.setattr(tick.road, "compute_travel_min", stub)
    fake_deliver.responses = []

    first = tick.reminders(db, now_utc=NOW, cfg=ROAD_CFG)
    assert first["road_recomputed"] == 1
    got = cal.get(db, e["id"])
    assert got["travel_min_road"] == 45
    assert got["road_checked_at"] is None  # forces the follow-up
    # first call was anchored at the OLD leave (start - 20min = 05:40Z)
    assert stub.calls[0]["now_utc"] == "2026-07-20T05:40:00+00:00"

    now2 = (datetime.fromisoformat(NOW) + timedelta(minutes=1)).isoformat(
        timespec="seconds")
    second = tick.reminders(db, now_utc=now2, cfg=ROAD_CFG)
    assert second["road_recomputed"] == 1
    # follow-up anchored at the CORRECTED leave (start - 45min = 05:15Z)
    assert stub.calls[1]["now_utc"] == "2026-07-20T05:15:00+00:00"
    # stable result (45 again) -> unchanged branch: checked_at sticks
    got2 = cal.get(db, e["id"])
    assert got2["road_checked_at"] == now2

    # third tick: freshness invariant satisfied, no further calls
    now3 = (datetime.fromisoformat(NOW) + timedelta(minutes=2)).isoformat(
        timespec="seconds")
    third = tick.reminders(db, now_utc=now3, cfg=ROAD_CFG)
    assert third["road_recomputed"] == 0
    assert len(stub.calls) == 2


def test_road_recompute_small_shift_sets_checked_at_no_followup(
        db, fake_deliver, monkeypatch):
    place = _road_place(db)
    e = _add_event_neutral_road(db, monkeypatch, place,
                                 start="2026-07-20T06:00:00+00:00")
    db.execute(
        "UPDATE events SET travel_min_road=20, road_checked_at=? WHERE id=?",
        ("2026-07-19T00:00:00+00:00", e["id"]))
    db.commit()

    stub = RecordingRoad([(25, "tomtom")])  # 20 -> 25: small shift (5 <= 10)
    monkeypatch.setattr(tick.road, "compute_travel_min", stub)
    fake_deliver.responses = []

    first = tick.reminders(db, now_utc=NOW, cfg=ROAD_CFG)
    assert first["road_recomputed"] == 1
    got = cal.get(db, e["id"])
    assert got["travel_min_road"] == 25
    assert got["road_checked_at"] == NOW  # small delta: no anchor re-check
    assert audit.query(db, None, "road.recompute", None)  # still audited

    now2 = (datetime.fromisoformat(NOW) + timedelta(minutes=1)).isoformat(
        timespec="seconds")
    second = tick.reminders(db, now_utc=now2, cfg=ROAD_CFG)
    assert second["road_recomputed"] == 0  # no follow-up
    assert len(stub.calls) == 1


def test_road_recompute_event_without_coords_never_a_candidate(
        db, fake_deliver, monkeypatch):
    places.add(db, "Клиника без координат")  # no lat/lon
    db.commit()
    cal.add(db, "Врач", "2026-07-20T06:29:00+00:00",
            place="Клиника без координат")
    db.commit()

    monkeypatch.setattr(
        tick.road, "compute_travel_min",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))
    fake_deliver.responses = []
    counts = tick.reminders(db, now_utc=NOW, cfg=ROAD_CFG)
    assert counts["road_recomputed"] == 0


def test_road_recompute_no_home_coords_means_no_candidates(
        db, fake_deliver, monkeypatch):
    place = _road_place(db)
    _add_event_neutral_road(db, monkeypatch, place)

    monkeypatch.setattr(
        tick.road, "compute_travel_min",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))
    fake_deliver.responses = []
    counts = tick.reminders(db, now_utc=NOW, cfg=NO_HOME_ROAD_CFG)
    assert counts["road_recomputed"] == 0


def test_road_recompute_error_is_audited_and_tick_continues(
        db, fake_deliver, monkeypatch):
    place = _road_place(db)
    e = _add_event_neutral_road(db, monkeypatch, place)
    due_id = _insert_reminder(db, e["id"], fire_at=PAST)
    db.commit()

    def boom(conn, event, cfg, now_utc=None):
        raise RuntimeError("boom")
    monkeypatch.setattr(tick.road, "compute_travel_min", boom)
    fake_deliver.responses = ["sent"]

    counts = tick.reminders(db, now_utc=NOW, cfg=ROAD_CFG)

    assert counts["road_recomputed"] == 0
    rows = audit.query(db, None, "road.hook_error", None)
    assert rows and rows[0]["payload"] == {"event_id": e["id"]}
    # due processing still ran despite the road_recompute failure
    assert counts["due"] == 1 and counts["sent"] == 1
    assert db.execute("SELECT status FROM reminders WHERE id=?",
                       (due_id,)).fetchone()["status"] == "sent"


def test_road_recompute_key_always_present_on_quiet_run(db, fake_deliver):
    counts = tick.reminders(db, now_utc=NOW, cfg=ROAD_CFG)
    assert counts == {"due": 0, "sent": 0, "quiet": 0, "budget": 0,
                       "error": 0, "cancelled": 0, "stale": 0,
                       "error_capped": 0, "road_recomputed": 0}


# ==== Task 7: fam tick digest ====
# NOW ("2026-07-20T04:30:00+00:00") is 2026-07-20T09:30 Almaty, so "today"
# for the digest is 2026-07-20 -- reused from the reminders section above.

WX = {
    "today": {"tmin": 19.0, "tmax": 33.0, "precip_mm": 0.0,
              "precip_hours": 0.0, "wind": 10.0},
    "tomorrow": {"tmin": 18.0, "tmax": 30.0, "precip_mm": 2.0,
                 "precip_hours": 3.0, "wind": 12.0},
}


def _fetch_wx(wx=WX):
    return lambda: wx


def _insert_gate_sent(db, ts_utc, payload):
    db.execute(
        "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
        (ts_utc, "gate.sent", "test", json.dumps(payload, ensure_ascii=False)),
    )


# ---- raw assembly ----

def test_digest_raw_shape_with_weather_and_event(db, fake_deliver):
    e = _event(db, title="Встреча", start="2026-07-20T10:00:00+00:00")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    call = fake_deliver.calls[0]
    assert call["kind"] == "digest"
    assert call["force"] is True
    raw = call["raw"]
    assert raw == {
        "kind": "digest",
        "date_local": "2026-07-20",
        "weather": WX,
        "events": [{"event_id": e["id"], "title": "Встреча",
                     "start_local": e["start_local"]}],
        "burning_plans": [],
        "busy_two_days": [{"start_local": e["start_local"],
                            "title": "Встреча"}],
        "meds": {"today": [], "missed_yesterday": [], "low_stock": []},
        "question": tick.DIGEST_QUESTION,
    }


def test_digest_raw_event_includes_place_name_when_present(db, fake_deliver):
    places.add(db, "Клиника")
    db.commit()
    e = _event(db, title="Врач", place="Клиника",
               start="2026-07-20T10:00:00+00:00")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    raw = fake_deliver.calls[0]["raw"]
    assert raw["events"] == [
        {"event_id": e["id"], "title": "Врач", "start_local": e["start_local"],
         "place_name": "Клиника"}
    ]


def test_digest_raw_weather_none_when_fetch_returns_none(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=lambda: None)

    assert fake_deliver.calls[0]["raw"]["weather"] is None


def test_digest_raw_events_empty_when_none_today(db, fake_deliver):
    # tomorrow, not today -- cal.day() must not pick it up
    _event(db, start="2026-07-21T05:00:00+00:00")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["events"] == []


def test_digest_raw_omits_cancelled_events(db, fake_deliver):
    # cal.day() is active-only by design (plan: right choice for a "what's
    # still on the plan today" digest) -- a cancelled event today must not
    # appear.
    e = _event(db, start="2026-07-20T10:00:00+00:00")
    cal.cancel(db, e["id"])
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["events"] == []


# ---- fallback text shape ----

def test_digest_fallback_includes_all_sections_and_question(db, fake_deliver):
    e = _event(db, title="Встреча", start="2026-07-20T10:00:00+00:00")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "2026-07-20" in fallback
    assert "19" in fallback and "33" in fallback and "без осадков" in fallback
    time_str = datetime.fromisoformat(e["start_local"]).strftime("%H:%M")
    assert f"{time_str} Встреча" in fallback
    assert fallback.rstrip().endswith(tick.DIGEST_QUESTION)
    assert len(fallback) <= 900


def test_digest_fallback_no_events_says_so(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "Событий нет" in fallback
    assert fallback.rstrip().endswith(tick.DIGEST_QUESTION)


def test_digest_fallback_omits_weather_section_when_none(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=lambda: None)

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "°C" not in fallback
    assert fallback.rstrip().endswith(tick.DIGEST_QUESTION)


def test_digest_fallback_mentions_precipitation_when_present(db, fake_deliver):
    wx = {"today": dict(WX["today"], precip_mm=5.0), "tomorrow": WX["tomorrow"]}
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=lambda: wx)

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "без осадков" not in fallback
    assert "осад" in fallback


# ---- dup guard: already sent today ----

def test_digest_dup_guard_skips_when_gate_sent_digest_already_today(db, fake_deliver):
    # 2026-07-20T02:00:00Z falls inside today's Almaty day bounds relative
    # to NOW (2026-07-19T19:00Z .. 2026-07-20T19:00Z). The guard's day
    # window is anchored to the real wall clock (Fix 3), so this test
    # injects _real_now=NOW to pin it to the same fixed instant as the
    # rest of this fixed-NOW test suite, rather than the actual clock.
    _insert_gate_sent(db, "2026-07-20T02:00:00+00:00", {"kind": "digest"})
    db.commit()
    fake_deliver.responses = []  # must not be called

    summary = tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx(),
                           _real_now=NOW)

    assert summary == {"skipped": "already_sent", "date_local": "2026-07-20"}
    assert fake_deliver.calls == []
    rows = audit.query(db, since_utc=None, kind_prefix="tick.digest",
                        grep=None, limit=10)
    assert len(rows) == 1
    assert rows[0]["payload"] == {"skipped": "already_sent",
                                   "date_local": "2026-07-20"}


def test_digest_dup_guard_ignores_gate_sent_reminder_kind(db, fake_deliver):
    # A reminder's gate.sent row (payload kind="reminder") must not trip
    # the digest's own dup guard -- only kind=="digest" counts.
    _insert_gate_sent(db, "2026-07-20T02:00:00+00:00", {"kind": "reminder"})
    db.commit()
    fake_deliver.responses = ["sent"]

    summary = tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx(),
                           _real_now=NOW)

    assert summary["status"] == "sent"
    assert len(fake_deliver.calls) == 1


def test_digest_dup_guard_ignores_yesterdays_digest(db, fake_deliver):
    _insert_gate_sent(db, "2026-07-19T10:00:00+00:00", {"kind": "digest"})
    db.commit()
    fake_deliver.responses = ["sent"]

    summary = tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx(),
                           _real_now=NOW)

    assert summary["status"] == "sent"


def test_digest_dup_guard_follows_real_clock_not_now_utc_override(db, fake_deliver):
    # Fix 3: audit.log() always stamps ts_utc from the REAL wall clock
    # (see audit.py), regardless of any now_utc a caller passes -- so a
    # live run's dup-guard window must be computed from the real clock
    # too, never from now_utc. Model a gate.sent digest row anchored to
    # the real current day (as audit.log() would actually write it in
    # production), then call digest() with now_utc pointing at
    # "yesterday" relative to the real clock -- e.g. a stale/incorrect
    # --now override on a live run. The guard must still see today's
    # real-clock row and skip; if the guard wrongly used now_utc for its
    # window instead, it would look at the wrong day and miss the row,
    # causing a live double-send.
    real_now = datetime.now(timezone.utc)
    _insert_gate_sent(db, real_now.isoformat(timespec="seconds"),
                       {"kind": "digest"})
    db.commit()
    stale_now_utc = (real_now - timedelta(days=1)).isoformat(timespec="seconds")
    fake_deliver.responses = []  # must not be called

    summary = tick.digest(db, now_utc=stale_now_utc, cfg=CFG,
                           _fetch_weather=_fetch_wx())

    assert summary["skipped"] == "already_sent"
    assert fake_deliver.calls == []


class _FakeRunOK:
    """Real gate.deliver, fake hermes subprocess -- exercises the dup
    guard end-to-end against the gate.sent row gate.deliver itself
    writes, unlike the fake_deliver-based tests above which never touch
    gate.py's own audit trail."""

    def __call__(self, args, **kwargs):
        if "-z" in args:
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="Доброе утро!", stderr="")
        if "send" in args:
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected hermes invocation: {args}")


def test_digest_real_second_run_same_day_is_skipped(db, monkeypatch):
    # Uses the REAL wall clock (no now_utc override) on purpose:
    # audit.log() always stamps ts_utc from the real clock, regardless of
    # any now_utc a caller passes to a domain function (see audit.py) --
    # so the gate.sent row gate.deliver writes here is real-time-stamped,
    # and this test's day-bounds must be anchored to that same real "now"
    # for the dup guard to see it. This matches production exactly:
    # systemd invokes `fam tick digest` with no --now override at all.
    monkeypatch.setattr(gate.subprocess, "run", _FakeRunOK())

    first = tick.digest(db, cfg=CFG, _fetch_weather=_fetch_wx())
    assert first["status"] == "sent"

    second = tick.digest(db, cfg=CFG, _fetch_weather=_fetch_wx())
    assert second["skipped"] == "already_sent"
    assert "date_local" in second


# ---- gate.deliver wiring: force=True, outside budget ----

def test_digest_uses_force_true_bypassing_quiet_and_budget(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["force"] is True
    assert fake_deliver.calls[0]["now_utc"] == NOW


# ---- always audits tick.digest ----

def test_digest_audits_tick_digest_with_status_on_sent(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    summary = tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert summary["status"] == "sent"
    rows = audit.query(db, since_utc=None, kind_prefix="tick.digest",
                        grep=None, limit=10)
    assert len(rows) == 1
    assert rows[0]["payload"]["status"] == "sent"
    assert rows[0]["payload"]["date_local"] == "2026-07-20"


def test_digest_audits_tick_digest_with_status_on_error(db, fake_deliver):
    fake_deliver.responses = ["error"]

    summary = tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert summary["status"] == "error"
    rows = audit.query(db, since_utc=None, kind_prefix="tick.digest",
                        grep=None, limit=10)
    assert rows[0]["payload"]["status"] == "error"


# ---- default now_utc / cfg wiring ----

def test_digest_uses_real_now_when_not_given(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    summary = tick.digest(db, cfg=CFG, _fetch_weather=_fetch_wx())

    assert summary["status"] == "sent"
    assert "date_local" in summary


def test_digest_loads_config_when_not_given(db, fake_deliver, monkeypatch, tmp_path):
    example = tmp_path / "fam-config.example.json"
    example.write_text(json.dumps(CFG, ensure_ascii=False), encoding="utf-8")
    target = tmp_path / "fam-config.json"
    monkeypatch.setattr(gate, "CONFIG_PATH", target)
    monkeypatch.setattr(gate, "CONFIG_EXAMPLE_PATH", example)
    fake_deliver.responses = ["sent"]

    summary = tick.digest(db, now_utc=NOW, _fetch_weather=_fetch_wx())

    assert summary["status"] == "sent"
    assert target.exists()


def test_digest_defaults_fetch_weather_to_weather_fetch_almaty(db, fake_deliver, monkeypatch):
    # Pins the production default injection point without ever touching
    # the network: weather.fetch_almaty itself is monkeypatched.
    from fam import weather as weather_mod
    monkeypatch.setattr(weather_mod, "fetch_almaty", lambda: WX)
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG)

    assert fake_deliver.calls[0]["raw"]["weather"] == WX
