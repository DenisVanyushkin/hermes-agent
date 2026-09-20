"""`plan due` -- move an open plan's deadline.

The weekly ritual's tails need this: before it existed, "перенеси на
среду" could only be done by dropping the plan and adding a new one,
which loses the row id, its created_at and its audit history.
"""
import pytest

from fam import plans


def test_reschedule_moves_the_deadline_keeping_the_same_row(db):
    """The point of the verb: a new date, everything else untouched."""
    pid = plans.add(db, "Забрать куртку", deadline="2026-09-18")
    db.commit()
    created = plans.get(db, pid)["created_at"]

    assert plans.reschedule(db, pid, "2026-09-24") is True
    db.commit()

    after = plans.get(db, pid)
    assert after["deadline"] == "2026-09-24"
    assert after["id"] == pid
    assert after["created_at"] == created
    assert after["status"] == "open"
    assert "plan.reschedule" in [r[0] for r in
                                 db.execute("SELECT kind FROM audit_log")]


@pytest.mark.parametrize("closed_status", ["done", "dropped"])
def test_reschedule_refuses_a_closed_plan(db, closed_status):
    """A closed plan's deadline records when it HAD been due; rewriting
    it falsifies history, and `fam plan due` on one is a mistyped id far
    more often than an intent."""
    pid = plans.add(db, "Закрытое", deadline="2026-09-18")
    plans.mark(db, pid, closed_status)
    db.commit()

    assert plans.reschedule(db, pid, "2026-09-24") is False
    assert plans.get(db, pid)["deadline"] == "2026-09-18"


def test_reschedule_validates_the_deadline_before_writing(db):
    """Same "raise before any write" contract as plans.add."""
    pid = plans.add(db, "Забрать куртку", deadline="2026-09-18")
    db.commit()

    with pytest.raises(ValueError):
        plans.reschedule(db, pid, "24.09.2026")

    assert plans.get(db, pid)["deadline"] == "2026-09-18"


def test_reschedule_returns_false_for_an_unknown_plan(db):
    assert plans.reschedule(db, 999, "2026-09-24") is False
