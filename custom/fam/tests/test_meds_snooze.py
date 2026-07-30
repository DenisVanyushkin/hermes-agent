"""⏰-реакция на напоминании о лекарстве -- отложить дозу на час.

Мотив (спека 2026-07-29): "принято" и "пропускаю" доступны одним тапом,
а "позже" требует набрать текст -- и именно в этом сценарии чаще всего
возникает раздражение.
"""
import pytest

from fam import meds, react

NOW = "2026-07-20T05:00:00+00:00"   # 10:00 Алматы


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
