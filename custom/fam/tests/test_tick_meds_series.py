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


# ---- quiet hours: series fires through, per Denis's 2026-07-16 go-live
# decision (was "pause, don't move series" before that) ----

def test_quiet_hours_no_longer_pauses_send_or_series(db, fake_deliver):
    # Was test_quiet_hours_skips_send_and_does_not_move_series before
    # Denis's 2026-07-16 go-live decision: a dose due at 22:00 Almaty
    # (inside the 21:30-07:30 quiet window) used to be skipped by this
    # loop and then marked missed by meds_gen's midnight closeout -- a
    # scheduled dose was silently never delivered at all. Now the whole
    # series (initial + escalations) fires through quiet hours instead.
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    plan_ts = "2026-07-20T03:00:00+00:00"
    intake_id = _insert_intake(db, med_id, plan_ts, plan_ts)
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=QUIET_NOW, cfg=CFG)

    calls = _med_calls(fake_deliver)
    assert len(calls) == 1
    assert calls[0]["force"] is True
    assert calls[0]["raw"]["mode"] == "take"
    row = db.execute(
        "SELECT status, series_next_utc FROM med_intakes WHERE id=?",
        (intake_id,),
    ).fetchone()
    assert row["status"] == "pending"
    # QUIET_NOW (17:00 UTC) + 45 min = 17:45 UTC -- series keeps advancing.
    assert row["series_next_utc"] == "2026-07-20T17:45:00+00:00"
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='tick.med'"
    ).fetchone()[0] == 1


def test_med_series_escalation_also_fires_through_quiet_hours(db, fake_deliver):
    # Denis's decision explicitly covers "+45-min escalations" too, not
    # just the very first reminder -- exercise a dose whose series is
    # already mid-escalation (series_next_utc != plan_ts) to prove the
    # repeat path isn't paused by quiet hours either.
    med_id = meds.add(db, "Магний", ["22:00"], dose="1 таб")
    db.commit()
    plan_ts = "2026-07-20T17:00:00+00:00"  # 22:00 Almaty
    series_next = "2026-07-20T17:30:00+00:00"  # 22:30 Almaty, first escalation
    intake_id = _insert_intake(db, med_id, plan_ts, series_next)
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc="2026-07-20T17:30:00+00:00", cfg=CFG)

    calls = _med_calls(fake_deliver)
    assert len(calls) == 1
    assert calls[0]["force"] is True
    assert calls[0]["raw"]["mode"] == "take"
    row = db.execute(
        "SELECT status, series_next_utc FROM med_intakes WHERE id=?",
        (intake_id,),
    ).fetchone()
    assert row["status"] == "pending"
    assert row["series_next_utc"] == "2026-07-20T18:15:00+00:00"  # +45 min


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
        "intake_id": intake_id, "mode": "out_of_stock", "deduped": False,
    }

    # A later tick the same day must not re-send: series_next_utc is now
    # NULL, so this row no longer matches the due-selection query at all.
    tick.reminders(db, now_utc="2026-07-20T11:00:00+00:00", cfg=CFG)
    assert len(_med_calls(fake_deliver)) == 1


