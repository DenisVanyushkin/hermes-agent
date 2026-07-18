"""Recurring event series (weekly-by-days) + generator.

A series is a rule (title, place, weekdays, start/end time, optional until);
the generator materializes concrete `events` rows from active series up to a
horizon, mirroring the meds-gen pattern (Phase 5). Materialized occurrences
are ordinary events (series_id set) so reminders, grid, digest, road and
done/ack all work on them unchanged.

Domain functions never commit -- callers (tests, CLI) own the transaction,
mirroring cal.py / places.py.
"""
from datetime import datetime, timedelta, timezone

from fam import audit, cal, rem

_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_WD_INDEX = {w: i for i, w in enumerate(_WEEKDAYS)}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canon_weekdays(days):
    """Normalize weekdays to a canonical CSV in week order, e.g. "mon,wed,fri".

    Accepts a CSV string or an iterable of 3-letter lowercase day names.
    Raises ValueError on an unknown day or an empty set.
    """
    if isinstance(days, str):
        parts = [d.strip().lower() for d in days.split(",") if d.strip()]
    else:
        parts = [str(d).strip().lower() for d in days]
    if not parts:
        raise ValueError("no weekdays given")
    seen, out = set(), []
    for d in parts:
        if d not in _WD_INDEX:
            raise ValueError(f"unknown weekday: {d}")
        if d not in seen:
            seen.add(d)
            out.append(d)
    out.sort(key=lambda d: _WD_INDEX[d])
    return ",".join(out)


def _validate_hhmm(t):
    datetime.strptime(t, "%H:%M")  # raises ValueError on bad form
    return t


def add(conn, title, weekdays, start_time, end_time=None, place=None,
        participants=(), transport="unknown", notes="", until_local=None,
        prep_min=None):
    """Create an active event_series. Validates refs/weekdays/times before any
    insert (mirrors cal.add). Groups in participants expand to members. Does
    NOT generate occurrences -- the caller runs generate() next. prep_min
    (Task 4, phase 7), when set, is copied onto every occurrence generate()
    materializes (via cal.add's prep_min), so each one gets its reminder
    chain from rem.build_stages(prep_min) instead of the rule engine.
    """
    pl = cal._resolve_place(conn, place)
    resolved = cal._resolve_participants(conn, participants)
    wd = canon_weekdays(weekdays)
    _validate_hhmm(start_time)
    if end_time is not None:
        _validate_hhmm(end_time)
    if until_local is not None:
        datetime.strptime(until_local, "%Y-%m-%d")  # raises on bad form
    now = _now()
    cur = conn.execute(
        "INSERT INTO event_series(title, place_id, weekdays, start_time, "
        "end_time, transport, notes, until_local, prep_min, status, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,'active',?,?)",
        (title, pl["id"] if pl else None, wd, start_time, end_time,
         transport, notes, until_local, prep_min, now, now),
    )
    sid = cur.lastrowid
    for m in resolved:
        conn.execute(
            "INSERT INTO event_series_participants(series_id, person_id) "
            "VALUES (?,?)", (sid, m["id"]))
    audit.log(conn, "cal.series.add", {
        "id": sid, "title": title, "weekdays": wd, "start_time": start_time,
        "end_time": end_time, "place": place,
        "participants": list(participants), "until_local": until_local,
        "prep_min": prep_min})
    return get(conn, sid)


