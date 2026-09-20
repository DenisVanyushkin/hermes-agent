"""Weekly planning ritual: ISO-week helpers, target week, ritual state.

The ritual asks on Sunday evening about the NEXT ISO week, and repeats
once in Monday's digest about that same week -- which by then is the
current one. Like goals.compute_target_month, the target is a pure
calendar rule with no stored state: once the calendar crosses into
Monday, "next week" naturally becomes "this week", so an unanswered
Sunday offer keeps resolving to the same target.
"""
import pytest

from fam import weekly


# --- period helpers ---------------------------------------------------

def test_validate_week_accepts_iso_week():
    assert weekly.validate_week("2026-W39") == "week"


@pytest.mark.parametrize("bad", [
    "2026-W00", "2026-W54", "2026-39", "2026-W9", "2026-w39",
    "2026", "", None, 20260939,
])
def test_validate_week_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        weekly.validate_week(bad)


def test_validate_week_accepts_week_53():
    """2026 really has an ISO week 53 (28 Dec 2026 - 3 Jan 2027)."""
    assert weekly.validate_week("2026-W53") == "week"


def test_current_week_sunday_belongs_to_the_week_that_ends_today():
    # 2026-09-20 is a Sunday: ISO puts it at the END of week 38.
    assert weekly.current_week("2026-09-20") == "2026-W38"


def test_current_week_monday_starts_the_next_one():
    assert weekly.current_week("2026-09-21") == "2026-W39"


def test_current_week_uses_iso_year_not_calendar_year():
    """1 Jan 2027 is a Friday and still belongs to 2026-W53."""
    assert weekly.current_week("2027-01-01") == "2026-W53"


def test_next_week_rolls_over_the_iso_year():
    assert weekly.next_week("2026-W53") == "2027-W01"


def test_next_week_regular():
    assert weekly.next_week("2026-W39") == "2026-W40"


def test_week_bounds_is_monday_to_sunday():
    assert weekly.week_bounds("2026-W39") == ("2026-09-21", "2026-09-27")


def test_week_bounds_across_the_year_boundary():
    assert weekly.week_bounds("2026-W53") == ("2026-12-28", "2027-01-03")


# --- target week: the pure calendar rule ------------------------------

def test_target_week_on_sunday_is_the_week_that_starts_tomorrow():
    assert weekly.target_week("2026-09-20") == "2026-W39"


def test_target_week_on_monday_is_that_same_week():
    """The Monday repeat must land on the week Sunday asked about."""
    assert weekly.target_week("2026-09-21") == "2026-W39"


def test_target_week_is_stable_across_the_sunday_monday_boundary():
    assert weekly.target_week("2026-09-20") == weekly.target_week("2026-09-21")


def test_is_ritual_day_only_on_sunday():
    assert weekly.is_ritual_day("2026-09-20") is True
    assert weekly.is_ritual_day("2026-09-21") is False


# --- ritual state ------------------------------------------------------

def test_plan_state_is_none_before_any_cycle(db):
    assert weekly.plan_state_get(db, "2026-W39") is None


def test_plan_state_round_trips(db):
    weekly.plan_state_set(db, "2026-W39", "offered", "2026-09-20")
    assert weekly.plan_state_get(db, "2026-W39") == ("offered", "2026-09-20")


def test_plan_state_rejects_unknown_status(db):
    with pytest.raises(ValueError):
        weekly.plan_state_set(db, "2026-W39", "maybe", "2026-09-20")


def test_plan_state_rejects_a_non_week_period(db):
    with pytest.raises(ValueError):
        weekly.plan_state_set(db, "2026-09", "offered", "2026-09-20")


# --- the snapshot the Sunday message is built from --------------------

def _almaty_utc(date_local, hhmm="10:00"):
    """Local Almaty wall time -> the UTC ISO string cal.add stores."""
    from datetime import datetime
    from fam.gate import ALMATY
    from datetime import timezone as _tz
    y, m, d = (int(x) for x in date_local.split("-"))
    hh, mm = (int(x) for x in hhmm.split(":"))
    return (datetime(y, m, d, hh, mm, tzinfo=ALMATY)
            .astimezone(_tz.utc).isoformat(timespec="seconds"))


def test_info_on_an_empty_base_still_names_the_target_week(db):
    info = weekly.info(db, "2026-09-20")
    assert info["target_week"] == "2026-W39"
    assert info["bounds"] == ("2026-09-21", "2026-09-27")
    assert info["state"] is None
    assert info["events"] == []
    assert info["tails"] == []


def test_info_carries_the_recorded_state(db):
    weekly.plan_state_set(db, "2026-W39", "offered", "2026-09-20")
    assert weekly.info(db, "2026-09-20")["state"] == "offered"


def test_info_lists_events_inside_the_target_week(db):
    from fam import cal
    cal.add(db, "Тренировка", _almaty_utc("2026-09-23"))
    db.commit()
    titles = [e["title"] for e in weekly.info(db, "2026-09-20")["events"]]
    assert titles == ["Тренировка"]


def test_info_excludes_events_outside_the_target_week(db):
    from fam import cal
    # Sunday of the CURRENT week (today) and Monday of the week after.
    cal.add(db, "Сегодняшнее", _almaty_utc("2026-09-20"))
    cal.add(db, "Через две недели", _almaty_utc("2026-09-28"))
    db.commit()
    assert weekly.info(db, "2026-09-20")["events"] == []


