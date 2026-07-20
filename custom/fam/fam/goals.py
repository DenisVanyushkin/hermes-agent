"""Goals: period-scoped targets (quarter -> month), no time/place lifecycle.

Domain functions never commit -- callers (tests, CLI) own the transaction,
mirroring plans.py's pattern. Every mutation logs to the audit trail in the
SAME transaction via audit.log (goal.add / goal.mark / goal.take).

Period helpers derive period_type from the period string itself (month
'YYYY-MM' or quarter 'YYYY-Qn') -- period_type is never passed in separately,
matching spec §3: "period_type выводится из формата period".
"""
import re
from datetime import datetime, timezone

from fam import audit
from fam.gate import ALMATY, _parse_utc

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_QUARTER_RE = re.compile(r"^\d{4}-Q[1-4]$")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_period(period):
    """Validate a period string and return its period_type ('month' or
    'quarter'). Raises ValueError on anything else -- before any insert,
    same "raise before any insert" contract as plans._validate_deadline.
    """
    if not isinstance(period, str):
        raise ValueError(f"invalid period: {period!r}")
    if _MONTH_RE.match(period):
        return "month"
    if _QUARTER_RE.match(period):
        return "quarter"
    raise ValueError(f"invalid period (expected YYYY-MM or YYYY-Qn): {period}")


def current_month(now_utc=None):
    """Current Asia/Almaty month as 'YYYY-MM'. now_utc: optional ISO UTC
    timestamp override (for tests); defaults to the real current time.
    Reuses gate.ALMATY/_parse_utc -- the same almaty-local-date machinery
    tick._today_almaty and gate._almaty_day_utc_bounds already use --
    instead of hand-rolling timezone math here.
    """
    if now_utc is None:
        now_utc = _now()
    local = _parse_utc(now_utc).astimezone(ALMATY)
    return f"{local.year:04d}-{local.month:02d}"


def next_month(m):
    """'YYYY-MM' -> next month's 'YYYY-MM'. December rolls to January of
    the following year.
    """
    if validate_period(m) != "month":
        raise ValueError(f"not a month period: {m}")
    year, month = int(m[:4]), int(m[5:7])
    if month == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{month + 1:02d}"


def prev_month(m):
    """'YYYY-MM' -> previous month's 'YYYY-MM'. January rolls back to
    December of the previous year.
    """
    if validate_period(m) != "month":
        raise ValueError(f"not a month period: {m}")
    year, month = int(m[:4]), int(m[5:7])
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def quarter_of(month_str):
    """'YYYY-MM' -> that month's quarter, 'YYYY-Qn'."""
    if validate_period(month_str) != "month":
        raise ValueError(f"not a month period: {month_str}")
    year, month = int(month_str[:4]), int(month_str[5:7])
    q = (month - 1) // 3 + 1
    return f"{year:04d}-Q{q}"


def is_first_month_of_quarter(month_str):
    """True when month_str is the first month of its quarter (01/04/07/10)."""
    if validate_period(month_str) != "month":
        raise ValueError(f"not a month period: {month_str}")
    month = int(month_str[5:7])
    return month in (1, 4, 7, 10)


def _row_to_dict(row):
    return dict(row)


