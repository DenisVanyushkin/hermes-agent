"""Nightly diagnostics digest for fam (design 2026-08-01).

Collects a structured 24h picture -- error signatures, probes, activity
counters, timers, backups -- into ~/.hermes/diagnostics/fam-digest-latest.json
for the agent's `fam-nightly-report` cron job to render via LLM. Collects
only; never delivers. Delivery and the watermark contract live in maint.py.
"""
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from . import db as famdb
from . import gate

MAX_EXAMPLES = 3
MAX_CONTEXT_VALUES = 10
SIGNATURE_MAX = 300

_HEX_RE = re.compile(r"[0-9a-fA-F]{8,}")
_NUM_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")

# Per-kind ALLOW-list (design §7). A deny-list would leak the day someone
# adds a field: gate.error already carries `raw` and `final` -- the full
# message text to Amina -- and this digest is handed to an external LLM.
#   sig  -- fields that are part of the signature (a different value is a
#           different defect)
#   ctx  -- fields collected as context (a different value is the SAME
#           defect hitting another row; intake_id must not split findings)
#   text -- whether payload["error"] may be used for signature/examples
ERROR_SPEC = {
    "tick.error": {"sig": ("where", "exc_type"), "ctx": ("intake_id",), "text": True},
    "gate.error": {"sig": ("kind",), "ctx": ("attempt",), "text": False},
    "mail.error": {"sig": (), "ctx": ("event_id",), "text": True},
    "road.error": {"sig": (), "ctx": ("event_id", "via"), "text": False},
}
ERROR_KINDS = tuple(ERROR_SPEC)


def normalize_signature(text):
    text = _HEX_RE.sub("<hex>", text or "")
    text = _NUM_RE.sub("<n>", text)
    return _WS_RE.sub(" ", text).strip()[:SIGNATURE_MAX]


def collect_errors(conn, since):
    """Fold every *.error row since `since` into deduplicated findings.

    Returns findings sorted by descending count. A row whose payload is
    not a JSON object still produces a finding keyed on its kind alone --
    silently dropping a malformed error row would hide exactly the kind
    of breakage this digest exists to surface."""
    placeholders = ",".join("?" * len(ERROR_KINDS))
    rows = conn.execute(
        f"SELECT kind, payload FROM audit_log WHERE ts_utc >= ? "
        f"AND kind IN ({placeholders}) ORDER BY id",
        (since, *ERROR_KINDS)).fetchall()
    buckets = {}
    for row in rows:
        spec = ERROR_SPEC[row["kind"]]
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        parts = [row["kind"]]
        for field in spec["sig"]:
            if payload.get(field) is not None:
                parts.append(f"{field}={payload[field]}")
        text = normalize_signature(str(payload.get("error") or "")) if spec["text"] else ""
        if text:
            parts.append(text)
        signature = "|".join(parts)
        bucket = buckets.get(signature)
        if bucket is None:
            bucket = {"signature": signature, "kind": row["kind"], "count": 0,
                      "context": {}, "examples": []}
            for field in spec["sig"]:
                if payload.get(field) is not None:
                    # p_ prefix: payload field names share a namespace with
                    # the finding's own keys, and gate.error's payload has
                    # a "kind" of its own (reminder/med/digest) that would
                    # otherwise overwrite the audit event kind above.
                    bucket[f"p_{field}"] = payload[field]
            buckets[signature] = bucket
        bucket["count"] += 1
        for field in spec["ctx"]:
            value = payload.get(field)
            if value is None:
                continue
            values = bucket["context"].setdefault(field, [])
            if value not in values and len(values) < MAX_CONTEXT_VALUES:
                values.append(value)
        if spec["text"]:
            example = str(payload.get("error") or "")[:200]
            if example and example not in bucket["examples"] \
                    and len(bucket["examples"]) < MAX_EXAMPLES:
                bucket["examples"].append(example)
    return sorted(buckets.values(), key=lambda b: -b["count"])


STATE_KEY = "maint_known_issues"


def load_state(conn):
    """Known-issue state from the meta table. Corrupt state degrades to
    empty rather than raising: a bad value must cost one night of age
    tracking, not the whole nightly sweep."""
    raw = famdb.meta_get(conn, STATE_KEY)
    if not raw:
        return {}
    try:
        state = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(state, dict):
        return {}
    # Leaf shape matters as much as the top level: a hand-edited or
    # half-written value like {"sig": "oops"} would otherwise sail past
    # this check and blow up in diff_known_issues' prior.get(). Dropping
    # the bad entry costs one night of age tracking for that signature;
    # raising here would cost the whole nightly sweep.
    return {k: v for k, v in state.items() if isinstance(v, dict)}


def save_state(conn, state):
    famdb.meta_set(conn, STATE_KEY, json.dumps(state, ensure_ascii=False))


def diff_known_issues(state, findings, now):
    """Annotate findings with new/known/age_days against prior state.

    Returns (annotated, resolved, new_state). Signatures absent from this
    window drop out of new_state entirely -- otherwise state would grow
    without bound and every long-gone defect would keep re-reporting as
    'resolved' every night."""
    now_iso = now.isoformat(timespec="seconds")
    annotated = []
    new_state = {}
    for finding in findings:
        signature = finding["signature"]
        prior = state.get(signature) or {}
        first_seen = prior.get("first_seen")
        if first_seen:
            status = "known"
            try:
                age_days = (now - datetime.fromisoformat(first_seen)).days
            except (TypeError, ValueError):
                first_seen, age_days, status = now_iso, 0, "new"
        else:
            first_seen, age_days, status = now_iso, 0, "new"
        annotated.append({**finding, "status": status,
                          "first_seen": first_seen, "age_days": age_days})
        new_state[signature] = {"first_seen": first_seen, "last_seen": now_iso,
                                "count": finding["count"]}
    resolved = [{"signature": sig, "first_seen": meta.get("first_seen"),
                 "last_seen": meta.get("last_seen")}
                for sig, meta in state.items() if sig not in new_state]
    return annotated, resolved, new_state


