"""Snapshot of open questions awaiting the user's answer.

The gateway reads this file at the start of every turn and injects a short
note into the turn's sidecar, so "there is an unanswered dose" reaches the
agent even when the conversation itself carries no trace of it.

Why a file and not a query: the gateway venv does not import ``fam`` (fam
ships as a CLI + cron), and a subprocess per inbound message is both slow
and fragile. fam owns the semantics and writes the snapshot; the gateway
owns a dumb, cheap reader. Written after every tick and after every
med ack, so it self-heals -- a missed write costs one stale-or-missing
note, never a wrong DB state.

Background (2026-07-23): the daily session reset fired between the 09:00
medication reminder and Amina's 09:25 "Готово". The fresh session had
history=0, the agent called no tools, intake #3 stayed ``pending`` and she
was re-nagged at 09:45. Chat history is not a durable place for pending
state; assistant.db is, and this file is its projection.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fam import meds, rem
from fam.cal import ALMATY


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(value):
    """Parse an ISO-8601 string into an aware UTC datetime. A naive string
    (no tzinfo) is treated as already UTC -- a local copy of tick.py's
    helper, kept local because tick imports this module.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

HOST_PATH = Path("/home/denis/.hermes/private/amina/pending-acks.json")
SANDBOX_PATH = Path("/root/.hermes/private/amina/pending-acks.json")

# How long an unanswered dose stays an "open question".  Past this it is
# history, not something to re-raise on every turn -- the nightly sweep
# owns it from there.
DUE_WINDOW_HOURS = 12




def _message_ids(conn, kind, ref_id):
    ids = [row[0] for row in conn.execute(
        "SELECT wa_message_id FROM sent_messages WHERE kind=? AND ref_id=?",
        (kind, ref_id),
    ).fetchall()]
    ids.extend(row[0] for row in conn.execute(
        "SELECT sm.wa_message_id FROM sent_message_refs smr "
        "JOIN sent_messages sm ON sm.id=smr.sent_message_id "
        "WHERE smr.kind=? AND smr.ref_id=?",
        (kind, ref_id),
    ).fetchall())
    return list(dict.fromkeys(ids))


def resolve_path():
    """Host path when its directory exists, else the sandbox path.

    Mirrors ``db.resolve_db_path``: the same tree is bind-mounted into the
    agent container under a different root.
    """
    for p in (HOST_PATH, SANDBOX_PATH):
        if p.parent.is_dir():
            return p
    return HOST_PATH


def build(conn, cfg=None, now_utc=None):
    """Return the snapshot dict: {generated_at, target, items[]}.

    Only intakes that are already due and still inside DUE_WINDOW_HOURS are
    included -- a dose scheduled for tonight is not an open question yet,
    and yesterday's must not haunt every turn.
    """
    now_raw = now_utc or _now()
    now = _parse_utc(now_raw)
    floor = now - timedelta(hours=DUE_WINDOW_HOURS)

    items = []
    for row in meds.list_pending(conn):
        plan = _parse_utc(row["plan_ts_utc"])
        if plan > now or plan < floor:
            continue
        med = meds.get(conn, row["med_id"]) or {}
        due_local = plan.astimezone(ALMATY).strftime("%H:%M")
        deferred = False
        deferred_until = row.get("deferred_until_utc")
        if deferred_until:
            deferred_dt = _parse_utc(deferred_until)
            if deferred_dt > now:
                due_local = deferred_dt.astimezone(ALMATY).strftime("%H:%M")
                deferred = True
        item = {
            "kind": "med_intake",
            "id": row["intake_id"],
            "ref_id": row["intake_id"],
            "current_state": "pending",
            "wa_message_ids": _message_ids(conn, "med", row["intake_id"]),
            "name": row["name"],
            "dose": med.get("dose") or "",
            "plan_ts_utc": row["plan_ts_utc"],
            "due_local": due_local,
            "ack_cmd": f"fam med taken {row['intake_id']}",
            "skip_cmd": f"fam med skip {row['intake_id']}",
        }
        if deferred:
            item["deferred"] = True
        items.append(item)

    items.extend(rem.open_resolution_candidates(
        conn,
        now_utc=now_raw,
        max_age_min=(cfg or {}).get("reminder_max_age_min", 120),
    ))

    return {
        "generated_at": now_raw if isinstance(now_raw, str) else now.isoformat(),
        "target": (cfg or {}).get("target", ""),
        "items": items,
    }


def write(conn, cfg=None, path=None, now_utc=None):
    """Write the snapshot atomically. Never raises.

    Always writes, including an empty item list: an empty snapshot is what
    tells the gateway to stop injecting an already-answered question, so
    "nothing pending" must overwrite, not skip.
    """
    target = Path(path) if path is not None else Path(
        (cfg or {}).get("pending_acks_path") or resolve_path())
    try:
        snapshot = build(conn, cfg=cfg, now_utc=now_utc)
        tmp = target.with_name(target.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, target)
        return snapshot
    except Exception:  # noqa: BLE001 -- a snapshot must never break a tick
        try:
            tmp = target.with_name(target.name + ".tmp")
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return None
