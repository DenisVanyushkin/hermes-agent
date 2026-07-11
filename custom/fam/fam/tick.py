"""Scheduled ticks: `fam tick reminders` runs one reminders sweep with no
LLM in the orchestration loop itself (gate.deliver's rewrite subprocess is
the only place an LLM is involved).

Unlike cal.py/rem.py/gate.py/people.py/places.py -- which never commit,
leaving the transaction boundary to their caller -- this module DOES own
its own commits. A tick has no external caller managing a shared
transaction; the tick run IS the transaction boundary, and it is invoked
on a schedule (systemd timer, Task 8) with no human watching. Committing
once per due reminder (rather than once for the whole tick) narrows the
blast radius of a mid-run crash: gate.deliver's send is an irreversible
real-world side effect (a WhatsApp message actually left), so once a
reminder's outcome is committed it will never be resent by a later tick.
With a single end-of-tick commit, a crash after reminder N of M would
leave all N already-sent messages still marked "pending" in the DB, and
the next tick would resend all of them. Net effect: this module guarantees
at-least-once delivery -- a crash between gate.deliver's send and the
per-reminder commit can cause the next tick to resend that one reminder,
but a reminder already committed as sent is never resent, and a due
reminder is never silently dropped.
"""
from datetime import datetime, timedelta, timezone

from fam import audit, cal, gate, rem

# On repeated "error" outcomes from gate.deliver, a reminder is cancelled
# rather than retried forever once its error_count reaches this many.
ERROR_CAP = 3


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(value):
    """Parse an ISO-8601 string into an aware UTC datetime. A naive string
    (no tzinfo) is treated as already UTC -- mirrors gate.py/rem.py's own
    local copy of this helper.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def reminders(conn, now_utc=None, cfg=None):
    """Run one reminders tick.

    Selects pending reminders with fire_at_utc <= now (rem.list_reminders
    due=True). For each:
      - if its event is no longer active (cancelled/done, or missing --
        the latter shouldn't happen given events(id) ON DELETE CASCADE,
        but is treated the same defensively), the reminder itself is
        marked cancelled and audited as rem.cancel_stale, with no send
        attempt. This is a backstop for reminders whose event's status
        changed by some path other than cal.cancel()/cal.done() (which
        already cancel their own chain via rem.cancel_chain).
      - else if it is stale -- its age (now - fire_at_utc) exceeds
        cfg["reminder_max_age_min"] (default 120, see
        gate.CONFIG_DEFAULTS), OR now is already past event.start_utc
        plus that same max age -- it is cancelled and audited as
        rem.cancel_stale_age, with no send attempt. This is a backstop
        for a reminder repeatedly parked by quiet hours/budget until it
        has become operationally pointless (e.g. an 8-hour-old "pora
        vyhodit'" for an event that already happened).
      - otherwise gate.deliver(kind="reminder", ...) rewrites and sends
        it: "sent" -> status=sent + sent_at=now; "quiet"/"budget" -> left
        pending (a later tick will retry once the window/budget clears);
        "error" -> the reminder's error_count is incremented; once it
        reaches ERROR_CAP (3), the reminder is cancelled and audited as
        rem.cancel_error_cap instead of being retried forever, otherwise
        it is left pending (a later tick will retry) and counted as
        "error" so a persistent-but-not-yet-capped failure stays visible.

    Commits once per due reminder (see module docstring), plus once more
    for the tick-level audit row below.

    ALWAYS audits tick.reminders {due, sent, quiet, budget, error,
    cancelled, stale, error_capped} -- including an all-zero run, so
    "nothing was due" is a recorded fact rather than indistinguishable
    from "the tick didn't run at all". This is a deliberately richer
    contract than the original implementation plan's literal
    {due, sent, skipped}: quiet/budget/error/cancelled/stale/error_capped
    are each their own bucket instead of being collapsed into "skipped",
    because a persistent delivery failure, a stale-age cancel, and a
    quiet-hours defer are operationally different things an admin needs
    to tell apart in the tick summary.

    Returns the counts dict.
    """
    now = now_utc or _now()
    now_dt = _parse_utc(now)
    cfg = cfg if cfg is not None else gate.load_config()
    max_age_min = cfg.get("reminder_max_age_min", 120)
    max_age = timedelta(minutes=max_age_min)

    due = rem.list_reminders(conn, due=True, now_utc=now)
    counts = {"due": len(due), "sent": 0, "quiet": 0, "budget": 0,
              "error": 0, "cancelled": 0, "stale": 0, "error_capped": 0}

    for reminder in due:
        event = cal.get(conn, reminder["event_id"])
        if event is None or event["status"] != "active":
            conn.execute(
                "UPDATE reminders SET status='cancelled' WHERE id=?",
                (reminder["id"],),
            )
            audit.log(conn, "rem.cancel_stale",
                      {"reminder_id": reminder["id"],
                       "event_id": reminder["event_id"]})
            counts["cancelled"] += 1
            conn.commit()
            continue

        fire_dt = _parse_utc(reminder["fire_at_utc"])
        start_dt = _parse_utc(event["start_utc"])
        age = now_dt - fire_dt
        if age > max_age or now_dt > start_dt + max_age:
            conn.execute(
                "UPDATE reminders SET status='cancelled' WHERE id=?",
                (reminder["id"],),
            )
            audit.log(conn, "rem.cancel_stale_age",
                      {"reminder_id": reminder["id"], "event_id": event["id"],
                       "fire_at_utc": reminder["fire_at_utc"],
                       "age_min": round(age.total_seconds() / 60)})
            counts["stale"] += 1
            conn.commit()
            continue

        raw = {
            "label": reminder["label"],
            "event_id": event["id"],
            "title": event["title"],
            "start_local": event["start_local"],
            "participants": [p["name"] for p in event["participants"]],
        }
        if event["place"]:
            raw["place_name"] = event["place"]["name"]
        human_fallback = (
            f"{reminder['label']}: {event['title']} — {event['start_local']}"
        )

        status = gate.deliver(conn, "reminder", raw, human_fallback, cfg,
                               now_utc=now)
        if status == "sent":
            conn.execute(
                "UPDATE reminders SET status='sent', sent_at=? WHERE id=?",
                (now, reminder["id"]),
            )
            counts["sent"] += 1
        elif status == "error":
            new_error_count = reminder["error_count"] + 1
            if new_error_count >= ERROR_CAP:
                conn.execute(
                    "UPDATE reminders SET status='cancelled', error_count=? "
                    "WHERE id=?",
                    (new_error_count, reminder["id"]),
                )
                audit.log(conn, "rem.cancel_error_cap",
                          {"reminder_id": reminder["id"],
                           "errors": new_error_count})
                counts["error_capped"] += 1
            else:
                conn.execute(
                    "UPDATE reminders SET error_count=? WHERE id=?",
                    (new_error_count, reminder["id"]),
                )
                counts["error"] += 1
        else:
            counts[status] += 1
        conn.commit()

    audit.log(conn, "tick.reminders", counts)
    conn.commit()
    return counts
