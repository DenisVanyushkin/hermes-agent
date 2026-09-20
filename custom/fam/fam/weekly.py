"""Weekly planning ritual: ISO weeks, ritual state, and the Sunday offer.

Unlike goals (quarter -> month targets), the weekly ritual creates no
entity of its own: it is a conversation that ends in ordinary `plans`
rows. So there is no weekly goal, no new table and no migration -- only
the calendar arithmetic, the ritual's own meta state, and the text.

Weeks are ISO weeks ('YYYY-Www', Monday..Sunday). The ISO YEAR is not
always the calendar year: 1 Jan 2027 is a Friday and belongs to
2026-W53, so every conversion here goes through date.isocalendar() /
date.fromisocalendar() rather than any month/year arithmetic.

Domain functions never commit -- callers (tests, CLI, tick) own the
transaction, mirroring plans.py and goals.py.
"""
import re
from datetime import date, timedelta

from fam import audit, cal, db, plans

_WEEK_RE = re.compile(r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$")

_VALID_PLAN_STATES = ("offered", "done", "declined")


def validate_week(period):
    """Validate an ISO week string and return its period_type ('week').

    Mirrors goals.validate_period's "raise before any insert" contract.
    The shape check alone is not enough: most years have 52 ISO weeks,
    so 2021-W53 is well-formed but does not exist. Accepting it here
    would only move the ValueError to whichever helper parsed it next,
    which is the opposite of what a validator is for -- hence the
    round-trip through fromisocalendar.
    """
    if not isinstance(period, str):
        raise ValueError(f"invalid week: {period!r}")
    if not _WEEK_RE.match(period):
        raise ValueError(f"invalid week (expected YYYY-Www): {period}")
    try:
        date.fromisocalendar(int(period[:4]), int(period[6:]), 1)
    except ValueError:
        raise ValueError(f"no such ISO week: {period}") from None
    return "week"


def _parse_week(period):
    """'YYYY-Www' -> (iso_year, iso_week) after validation."""
    validate_week(period)
    return int(period[:4]), int(period[6:])


def _format_week(iso_year, iso_week):
    return f"{iso_year:04d}-W{iso_week:02d}"


def current_week(date_local):
    """'YYYY-MM-DD' (Asia/Almaty calendar date) -> its ISO week.

    On a Sunday this is the week that ENDS today, since ISO weeks run
    Monday..Sunday -- which is exactly why the Sunday ritual targets
    next_week() rather than this one.
    """
    iso = date.fromisoformat(date_local).isocalendar()
    return _format_week(iso[0], iso[1])


def next_week(period):
    """'YYYY-Www' -> the following ISO week, rolling the ISO year over.

    Computed by stepping 7 days from the week's Monday rather than by
    incrementing the number, because the last week of an ISO year is
    52 or 53 depending on the year.
    """
    iso_year, iso_week = _parse_week(period)
    monday = date.fromisocalendar(iso_year, iso_week, 1) + timedelta(days=7)
    iso = monday.isocalendar()
    return _format_week(iso[0], iso[1])


def week_bounds(period):
    """'YYYY-Www' -> ('YYYY-MM-DD' Monday, 'YYYY-MM-DD' Sunday)."""
    iso_year, iso_week = _parse_week(period)
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def is_ritual_day(date_local):
    """True on Sunday -- the day the ritual offer goes out."""
    return date.fromisoformat(date_local).isocalendar()[2] == 7


def target_week(date_local):
    """Which week the ritual currently targets, given today's Almaty date.

    Sunday -> next week (the ritual gets ahead of the week it is about).
    Any other day -> the current week, which is what makes the Monday
    repeat land on the very week Sunday asked about: once the calendar
    crosses midnight, Sunday's "next week" IS Monday's "current week".
    Like goals.compute_target_month this is a pure calendar rule and
    deliberately reads no stored state, so an unanswered offer keeps
    resolving to the same target without any extra bookkeeping.
    """
    if is_ritual_day(date_local):
        return next_week(current_week(date_local))
    return current_week(date_local)


# --- ritual state ------------------------------------------------------

def plan_state_get(conn, week):
    """Read the ritual state for `week`. Returns (status, date_local)
    or None if no cycle has ever been recorded for that week.
    """
    validate_week(week)
    raw = db.meta_get(conn, f"weekly_plan_state:{week}")
    if raw is None:
        return None
    status, _, date_local = raw.partition(":")
    return status, date_local


def repeat_question(conn, date_local):
    """Monday's one repeat of an unanswered Sunday offer, or None.

    Returns a single line, not the whole context block: the digest
    passes it as raw["question"], which gate.deliver guarantees as the
    final line (see question_line).

    Due only on a Monday whose target week is still "offered". Three
    silences matter:
      * no state at all -> Sunday never actually asked (budget, error,
        or a week already answered before the ritual existed), so there
        is nothing to repeat and inventing a question would be worse
        than saying nothing;
      * done/declined -> answered, permanently quiet;
      * any day but Monday -> exactly one repeat. Tuesday onwards the
        week is left alone rather than nagged daily, which is where the
        monthly ritual's repeat-until-answered would become noise at
        weekly cadence.
    """
    if date.fromisoformat(date_local).isocalendar()[2] != 1:
        return None
    state_row = plan_state_get(conn, target_week(date_local))
    if state_row is None or state_row[0] != "offered":
        return None
    return "Неделю так и не спланировали — что в неё добавим?"


def mark(conn, date_local, status):
    """Close the ritual cycle for whatever week `date_local` targets.

    This is the verb the chat agent calls once the planning dialog
    ends -- without it the cycle would sit in "offered" forever, the
    plans created but the ritual never told it had been answered.
    Works even with no prior offer: she may start planning on her own
    before the ritual gets round to asking.
    """
    if status not in ("done", "declined"):
        raise ValueError(f"invalid weekly plan state: {status}")
    week = target_week(date_local)
    plan_state_set(conn, week, status, date_local)
    audit.log(conn, "weekly.mark", {"week": week, "status": status})
    return week


def plan_state_set(conn, week, status, today):
    """Stamp the ritual state for `week` with `today` ('YYYY-MM-DD',
    Asia/Almaty). status: offered|done|declined. Plain meta write, no
    audit row -- same rationale as goals.plan_state_set (the caller's
    own send/mark action is what gets audited).
    """
    if status not in _VALID_PLAN_STATES:
        raise ValueError(f"invalid plan state: {status}")
    validate_week(week)
    db.meta_set(conn, f"weekly_plan_state:{week}", f"{status}:{today}")


# --- the snapshot behind the Sunday message ---------------------------

_MONTH_GEN_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}

