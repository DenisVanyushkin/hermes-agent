"""Calendar events: CRUD with glossary resolution (people/places).

Domain functions never commit — callers (tests, CLI) own the transaction,
mirroring people.py/places.py's pattern.

Events store UTC ISO timestamps only (start_utc/end_utc). Local-time
presentation fields (start_local/end_local) and the `day()` boundary query
convert through Asia/Almaty via zoneinfo.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fam import audit, gate, people, places, rem, road

ALMATY = ZoneInfo("Asia/Almaty")


class UnknownRefError(Exception):
    """Raised when a participant/place ref doesn't resolve. Raised BEFORE
    any insert — cal.add() validates every ref first, then inserts.
    """
    def __init__(self, kind, text):
        self.kind = kind
        self.text = text
        super().__init__(f"unknown {kind}: {text}")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_utc_iso(value):
    """Accept an ISO-8601 string with an explicit UTC offset
    (fromisoformat) and normalize to a UTC ISO string. A naive string
    (no tzinfo) is rejected rather than silently assumed to be UTC --
    callers must state their offset explicitly.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError(
            f"datetime must include an explicit UTC offset: {value}"
        )
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _to_local_iso(utc_iso):
    if utc_iso is None:
        return None
    dt = datetime.fromisoformat(utc_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ALMATY).isoformat(timespec="seconds")


def _resolve_participants(conn, participants):
    """Resolve each ref to a list of (person_id, name) pairs, expanding
    groups to their members. Raises UnknownRefError on the first unresolved
    ref, before any DB mutation. Returns a de-duplicated list of person ids
    (preserving first-seen order) alongside the resolved dicts, plus the
    original refs for the audit payload.
    """
    resolved_people = []  # list of person dicts (kind == 'person')
    seen_ids = set()
    for ref in participants:
        p = people.resolve(conn, ref)
        if p is None:
            raise UnknownRefError("person", ref)
        if p["kind"] == "group":
            members = p.get("members", [])
        else:
            members = [p]
        for m in members:
            if m["id"] not in seen_ids:
                seen_ids.add(m["id"])
                resolved_people.append(m)
    return resolved_people


def _resolve_place(conn, place_ref):
    if place_ref is None:
        return None
    pl = places.resolve(conn, place_ref)
    if pl is None:
        raise UnknownRefError("place", place_ref)
    return pl


# Columns whose change should trigger a road recompute in update() --
# mirrors _REGEN_TRIGGER_COLUMNS's before/after-snapshot technique below.
# transport is included even though compute_travel_min doesn't currently
# branch on it, matching the plan's literal trigger set (future-proofing
# for a transport-aware ladder rung without a second migration).
_ROAD_TRIGGER_COLUMNS = ("start_utc", "place_id", "transport")


def recompute_road(conn, event_id):
    """Compute and persist real-road travel time for event_id, if the
    place has coordinates and home is configured. Called from add() (always)
    and update() (only when _ROAD_TRIGGER_COLUMNS changed), BEFORE
    rem.regenerate so the regenerated chain sees fresh travel_min_road.
    Also the single code path behind `fam road` (Task 5) -- the manual
    recompute must behave byte-identically to the hooks (same audit
    kinds: road.computed / road.hook_error).

    Returns {"minutes": M, "source": S} when a computed value was
    persisted; otherwise {"minutes": None, "reason": R} with R one of
    "no_place_coords" (no place, or place without lat/lon),
    "no_home_config" (road_home_lat/lon unset), "fallback_source:<source>"
    (compute_travel_min landed on a non-computed rung: manual/place/none)
    or "error" (swallowed exception, audited as road.hook_error; also the
    can't-happen-from-the-hooks unknown-event case). The hooks ignore the
    return value; `fam road` passes the reason through to its output.

    Only "tomtom"/"straight" sources are computed values worth persisting
    -- "manual"/"place"/"none" are leave_at()'s own lower-rung fallbacks,
    not something road.py computed, so travel_min_road/road_checked_at
    are left NULL/untouched in those cases (nothing was computed).

    Never raises: calendar operations must not fail because of road
    logic. Any unexpected exception here is swallowed and audited as
    road.hook_error (road.py's own tomtom failures already audit
    road.error internally and don't raise).
    """
    try:
        event = get(conn, event_id)
        if event is None:
            return {"minutes": None, "reason": "error"}
        place = event.get("place")
        if not place or place.get("lat") is None or place.get("lon") is None:
            return {"minutes": None, "reason": "no_place_coords"}
        cfg = gate.load_config()
        if cfg.get("road_home_lat") is None or cfg.get("road_home_lon") is None:
            return {"minutes": None, "reason": "no_home_config"}

        depart_at = event["start_utc"]
        minutes, source = road.compute_travel_min(conn, event, cfg, now_utc=depart_at)
        if source in ("tomtom", "straight"):
            now = _now()
            conn.execute(
                "UPDATE events SET travel_min_road=?, road_checked_at=? "
                "WHERE id=?",
                (minutes, now, event_id),
            )
            audit.log(conn, "road.computed",
                      {"event_id": event_id, "minutes": minutes, "source": source})
            return {"minutes": minutes, "source": source}
        return {"minutes": None, "reason": f"fallback_source:{source}"}
    except Exception:
        audit.log(conn, "road.hook_error", {"event_id": event_id})
        return {"minutes": None, "reason": "error"}