SYSTEMCTL_TIMEOUT = 30


def collect_activity(conn, cfg, since, now):
    """Counters only -- never message text. `sent_by_kind` uses the inner
    payload["kind"] (reminder/med/digest), which is a fixed vocabulary,
    not user content."""
    def _count(kind):
        return conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE kind=? AND ts_utc >= ?",
            (kind, since)).fetchone()[0]

    sent_by_kind = {}
    for row in conn.execute(
            "SELECT payload FROM audit_log WHERE kind='gate.sent' AND ts_utc >= ?",
            (since,)):
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        kind = payload.get("kind") or "?"
        sent_by_kind[kind] = sent_by_kind.get(kind, 0) + 1
    return {
        "sent_by_kind": sent_by_kind,
        "messages_sent": sum(sent_by_kind.values()),
        "meds_generated": _count("tick.med"),
        "meds_taken": _count("meds.take"),
        "reminder_chains_built": _count("rem.regenerate"),
        # gate.budget_spent_today's now_utc contract is a string (its
        # _parse_utc calls datetime.fromisoformat directly) -- every other
        # caller in the codebase passes one; `now` here is a datetime.
        "budget_spent": gate.budget_spent_today(
            conn, now_utc=now.isoformat(timespec="seconds")),
        "budget_limit": cfg.get("daily_budget", 8),
    }


def collect_calendar(conn, since):
    """Counter only. The design's audit rule for iPhone-controlled data
    is UID-and-counts-only: no event titles, calendar names or URLs ever
    leave the host through this digest."""
    total = 0
    for row in conn.execute(
            "SELECT payload FROM audit_log WHERE kind='cal.ext.sync' AND ts_utc >= ?",
            (since,)):
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        collisions = payload.get("collisions") if isinstance(payload, dict) else None
        if isinstance(collisions, int):
            total += collisions
    return {"collisions": total}


def _systemctl(runner, *args):
    result = runner(["systemctl", "--user", "--no-legend", "--no-pager", *args],
                    capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT)
    if result.returncode != 0:
        # Not an exception path: systemctl answers a dead session bus with a
        # non-zero code and EMPTY stdout, which would otherwise parse into
        # "no failed units, no timers" -- a monitoring collector inventing
        # health it never observed. Raise so build_digest's per-section
        # guard records it in section_errors instead.
        raise RuntimeError(
            f"systemctl {' '.join(args)} exited {result.returncode}: "
            f"{(result.stderr or '').strip()[:200]}")
    return result


def collect_timers(runner=None):
    """Failed fam units + the roster of loaded fam timers.

    Uses `list-units` rather than `list-timers`: list-timers output is
    six positional columns whose widths shift with locale and schedule,
    while list-units puts the unit name first on every line."""
    runner = runner or subprocess.run
    failed = []
    result = _systemctl(runner, "list-units", "--failed", "--all", "fam-*.service")
    for line in (result.stdout or "").splitlines():
        parts = line.replace("●", " ").split()
        if not parts:
            continue
        sub = parts[3] if len(parts) > 3 else "failed"
        description = " ".join(parts[4:])
        # SUB is usually the literal "failed" again for a failed unit --
        # redundant with the ACTIVE column already implying failure. Fall
        # back to the unit's Description text so the digest carries
        # something a human can actually act on.
        detail = description if sub == "failed" and description else sub
        failed.append({"unit": parts[0], "detail": detail})
    ok = []
    result = _systemctl(runner, "list-units", "--type=timer", "--all", "fam-*.timer")
    for line in (result.stdout or "").splitlines():
        parts = line.replace("●", " ").split()
        if parts:
            ok.append(parts[0])
    return {"failed": failed, "ok": ok}


def collect_backups(cfg, now, verify):
    """Newest dated backup + its integrity verdict. `verify` is injected
    (maint.verify_backup) so diag never imports maint -- maint imports
    diag, and the reverse edge would be a cycle."""
    backup_dir = Path(cfg["backup_dir"])
    files = sorted(backup_dir.glob("*-????????.db")) if backup_dir.is_dir() else []
    if not files:
        return {"last_path": None, "verify": "missing", "schema_version": None,
                "offsite_age_days": None}
    newest = files[-1]
    ok, detail = verify(newest)
    result = {"last_path": newest.name,
              "verify": "ok" if ok else str(detail.get("integrity")),
              "schema_version": detail.get("schema_version"),
              "offsite_age_days": None}
    if cfg.get("offsite_enabled"):
        offsite = Path(cfg["offsite_dir"])
        dumps = sorted(offsite.glob("*-????????.db.age")) if offsite.is_dir() else []
        if dumps:
            stamp = dumps[-1].name.split("-")[-1][:8]
            try:
                written = datetime.strptime(stamp, "%Y%m%d").replace(tzinfo=now.tzinfo)
                result["offsite_age_days"] = (now - written).days
            except ValueError:
                result["offsite_age_days"] = None
    return result