def test_out_of_stock_notice_once_per_med_per_day(db, fake_deliver, monkeypatch):
    # Finding 10 (go-live review): a multi-dose schedule (times=08:00,
    # 20:00) opens one med_intakes row PER dose (meds_gen's own
    # contract), so without a per-med/per-day dedup, an out-of-stock med
    # would nag "надо купить" twice in the same day -- once per dose.
    # The dedup key is med_oos_sent:<med_id>:<Almaty date>, same raw-SQL
    # meta pattern _followup already uses for its own once-a-day guard.
    # The 08:00 Almaty tick falls inside Task 6's digest-retry window
    # (07:40-12:00), so neutralize the hook to keep this test hermetic --
    # otherwise it would trigger a live digest() -> Open-Meteo fetch.
    monkeypatch.setattr(tick, "_digest_retry", lambda *a, **k: None)
    med_id = meds.add(db, "Витамин", ["08:00", "20:00"], remaining=0,
                       threshold=5)
    db.commit()
    morning_ts = "2026-07-20T03:00:00+00:00"  # 08:00 Almaty
    evening_ts = "2026-07-20T15:00:00+00:00"  # 20:00 Almaty, same Almaty day
    morning_id = _insert_intake(db, med_id, morning_ts, morning_ts)
    evening_id = _insert_intake(db, med_id, evening_ts, evening_ts)
    fake_deliver.responses = ["sent", "sent"]

    tick.reminders(db, now_utc=morning_ts, cfg=CFG)  # first dose due

    calls = _med_calls(fake_deliver)
    assert len(calls) == 1
    assert calls[0]["raw"]["mode"] == "out_of_stock"
    morning_row = db.execute(
        "SELECT series_next_utc FROM med_intakes WHERE id=?", (morning_id,)
    ).fetchone()
    assert morning_row["series_next_utc"] is None  # series still closes

    tick.reminders(db, now_utc=evening_ts, cfg=CFG)  # second dose due, same day

    # Second dose's series is still closed, but no second "надо купить" --
    # gate.deliver was only ever called once across both ticks.
    calls = _med_calls(fake_deliver)
    assert len(calls) == 1
    evening_row = db.execute(
        "SELECT series_next_utc FROM med_intakes WHERE id=?", (evening_id,)
    ).fetchone()
    assert evening_row["series_next_utc"] is None

    audit_rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.med' ORDER BY id"
    ).fetchall()
    assert len(audit_rows) == 2
    assert json.loads(audit_rows[0]["payload"]) == {
        "intake_id": morning_id, "mode": "out_of_stock", "deduped": False,
    }
    assert json.loads(audit_rows[1]["payload"]) == {
        "intake_id": evening_id, "mode": "out_of_stock", "deduped": True,
    }


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


# ---- disabled med: series stops silently, digest already excludes it ----
# (5 T9 final review, FIX-1) ----

def test_disabled_med_is_not_sent_and_series_cleared(db, fake_deliver):
    # Skill contract: "stop reminding me about X" -> fam meds edit <id>
    # --enabled 0. Before this fix, _meds_series ignored enabled
    # entirely and kept nagging "пора принять" every med_repeat_min
    # until midnight. A disabled med must never be delivered, and its
    # series must not keep re-arming itself.
    med_id = meds.add(db, "Магний", ["08:00"], dose="1 таб")
    meds.edit(db, med_id, enabled=0)
    db.commit()
    plan_ts = "2026-07-20T03:00:00+00:00"
    intake_id = _insert_intake(db, med_id, plan_ts, plan_ts)

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert _med_calls(fake_deliver) == []
    row = db.execute(
        "SELECT status, series_next_utc FROM med_intakes WHERE id=?",
        (intake_id,),
    ).fetchone()
    assert row["status"] == "pending"
    assert row["series_next_utc"] is None

    # A later tick the same day must not re-send either: series_next_utc
    # is now NULL, so this row no longer matches the due-selection query.
    tick.reminders(db, now_utc="2026-07-20T11:00:00+00:00", cfg=CFG)
    assert _med_calls(fake_deliver) == []


def test_disabled_med_does_not_raise_and_no_tick_error(db, fake_deliver):
    # Disabling a med mid-series must be a silent no-op, never an
    # uncaught exception that would sink the whole tick (module
    # docstring's guard-per-hook contract, and the brief's own "не
    # падать" requirement).
    med_id = meds.add(db, "Магний", ["08:00"])
    meds.edit(db, med_id, enabled=0)
    db.commit()
    plan_ts = "2026-07-20T03:00:00+00:00"
    _insert_intake(db, med_id, plan_ts, plan_ts)

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert isinstance(counts, dict)
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='tick.error'"
    ).fetchone()[0] == 0