_WEEKDAY_SHORT_RU = {1: "пн", 2: "вт", 3: "ср", 4: "чт",
                     5: "пт", 6: "сб", 7: "вс"}


def _day_month_ru(date_local):
    """'YYYY-MM-DD' -> '27 сентября' (genitive, as in a date phrase)."""
    d = date.fromisoformat(date_local)
    return f"{d.day} {_MONTH_GEN_RU[d.month]}"


def week_label_ru(period):
    """'YYYY-Www' -> '21–27 сентября', or '28 сентября — 4 октября' when
    the week straddles two months (the month is only named once when
    both ends share it).
    """
    monday, sunday = week_bounds(period)
    start, end = date.fromisoformat(monday), date.fromisoformat(sunday)
    if start.month == end.month:
        return f"{start.day}–{end.day} {_MONTH_GEN_RU[end.month]}"
    return f"{_day_month_ru(monday)} — {_day_month_ru(sunday)}"


def _week_utc_bounds(period):
    """ISO week -> the UTC [from, to) covering its Almaty calendar days.

    Same math as tick._followup_day_bounds_utc, widened to seven days:
    an event at 00:30 Almaty on Monday and one at 23:30 on Sunday both
    have to fall inside, and neither does if the bounds are taken as
    plain UTC midnights.
    """
    from datetime import datetime, timezone
    from fam.gate import ALMATY
    monday, sunday = week_bounds(period)
    y, m, d = (int(x) for x in monday.split("-"))
    start = datetime(y, m, d, 0, 0, 0, tzinfo=ALMATY)
    end = start + timedelta(days=7)
    return (start.astimezone(timezone.utc).isoformat(timespec="seconds"),
            end.astimezone(timezone.utc).isoformat(timespec="seconds"))


