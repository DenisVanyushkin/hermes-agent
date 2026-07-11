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
the next tick would resend all of them.
"""
from datetime import datetime, timezone

from fam import audit, cal, gate, rem


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
      - otherwise gate.deliver(kind="reminder", ...) rewrites and sends
        it: "sent" -> status=sent + sent_at=now; "quiet"/"budget" -> left
        pending (a later tick will retry once the window/budget clears);
        "error" -> left pending too, but counted separately so a
        persistent delivery failure is visible in the tick summary.

    Commits once per due reminder (see module docstring), plus once more
    for the tick-level audit row below.

    ALWAYS audits tick.reminders {due, sent, quiet, budget, error,
    cancelled} -- including an all-zero run, so "nothing was due" is a
    recorded fact rather than indistinguishable from "the tick didn't
    run at all".

    Returns the counts dict.
    """
    now = now_utc or _now()
    cfg = cfg if cfg is not None else gate.load_config()

    due = rem.list_reminders(conn, due=True, now_utc=now)
    counts = {"due": len(due), "sent": 0, "quiet": 0, "budget": 0,
              "error": 0, "cancelled": 0}

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
        counts[status] += 1
        conn.commit()

    audit.log(conn, "tick.reminders", counts)
    conn.commit()
    return counts
