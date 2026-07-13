"""Plans: dela-without-time (deadline-only, no calendar slot).

Domain functions never commit — callers (tests, CLI) own the transaction,
mirroring cal.py/people.py/places.py's pattern. place/person refs are
resolved via places.resolve()/people.resolve() (id/name/alias, same
resolvers cal.py uses) -- an unresolvable ref raises ValueError, before
any insert.
"""
from datetime import datetime, timezone

from fam import audit, people, places


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve_place(conn, ref):
    if ref is None:
        return None
    pl = places.resolve(conn, ref)
    if pl is None:
        raise ValueError(f"unknown place: {ref}")
    return pl


def _resolve_person(conn, ref):
    if ref is None:
        return None
    p = people.resolve(conn, ref)
    if p is None:
        raise ValueError(f"unknown person: {ref}")
    return p


def add(conn, title, place=None, person=None, deadline=None, notes=""):
    """Create a plan. place/person are text refs (id/name/alias/slug); an
    unresolvable ref raises ValueError and nothing is inserted. Returns
    the new plan's id.
    """
    pl = _resolve_place(conn, place)
    pe = _resolve_person(conn, person)

    now = _now()
    cur = conn.execute(
        "INSERT INTO plans(title, place_id, person_id, deadline, status, "
        "notes, created_at) VALUES (?,?,?,?,?,?,?)",
        (title, pl["id"] if pl else None, pe["id"] if pe else None,
         deadline, "open", notes, now),
    )
    plan_id = cur.lastrowid

    audit.log(
        conn, "plan.add",
        {"id": plan_id, "title": title, "place": place, "person": person,
         "deadline": deadline, "notes": notes},
    )

    return plan_id


def _row_to_dict(conn, row):
    d = dict(row)
    d["place"] = places.get(conn, d["place_id"]) if d["place_id"] else None
    d["person"] = people.get(conn, d["person_id"]) if d["person_id"] else None
    return d


def get(conn, plan_id):
    """Fetch a plan with joined place/person, or None if unknown."""
    row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        return None
    return _row_to_dict(conn, row)


def list_open(conn):
    """List plans with status='open', joined with place/person, ordered by
    deadline (NULLs last), then created_at.
    """
    rows = conn.execute(
        "SELECT * FROM plans WHERE status='open' "
        "ORDER BY (deadline IS NULL), deadline, created_at"
    ).fetchall()
    return [_row_to_dict(conn, r) for r in rows]


def list_all(conn):
    """List every plan regardless of status, joined with place/person,
    ordered by deadline (NULLs last), then created_at.
    """
    rows = conn.execute(
        "SELECT * FROM plans ORDER BY (deadline IS NULL), deadline, created_at"
    ).fetchall()
    return [_row_to_dict(conn, r) for r in rows]


def mark(conn, plan_id, status):
    """Set a plan's status (open|done|dropped). 'done' also stamps
    done_at; any other status leaves done_at untouched. Returns False on
    an unknown plan_id (no write, no audit); True on success.
    """
    existing = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if existing is None:
        return False

    if status == "done":
        conn.execute(
            "UPDATE plans SET status=?, done_at=? WHERE id=?",
            (status, _now(), plan_id),
        )
    else:
        conn.execute("UPDATE plans SET status=? WHERE id=?", (status, plan_id))

    audit.log(conn, "plan.mark", {"id": plan_id, "status": status})
    return True


def attach(conn, plan_id, event_id):
    """Attach a plan to a calendar event (sets attached_event_id). Returns
    False on an unknown plan_id (no write, no audit); True on success.
    Does not validate event_id against the events table (mirrors the
    brief's minimal contract; callers that need the guarantee should
    check cal.get() first, as cli.py's cmd_plan_attach does).
    """
    existing = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if existing is None:
        return False

    conn.execute(
        "UPDATE plans SET attached_event_id=? WHERE id=?",
        (event_id, plan_id),
    )
    audit.log(conn, "plan.attach", {"id": plan_id, "event_id": event_id})
    return True