# ---- ack↔tick race guard (5 T9 final review, FIX-2 / Backlog #5) ----

def _race_ack(intake_id, sentinel_next_utc):
    """A fake gate.deliver that plants a concurrent ack on intake_id
    right in the window between _meds_series's own SELECT (already
    read, so the row is status='pending' in the caller's hand) and its
    own UPDATE -- gate.deliver is exactly where that window sits in
    production, since a real send takes real wall-clock time. Sets a
    sentinel series_next_utc (not NULL, unlike a real ack) so the test
    can tell "the guarded UPDATE left this alone" apart from "the
    guarded UPDATE also happened to write NULL".
    """
    def _deliver(conn, kind, raw, human_fallback, cfg, force=False,
                 now_utc=None):
        conn.execute(
            "UPDATE med_intakes SET status='taken', series_next_utc=? "
            "WHERE id=?",
            (sentinel_next_utc, intake_id),
        )
        return "sent"
    return _deliver


def test_ack_race_take_branch_update_is_guarded_by_status(db, monkeypatch):
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    plan_ts = "2026-07-20T03:00:00+00:00"
    intake_id = _insert_intake(db, med_id, plan_ts, plan_ts)
    sentinel = "2030-01-01T00:00:00+00:00"
    monkeypatch.setattr(gate, "deliver", _race_ack(intake_id, sentinel))

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    row = db.execute(
        "SELECT status, series_next_utc FROM med_intakes WHERE id=?",
        (intake_id,),
    ).fetchone()
    # The take-branch UPDATE must not clobber the mid-flight ack: status
    # stays 'taken' (it always would -- this UPDATE never touches
    # status) and series_next_utc keeps the race's sentinel rather than
    # being advanced by med_repeat_min, which is what an unguarded
    # UPDATE ... WHERE id=? would do.
    assert row["status"] == "taken"
    assert row["series_next_utc"] == sentinel


def test_ack_race_out_of_stock_branch_update_is_guarded_by_status(
        db, monkeypatch):
    med_id = meds.add(db, "Магний", ["08:00"], remaining=0, threshold=5)
    db.commit()
    plan_ts = "2026-07-20T03:00:00+00:00"
    intake_id = _insert_intake(db, med_id, plan_ts, plan_ts)
    sentinel = "2030-01-01T00:00:00+00:00"
    monkeypatch.setattr(gate, "deliver", _race_ack(intake_id, sentinel))

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    row = db.execute(
        "SELECT status, series_next_utc FROM med_intakes WHERE id=?",
        (intake_id,),
    ).fetchone()
    assert row["status"] == "taken"
    assert row["series_next_utc"] == sentinel


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
    payload = json.loads(rows[0]["payload"])
    assert payload["where"] == "meds"
    assert "meds series blew up" in payload["error"]

    # tick.reminders' own summary audit row is still written after.
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='tick.reminders'"
    ).fetchone()[0] == 1