def test_info_includes_the_boundary_days_of_the_week(db):
    from fam import cal
    cal.add(db, "Понедельник", _almaty_utc("2026-09-21", "00:30"))
    cal.add(db, "Воскресенье", _almaty_utc("2026-09-27", "23:30"))
    db.commit()
    titles = sorted(e["title"] for e in weekly.info(db, "2026-09-20")["events"])
    assert titles == ["Воскресенье", "Понедельник"]


def test_tails_are_open_plans_overdue_before_the_target_week(db):
    from fam import plans
    plans.add(db, "Забрать куртку", deadline="2026-09-18")
    db.commit()
    tails = weekly.info(db, "2026-09-20")["tails"]
    assert [t["title"] for t in tails] == ["Забрать куртку"]
    assert tails[0]["deadline"] == "2026-09-18"


def test_a_plan_due_inside_the_target_week_is_not_a_tail(db):
    from fam import plans
    plans.add(db, "Внутри недели", deadline="2026-09-24")
    db.commit()
    assert weekly.info(db, "2026-09-20")["tails"] == []


def test_a_plan_attached_to_an_event_is_not_a_tail(db):
    from fam import cal, plans
    ev = cal.add(db, "Дантист", _almaty_utc("2026-09-23"))
    pid = plans.add(db, "Забрать справку", deadline="2026-09-18")
    plans.attach(db, pid, ev["id"] if isinstance(ev, dict) else ev)
    db.commit()
    assert weekly.info(db, "2026-09-20")["tails"] == []


def test_a_closed_plan_is_not_a_tail(db):
    from fam import plans
    pid = plans.add(db, "Сделано", deadline="2026-09-18")
    plans.mark(db, pid, "done")
    db.commit()
    assert weekly.info(db, "2026-09-20")["tails"] == []


def test_a_plan_with_no_deadline_is_not_a_tail(db):
    """No deadline means no week to be late for -- rule 26 of the skill
    is what stops such plans being created during the ritual."""
    from fam import plans
    plans.add(db, "Когда-нибудь", deadline=None)
    db.commit()
    assert weekly.info(db, "2026-09-20")["tails"] == []


# --- the message text --------------------------------------------------

def test_question_text_names_the_week_range(db):
    text = weekly.question_text(weekly.info(db, "2026-09-20"))
    assert "21" in text and "27 сентября" in text


def test_question_text_spans_two_months_when_the_week_does(db):
    text = weekly.question_text(weekly.info(db, "2026-09-27"))
    assert "28 сентября" in text and "4 октября" in text


def test_question_text_lists_events_and_tails(db):
    from fam import cal, plans
    cal.add(db, "Тренировка", _almaty_utc("2026-09-23"))
    plans.add(db, "Забрать куртку", deadline="2026-09-18")
    db.commit()
    text = weekly.question_text(weekly.info(db, "2026-09-20"))
    assert "Тренировка" in text
    assert "Забрать куртку" in text


def test_question_text_ends_with_a_question(db):
    assert weekly.question_text(weekly.info(db, "2026-09-20")).rstrip().endswith("?")


def test_question_text_says_the_week_is_empty_when_it_is(db):
    text = weekly.question_text(weekly.info(db, "2026-09-20"))
    assert "пока пусто" in text.lower()


# --- a busy week must not arrive as a wall of text --------------------

def _seed_busy_week(db):
    from fam import cal
    # 16 events across Mon-Fri, the real shape of Amina's 2026-W39.
    for day, times in (("2026-09-21", ("10:00", "14:00", "15:00", "19:00")),
                       ("2026-09-22", ("14:00",)),
                       ("2026-09-23", ("10:00", "15:15", "19:00")),
                       ("2026-09-24", ("12:30", "13:30", "14:00", "19:00")),
                       ("2026-09-25", ("10:00", "13:00", "15:15", "17:00"))):
        for hhmm in times:
            cal.add(db, f"Дело {day} {hhmm}", _almaty_utc(day, hhmm))
    db.commit()


def test_a_busy_week_is_summarised_not_listed(db):
    """16 events must not become 16 bullet lines in a WhatsApp message."""
    _seed_busy_week(db)
    text = weekly.question_text(weekly.info(db, "2026-09-20"))
    assert len(text.splitlines()) <= 8


def test_a_busy_week_names_the_free_days(db):
    """Where to put something new is the point of the context."""
    _seed_busy_week(db)
    text = weekly.question_text(weekly.info(db, "2026-09-20"))
    assert "сб" in text and "вс" in text


def test_a_busy_week_still_counts_everything(db):
    _seed_busy_week(db)
    text = weekly.question_text(weekly.info(db, "2026-09-20"))
    assert "16" in text


def test_a_quiet_week_is_still_listed_in_full(db):
    """Few events -> naming them is shorter than summarising them."""
    from fam import cal
    cal.add(db, "Тренировка", _almaty_utc("2026-09-23"))
    cal.add(db, "Дантист", _almaty_utc("2026-09-24"))
    db.commit()
    text = weekly.question_text(weekly.info(db, "2026-09-20"))
    assert "Тренировка" in text and "Дантист" in text