def add(conn, title, start_utc, end_utc=None, place=None, participants=(),
        transport="unknown", notes="", travel_min=None, series_id=None):
    """Create an event. place/participants are text refs (id/name/alias/
    slug); an unresolvable ref raises UnknownRefError and nothing is
    inserted. Group participants expand to their members at add-time (the
    audit payload keeps the original ref, e.g. "татешки"). travel_min
    overrides the place's travel_min for rem.leave_at() -- None (default)
    means "take it from the place" (see rem.leave_at).

    Regenerates the event's reminder chain (rem.regenerate) in the same
    transaction, after the insert.
    """
    # Validate all refs first, before any insert.
    pl = _resolve_place(conn, place)
    resolved_people = _resolve_participants(conn, participants)

    start = _to_utc_iso(start_utc)
    end = _to_utc_iso(end_utc) if end_utc is not None else None
    now = _now()

    cur = conn.execute(
        "INSERT INTO events(title, start_utc, end_utc, place_id, transport, "
        "status, notes, travel_min, series_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (title, start, end, pl["id"] if pl else None, transport, "active",
         notes, travel_min, series_id, now, now),
    )
    event_id = cur.lastrowid

    for person in resolved_people:
        conn.execute(
            "INSERT INTO event_participants(event_id, person_id) VALUES (?,?)",
            (event_id, person["id"]),
        )

    audit.log(
        conn,
        "cal.add",
        {"id": event_id, "title": title, "start_utc": start, "end_utc": end,
         "place": place, "participants": list(participants),
         "transport": transport, "notes": notes, "travel_min": travel_min},
    )

    recompute_road(conn, event_id)

    rem.regenerate(conn, event_id)

    return get(conn, event_id)


def get(conn, event_id):
    """Fetch an event with its participants and place, plus start_local/
    end_local presentation fields in Asia/Almaty. Returns None if unknown.
    """
    row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)

    d["place"] = places.get(conn, d["place_id"]) if d["place_id"] else None

    part_rows = conn.execute(
        "SELECT pe.* FROM event_participants ep "
        "JOIN people pe ON pe.id = ep.person_id "
        "WHERE ep.event_id = ? "
        "ORDER BY pe.name COLLATE NOCASE",
        (event_id,),
    ).fetchall()
    d["participants"] = [dict(r) for r in part_rows]

    d["start_local"] = _to_local_iso(d["start_utc"])
    d["end_local"] = _to_local_iso(d["end_utc"])

    return d


_UPDATE_FIELDS = {
    "title", "start_utc", "end_utc", "place", "transport", "notes",
    "add_person", "rm_person", "travel_min",
}

# Fields whose change should trigger a reminder-chain regeneration
# (compared as actual DB column values before vs. after the write --
# NOT "was the field passed", so an offset-only start_utc rewrite that
# normalizes to the same instant correctly does not trigger a regen).
_REGEN_TRIGGER_COLUMNS = ("start_utc", "travel_min", "place_id")

# Fields whose change should (re-)trigger cli.py's cal-update mail hook
# (_maybe_email_event -> Denis's .ics email, Task 10) -- a superset of
# _REGEN_TRIGGER_COLUMNS: adds end_utc and title, which don't affect the
# reminder chain but DO change what's on the calendar entry, so a
# notes-only edit (no _MAIL_TRIGGER_COLUMNS or participant-set change)
# must not re-send while a bare end_utc or title edit must.
# title's inclusion is a product decision (Denis, phase-2b final review
# Minor #7), superseding the earlier spec-literal reading that left it
# out: title feeds the .ics SUMMARY, and the stable UID means the
# admin's calendar entry just updates its name on the re-sent email.
# Participant-set changes are checked the same way as for regen (see
# update() below).
_MAIL_TRIGGER_COLUMNS = _REGEN_TRIGGER_COLUMNS + ("end_utc", "title")


