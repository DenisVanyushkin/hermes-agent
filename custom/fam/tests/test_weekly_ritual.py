"""Weekly planning ritual: ISO-week helpers, the snapshot, the message.

The ritual asks on Sunday evening about the NEXT ISO week and repeats
once in Monday's digest about that same week -- which by then is the
current one. Like goals.compute_target_month, the target is a pure
calendar rule with no stored state, so an unanswered Sunday offer keeps
resolving to the same target once the calendar crosses midnight.
"""
import pytest

from fam import cal, plans, weekly


def _almaty_utc(date_local, hhmm="10:00"):
    """Local Almaty wall time -> the UTC ISO string cal.add stores."""
    from datetime import datetime, timezone
    from fam.gate import ALMATY
    y, m, d = (int(x) for x in date_local.split("-"))
    hh, mm = (int(x) for x in hhmm.split(":"))
    return (datetime(y, m, d, hh, mm, tzinfo=ALMATY)
            .astimezone(timezone.utc).isoformat(timespec="seconds"))


# --- calendar arithmetic ----------------------------------------------

@pytest.mark.parametrize("week", ["2026-W39", "2026-W53"])
def test_validate_week_accepts_real_weeks(week):
    """2026 really does have a W53 (28 Dec 2026 - 3 Jan 2027)."""
    assert weekly.validate_week(week) == "week"


@pytest.mark.parametrize("bad", [
    "2026-W00", "2026-W54", "2026-39", "2026-W9", "2026-w39",
    "2026", "", None, 20260939,
    "2021-W53",   # well-formed, but 2021 has only 52 ISO weeks
])
def test_validate_week_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        weekly.validate_week(bad)


@pytest.mark.parametrize("day,expected", [
    ("2026-09-20", "2026-W38"),   # Sunday ENDS its week
    ("2026-09-21", "2026-W39"),   # Monday starts the next
    ("2027-01-01", "2026-W53"),   # a Friday whose ISO year is 2026
])
def test_current_week(day, expected):
    assert weekly.current_week(day) == expected


def test_next_week_rolls_over_the_iso_year():
    """Stepping by number would invent a 2026-W54."""
    assert weekly.next_week("2026-W53") == "2027-W01"


@pytest.mark.parametrize("week,bounds", [
    ("2026-W39", ("2026-09-21", "2026-09-27")),
    ("2026-W53", ("2026-12-28", "2027-01-03")),
])
def test_week_bounds_run_monday_to_sunday(week, bounds):
    assert weekly.week_bounds(week) == bounds


@pytest.mark.parametrize("day", ["2026-09-20", "2026-09-21"])
def test_target_week_is_the_same_across_the_sunday_monday_handover(day):
    """Sunday's "next week" IS Monday's "this week" -- which is what
    lets the Monday repeat land on the week Sunday asked about, with no
    stored bookkeeping."""
    assert weekly.target_week(day) == "2026-W39"


def test_is_ritual_day_only_on_sunday():
    assert weekly.is_ritual_day("2026-09-20") is True
    assert weekly.is_ritual_day("2026-09-21") is False


# --- ritual state ------------------------------------------------------

def test_plan_state_round_trips(db):
    assert weekly.plan_state_get(db, "2026-W39") is None

    weekly.plan_state_set(db, "2026-W39", "offered", "2026-09-20")

    assert weekly.plan_state_get(db, "2026-W39") == ("offered", "2026-09-20")


def test_plan_state_rejects_unknown_status(db):
    with pytest.raises(ValueError):
        weekly.plan_state_set(db, "2026-W39", "maybe", "2026-09-20")


# --- the snapshot ------------------------------------------------------

def test_info_on_an_empty_base_still_names_the_target_week(db):
    info = weekly.info(db, "2026-09-20")
    assert info["target_week"] == "2026-W39"
    assert info["bounds"] == ("2026-09-21", "2026-09-27")
    assert info["state"] is None
    assert info["events"] == []
    assert info["tails"] == []


