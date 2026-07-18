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


def add(conn, title, place=None, person=None, deadline=None, notes="",
        prep_for_event=None, prep_when=None):
    """Create a plan. place/person are text refs (id/name/alias/slug); an
    unresolvable ref raises ValueError and nothing is inserted. deadline,
    if given, must be a real YYYY-MM-DD date -- a malformed value raises
    ValueError (before any insert) rather than being stored as-is (Final
    review Finding 1: tick._burning_plans parses deadline with
    date.fromisoformat and would otherwise crash the daily digest on a
    bad value). Returns the new plan's id.

    prep_for_event/prep_when mark this plan as "prep" for a calendar
    event -- something that needs doing ahead of the event, not the
    event itself (e.g. "собрать документы" before a doctor visit).
    Both or neither must be given (ValueError otherwise); prep_when must
    be "date" (deadline required) or "departure" (deadline optional --
    the prep has no fixed date of its own, it just needs doing before
    departure). prep_for_event must resolve to a real event (ValueError
    on an unknown id), checked -- like place/person -- before any insert.
    On success, the referenced event's prep_asked flag is set to 1 (if
    not already), so a caller can tell "has prep already been asked
    about for this event" without re-querying plans.
    """
    pl = _resolve_place(conn, place)
    pe = _resolve_person(conn, person)
    _validate_deadline(deadline)

    if (prep_for_event is None) != (prep_when is None):
        raise ValueError("prep_for_event and prep_when go together")

    ev = None
    if prep_for_event is not None:
        if prep_when not in ("date", "departure"):
            raise ValueError(f"invalid prep_when: {prep_when}")
        if prep_when == "date" and deadline is None:
            raise ValueError("prep 'date' plan requires a deadline")
        from fam import cal
        ev = cal.get(conn, int(prep_for_event))
        if ev is None:
            raise ValueError(f"unknown event: {prep_for_event}")

    now = _now()
    cur = conn.execute(
        "INSERT INTO plans(title, place_id, person_id, deadline, status, "
        "notes, created_at, prep_for_event_id, prep_when) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (title, pl["id"] if pl else None, pe["id"] if pe else None,
         deadline, "open", notes, now,
         ev["id"] if ev else None, prep_when),
    )
    plan_id = cur.lastrowid

    audit.log(
        conn, "plan.add",
        {"id": plan_id, "title": title, "place": place, "person": person,
         "deadline": deadline, "notes": notes,
         "prep_for_event": prep_for_event, "prep_when": prep_when},
    )

    if ev is not None and not ev.get("prep_asked"):
        conn.execute("UPDATE events SET prep_asked=1 WHERE id=?", (ev["id"],))

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

    Phase 7b (detours): attached_via_points only ever considers OPEN
    plans, so marking a done/dropped plan that was attached_event_id-
    linked to an event drops it out of that event's waypoint set on the
    very next recompute -- but nothing re-triggers that recompute on its
    own. Same fix as attach(): when the plan being marked done/dropped
    carries an attached_event_id, recompute + regenerate that event in
    this same transaction (recompute first, so the regen sees the
    now-direct travel_min_road) so the route collapses back to direct
    immediately instead of drifting stale until the next unrelated
    trigger. Re-opening a plan (status='open') is the symmetric case --
    a still-attached plan re-entering the OPEN set puts its waypoint
    BACK on the event's route -- so it triggers the same recompute+
    regenerate for the attached event.
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

    if status in ("done", "dropped", "open") and existing["attached_event_id"] is not None:
        from fam import cal, rem
        cal.recompute_road(conn, existing["attached_event_id"])
        rem.regenerate(conn, existing["attached_event_id"])

    return True


def attach(conn, plan_id, event_id):
    """Attach a plan to a calendar event (sets attached_event_id). Returns
    False on an unknown plan_id or an unknown event_id (no write, no
    audit); True on success. Mirrors mark()'s unknown-id contract: a
    bad id is reported via return value, not an exception -- the FK
    (PRAGMA foreign_keys=ON in db.connect) would otherwise surface an
    sqlite3.IntegrityError for an unknown event_id.

    Phase 7b (detours): once attached, an OPEN plan becomes a candidate
    waypoint for the event's road route (road.route_for_event/
    compute_travel_min pick it up via attached_via_points below) -- so
    the event's road figure and reminder chain are stale the instant
    this returns unless recomputed. Same transaction, same order cal.py
    itself uses (recompute BEFORE regen, so the regen reads the fresh
    travel_min_road): cal.recompute_road(conn, event_id) then
    rem.regenerate(conn, event_id). Local imports (cal imports road at
    module level, and this mirrors add()'s existing local `from fam
    import cal` below) to avoid a module-load cycle.

    Re-attach: when the plan was ALREADY attached to a different event,
    that OLD event's route just lost its waypoint too -- so the same
    recompute+regenerate runs for the old event first (its crook comes
    off), then for the new one (its crook goes on). Attaching to the
    same event it's already on skips the redundant old-event pass.
    """
    existing = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if existing is None:
        return False

    event = conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone()
    if event is None:
        return False

    old_event_id = existing["attached_event_id"]

    conn.execute(
        "UPDATE plans SET attached_event_id=? WHERE id=?",
        (event_id, plan_id),
    )
    audit.log(conn, "plan.attach", {"id": plan_id, "event_id": event_id})

    from fam import cal, rem
    if old_event_id is not None and old_event_id != event_id:
        cal.recompute_road(conn, old_event_id)
        rem.regenerate(conn, old_event_id)
    cal.recompute_road(conn, event_id)
    rem.regenerate(conn, event_id)

    return True


