"""`plan due` -- move an open plan's deadline.

The weekly ritual's tails need this: "перенеси на среду" was previously
impossible without dropping the plan and re-adding it, which loses the
row's id, its created_at and its audit history.
"""
import pytest

from fam import audit, plans


def test_reschedule_moves_the_deadline(db):
    pid = plans.add(db, "Забрать куртку", deadline="2026-09-18")
    db.commit()

    plans.reschedule(db, pid, "2026-09-24")
    db.commit()

    assert plans.get(db, pid)["deadline"] == "2026-09-24"


def test_reschedule_keeps_the_same_row(db):
    pid = plans.add(db, "Забрать куртку", deadline="2026-09-18")
    db.commit()
    created = plans.get(db, pid)["created_at"]

    plans.reschedule(db, pid, "2026-09-24")
    db.commit()

    after = plans.get(db, pid)
    assert after["id"] == pid
    assert after["created_at"] == created
    assert after["status"] == "open"


def test_reschedule_can_clear_the_deadline(db):
    pid = plans.add(db, "Когда-нибудь", deadline="2026-09-18")
    db.commit()

    plans.reschedule(db, pid, None)
    db.commit()

    assert plans.get(db, pid)["deadline"] is None


def test_reschedule_validates_the_deadline_before_writing(db):
    pid = plans.add(db, "Забрать куртку", deadline="2026-09-18")
    db.commit()

    with pytest.raises(ValueError):
        plans.reschedule(db, pid, "24.09.2026")

    assert plans.get(db, pid)["deadline"] == "2026-09-18"


def test_reschedule_returns_false_for_an_unknown_plan(db):
    assert plans.reschedule(db, 999, "2026-09-24") is False


def test_reschedule_is_audited(db):
    pid = plans.add(db, "Забрать куртку", deadline="2026-09-18")
    db.commit()

    plans.reschedule(db, pid, "2026-09-24")
    db.commit()

    kinds = [r["kind"] for r in db.execute("SELECT kind FROM audit_log")]
    assert "plan.reschedule" in kinds
