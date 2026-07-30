"""⏰-реакция на напоминании о лекарстве -- отложить дозу на час.

Мотив (спека 2026-07-29): "принято" и "пропускаю" доступны одним тапом,
а "позже" требует набрать текст -- и именно в этом сценарии чаще всего
возникает раздражение.

Fix round 1 (Finding 3): run_hook's "handled" boundary was never covered
for ⏰, which is exactly why Finding 1 (run_hook forgot "snoozed" in its
handled tuple -> a real snooze fell through to an agent turn) slipped
past review. test_hook_* below close that gap.
"""
import io
import json

import pytest

from fam import meds, react

NOW = "2026-07-20T05:00:00+00:00"   # 10:00 Алматы


def _run_hook(db, event):
    out = io.StringIO()
    rc = react.run_hook(stdin=io.StringIO(json.dumps(event)), stdout=out,
                        connect=lambda: db)
    return rc, json.loads(out.getvalue())


def _intake(db, name="Эутирокс"):
    med_id = meds.add(db, name, ["09:00"], remaining=10)
    cur = db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, status, "
        "series_next_utc, created_at) VALUES(?,?,'pending',?,?)",
        (med_id, "2026-07-20T04:00:00+00:00", "2026-07-20T04:00:00+00:00",
         "2026-07-20T04:00:00+00:00"))
    db.commit()
    return cur.lastrowid


def test_snooze_defers_by_an_hour(db):
    iid = _intake(db)
    react.record_sent(db, "wa1", "med", iid)
    db.commit()

    out = react.handle(db, "wa1", "⏰", now_utc=NOW)

    assert out["result"] == "snoozed"
    row = db.execute("SELECT * FROM med_intakes WHERE id=?", (iid,)).fetchone()
    assert row["status"] == "pending", "snooze не закрывает дозу"
    assert row["series_next_utc"] == "2026-07-20T06:00:00+00:00"
    assert row["deferred_until_utc"] == "2026-07-20T06:00:00+00:00"


def test_snooze_on_reminder_kind_is_ignored(db):
    # ⏰ имеет смысл только для лекарств; на событийном напоминании
    # ack-семантики нет.
    react.record_sent(db, "wa2", "reminder", 1, event_id=1)
    db.commit()
    out = react.handle(db, "wa2", "⏰", now_utc=NOW)
    assert out["result"] == "ignored"


def test_snooze_clamped_before_almaty_midnight(db):
    # 18:30 UTC = 23:30 Алматы; +60 минут ушло бы за полночь, что
    # meds.defer запрещает (столкновение с завтрашней дозой).
    iid = _intake(db)
    react.record_sent(db, "wa3", "med", iid)
    db.commit()

    out = react.handle(db, "wa3", "⏰", now_utc="2026-07-20T18:30:00+00:00")

    assert out["result"] == "snoozed"
    row = db.execute("SELECT * FROM med_intakes WHERE id=?", (iid,)).fetchone()
    # прижато к 23:59 Алматы = 18:59 UTC
    assert row["series_next_utc"] == "2026-07-20T18:59:00+00:00"


def test_snooze_in_the_last_minute_of_the_day_is_not_a_success(db):
    """Final review, Blocker 3: 23:59:30 Алматы -- клэмп на 23:59:00 уже
    в прошлом, meds.defer отвергает цель, отложить некуда. Раньше это
    отдавалось как "snooze_moot", то есть handled=true + ✅: ложная
    положительная обратная связь за полное отсутствие эффекта."""
    iid = _intake(db)
    react.record_sent(db, "wa-late", "med", iid)
    db.commit()

    out = react.handle(db, "wa-late", "⏰",
                       now_utc="2026-07-20T18:59:30+00:00")

    assert out["result"] == "snooze_too_late"
    assert out["deferred"] == 0
    row = db.execute("SELECT * FROM med_intakes WHERE id=?", (iid,)).fetchone()
    assert row["status"] == "pending"
    # серия не тронута, дозу по-прежнему закроет полуночный closeout
    assert row["series_next_utc"] == "2026-07-20T04:00:00+00:00"
    assert row["deferred_until_utc"] is None


