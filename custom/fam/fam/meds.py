"""Meds: medication schedule (times/day), remaining stock, low-stock threshold.

Domain functions never commit — callers (tests, CLI) own the transaction,
mirroring plans.py/people.py/places.py's pattern.
"""
import json
from datetime import datetime, timezone

from fam import audit, shopping


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


def list_pending(conn):
    """List pending med_intakes (phase 5 T8 review round 1). Replaces
    the amina-fam skill's old audit-log join (gate.sent "name" +
    tick.med intake_id, matched by timestamp) for resolving "what dose
    is Amina being asked about" -- that join breaks two ways: (1) the
    digest logs a gate.sent row for EVERY today's dose, not only the
    ones actually delivered as a tick.med reminder, so a grep can land
    on the digest's row (no tick.med twin) and wrongly conclude
    "nothing pending"; (2) tick.med carries no name, so two doses due
    at the same timestamp are indistinguishable. This is the single,
    unambiguous source of truth instead: JOIN med_intakes to its med
    for the name, filtered to status='pending', ordered by
    plan_ts_utc (earliest-due first, matching what a "выпила"/"скипнула"
    reaction almost always means).

    Returns a list of dicts: intake_id, med_id, name, plan_ts_utc,
    status (always "pending" -- included so a caller need not assume
    it, same as meds.take()/skip()'s result dicts always echoing
    status back).
    """
    rows = conn.execute(
        "SELECT m.id AS intake_id, m.med_id AS med_id, d.name AS name, "
        "m.plan_ts_utc AS plan_ts_utc, m.status AS status "
        "FROM med_intakes m JOIN meds d ON d.id = m.med_id "
        "WHERE m.status='pending' ORDER BY m.plan_ts_utc"
    ).fetchall()
    return [dict(r) for r in rows]


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


def take(conn, intake_id, now_utc=None):
    """Ack a med_intakes row as taken (phase 5 Task 5). status=taken,
    taken_ts_utc is stamped, series_next_utc is cleared to NULL -- this
    dose's persistent reminder series (tick._meds_series) is done
    escalating once it's acked, same as the out-of-stock branch there
    already does. remaining decrements by 1, floored at 0, when tracked
    (not None); an untracked med (remaining=None) is left alone and
    never triggers a restock.

    Restock trigger: once remaining is updated, if it is not None and
    remaining <= the med's threshold -- guarded so a med at the default
    threshold=0 only triggers once remaining actually hits 0, not on
    every take (threshold>0 or remaining==0) -- this calls
    shopping.add_from_meds(conn, med_name), which self-dedups against an
    already-open source='meds' row for the same name. The outcome is
    reported back via restock (True whenever the threshold condition
    fired) and restock_added (False when add_from_meds's own dedup
    returned None, i.e. a restock item was already open).

    audit meds.take: {intake_id, med_id, remaining, restock}.

    Raises ValueError on an unknown intake_id, before any write --
    same "raise, don't fail silently" contract unknown place/person/
    plan refs already use elsewhere in fam/*.py (Denis's "не падай
    молча" instruction for T5: an exception here is the CLI-visible,
    exit-2 path via cli.main's except ValueError). Also raises
    ValueError, before any write, when the row is not status='pending'
    -- a retried skill call or a duplicate "выпила" on an
    already-taken (or already-skipped) dose must not double-decrement
    remaining, overwrite taken_ts_utc, or write a second meds.take
    audit row (review finding, 5 T5 round 1).
    """
    row = conn.execute(
        "SELECT * FROM med_intakes WHERE id=?", (intake_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown intake: {intake_id}")
    if row["status"] != "pending":
        raise ValueError(f"intake {intake_id} already {row['status']}")

    now = now_utc or _now()
    med_id = row["med_id"]
    med = get(conn, med_id)

    remaining = med["remaining"] if med is not None else None
    if remaining is not None:
        remaining = max(0, remaining - 1)
        edit(conn, med_id, remaining=remaining)

    conn.execute(
        "UPDATE med_intakes SET status='taken', taken_ts_utc=?, "
        "series_next_utc=NULL WHERE id=?",
        (now, intake_id),
    )

    restock = False
    restock_added = False
    if remaining is not None and remaining <= med["threshold"] \
            and (med["threshold"] > 0 or remaining == 0):
        restock = True
        added_id = shopping.add_from_meds(conn, med["name"])
        restock_added = added_id is not None

    audit.log(conn, "meds.take", {
        "intake_id": intake_id, "med_id": med_id, "remaining": remaining,
        "restock": restock,
    })

    result = dict(row)
    result["status"] = "taken"
    result["taken_ts_utc"] = now
    result["series_next_utc"] = None
    result["remaining"] = remaining
    result["restock"] = restock
    result["restock_added"] = restock_added
    return result


def skip(conn, intake_id):
    """Ack a med_intakes row as skipped (phase 5 Task 5) -- ONLY this
    one dose: status=skipped, series_next_utc cleared to NULL (stops
    just this row's own persistent-reminder escalation, same series
    field take() clears). remaining is left untouched (a skipped dose
    was never consumed), and the next scheduled intake is unaffected --
    it is a separate med_intakes row, generated daily by tick.meds_gen,
    that this call never touches.

    audit meds.skip: {intake_id, med_id}.

    Raises ValueError on an unknown intake_id, before any write --
    same contract as take(). Also raises ValueError, before any write,
    when the row is not status='pending' -- an already-taken dose must
    not roll back to 'skipped' out from under an already-decremented
    remaining (review finding, 5 T5 round 1).
    """
    row = conn.execute(
        "SELECT * FROM med_intakes WHERE id=?", (intake_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown intake: {intake_id}")
    if row["status"] != "pending":
        raise ValueError(f"intake {intake_id} already {row['status']}")

    conn.execute(
        "UPDATE med_intakes SET status='skipped', series_next_utc=NULL "
        "WHERE id=?",
        (intake_id,),
    )

    audit.log(conn, "meds.skip",
              {"intake_id": intake_id, "med_id": row["med_id"]})

    result = dict(row)
    result["status"] = "skipped"
    result["series_next_utc"] = None
    return result