def update(conn, event_id, **fields):
    """Update mutable fields on an event. Accepts any of: title, start_utc,
    end_utc, place, transport, notes, travel_min, add_person (list of
    refs), rm_person (list of refs). Any other keyword raises ValueError
    before any write. place/add_person refs are resolved (UnknownRefError
    on failure) before any write. start_utc/end_utc are normalized to UTC
    exactly once, and that same normalized string is used for both the
    SQL SET clause and the audit payload below. Writes updated_at.

    Regenerates the event's reminder chain (rem.regenerate) in the same
    transaction, but ONLY if start_utc, travel_min, place, or the
    participant set actually changed (updated_at is never a regen
    signal) -- e.g. update(notes=...) never touches the reminder chain.

    The returned dict carries one extra transient key, "_material_changed"
    -- True iff any of _MAIL_TRIGGER_COLUMNS or the participant set
    changed (reusing the same before/after snapshots taken for the regen
    decision above, just compared against the slightly larger mail column
    set). This is cli.py's cal-update mail hook's dedup signal: it must
    only re-send Denis's .ics email on a material change, never on e.g. a
    notes-only edit. Callers that don't care (get()/add() callers, most
    test assertions) can simply ignore the key; cli.py's cmd_cal_update
    pops it off before further use (JSON output, etc.).
    """
    existing = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if existing is None:
        raise ValueError(f"unknown event: {event_id}")

    unknown_fields = set(fields) - _UPDATE_FIELDS
    if unknown_fields:
        name = sorted(unknown_fields)[0]
        raise ValueError(
            f"unknown field: {name} (valid: {', '.join(sorted(_UPDATE_FIELDS))})"
        )

    # Snapshot old regen-relevant state before any mutation.
    old_regen_state = tuple(existing[c] for c in _REGEN_TRIGGER_COLUMNS)
    old_road_state = tuple(existing[c] for c in _ROAD_TRIGGER_COLUMNS)
    old_participant_ids = {r["person_id"] for r in conn.execute(
        "SELECT person_id FROM event_participants WHERE event_id=?",
        (event_id,),
    ).fetchall()}

    # Normalize start_utc/end_utc once, in place, before either the SQL
    # write or the audit payload consume them — both must see the same
    # UTC-normalized string, never the raw caller-supplied offset.
    for key in ("start_utc", "end_utc"):
        if key in fields and fields[key] is not None:
            fields[key] = _to_utc_iso(fields[key])

    add_person = fields.pop("add_person", None) or []
    rm_person = fields.pop("rm_person", None) or []

    # Validate all refs first, before any write.
    place_id = None
    place_given = "place" in fields
    if place_given:
        pl = _resolve_place(conn, fields["place"])
        place_id = pl["id"] if pl else None

    to_add = _resolve_participants(conn, add_person) if add_person else []
    to_remove = _resolve_participants(conn, rm_person) if rm_person else []

    set_clauses = []
    params = []
    column_map = {
        "title": "title",
        "start_utc": "start_utc",
        "end_utc": "end_utc",
        "transport": "transport",
        "notes": "notes",
        "travel_min": "travel_min",
    }
    for key, col in column_map.items():
        if key in fields:
            set_clauses.append(f"{col}=?")
            params.append(fields[key])
    if place_given:
        set_clauses.append("place_id=?")
        params.append(place_id)

    now = _now()
    set_clauses.append("updated_at=?")
    params.append(now)
    params.append(event_id)

    conn.execute(
        f"UPDATE events SET {', '.join(set_clauses)} WHERE id=?", params
    )

    for person in to_add:
        conn.execute(
            "INSERT OR IGNORE INTO event_participants(event_id, person_id) "
            "VALUES (?,?)",
            (event_id, person["id"]),
        )
    for person in to_remove:
        conn.execute(
            "DELETE FROM event_participants WHERE event_id=? AND person_id=?",
            (event_id, person["id"]),
        )

    audit_payload = {"id": event_id}
    audit_payload.update(fields)
    if add_person:
        audit_payload["add_person"] = list(add_person)
    if rm_person:
        audit_payload["rm_person"] = list(rm_person)
    audit.log(conn, "cal.update", audit_payload)

    new_row = conn.execute(
        "SELECT * FROM events WHERE id=?", (event_id,)
    ).fetchone()
    new_regen_state = tuple(new_row[c] for c in _REGEN_TRIGGER_COLUMNS)
    new_road_state = tuple(new_row[c] for c in _ROAD_TRIGGER_COLUMNS)
    new_participant_ids = {r["person_id"] for r in conn.execute(
        "SELECT person_id FROM event_participants WHERE event_id=?",
        (event_id,),
    ).fetchall()}
    participants_changed = new_participant_ids != old_participant_ids

    # Road recompute BEFORE regen, so a fresh regen reads the just-written
    # travel_min_road (rem.regenerate re-fetches the event from DB).
    #
    # _REGEN_TRIGGER_COLUMNS does NOT include travel_min_road (it's a
    # derived/computed column, not a caller-settable field), but
    # _ROAD_TRIGGER_COLUMNS includes transport -- so a transport-only
    # update that changes what recompute_road() computes would otherwise
    # skip regen entirely, AND the fresh road_checked_at it just wrote
    # would suppress the tick's self-heal from ever catching the desync.
    # Snapshot travel_min_road before the recompute and force a regen if
    # the computed value actually changed, even when none of
    # _REGEN_TRIGGER_COLUMNS tripped.
    old_travel_min_road = existing["travel_min_road"]
    road_value_changed = False
    if new_road_state != old_road_state:
        road_result = recompute_road(conn, event_id)
        if road_result.get("minutes") is not None:
            road_value_changed = road_result["minutes"] != old_travel_min_road

    if (new_regen_state != old_regen_state or participants_changed
            or road_value_changed):
        rem.regenerate(conn, event_id)

    # Mail-material check derives both snapshots straight from
    # _MAIL_TRIGGER_COLUMNS (a superset of the regen columns -- see its
    # docstring, incl. why title is in it) plus the participant-change
    # flag already computed above. `existing` is the pre-mutation row
    # fetched at the top, so this reads the true "before" values even
    # though it runs after the UPDATE. Deriving from the column set
    # directly (rather than hand-appending each extra column to the
    # regen tuples, as an earlier revision did) makes _MAIL_TRIGGER_COLUMNS
    # the single place to widen the mail trigger.
    old_mail_state = tuple(existing[c] for c in _MAIL_TRIGGER_COLUMNS)
    new_mail_state = tuple(new_row[c] for c in _MAIL_TRIGGER_COLUMNS)
    material_changed = new_mail_state != old_mail_state or participants_changed

    result = get(conn, event_id)
    result["_material_changed"] = material_changed
    return result


