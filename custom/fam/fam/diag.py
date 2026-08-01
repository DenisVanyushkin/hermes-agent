"""Nightly diagnostics digest for fam (design 2026-08-01).

Collects a structured 24h picture -- error signatures, probes, activity
counters, timers, backups -- into ~/.hermes/diagnostics/fam-digest-latest.json
for the agent's `fam-nightly-report` cron job to render via LLM. Collects
only; never delivers. Delivery and the watermark contract live in maint.py.
"""
import json
import re

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
                    bucket[field] = payload[field]
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