def test_info_window_is_the_target_weeks_almaty_days(db):
    """Both edges included, neighbours excluded -- and the edges are
    LOCAL midnights, so a 00:30 Monday and a 23:30 Sunday both fall
    inside even though plain UTC bounds would drop one."""
    cal.add(db, "Понедельник", _almaty_utc("2026-09-21", "00:30"))
    cal.add(db, "Воскресенье", _almaty_utc("2026-09-27", "23:30"))
    cal.add(db, "Сегодняшнее", _almaty_utc("2026-09-20"))
    cal.add(db, "Через две недели", _almaty_utc("2026-09-28"))
    db.commit()

    titles = sorted(e["title"] for e in weekly.info(db, "2026-09-20")["events"])
    assert titles == ["Воскресенье", "Понедельник"]


def test_a_plan_overdue_before_today_is_a_tail(db):
    plans.add(db, "Вчерашнее", deadline="2026-09-19")
    db.commit()

    tails = weekly.info(db, "2026-09-20")["tails"]

    assert [t["title"] for t in tails] == ["Вчерашнее"]
    assert tails[0]["deadline"] == "2026-09-19"


def test_a_plan_due_today_is_not_yet_a_tail(db):
    """Overdue means overdue TODAY, not "before the target week starts".
    The ritual runs on Sunday evening and the week begins next morning,
    so comparing against Monday would brand a task still due today as
    late. Matches tick._burning_plans (deadline < today)."""
    plans.add(db, "Сегодня ещё можно", deadline="2026-09-20")
    db.commit()

    assert weekly.info(db, "2026-09-20")["tails"] == []


def test_plans_that_are_not_tails(db):
    """Due inside the week, attached to an event, closed, or undated --
    none of them are leftovers from the week before."""
    plans.add(db, "Внутри недели", deadline="2026-09-24")
    plans.add(db, "Без срока", deadline=None)
    closed = plans.add(db, "Сделано", deadline="2026-09-18")
    plans.mark(db, closed, "done")
    ev = cal.add(db, "Дантист", _almaty_utc("2026-09-23"))
    attached = plans.add(db, "Забрать справку", deadline="2026-09-18")
    plans.attach(db, attached, ev["id"] if isinstance(ev, dict) else ev)
    db.commit()

    assert weekly.info(db, "2026-09-20")["tails"] == []


# --- the message -------------------------------------------------------

@pytest.mark.parametrize("today,expected", [
    ("2026-09-20", "21–27 сентября"),
    ("2026-09-27", "28 сентября — 4 октября"),   # spans two months
])
def test_question_text_names_the_week_range(db, today, expected):
    assert expected in weekly.question_text(weekly.info(db, today))


def test_question_text_says_the_week_is_empty_when_it_is(db):
    assert "пока пусто" in weekly.question_text(
        weekly.info(db, "2026-09-20")).lower()


def test_every_week_renders_as_one_line_per_day(db):
    """One rendering, however full the week: titles kept, height bounded
    at seven lines. 16 events as 16 lines is not a chat message; bare
    counts would drop the titles she answers against."""
    for day, times in (("2026-09-21", ("10:00", "14:00", "15:00", "19:00")),
                       ("2026-09-22", ("14:00",)),
                       ("2026-09-23", ("10:00", "15:15", "19:00")),
                       ("2026-09-24", ("12:30", "13:30", "14:00", "19:00")),
                       ("2026-09-25", ("10:00", "13:00", "15:15", "17:00"))):
        for hhmm in times:
            cal.add(db, f"Дело {day} {hhmm}", _almaty_utc(day, hhmm))
    db.commit()

    text = weekly.question_text(weekly.info(db, "2026-09-20"))

    day_lines = [ln for ln in text.splitlines()
                 if ln[:2] in ("пн", "вт", "ср", "чт", "пт", "сб", "вс")]
    assert len(day_lines) == 5
    assert "Свободны: сб, вс." in text


def test_a_days_events_share_its_line_with_the_tails_below(db):
    cal.add(db, "Тренировка", _almaty_utc("2026-09-23", "10:00"))
    cal.add(db, "Робототехника", _almaty_utc("2026-09-23", "15:15"))
    plans.add(db, "Забрать куртку", deadline="2026-09-18")
    db.commit()

    text = weekly.question_text(weekly.info(db, "2026-09-20"))

    assert "ср: Тренировка, Робототехника" in text
    assert "Забрать куртку" in text
