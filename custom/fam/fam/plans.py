"""Plans: dela-without-time (deadline-only, no calendar slot).

Domain functions never commit — callers (tests, CLI) own the transaction,
mirroring cal.py/people.py/places.py's pattern. place/person refs are
resolved via places.resolve()/people.resolve() (id/name/alias, same
resolvers cal.py uses) -- an unresolvable ref raises ValueError, before
any insert.
"""
from datetime import date, datetime, timezone

from fam import audit, people, places, road


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_deadline(value):
    """deadline must be a real YYYY-MM-DD date, or None. Raises
    ValueError with a human-readable message otherwise -- same
    "raise before any insert" contract as _resolve_place/_resolve_person,
    and the same ValueError -> CLI exit 2 path (see cli.main's
    except ValueError), matching the unknown-place/unknown-person
    error UX (Final review Finding 1).
    """
    if value is None:
        return
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid deadline (expected YYYY-MM-DD): {value}")


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
    unresolvable ref raises ValueError and nothing is inserted. deadline,
    if given, must be a real YYYY-MM-DD date -- a malformed value raises
    ValueError (before any insert) rather than being stored as-is (Final
    review Finding 1: tick._burning_plans parses deadline with
    date.fromisoformat and would otherwise crash the daily digest on a
    bad value). Returns the new plan's id.
    """
    pl = _resolve_place(conn, place)
    pe = _resolve_person(conn, person)
    _validate_deadline(deadline)

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
    False on an unknown plan_id or an unknown event_id (no write, no
    audit); True on success. Mirrors mark()'s unknown-id contract: a
    bad id is reported via return value, not an exception -- the FK
    (PRAGMA foreign_keys=ON in db.connect) would otherwise surface an
    sqlite3.IntegrityError for an unknown event_id.
    """
    existing = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if existing is None:
        return False

    event = conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone()
    if event is None:
        return False

    conn.execute(
        "UPDATE plans SET attached_event_id=? WHERE id=?",
        (event_id, plan_id),
    )
    audit.log(conn, "plan.attach", {"id": plan_id, "event_id": event_id})
    return True


def match_enroute(conn, event, cfg, now_utc=None, route=None):
    """Which open plans are 'on the way' for this event.

    Two independent reasons a plan can match, both checked against every
    open (not yet attached) plan:

    - geo: plan has a place with lat/lon, the event has a resolvable
      route (road.route_for_event doesn't return "none"), and the
      plan's place is within corridor distance of that route --
      cfg["enroute_walk_km"] (default 0.5) when event.transport == "walk",
      else cfg["enroute_car_km"] (default 3.0).
    - person: plan.person_id is among the event's participants.

    Never raises. Returns a list of {"plan": <dict>, "reason": "geo"|"person"},
    ordered like plans.list_open(). A plan matching both reasons is
    reported once with reason "geo" (geo takes priority in the dedup).

    route: optional pre-computed (route_points, source) tuple (same
    shape road.route_for_event returns). When given, it is used instead
    of calling road.route_for_event -- lets a caller (tick.reminders)
    compute the route once per event and share it with
    shopping.match_enroute, instead of each matcher hitting TomTom
    separately (B1). When omitted, behavior is unchanged: this function
    calls road.route_for_event itself.
    """
    open_plans = [p for p in list_open(conn) if p.get("attached_event_id") is None]
    if not open_plans:
        return []

    event_id = event.get("id")
    participant_ids = {
        r["person_id"] for r in conn.execute(
            "SELECT person_id FROM event_participants WHERE event_id=?",
            (event_id,),
        ).fetchall()
    }

    def _effective_place(plan):
        """The plan's own place, or -- when the plan has no place but does
        have a person -- that person's home place. Lets a homebound plan
        (e.g. "return Aisha's book" with no explicit place) match on the
        route via where the person lives, same as an explicit-place plan.
        """
        place = plan.get("place")
        if place is None and plan.get("person_id"):
            person = people.get(conn, plan["person_id"])
            place = person.get("home_place") if person else None
        return place

    route_points = None
    if any(_effective_place(p) and _effective_place(p).get("lat") is not None
           and _effective_place(p).get("lon") is not None for p in open_plans):
        if route is not None:
            route_points, source = route
        else:
            route_points, source = road.route_for_event(conn, event, cfg, now_utc=now_utc)
        if source == "none":
            route_points = None

    if event.get("transport") == "walk":
        threshold_km = cfg.get("enroute_walk_km", 0.5)
    else:
        threshold_km = cfg.get("enroute_car_km", 3.0)

    results = []
    for plan in open_plans:
        reason = None

        place = _effective_place(plan)
        if route_points and place and place.get("lat") is not None and place.get("lon") is not None:
            dist_km = road.point_to_route_km(place["lat"], place["lon"], route_points)
            if dist_km <= threshold_km:
                reason = "geo"

        if reason is None and plan.get("person_id") in participant_ids:
            reason = "person"

        if reason is not None:
            results.append({"plan": plan, "reason": reason})

    return results