def effective_place(conn, plan):
    """The plan's own place, or -- when the plan has no place but does
    have a person -- that person's home place. Lets a homebound plan
    (e.g. "return Aisha's book" with no explicit place) match on the
    route via where the person lives, same as an explicit-place plan.
    Extracted from match_enroute's original inline closure (Phase 7b,
    Task 2) so attached_via_points below can share the exact same
    resolution rule instead of duplicating it.
    """
    place = plan.get("place")
    if place is None and plan.get("person_id"):
        person = people.get(conn, plan["person_id"])
        place = person.get("home_place") if person else None
    return place


def attached_via_points(conn, event):
    """Coordinates of every OPEN plan attached to event (attached_event_id
    == event["id"]), via each plan's effective_place() -- its own place,
    or its person's home place. Phase 7b (detours): these are the
    waypoints road.route_for_event/compute_travel_min detour the route
    through, so travel_min_road grows by however much the stop costs.

    Guard: a via point identical to the event's own place is skipped --
    detouring "through" the destination isn't a waypoint. Identity is
    checked two ways since either can be the only information available:
    same place_id (when both plan and event resolve to the same places
    row), or, failing that, identical lat/lon (covers a plan whose
    effective place is a different places row that happens to sit at the
    exact same coordinates as the event's place).

    Plans without a resolvable lat/lon effective place are skipped
    silently -- same "as before, no via" behavior a place-less plan had
    prior to this feature. Never raises. Returns a list of (lat, lon)
    tuples, ordered by plan id.
    """
    event_id = event.get("id")
    if event_id is None:
        return []

    event_place = event.get("place") or {}
    event_place_id = event_place.get("id")
    event_lat, event_lon = event_place.get("lat"), event_place.get("lon")

    rows = conn.execute(
        "SELECT id FROM plans WHERE attached_event_id=? AND status='open' "
        "ORDER BY id",
        (event_id,),
    ).fetchall()

    points = []
    for row in rows:
        plan = get(conn, row["id"])
        place = effective_place(conn, plan)
        if place is None or place.get("lat") is None or place.get("lon") is None:
            continue
        if event_place_id is not None and place.get("id") == event_place_id:
            continue
        if (event_lat is not None and event_lon is not None
                and place["lat"] == event_lat and place["lon"] == event_lon):
            continue
        points.append((place["lat"], place["lon"]))
    return points


def _is_event_place(event_place, place):
    """True when `place` is the same as event_place -- by id when both
    resolve to a places row, else by identical lat/lon. Shared identity
    check between attached_via_points' via-guard and detours' candidate
    filter (Phase 7b, Task 3) so "detouring through the destination"
    is excluded the same way in both places.
    """
    if place is None:
        return False
    event_place = event_place or {}
    event_place_id = event_place.get("id")
    if event_place_id is not None and place.get("id") == event_place_id:
        return True
    event_lat, event_lon = event_place.get("lat"), event_place.get("lon")
    if (event_lat is not None and event_lon is not None
            and place.get("lat") == event_lat and place.get("lon") == event_lon):
        return True
    return False


def detours(conn, event, cfg, matches=None):
    """Detour candidates for event: open, not-yet-attached plans that
    match_enroute's "geo" reason found on the way, whose effective place
    differs from the event's own place, with a live (TomTom-only)
    detour_min between cfg's detour_offer_min_min and detour_max_min
    inclusive. Never raises externally -- a missing/failed direct leg or
    per-candidate detour simply drops that candidate (or yields []).

    Backs `fam cal detours <event_id>` (CLI, Phase 7b Task 3) and the
    first-prepare-stage detour offer in tick.reminders(). Returns a list
    of {"plan": <dict>, "detour_min": <int>}, ordered like match_enroute
    (plan id order).

    matches: optional pre-computed match_enroute(conn, event, cfg, ...)
    result. tick.reminders() already calls match_enroute once per due
    leave/prepare reminder (and shares its route with shopping.match_
    enroute) -- passing that same result in here avoids a SECOND
    match_enroute call (and the road.route_for_event/TomTom spend it may
    trigger) for the exact same event on the exact same tick. When
    omitted (the CLI path, a one-off manual lookup), match_enroute is
    called here instead.

    Budget: regardless of how many geo candidates there are, the direct
    home->event leg is fetched from TomTom at most ONCE per call (via
    road.direct_leg_min), then reused (road.detour_min's direct_min=)
    for every candidate's via leg -- not once per candidate.
    """
    if matches is None:
        matches = match_enroute(conn, event, cfg)

    event_place = event.get("place") or {}
    candidates = []
    for m in matches:
        if m["reason"] != "geo":
            continue
        plan = m["plan"]
        place = effective_place(conn, plan)
        if place is None or place.get("lat") is None or place.get("lon") is None:
            continue
        if _is_event_place(event_place, place):
            continue
        candidates.append((plan, place))

    if not candidates:
        return []

    direct_min = road.direct_leg_min(conn, event, cfg)
    if direct_min is None:
        return []

    lo = cfg.get("detour_offer_min_min", 2)
    hi = cfg.get("detour_max_min", 30)

    results = []
    for plan, place in candidates:
        d = road.detour_min(conn, event, place, cfg, direct_min=direct_min)
        if d is None:
            continue
        if lo <= d <= hi:
            results.append({"plan": plan, "detour_min": d})
    return results


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
        return effective_place(conn, plan)

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
