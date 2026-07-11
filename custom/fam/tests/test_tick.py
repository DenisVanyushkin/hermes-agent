from datetime import datetime, timedelta, timezone

import pytest

from fam import audit, cal, gate, people, places, tick

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
                       "error_capped": 0}
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
    }
    assert call["human_fallback"] == (
        f"пора выходить: Врач — {e['start_local']}"
    )
    assert call["now_utc"] == NOW


def test_raw_omits_place_name_when_no_place(db, fake_deliver):
    e = _event(db)
    db.commit()
    _insert_reminder(db, e["id"])
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert "place_name" not in fake_deliver.calls[0]["raw"]
    assert fake_deliver.calls[0]["raw"]["participants"] == []


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
                       "error_capped": 0}
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
                       "error_capped": 0}
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
                       "error_capped": 0}
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
    import json
    example.write_text(json.dumps(CFG, ensure_ascii=False), encoding="utf-8")
    target = tmp_path / "fam-config.json"
    monkeypatch.setattr(gate, "CONFIG_PATH", target)
    monkeypatch.setattr(gate, "CONFIG_EXAMPLE_PATH", example)
    fake_deliver.responses = []

    counts = tick.reminders(db, now_utc=NOW)

    assert counts["due"] == 0
    assert target.exists()
