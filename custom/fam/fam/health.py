"""Phase 6b health probes: pure reads, no side-effects.

Each probe returns a ProbeResult dict. Delivery and de-dup live in the
callers (maint.problem_summary, tick readiness alert) -- a probe never
sends a message or writes to the DB.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from . import car
from . import db as famdb, gate

def _result(name, status, detail="", last_ok_ts=None, **extra):
    result = {
        "name": name, "status": status, "detail": detail,
        "last_ok_ts": last_ok_ts,
    }
    result.update(extra)
    return result

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

def extcal_staleness(conn, cfg, now_utc=None):
    """Calque of car.check_staleness, but reading `meta.extcal_last_run`
    instead of a car_metrics row -- Task 6's cal-ext tick is a 15-minute
    silent timer that never messages Amina on its own (invariant), so a
    dead timer (unit disabled, VM rebooted, Apple ID password rotated)
    would otherwise go unnoticed forever: `tick.error` only catches a
    *failed run*, never the absence of any run at all.

    Three-way read, pure (never writes conn or meta):
    The shared result field last_ok_ts carries the
    extcal_last_run timestamp for this probe.
    - `extcal_enabled` falsy -> "ok", silent: sync is deliberately off,
      that is not a degradation. Must be checked BEFORE looking at
      `extcal_last_run` -- a prod box with the sync never turned on has no
      such key either, and that is the *other*, non-degraded, reason for
      it being absent.
    - enabled but `meta.extcal_last_run` missing entirely -> "degraded":
      the sync has never run, distinct from
      merely being stale.
    - enabled and present but older than `extcal_stale_hours` -> "degraded"
      with the human-readable age since the last run.
    - enabled and fresh -> "ok".
    """
    if not cfg.get("extcal_enabled"):
        return _result("extcal_staleness", "ok", "extcal выключен")
    last = famdb.meta_get(conn, "extcal_last_run")
    if not last:
        return _result("extcal_staleness", "degraded",
                        "extcal включён, но синк ни разу не запускался")
    now_dt = datetime.now(timezone.utc) if now_utc is None else now_utc
    if isinstance(now_dt, str):
        now_dt = datetime.fromisoformat(now_dt)
    last_dt = datetime.fromisoformat(last)
    age = now_dt - last_dt
    stale_hours = cfg["extcal_stale_hours"]
    if age > timedelta(hours=stale_hours):
        age_hours = age.total_seconds() / 3600
        return _result(
            "extcal_staleness", "degraded",
            f"Синк iCloud не запускался {age_hours:.1f}ч "
            f"(порог {stale_hours}ч)",
            last_ok_ts=last)
    return _result("extcal_staleness", "ok", "свежо", last_ok_ts=last)

def extcal_failures(conn, cfg, now_utc=None):
    """Pure read of active export issues and split terminal streaks.

    The probe exposes only event IDs and bounded enum/status fields.  It
    deliberately never reads event titles, hrefs, or ICS bodies, and never
    changes the database or sends a message.  Its streak view is separate
    from extcal_staleness: it does not use a timestamp or extcal_last_ok.
    """
    rows = conn.execute(
        "SELECT event_id, action, kind, http_status, reason_code, "
        "first_seen_utc, last_seen_utc "
        "FROM extcal_export_issues WHERE target=? ORDER BY event_id",
        ("hermes",),
    ).fetchall()
    issues = [dict(row) for row in rows]
    streaks = {}
    for label, key in (
        ("import_apply", "__import_apply__"),
        ("export", "__export__"),
    ):
        raw = famdb.meta_get(conn, f"extcal_fail_streak:{key}", "0")
        try:
            streaks[label] = max(0, int(raw))
        except (TypeError, ValueError):
            streaks[label] = 0

    if issues:
        ids = ", ".join(str(row["event_id"]) for row in issues)
        actions = "; ".join(
            f"{str(row['action']).upper()} {row['http_status'] or ''}".strip()
            for row in issues)
        count = len(issues)
        if 10 < count % 100 < 20:
            word = "\u0441\u043e\u0431\u044b\u0442\u0438\u0439"
        elif count % 10 == 1:
            word = "\u0441\u043e\u0431\u044b\u0442\u0438\u0435"
        elif count % 10 in (2, 3, 4):
            word = "\u0441\u043e\u0431\u044b\u0442\u0438\u044f"
        else:
            word = "\u0441\u043e\u0431\u044b\u0442\u0438\u0439"
        detail = (
            f"\u044d\u043a\u0441\u043f\u043e\u0440\u0442 iCloud "
            f"\u0437\u0430\u043b\u0438\u043f: {count} {word} "
            f"({ids}), {actions}")
        return _result("extcal_failures", "degraded", detail,
                       issues=issues, streaks=streaks)
    active_streaks = ", ".join(
        f"{label}={value}" for label, value in streaks.items() if value)
    if active_streaks:
        return _result(
            "extcal_failures", "degraded",
            "\u043e\u0448\u0438\u0431\u043a\u0438 cal-ext: "
            f"{active_streaks}",
            issues=issues, streaks=streaks)
    return _result("extcal_failures", "ok",
                   "\u043e\u0448\u0438\u0431\u043e\u043a "
                   "\u044d\u043a\u0441\u043f\u043e\u0440\u0442\u0430 "
                   "\u043d\u0435\u0442",
                   issues=issues, streaks=streaks)



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

def maybe_alert_readiness(conn, cfg, now=None, notify=None):
    """Edge-triggered bridge-down alert (meta flag bridge_down_alerted),
    mirroring car.maybe_alert_staleness: alert once on ok->down, stay
    silent while down, clear on recovery so the next episode alerts anew.
    Returns True iff an alert was sent this call. Caller owns the commit."""
    notify = notify or gate.notify_denis
    status = bridge_readiness(conn, cfg, now=now)["status"]
    alerted = famdb.meta_get(conn, "bridge_down_alerted", "0") == "1"
    if status == "down" and not alerted:
        notify("Гермес: приём отвалился (bridge down)")
        famdb.meta_set(conn, "bridge_down_alerted", "1")
        return True
    if status == "ok" and alerted:
        famdb.meta_set(conn, "bridge_down_alerted", "0")
    return False

def all_probes(conn, cfg, now=None):
    """Run every probe; a probe that raises becomes a down result so one
    broken probe never sinks the summary.

    Called positionally (not `now=now`) so this loop stays agnostic to a
    probe's own parameter name -- `extcal_staleness` names its third
    parameter `now_utc` (matching the rest of the `extcal` module family)
    rather than `now`."""
    out = []
    for fn in (bridge_readiness, starline_staleness, degradation_flags,
               extcal_staleness, extcal_failures):
        try:
            out.append(fn(conn, cfg, now))
        except Exception as e:                        # noqa: BLE001 -- isolate
            out.append(_result(fn.__name__, "down", f"проба упала: {e}"))
    return out
