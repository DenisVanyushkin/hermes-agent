"""Phase 5 Task 4: persistent medication reminder series in the minute
tick -- unlike meds_gen (once a day), this is exercised as part of
tick.reminders() itself, at the very end (after event reminders and the
evening follow-up).

Follows test_tick.py's / test_tick_followup.py's FakeDeliver convention:
gate.deliver is monkeypatched so no real hermes subprocess is touched;
gate.py's own subprocess contract is exercised in test_gate.py.
"""
import json

import pytest

from fam import audit, cal, gate, meds, tick

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
    "med_repeat_min": 45,
}

# 2026-07-20, Asia/Almaty is UTC+5 (no DST): 15:00 Almaty -- ordinary
# daytime, never in the quiet window (21:30-07:30 Almaty).
NOW = "2026-07-20T10:00:00+00:00"
# 22:00 Almaty on the same day -- inside the quiet window.
QUIET_NOW = "2026-07-20T17:00:00+00:00"


class FakeDeliver:
    """Records every gate.deliver() call and returns canned statuses
    consumed in order -- mirrors test_tick.py's own FakeDeliver."""

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


def _insert_intake(db, med_id, plan_ts_utc, series_next_utc, status="pending",
                    created_at=None):
    created_at = created_at or plan_ts_utc
    cur = db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, taken_ts_utc, status, "
        "series_next_utc, created_at) VALUES (?,?,?,?,?,?)",
        (med_id, plan_ts_utc, None, status, series_next_utc, created_at),
    )
    return cur.lastrowid


def _med_calls(fake_deliver):
    return [c for c in fake_deliver.calls if c["kind"] == "med"]


# ---- due "take" send + series advance ----

def test_due_intake_is_delivered_and_series_advances_by_med_repeat_min(
        db, fake_deliver):
    med_id = meds.add(db, "Магний", ["08:00"], dose="1 таб")
    db.commit()
    plan_ts = "2026-07-20T03:00:00+00:00"  # 08:00 Almaty
    intake_id = _insert_intake(db, med_id, plan_ts, plan_ts)
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    calls = _med_calls(fake_deliver)
    assert len(calls) == 1
    assert calls[0]["force"] is True
    assert calls[0]["raw"]["mode"] == "take"
    assert calls[0]["raw"]["name"] == "Магний"
    assert calls[0]["raw"]["dose"] == "1 таб"

    row = db.execute(
        "SELECT status, series_next_utc FROM med_intakes WHERE id=?",
        (intake_id,),
    ).fetchone()
    assert row["status"] == "pending"
    # NOW (10:00 UTC) + 45 min = 10:45 UTC.
    assert row["series_next_utc"] == "2026-07-20T10:45:00+00:00"

    audit_rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.med'"
    ).fetchall()
    assert len(audit_rows) == 1
    payload = json.loads(audit_rows[0]["payload"])
    assert payload == {"intake_id": intake_id, "mode": "take", "status": "sent"}


def test_series_advances_even_when_deliver_returns_quiet(db, fake_deliver):
    # Brief: series_next_utc advances "при статусе sent/quiet/..." -- i.e.
    # regardless of gate.deliver's own outcome, not just on "sent".
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    plan_ts = "2026-07-20T03:00:00+00:00"
    intake_id = _insert_intake(db, med_id, plan_ts, plan_ts)
    fake_deliver.responses = ["quiet"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    row = db.execute(
        "SELECT status, series_next_utc FROM med_intakes WHERE id=?",
        (intake_id,),
    ).fetchone()
    assert row["status"] == "pending"
    assert row["series_next_utc"] == "2026-07-20T10:45:00+00:00"


def test_not_yet_due_intake_is_left_alone(db, fake_deliver):
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    plan_ts = "2026-07-20T03:00:00+00:00"
    future_next = "2099-01-01T00:00:00+00:00"
    intake_id = _insert_intake(db, med_id, plan_ts, future_next)

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert _med_calls(fake_deliver) == []
    row = db.execute(
        "SELECT series_next_utc FROM med_intakes WHERE id=?", (intake_id,)
    ).fetchone()
    assert row["series_next_utc"] == future_next


# ---- quiet hours: pause, don't move series ----

def test_quiet_hours_skips_send_and_does_not_move_series(db, fake_deliver):
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    plan_ts = "2026-07-20T03:00:00+00:00"
    intake_id = _insert_intake(db, med_id, plan_ts, plan_ts)

    tick.reminders(db, now_utc=QUIET_NOW, cfg=CFG)

    assert _med_calls(fake_deliver) == []
    row = db.execute(
        "SELECT status, series_next_utc FROM med_intakes WHERE id=?",
        (intake_id,),
    ).fetchone()
    assert row["status"] == "pending"
    assert row["series_next_utc"] == plan_ts
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='tick.med'"
    ).fetchone()[0] == 0


