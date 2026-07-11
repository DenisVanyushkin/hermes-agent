"""Calendar events: CRUD with glossary resolution (people/places).

Domain functions never commit — callers (tests, CLI) own the transaction,
mirroring people.py/places.py's pattern.

Events store UTC ISO timestamps only (start_utc/end_utc). Local-time
presentation fields (start_local/end_local) and the `day()` boundary query
convert through Asia/Almaty via zoneinfo.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fam import audit, people, places

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
    """Accept an ISO-8601 string with any offset (fromisoformat) and
    normalize to a UTC ISO string. Also accepts an already-UTC string.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
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


def add(conn, title, start_utc, end_utc=None, place=None, participants=(),
        transport="unknown", notes=""):
    """Create an event. place/participants are text refs (id/name/alias/
    slug); an unresolvable ref raises UnknownRefError and nothing is
    inserted. Group participants expand to their members at add-time (the
    audit payload keeps the original ref, e.g. "татешки").
    """
    # Validate all refs first, before any insert.
    pl = _resolve_place(conn, place)
    resolved_people = _resolve_participants(conn, participants)

    start = _to_utc_iso(start_utc)
    end = _to_utc_iso(end_utc) if end_utc is not None else None
    now = _now()

    cur = conn.execute(
        "INSERT INTO events(title, start_utc, end_utc, place_id, transport, "
        "status, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (title, start, end, pl["id"] if pl else None, transport, "active",
         notes, now, now),
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
         "transport": transport, "notes": notes},
    )

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
    "add_person", "rm_person",
}


def update(conn, event_id, **fields):
    """Update mutable fields on an event. Accepts any of: title, start_utc,
    end_utc, place, transport, notes, add_person (list of refs), rm_person
    (list of refs). Any other keyword raises ValueError before any write.
    place/add_person refs are resolved (UnknownRefError on failure) before
    any write. start_utc/end_utc are normalized to UTC exactly once, and
    that same normalized string is used for both the SQL SET clause and
    the audit payload below. Writes updated_at.
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

    return get(conn, event_id)


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
    """Mark an event cancelled."""
    return _set_status(conn, event_id, "cancelled", "cal.cancel")


def done(conn, event_id):
    """Mark an event done."""
    return _set_status(conn, event_id, "done", "cal.done")


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
