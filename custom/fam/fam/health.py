"""Phase 6b health probes: pure reads, no side-effects.

Each probe returns a ProbeResult dict. Delivery and de-dup live in the
callers (maint.problem_summary, tick readiness alert) -- a probe never
sends a message or writes to the DB.
"""
import json
import os
from . import car

def _result(name, status, detail="", last_ok_ts=None):
    return {"name": name, "status": status, "detail": detail, "last_ok_ts": last_ok_ts}

def bridge_readiness(conn, cfg, now=None):
    """ok/down from the last connect/disconnect marker seen in the current
    gateway.log. Streams the file line-by-line (it's rotation-bounded to a
    few MB) rather than loading it all into memory. Does NOT depend on
    message flow -- a quiet chat is not "down"."""
    log_path = cfg["gateway_log_path"]
    connect_markers = cfg["readiness_markers_connect"]
    disconnect_markers = cfg["readiness_markers_disconnect"]

    if not os.path.exists(log_path):
        return _result("bridge_readiness", "down", "gateway.log отсутствует")

    last = None  # (idx, "down"|"ok", line)
    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
        for idx, line in enumerate(fh):
            stripped = line.strip()
            if any(marker in stripped for marker in disconnect_markers):
                last = (idx, "down", stripped)
            elif any(marker in stripped for marker in connect_markers):
                last = (idx, "ok", stripped)

    if last is None:
        return _result("bridge_readiness", "ok",
                        "нет свежего маркера (re)connect — считаем, что на связи")

    _, status, line = last
    return _result("bridge_readiness", status, line)

def starline_staleness(conn, cfg, now=None):
    """degraded when the newest car_metrics row is older than
    car_staleness_hours (or none exists); ok otherwise. Never re-alerts."""
    stale = car.check_staleness(conn, cfg, now=now)
    row = conn.execute("SELECT MAX(ts_utc) AS m FROM car_metrics").fetchone()
    last = row["m"] if row else None
    return _result("starline_staleness",
                   "degraded" if stale else "ok",
                   "нет свежих данных о машине" if stale else "свежо",
                   last_ok_ts=last)

def degradation_flags(conn, cfg, now=None):
    """Informational: surface known fallback state (road on straight-line
    fallback). Reads the most recent road.* audit marker; absence == ok."""
    row = conn.execute(
        "SELECT payload FROM audit_log WHERE kind LIKE 'road.%' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        payload = json.loads(row["payload"])
        src = payload.get("source")
        if src and src != "tomtom":
            return _result("degradation_flags", "degraded",
                           f"дорога на фолбэке: {src}")
    return _result("degradation_flags", "ok", "без деградаций")

def all_probes(conn, cfg, now=None):
    """Run every probe; a probe that raises becomes a down result so one
    broken probe never sinks the summary."""
    out = []
    for fn in (bridge_readiness, starline_staleness, degradation_flags):
        try:
            out.append(fn(conn, cfg, now=now))
        except Exception as e:                        # noqa: BLE001 -- isolate
            out.append(_result(fn.__name__, "down", f"проба упала: {e}"))
    return out