# ---- out of stock: one "buy" message, series cleared ----

def test_out_of_stock_sends_one_buy_message_and_clears_series(db, fake_deliver):
    med_id = meds.add(db, "Магний", ["08:00"], dose="1 таб", remaining=0,
                       threshold=5)
    db.commit()
    plan_ts = "2026-07-20T03:00:00+00:00"
    intake_id = _insert_intake(db, med_id, plan_ts, plan_ts)
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    calls = _med_calls(fake_deliver)
    assert len(calls) == 1
    assert calls[0]["force"] is True
    assert calls[0]["raw"]["mode"] == "out_of_stock"
    assert calls[0]["raw"]["name"] == "Магний"

    row = db.execute(
        "SELECT status, series_next_utc FROM med_intakes WHERE id=?",
        (intake_id,),
    ).fetchone()
    assert row["status"] == "pending"
    assert row["series_next_utc"] is None

    audit_rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.med'"
    ).fetchall()
    assert len(audit_rows) == 1
    assert json.loads(audit_rows[0]["payload"]) == {
        "intake_id": intake_id, "mode": "out_of_stock",
    }

    # A later tick the same day must not re-send: series_next_utc is now
    # NULL, so this row no longer matches the due-selection query at all.
    tick.reminders(db, now_utc="2026-07-20T11:00:00+00:00", cfg=CFG)
    assert len(_med_calls(fake_deliver)) == 1


def test_remaining_none_never_treated_as_out_of_stock(db, fake_deliver):
    # remaining=None means "not tracked" -- must take the ordinary "take"
    # path, never the out_of_stock one (only remaining == 0 counts).
    med_id = meds.add(db, "Магний", ["08:00"])  # remaining defaults to None
    db.commit()
    plan_ts = "2026-07-20T03:00:00+00:00"
    _insert_intake(db, med_id, plan_ts, plan_ts)
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    calls = _med_calls(fake_deliver)
    assert len(calls) == 1
    assert calls[0]["raw"]["mode"] == "take"


# ---- budget exemption ----

def _insert_gate_sent_at(db, ts_utc, payload):
    # audit.log() always stamps ts_utc from the real wall clock (see
    # tick.py's _digest_already_sent_today docstring), so a test that
    # needs a row inside a FROZEN Almaty day must insert it directly --
    # mirrors test_gate.py's own _insert_audit helper.
    db.execute(
        "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
        (ts_utc, "gate.sent", "test", json.dumps(payload, ensure_ascii=False)),
    )


def test_med_kind_gate_sent_does_not_count_toward_budget(db):
    _insert_gate_sent_at(db, NOW, {"kind": "med", "raw": {}})
    _insert_gate_sent_at(db, NOW, {"kind": "reminder", "raw": {"event_id": 1}})
    db.commit()

    assert gate.budget_spent_today(db, now_utc=NOW) == 1


# ---- exception guard: tick must not fall over ----

def test_meds_block_exception_is_caught_and_normal_reminders_still_sent(
        db, fake_deliver, monkeypatch):
    e = cal.add(db, "Событие", "2026-07-20T10:05:00+00:00")
    db.commit()
    db.execute(
        "INSERT INTO reminders(event_id, label, anchor, fire_at_utc, "
        "status, created_at) VALUES (?,?,?,?,?,?)",
        (e["id"], "напоминание", "start", "2026-07-20T09:55:00+00:00",
         "pending", "2026-07-20T09:55:00+00:00"),
    )
    db.commit()
    fake_deliver.responses = ["sent"]

    def boom(conn, now_utc, cfg):
        raise RuntimeError("meds series blew up")

    monkeypatch.setattr(tick, "_meds_series", boom)

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert isinstance(counts, dict)
    assert counts["sent"] == 1  # the ordinary event reminder still went out
    reminder_calls = [c for c in fake_deliver.calls if c["kind"] == "reminder"]
    assert len(reminder_calls) == 1

    rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.error'"
    ).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0]["payload"]) == {"where": "meds"}

    # tick.reminders' own summary audit row is still written after.
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='tick.reminders'"
    ).fetchone()[0] == 1