def info(conn, date_local):
    """Snapshot the Sunday ritual message is built from.

    `events` -- active events already sitting in the target week, so she
    answers against the real week rather than into the void.
    `tails` -- open, unattached plans already overdue TODAY: the ones
    the previous week did not close. Overdue is measured against
    date_local, not against the target week's Monday -- the ritual runs
    on Sunday evening and the week starts next morning, so comparing
    against Monday would brand a task still due today as late. This is
    the same rule tick._burning_plans uses (deadline < today).
    A plan with no deadline is not a tail (it belongs to no week at all)
    and, per the skill's ritual rule, must not be created by the ritual
    in the first place -- an undated plan never surfaces in the digest.
    """
    target = target_week(date_local)
    monday, _sunday = week_bounds(target)
    today = date.fromisoformat(date_local)
    state_row = plan_state_get(conn, target)

    from_utc, to_utc = _week_utc_bounds(target)
    events = cal.list_range(conn, from_utc, to_utc, status="active")

    tails = []
    for plan in plans.list_open(conn):
        if plan.get("attached_event_id") is not None:
            continue
        deadline = plan.get("deadline")
        if deadline is None:
            continue
        try:
            overdue = date.fromisoformat(deadline) < today
        except (TypeError, ValueError):
            continue
        if overdue:
            tails.append({"plan_id": plan["id"], "title": plan["title"],
                          "deadline": deadline})

    return {
        "target_week": target,
        "bounds": (monday, _sunday),
        "label": week_label_ru(target),
        "state": state_row[0] if state_row else None,
        "events": events,
        "tails": tails,
    }



# Above this many events, naming them all turns a chat message into a
# wall of text -- 2026-W39 really held 16. Past the threshold the block
# switches to load-per-day plus the free days, which is what actually
# answers "where do I put something new".
_LIST_THRESHOLD = 4


def _plural_dela(n):
    """Russian count form for 'дело': 1 дело, 2 дела, 5 дел."""
    if 11 <= n % 100 <= 14:
        return "дел"
    last = n % 10
    if last == 1:
        return "дело"
    if last in (2, 3, 4):
        return "дела"
    return "дел"


def _events_block(events):
    """The context lines about the week's calendar."""
    if not events:
        return ["В календаре пока пусто."]

    if len(events) <= _LIST_THRESHOLD:
        block = ["Уже в календаре:"]
        for event in events:
            day_part, _, time_part = event["start_local"].partition("T")
            weekday = _WEEKDAY_SHORT_RU[date.fromisoformat(day_part).isocalendar()[2]]
            block.append(f"• {weekday} {_day_month_ru(day_part)}, "
                         f"{time_part[:5]} — {event['title']}")
        return block

    per_day = {}
    for event in events:
        day_part = event["start_local"].partition("T")[0]
        weekday = date.fromisoformat(day_part).isocalendar()[2]
        per_day[weekday] = per_day.get(weekday, 0) + 1

    busy = [f"{_WEEKDAY_SHORT_RU[wd]} {per_day[wd]}"
            for wd in range(1, 8) if per_day.get(wd)]
    free = [_WEEKDAY_SHORT_RU[wd] for wd in range(1, 8) if not per_day.get(wd)]

    block = [f"{len(events)} {_plural_dela(len(events))}: " + ", ".join(busy) + "."]
    if free:
        block.append("Свободны: " + ", ".join(free) + ".")
    return block


def question_text(snapshot):
    """The Sunday message: the week as it already stands, then the ask.

    Context first, question last -- she answers against what is really
    there. The closing line always ends in a question mark, which is
    what the gate's style pass keys the "expects a reply" shape on.
    """
    lines = [f"Неделя {snapshot['label']}."]

    lines.append("")
    lines.extend(_events_block(snapshot["events"]))

    if snapshot["tails"]:
        lines.append("")
        lines.append("С прошлой недели висят:")
        for tail in snapshot["tails"]:
            lines.append(f"• {tail['title']} — срок был "
                         f"{_day_month_ru(tail['deadline'])}")

    lines.append("")
    lines.append(question_line(snapshot))

    return "\n".join(lines)


def question_line(snapshot):
    """Just the closing question, as ONE line.

    gate.deliver treats raw["question"] as the message's guaranteed last
    line: it re-appends it verbatim after the LLM rewrite, and on the
    shorten path strips it off, compresses only the informational part
    and glues the question back. So this must be the question alone --
    handing it the whole context block would print the week summary a
    second time.
    """
    if snapshot["tails"]:
        return ("Что запланируем на неделю, и что делать с хвостами — "
                "перенести или закрыть?")
    return "Что запланируем на неделю?"
