# custom/fam/fam/brevity.py
"""Phase 6c weekly brevity audit: corpus builder (pure read) + deterministic
stats. The LLM reviewer (aux model) lives in review() (Task 2); stats are
computed HERE, in code, never by the model."""
import json
from datetime import datetime, timezone, timedelta

def _now_utc():
    return datetime.now(timezone.utc)

def _raw_text(raw):
    if not isinstance(raw, dict):
        return str(raw)
    for key in ("label", "text", "question", "summary"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return json.dumps(raw, ensure_ascii=False)

def collect_corpus(conn, cfg, now=None):
    now = now or _now_utc()
    days = cfg.get("brevity_window_days", 7)
    since = (now - timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT ts_utc, payload FROM audit_log WHERE kind='gate.sent' "
        "AND ts_utc >= ? ORDER BY id", (since,)).fetchall()
    items = []
    for r in rows:
        p = json.loads(r["payload"])
        final = p.get("final")
        if not final:
            continue
        items.append({"kind": p.get("kind"), "raw_text": _raw_text(p.get("raw")),
                      "final": final, "ts_utc": r["ts_utc"]})
    total = len(items)
    rewritten = sum(1 for i in items if i["raw_text"] != i["final"])
    avg_len = (sum(len(i["final"]) for i in items) / total) if total else 0.0
    stats = {"total": total, "days": days,
             "per_day": round(total / days, 2) if days else 0.0,
             "rewrite_ratio": round(rewritten / total, 2) if total else 0.0,
             "avg_len": round(avg_len, 1)}
    return {"items": items, "stats": stats}