def test_one_bad_med_row_does_not_defer_the_other(db, fake_deliver, monkeypatch):
    # Backlog: _meds_series used to wrap its whole per-med loop in a
    # single try/except (at the tick.reminders() call site) -- one bad
    # row's exception would sink the remaining meds for that minute,
    # deferring them to next tick. It must be a PER-ROW guard instead:
    # each med's own processing is isolated so one bad row can't take
    # down the others in the same tick.
    bad_med_id = meds.add(db, "Плохой", ["08:00"])
    good_med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    plan_ts = "2026-07-20T03:00:00+00:00"
    bad_intake_id = _insert_intake(db, bad_med_id, plan_ts, plan_ts)
    good_intake_id = _insert_intake(db, good_med_id, plan_ts, plan_ts)
    db.commit()  # both intakes exist as durably-committed rows before the
    # tick runs, same as real meds_gen-inserted rows would -- otherwise
    # the fix's conn.rollback() (connection-wide, not row-scoped) would
    # also undo this fixture's own still-uncommitted sibling insert,
    # which isn't a real-world scenario.
    fake_deliver.responses = ["sent"]

    real_get = meds.get

    def _boom_for_bad(conn, med_id):
        if med_id == bad_med_id:
            raise RuntimeError("boom on bad med")
        return real_get(conn, med_id)

    monkeypatch.setattr(meds, "get", _boom_for_bad)

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    calls = _med_calls(fake_deliver)
    assert len(calls) == 1
    assert calls[0]["raw"]["name"] == "Магний"

    good_row = db.execute(
        "SELECT status, series_next_utc FROM med_intakes WHERE id=?",
        (good_intake_id,),
    ).fetchone()
    assert good_row["status"] == "pending"
    assert good_row["series_next_utc"] == "2026-07-20T10:45:00+00:00"

    # The bad row is left untouched (still due) -- it was never
    # processed, not silently marked done -- but the error IS audited.
    bad_row = db.execute(
        "SELECT series_next_utc FROM med_intakes WHERE id=?",
        (bad_intake_id,),
    ).fetchone()
    assert bad_row["series_next_utc"] == plan_ts

    error_rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.error'"
    ).fetchall()
    assert len(error_rows) == 1
    payload = json.loads(error_rows[0]["payload"])
    assert payload["where"] == "meds_row"
    assert payload["intake_id"] == bad_intake_id
    assert "boom on bad med" in payload["error"]


def test_failure_after_update_but_before_audit_leaves_no_partial_commit(
        db, fake_deliver, monkeypatch):
    # Review finding (Important, atomicity gap in the per-row guard added
    # by the backlog fix above): the row's own UPDATE to series_next_utc
    # happens BEFORE its own audit.log("tick.med", ...) call. If audit.log
    # itself raises -- after the UPDATE already ran in this same
    # (uncommitted) transaction -- the except handler must not let that
    # partial UPDATE survive. The row must be left exactly as it was
    # found (series_next_utc unchanged), a tick.error must still be
    # recorded for it, and the OTHER due med in the same tick must still
    # be processed and committed normally.
    bad_med_id = meds.add(db, "Плохой", ["08:00"])
    good_med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    plan_ts = "2026-07-20T03:00:00+00:00"
    bad_intake_id = _insert_intake(db, bad_med_id, plan_ts, plan_ts)
    good_intake_id = _insert_intake(db, good_med_id, plan_ts, plan_ts)
    db.commit()
    fake_deliver.responses = ["sent", "sent"]

    real_log = audit.log

    def _boom_on_bad_tick_med(conn, kind, payload, actor="agent"):
        if kind == "tick.med" and payload.get("intake_id") == bad_intake_id:
            raise RuntimeError("boom on tick.med audit")
        return real_log(conn, kind, payload, actor=actor)

    monkeypatch.setattr(audit, "log", _boom_on_bad_tick_med)

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    # (a) the bad row's series_next_utc is UNCHANGED -- no partial
    # persist of the UPDATE that ran before the audit.log raise.
    bad_row = db.execute(
        "SELECT status, series_next_utc FROM med_intakes WHERE id=?",
        (bad_intake_id,),
    ).fetchone()
    assert bad_row["status"] == "pending"
    assert bad_row["series_next_utc"] == plan_ts

    # (b) a tick.error was still recorded for the bad row.
    error_rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.error'"
    ).fetchall()
    assert len(error_rows) == 1
    payload = json.loads(error_rows[0]["payload"])
    assert payload["where"] == "meds_row"
    assert payload["intake_id"] == bad_intake_id
    assert "boom on tick.med audit" in payload["error"]

    # (c) the other (good) med was still processed and committed.
    good_row = db.execute(
        "SELECT status, series_next_utc FROM med_intakes WHERE id=?",
        (good_intake_id,),
    ).fetchone()
    assert good_row["status"] == "pending"
    assert good_row["series_next_utc"] == "2026-07-20T10:45:00+00:00"

    calls = _med_calls(fake_deliver)
    assert len(calls) == 2
