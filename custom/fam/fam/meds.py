"""Meds: medication schedule (times/day), remaining stock, low-stock threshold.

Domain functions never commit — callers (tests, CLI) own the transaction,
mirroring plans.py/people.py/places.py's pattern.
"""
import json
from datetime import datetime, timezone

from fam import audit


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_times(times):
    """times must be a non-empty list of "HH:MM" strings. Raises
    ValueError (before any insert/update, same "raise before any write"
    contract as plans._validate_deadline) on an empty list or a
    malformed entry. Returns the storage form: a JSON-encoded,
    sorted, de-duplicated list.
    """
    if not times:
        raise ValueError("times must be a non-empty list of \"HH:MM\" values")
    normalized = []
    for t in times:
        try:
            normalized.append(datetime.strptime(t, "%H:%M").strftime("%H:%M"))
        except (TypeError, ValueError):
            raise ValueError(f"invalid time (expected HH:MM): {t}")
    return json.dumps(sorted(set(normalized)))


def add(conn, name, times, dose="", remaining=None, threshold=0):
    """Create a med. times: list of "HH:MM" strings, validated and
    stored sorted+deduped as JSON (empty list or a malformed entry
    raises ValueError before any insert). Returns the new med's id.
    """
    times_json = _validate_times(times)

    now = _now()
    cur = conn.execute(
        "INSERT INTO meds(name, dose, times, remaining, threshold, enabled, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (name, dose, times_json, remaining, threshold, 1, now, now),
    )
    med_id = cur.lastrowid

    audit.log(
        conn, "meds.add",
        {"id": med_id, "name": name, "dose": dose,
         "times": json.loads(times_json), "remaining": remaining,
         "threshold": threshold},
    )

    return med_id


def _row_to_dict(row):
    d = dict(row)
    d["times"] = json.loads(d["times"])
    d["enabled"] = bool(d["enabled"])
    return d


def get(conn, med_id):
    """Fetch a med by id, or None if unknown."""
    row = conn.execute("SELECT * FROM meds WHERE id=?", (med_id,)).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def list(conn, include_disabled=False):
    """List meds, ordered by name. include_disabled=False (default)
    filters out enabled=0 rows.
    """
    if include_disabled:
        rows = conn.execute(
            "SELECT * FROM meds ORDER BY name COLLATE NOCASE"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM meds WHERE enabled=1 ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


_EDIT_FIELDS = {"name", "dose", "times", "remaining", "threshold", "enabled"}


def edit(conn, med_id, **fields):
    """Update mutable fields on a med (whitelist: name, dose, times,
    remaining, threshold, enabled), following places.update's pattern.
    times is validated the same as add(). Raises ValueError on an
    unknown field or an empty field set -- always before any write.
    Returns False on an unknown med_id (no write, no audit); True on
    success.
    """
    existing = conn.execute("SELECT * FROM meds WHERE id=?", (med_id,)).fetchone()
    if existing is None:
        return False

    unknown_fields = set(fields) - _EDIT_FIELDS
    if unknown_fields:
        name = sorted(unknown_fields)[0]
        raise ValueError(
            f"unknown field: {name} (valid: {', '.join(sorted(_EDIT_FIELDS))})"
        )
    if not fields:
        raise ValueError("no fields to update")

    store = dict(fields)
    if "times" in store:
        store["times"] = _validate_times(store["times"])
    if "enabled" in store:
        store["enabled"] = 1 if store["enabled"] else 0

    set_clauses = []
    params = []
    for key in sorted(store):
        set_clauses.append(f"{key}=?")
        params.append(store[key])
    set_clauses.append("updated_at=?")
    params.append(_now())
    params.append(med_id)
    conn.execute(f"UPDATE meds SET {', '.join(set_clauses)} WHERE id=?", params)

    payload = {"id": med_id}
    payload.update(fields)
    audit.log(conn, "meds.edit", payload)
    return True


def remove(conn, med_id):
    """Delete a med (CASCADE removes its med_intakes). Returns False on
    an unknown med_id (no write, no audit); True on success.
    """
    existing = conn.execute("SELECT * FROM meds WHERE id=?", (med_id,)).fetchone()
    if existing is None:
        return False

    conn.execute("DELETE FROM meds WHERE id=?", (med_id,))
    audit.log(conn, "meds.remove", {"id": med_id, "name": existing["name"]})
    return True