def add(conn, title, period=None, parent=None, notes=""):
    """Create a goal. period defaults to the current Almaty month (the
    "хочу цель" mid-month case, spec §5). parent_goal_id is only valid
    when the NEW goal is a month goal AND the parent resolves to an
    existing quarter goal -- any other combination raises ValueError
    before any insert (spec §3: "parent_goal_id только у month-цели и
    только на quarter-цель").
    """
    if period is None:
        period = current_month()
    period_type = validate_period(period)

    parent_row = None
    if parent is not None:
        if period_type != "month":
            raise ValueError("parent_goal_id is only valid on a month goal")
        parent_row = conn.execute(
            "SELECT * FROM goals WHERE id=?", (parent,)
        ).fetchone()
        if parent_row is None:
            raise ValueError(f"unknown parent goal: {parent}")
        if parent_row["period_type"] != "quarter":
            raise ValueError("parent_goal_id must reference a quarter goal")

    now = _now()
    cur = conn.execute(
        "INSERT INTO goals(title, period_type, period, status, "
        "parent_goal_id, notes, created_at, closed_at) "
        "VALUES (?,?,?,?,?,?,?,NULL)",
        (title, period_type, period, "open",
         parent_row["id"] if parent_row else None, notes, now),
    )
    goal_id = cur.lastrowid

    audit.log(
        conn, "goal.add",
        {"id": goal_id, "title": title, "period": period,
         "period_type": period_type, "parent": parent, "notes": notes},
    )

    return goal_id


def get(conn, goal_id):
    """Fetch a goal by id, or None if unknown."""
    row = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def list_goals(conn, period=None, include_closed=False):
    """List goals. period=None defaults to open goals of the current
    Almaty month AND the current quarter (spec §5's CLI default). A
    given period filters to that single period_type/period pair.
    include_closed=True drops the status='open' filter in both cases.
    """
    if period is not None:
        period_type = validate_period(period)
        sql = "SELECT * FROM goals WHERE period_type=? AND period=?"
        params = [period_type, period]
        if not include_closed:
            sql += " AND status='open'"
        sql += " ORDER BY id"
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]

    month = current_month()
    quarter = quarter_of(month)
    sql = (
        "SELECT * FROM goals WHERE "
        "((period_type='month' AND period=?) OR "
        "(period_type='quarter' AND period=?))"
    )
    params = [month, quarter]
    if not include_closed:
        sql += " AND status='open'"
    sql += " ORDER BY period_type DESC, id"
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


_VALID_STATUSES = ("open", "done", "declined")


def mark(conn, goal_id, status):
    """Set a goal's status. open -> done|declined stamps closed_at (UTC
    ISO); done|declined -> open (reopen) clears closed_at. Any other
    transition (open->open, done->declined, declined->done, done->done,
    declined->declined) raises ValueError. Returns False on an unknown
    goal_id (no write, no audit); True on success.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")

    existing = conn.execute(
        "SELECT * FROM goals WHERE id=?", (goal_id,)
    ).fetchone()
    if existing is None:
        return False

    current = existing["status"]
    valid_transition = (
        (current == "open" and status in ("done", "declined"))
        or (current in ("done", "declined") and status == "open")
    )
    if not valid_transition:
        raise ValueError(f"invalid transition: {current} -> {status}")

    if status == "open":
        conn.execute(
            "UPDATE goals SET status=?, closed_at=NULL WHERE id=?",
            (status, goal_id),
        )
    else:
        conn.execute(
            "UPDATE goals SET status=?, closed_at=? WHERE id=?",
            (status, _now(), goal_id),
        )

    audit.log(conn, "goal.mark", {"id": goal_id, "status": status})

    return True


def take(conn, goal_id, period):
    """Move a goal to period P: sets period=P, status='open',
    closed_at=NULL -- one verb for both open-tail carry-forward and
    declined-tail revival (spec §5). Only month goals can be taken (a
    quarter goal's period_type never changes); the target period P must
    also be a month period. Returns False on an unknown goal_id (no
    write, no audit); raises ValueError on a non-month goal or a
    non-month target period.
    """
    existing = conn.execute(
        "SELECT * FROM goals WHERE id=?", (goal_id,)
    ).fetchone()
    if existing is None:
        return False

    if existing["period_type"] != "month":
        raise ValueError("only month goals can be taken")
    if validate_period(period) != "month":
        raise ValueError(f"take target must be a month period: {period}")

    conn.execute(
        "UPDATE goals SET period=?, status='open', closed_at=NULL WHERE id=?",
        (period, goal_id),
    )

    audit.log(conn, "goal.take", {"id": goal_id, "period": period})

    return True
