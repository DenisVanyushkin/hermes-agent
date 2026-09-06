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

_UNSET = object()

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
        prep_min=None, subject_person_id=None):
    """Create an active event_series. Validates refs/weekdays/times before any
    insert (mirrors cal.add). Groups in participants expand to members. Does
    NOT generate occurrences -- the caller runs generate() next. prep_min
    (Task 4, phase 7), when set, is copied onto every occurrence generate()
    materializes (via cal.add's prep_min), so each one gets its reminder
    chain from rem.build_stages(prep_min) instead of the rule engine.
    """
    pl = cal._resolve_place(conn, place)
    resolved = cal._resolve_participants(conn, participants)
    subject_person_id = cal._validate_subject_id(conn, subject_person_id)
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
        "created_at, updated_at, subject_person_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,'active',?,?,?)",
        (title, pl["id"] if pl else None, wd, start_time, end_time,
         transport, notes, until_local, prep_min, now, now, subject_person_id),
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
        "prep_min": prep_min, "subject": cal.subject_for_event(
            conn, {"subject_person_id": subject_person_id})})
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
    d["subject"] = cal.subject_for_event(conn, d)
    return d


def list_active(conn):
    """Active series with a count of future (start_utc > now) occurrences."""
    now = _now()
    out = []
    for row in conn.execute(
            "SELECT * FROM event_series WHERE status='active' ORDER BY id"):
        d = dict(row)
        d["subject"] = cal.subject_for_event(conn, d)
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
    """Cancel a series and process its future occurrences.

    Managed occurrences are tombstoned so their export journal or issue can
    drive the later external-calendar cleanup; their plan links stay intact.
    Unmanaged occurrences are physically deleted after their plan references
    are cleared. Returns the number of future occurrences processed.
    ``now_utc`` is a test seam (defaults to wall-clock now).
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
    physically_deleted = 0
    for r in future:
        event_id = r["id"]
        managed = conn.execute(
            "SELECT 1 FROM ext_exports WHERE event_id=? "
            "UNION SELECT 1 FROM ext_exports_taya WHERE event_id=? "
            "UNION SELECT 1 FROM extcal_export_issues WHERE event_id=?",
            (event_id, event_id, event_id),
        ).fetchone()
        if managed:
            conn.execute(
                "UPDATE events SET status='cancelled', updated_at=? WHERE id=?",
                (now, event_id))
            rem.cancel_chain(conn, event_id)
            continue

        # A physical delete must clear every plan FK first. Managed
        # tombstones retain these links so later cleanup can still identify
        # and report the cancelled occurrence without losing plan history.
        cal._prep_cascade_cancel(conn, event_id)
        conn.execute(
            "UPDATE plans SET prep_for_event_id=NULL "
            "WHERE prep_for_event_id=?", (event_id,))
        conn.execute(
            "UPDATE plans SET attached_event_id=NULL "
            "WHERE attached_event_id=?", (event_id,))
        conn.execute("DELETE FROM events WHERE id=?", (event_id,))
        physically_deleted += 1
    audit.log(conn, "cal.series.cancel",
              {"id": sid, "processed_future": len(future),
               "physically_deleted": physically_deleted})
    return len(future)


HORIZON_WEEKS = 8


def iter_occurrences(weekdays, start_time, end_time, until_local,
                     now_local, horizon_date):
    """The recurrence grid of ONE weekly series, as (start_utc, end_utc) pairs.

    Pure: no DB, no wall clock -- now_local (tz-aware, Asia/Almaty) and
    horizon_date come from the caller. Only occurrences strictly after
    now_local are returned, capped by horizon_date and until_local.

    Extracted from generate() (2026-08-01) so the CLI can preview a series'
    slots BEFORE writing anything -- the occupancy guardrail needs to know
    where the occurrences would land. generate() reads the same function,
    so there is exactly one definition of "when does this series happen".
    """
    wds = {_WD_INDEX[w] for w in canon_weekdays(weekdays).split(",")}
    until = (datetime.strptime(until_local, "%Y-%m-%d").date()
             if until_local else None)
    sh, sm = (int(x) for x in start_time.split(":"))
    end_hm = tuple(int(x) for x in end_time.split(":")) if end_time else None

    out = []
    d = now_local.date()
    while d <= horizon_date:
        if (until is None or d <= until) and d.weekday() in wds:
            start_local = datetime(d.year, d.month, d.day, sh, sm,
                                   tzinfo=cal.ALMATY)
            if start_local > now_local:
                end_utc = None
                if end_hm is not None:
                    end_local = datetime(d.year, d.month, d.day,
                                         end_hm[0], end_hm[1], tzinfo=cal.ALMATY)
                    end_utc = end_local.astimezone(timezone.utc).isoformat(
                        timespec="seconds")
                out.append((start_local.astimezone(timezone.utc).isoformat(
                    timespec="seconds"), end_utc))
        d += timedelta(days=1)
    return out


def generate(conn, now_utc=None, horizon_weeks=HORIZON_WEEKS):
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
        participants = [
            r["person_id"] for r in conn.execute(
                "SELECT person_id FROM event_series_participants "
                "WHERE series_id=?", (s["id"],))]
        for start_utc, end_utc in iter_occurrences(
                s["weekdays"], s["start_time"], s["end_time"],
                s["until_local"], now_local, horizon_date):
            occupied = conn.execute(
                "SELECT 1 FROM events WHERE series_id=? AND start_utc=?",
                (s["id"], start_utc)).fetchone()
            if occupied:
                continue
            cal.add(conn, s["title"], start_utc, end_utc,
                    place=s["place_id"], participants=participants,
                    transport=s["transport"], notes=s["notes"],
                    series_id=s["id"], prep_min=s["prep_min"],
                    subject_person_id=s["subject_person_id"])
            created += 1
    return created


def update_participants(conn, sid, add=(), remove=(), now_utc=None,
                       subject_person_id=_UNSET):
    """Update participants and/or the series subject atomically.

    Subject propagation is limited to active future occurrences that remain
    on the series' local grid and still carry the previous series subject.
    An occurrence explicitly changed through ``cal.update`` is therefore an
    individual override and is preserved.
    """
    s = get(conn, sid)
    if s is None:
        raise ValueError(f"unknown series: {sid}")
    to_add = cal._resolve_participants(conn, add) if add else []
    to_remove = cal._resolve_participants(conn, remove) if remove else []
    subject_given = subject_person_id is not _UNSET
    new_subject = (cal._validate_subject_id(conn, subject_person_id)
                   if subject_given else s["subject_person_id"])
    old_subject = s["subject_person_id"]
    now = _to_utc_iso(now_utc) if now_utc else _now()

    if to_add or to_remove:
        for person in to_add:
            conn.execute(
                "INSERT OR IGNORE INTO event_series_participants"
                "(series_id, person_id) VALUES (?,?)", (sid, person["id"]))
        for person in to_remove:
            conn.execute(
                "DELETE FROM event_series_participants WHERE series_id=? "
                "AND person_id=?", (sid, person["id"]))
    if subject_given:
        conn.execute(
            "UPDATE event_series SET subject_person_id=?, updated_at=? WHERE id=?",
            (new_subject, now, sid))

    candidates = conn.execute(
        "SELECT id, start_utc, subject_person_id FROM events WHERE series_id=? "
        "AND status='active' AND start_utc > ?", (sid, now)).fetchall()
    updated_events = []
    for row in candidates:
        if cal._to_local_iso(row["start_utc"])[11:16] != s["start_time"]:
            continue
        if subject_given and row["subject_person_id"] == old_subject:
            conn.execute("UPDATE events SET subject_person_id=?, updated_at=? WHERE id=?",
                         (new_subject, now, row["id"]))
        if to_add or to_remove:
            for person in to_add:
                conn.execute(
                    "INSERT OR IGNORE INTO event_participants"
                    "(event_id, person_id) VALUES (?,?)", (row["id"], person["id"]))
            for person in to_remove:
                conn.execute(
                    "DELETE FROM event_participants WHERE event_id=? AND person_id=?",
                    (row["id"], person["id"]))
            rem.regenerate(conn, row["id"])
        if (to_add or to_remove
                or (subject_given and row["subject_person_id"] == old_subject)):
            updated_events.append(row["id"])

    audit.log(conn, "cal.series.update", {
        "id": sid, "add": list(add), "remove": list(remove),
        "subject": cal.subject_for_event(conn, {"subject_person_id": new_subject}),
        "updated_events": updated_events})
    return {"series_id": sid, "updated_events": updated_events,
            "subject": cal.subject_for_event(conn, {"subject_person_id": new_subject})}