def test_hook_marks_snooze_too_late_handled_but_without_a_checkmark(db,
                                                                   monkeypatch):
    """Реакция съедена (в LLM-диалог ей нельзя), но ✅ не ставится:
    checkmark означал бы "перенесено", а перенесено ничего."""
    monkeypatch.setattr(react, "handle",
                        lambda *a, **k: {"result": "snooze_too_late"})

    rc, payload = _run_hook(db, {"target_message_id": "wa-x", "emoji": "⏰"})

    assert rc == 0
    assert payload["handled"] is True, (
        "запоздавшая ⏰ всё равно не должна становиться ходом агента")
    assert "react" not in payload, "успех рапортовать нечем"
    assert payload["result"] == "snooze_too_late"


def test_snooze_group_defers_every_member(db):
    a = _intake(db, "Эутирокс")
    b = _intake(db, "Магний")
    react.record_sent(db, "wa4", "med", a, ref_ids=[a, b])
    db.commit()

    react.handle(db, "wa4", "⏰", now_utc=NOW)

    for iid in (a, b):
        row = db.execute("SELECT series_next_utc FROM med_intakes WHERE id=?",
                         (iid,)).fetchone()
        assert row["series_next_utc"] == "2026-07-20T06:00:00+00:00"


def test_snooze_emoji_is_in_dialogue_whitelist():
    # ИНВАРИАНТ: фильтр DIALOGUE_EMOJI срабатывает ДО react-hook, поэтому
    # эмодзи, добавленный только сюда, молча никогда не доедет.
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    src = (root / "plugins/platforms/whatsapp/reactions.py").read_text()
    for emoji in react.EMOJI_SNOOZE:
        assert emoji in src, f"{emoji!r} отсутствует в DIALOGUE_EMOJI"


# ---- run_hook boundary (fix round 1, Finding 1 / Finding 3) ----
#
# handle() returning "snoozed" is not enough on its own: run_hook maps
# handle()'s result onto {"handled": bool} for the adapter, and it is
# THAT boolean the adapter's _apply_reaction_event branches on. Before
# this fix round, run_hook's handled tuple only listed
# ("confirmed", "skipped", "already_acked") -- "snoozed" fell through to
# handled=False, and a false "handled" makes the adapter dispatch the
# reaction to the agent (_dispatch_reaction_dialogue), spending an LLM
# turn on what was already a fully-applied deterministic snooze. That
# violates this module's own "no LLM in the reaction path" contract.

def test_hook_marks_snoozed_as_handled(db):
    med_id = meds.add(db, "Эутирокс", ["09:00"], remaining=10)
    cur = db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, status, "
        "series_next_utc, created_at) VALUES(?,?,'pending',?,?)",
        (med_id, "2026-07-20T04:00:00+00:00", "2026-07-20T04:00:00+00:00",
         "2026-07-20T04:00:00+00:00"))
    iid = cur.lastrowid
    react.record_sent(db, "wa-hook-snooze", "med", iid)
    db.commit()

    rc, payload = _run_hook(
        db, {"target_message_id": "wa-hook-snooze", "emoji": "⏰"})

    assert rc == 0
    assert payload["handled"] is True, (
        "a real snooze must not fall through to the agent-turn path")
    assert payload["result"] == "snoozed"
    assert payload["react"] == react.FEEDBACK_EMOJI


def test_hook_marks_snooze_moot_as_handled(db):
    # The dose left 'pending' through another door (a verbal "выпила")
    # before the ⏰ reaction arrived -- meds.defer has nothing left to
    # do, but the reaction was still consumed and must not reach the
    # agent either.
    med_id = meds.add(db, "Эутирокс", ["09:00"], remaining=10)
    cur = db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, status, "
        "series_next_utc, created_at) VALUES(?,?,'pending',?,?)",
        (med_id, "2026-07-20T04:00:00+00:00", "2026-07-20T04:00:00+00:00",
         "2026-07-20T04:00:00+00:00"))
    iid = cur.lastrowid
    react.record_sent(db, "wa-hook-moot", "med", iid)
    db.commit()
    meds.take(db, iid)
    db.commit()

    rc, payload = _run_hook(
        db, {"target_message_id": "wa-hook-moot", "emoji": "⏰"})

    assert rc == 0
    assert payload["handled"] is True, (
        "a moot snooze was still consumed and must not reach the agent")
    assert payload["result"] == "snooze_moot"
