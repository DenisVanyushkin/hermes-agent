"""Nightly diagnostics digest for fam (design 2026-08-01).

Collects a structured 24h picture -- error signatures, probes, activity
counters, timers, backups -- into ~/.hermes/diagnostics/fam-digest-latest.json
for the agent's `fam-nightly-report` cron job to render via LLM. Collects
only; never delivers. Delivery and the watermark contract live in maint.py.
"""
import json
import re
from datetime import datetime
from . import db as famdb

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
