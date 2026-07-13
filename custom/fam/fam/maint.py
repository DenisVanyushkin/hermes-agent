"""Phase 6a maintenance: audit_log retention + DB backups + verify."""
from datetime import datetime, timezone, timedelta
from . import audit

def _now_utc():
    return datetime.now(timezone.utc)

def prune_audit_log(conn, days, now=None):
    """Delete audit_log rows older than `days`; return deleted count.
    Records the prune itself (kind='tick.maintenance', actor='tick') AFTER
    the DELETE so the maintenance record (ts=now) is never self-pruned."""
    now = now or _now_utc()
    cutoff = (now - timedelta(days=days)).isoformat(timespec="seconds")
    deleted = conn.execute(
        "DELETE FROM audit_log WHERE ts_utc < ?", (cutoff,)).rowcount
    audit.log(conn, "tick.maintenance",
              {"op": "prune_audit_log", "days": days, "deleted": deleted},
              actor="tick")
    conn.commit()
    return deleted