def _set_status(conn, event_id, status, kind):
    existing = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if existing is None:
        raise ValueError(f"unknown event: {event_id}")
    conn.execute(
        "UPDATE events SET status=?, updated_at=? WHERE id=?",
        (status, _now(), event_id),
    )
    audit.log(conn, kind, {"id": event_id, "status": status})
    return get(conn, event_id)


def cancel(conn, event_id):
    """Mark an event cancelled and cancel its pending reminder chain."""
    result = _set_status(conn, event_id, "cancelled", "cal.cancel")
    rem.cancel_chain(conn, event_id)
    return result


def done(conn, event_id):
    """Mark an event done and cancel its pending reminder chain (it
    already happened -- no more reminders needed).
    """
    result = _set_status(conn, event_id, "done", "cal.done")
    rem.cancel_chain(conn, event_id)
    return result


def list_range(conn, from_utc, to_utc, status="active"):
    """List events with start_utc in [from_utc, to_utc), optionally
    filtered by status ("active" default; pass None for all statuses).
    """
    if status is None:
        rows = conn.execute(
            "SELECT id FROM events WHERE start_utc >= ? AND start_utc < ? "
            "ORDER BY start_utc",
            (from_utc, to_utc),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM events WHERE start_utc >= ? AND start_utc < ? "
            "AND status = ? ORDER BY start_utc",
            (from_utc, to_utc, status),
        ).fetchall()
    return [get(conn, r["id"]) for r in rows]


def day(conn, date_local):
    """List active events on date_local (YYYY-MM-DD, Asia/Almaty), i.e.
    events whose start falls within that local calendar day's UTC range.
    """
    y, m, d = (int(x) for x in date_local.split("-"))
    start_of_day = datetime(y, m, d, 0, 0, 0, tzinfo=ALMATY)
    end_of_day = start_of_day + timedelta(days=1)
    from_utc = start_of_day.astimezone(timezone.utc).isoformat(timespec="seconds")
    to_utc = end_of_day.astimezone(timezone.utc).isoformat(timespec="seconds")
    return list_range(conn, from_utc, to_utc, status="active")