def get(conn, sid):
    row = conn.execute(
        "SELECT * FROM event_series WHERE id=?", (sid,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["participants"] = [
        r["person_id"] for r in conn.execute(
            "SELECT person_id FROM event_series_participants WHERE series_id=?",
            (sid,))]
    return d


def list_active(conn):
    """Active series with a count of future (start_utc > now) occurrences."""
    now = _now()
    out = []
    for row in conn.execute(
            "SELECT * FROM event_series WHERE status='active' ORDER BY id"):
        d = dict(row)
        d["future_count"] = conn.execute(
            "SELECT COUNT(*) AS c FROM events WHERE series_id=? AND "
            "status='active' AND start_utc > ?", (d["id"], now)).fetchone()["c"]
        out.append(d)
    return out


def _to_utc_iso(now_utc):
    """Normalize an ISO string (any offset) to a UTC ISO string, so a string
    comparison against events.start_utc (stored UTC) is chronological."""
    dt = datetime.fromisoformat(now_utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def cancel(conn, sid, now_utc=None):
    """Cancel a series: mark it cancelled and delete FUTURE untouched
    occurrences (status='active', start_utc > now). Past, done and
    individually-cancelled occurrences are left intact. Returns the number of
    future occurrences removed. A cancelled series is never regenerated.
    now_utc is a test seam (defaults to wall-clock now).
    """
    s = get(conn, sid)
    if s is None:
        raise ValueError(f"unknown series: {sid}")
    now = _to_utc_iso(now_utc) if now_utc else _now()
    conn.execute(
        "UPDATE event_series SET status='cancelled', updated_at=? WHERE id=?",
        (now, sid))
    future = conn.execute(
        "SELECT id FROM events WHERE series_id=? AND status='active' AND "
        "start_utc > ?", (sid, now)).fetchall()
    for r in future:
        event_id = r["id"]
        # plans.prep_for_event_id and plans.attached_event_id both
        # REFERENCE events(id) with foreign_keys=ON, so deleting the
        # event out from under a plan that still points at it raises
        # IntegrityError. Drop any open prep-plan first (same cascade
        # cal.cancel() uses), then null out the dangling reference on
        # every OTHER plan still pointing at this event (a done/dropped
        # prep-plan, or a plan merely attached via plans.attach()) --
        # the plan row itself is kept, only the FK is cleared, so
        # history isn't lost.
        cal._prep_cascade_cancel(conn, event_id)
        conn.execute(
            "UPDATE plans SET prep_for_event_id=NULL "
            "WHERE prep_for_event_id=?", (event_id,))
        conn.execute(
            "UPDATE plans SET attached_event_id=NULL "
            "WHERE attached_event_id=?", (event_id,))
        conn.execute("DELETE FROM events WHERE id=?", (event_id,))
    audit.log(conn, "cal.series.cancel",
              {"id": sid, "deleted_future": len(future)})
    return len(future)


def generate(conn, now_utc=None, horizon_weeks=8):
    """Materialize concrete events from active series up to horizon_weeks
    ahead. Idempotent: an existing occupied (series_id, start_utc) slot is
    skipped -- including one that was individually cancelled (it stays a
    tombstone and is not recreated). Only future occurrences (start_local >
    now) are created. Returns the number of occurrences created.
    """
    if now_utc is None:
        now_dt = datetime.now(timezone.utc)
    else:
        now_dt = datetime.fromisoformat(now_utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
    now_local = now_dt.astimezone(cal.ALMATY)
    horizon_date = (now_local + timedelta(weeks=horizon_weeks)).date()

    created = 0
    for srow in conn.execute(
            "SELECT * FROM event_series WHERE status='active'").fetchall():
        s = dict(srow)
        wds = {_WD_INDEX[w] for w in s["weekdays"].split(",")}
        until = (datetime.strptime(s["until_local"], "%Y-%m-%d").date()
                 if s["until_local"] else None)
        sh, sm = (int(x) for x in s["start_time"].split(":"))
        end_hm = None
        if s["end_time"]:
            end_hm = tuple(int(x) for x in s["end_time"].split(":"))
        participants = [
            r["person_id"] for r in conn.execute(
                "SELECT person_id FROM event_series_participants "
                "WHERE series_id=?", (s["id"],))]

        d = now_local.date()
        while d <= horizon_date:
            if (until is None or d <= until) and d.weekday() in wds:
                start_local = datetime(d.year, d.month, d.day, sh, sm,
                                       tzinfo=cal.ALMATY)
                if start_local > now_local:
                    start_utc = start_local.astimezone(
                        timezone.utc).isoformat(timespec="seconds")
                    occupied = conn.execute(
                        "SELECT 1 FROM events WHERE series_id=? AND start_utc=?",
                        (s["id"], start_utc)).fetchone()
                    if not occupied:
                        end_utc = None
                        if end_hm is not None:
                            end_local = datetime(d.year, d.month, d.day,
                                                 end_hm[0], end_hm[1],
                                                 tzinfo=cal.ALMATY)
                            end_utc = end_local.astimezone(
                                timezone.utc).isoformat(timespec="seconds")
                        cal.add(conn, s["title"], start_utc, end_utc,
                                place=s["place_id"], participants=participants,
                                transport=s["transport"], notes=s["notes"],
                                series_id=s["id"], prep_min=s["prep_min"])
                        created += 1
            d += timedelta(days=1)
    return created


def update_participants(conn, sid, add=(), remove=(), now_utc=None):
    """Change the series' participant set and propagate it to every future
    UNTOUCHED occurrence (status='active', start_utc>now, local start time
    still matching the series' own start_time slot -- a rescheduled
    occurrence has drifted off the grid and is left alone, same as
    cancel()'s "future untouched" semantics). Past, done, cancelled/
    tombstone and rescheduled occurrences are never touched.

    add/remove are participant refs (name/alias/slug/group -- groups
    expand to members), resolved via cal._resolve_participants BEFORE any
    write (UnknownRefError on the first bad ref). For each affected
    occurrence, the same add/remove is applied to event_participants and
    rem.regenerate(conn, event_id) runs in the same transaction, so a
    newly-added participant's slug-scoped reminder rule (e.g. Тая's
    lead-60 chain) takes effect immediately.

    now_utc is a test seam (defaults to wall-clock now), mirroring
    cancel()'s now_utc parameter.

    Returns {"series_id": sid, "updated_events": [event_id, ...]}.
    """
    s = get(conn, sid)
    if s is None:
        raise ValueError(f"unknown series: {sid}")

    to_add = cal._resolve_participants(conn, add) if add else []
    to_remove = cal._resolve_participants(conn, remove) if remove else []

    for person in to_add:
        conn.execute(
            "INSERT OR IGNORE INTO event_series_participants"
            "(series_id, person_id) VALUES (?,?)", (sid, person["id"]))
    for person in to_remove:
        conn.execute(
            "DELETE FROM event_series_participants WHERE series_id=? "
            "AND person_id=?", (sid, person["id"]))

    now = _to_utc_iso(now_utc) if now_utc else _now()
    candidates = conn.execute(
        "SELECT id, start_utc FROM events WHERE series_id=? AND "
        "status='active' AND start_utc > ?", (sid, now)).fetchall()

    updated_events = []
    for row in candidates:
        local_hm = cal._to_local_iso(row["start_utc"])[11:16]
        if local_hm != s["start_time"]:
            continue  # rescheduled off the series grid -- leave alone
        event_id = row["id"]
        for person in to_add:
            conn.execute(
                "INSERT OR IGNORE INTO event_participants"
                "(event_id, person_id) VALUES (?,?)", (event_id, person["id"]))
        for person in to_remove:
            conn.execute(
                "DELETE FROM event_participants WHERE event_id=? AND "
                "person_id=?", (event_id, person["id"]))
        rem.regenerate(conn, event_id)
        updated_events.append(event_id)

    audit.log(conn, "cal.series.update", {
        "id": sid, "add": list(add), "remove": list(remove),
        "updated_events": updated_events})
    return {"series_id": sid, "updated_events": updated_events}
