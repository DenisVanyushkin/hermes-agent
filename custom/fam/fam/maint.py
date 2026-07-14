"""Phase 6a maintenance: audit_log retention + DB backups + verify."""
from datetime import datetime, timezone, timedelta
import json
import sqlite3
from pathlib import Path
from . import audit
from . import db as famdb
from . import gate, health

def _now_utc():
    return datetime.now(timezone.utc)

def _summary_watermark(conn, now):
    val = famdb.meta_get(conn, "maint_summary_last_run")
    if val:
        return val
    return (now - timedelta(hours=24)).isoformat(timespec="seconds")

def problem_summary(cfg, now=None, notify=None, run_errors=None):
    """Nightly day-wide health sweep. Scans audit_log since the last run
    for minute-tick failure markers, snapshots probes, folds in this run's
    maintenance errors (run_errors), and (if anything is non-clean)
    delivers ONE consolidated message to Denis. Clean -> silence.
    notify defaults to gate.notify_denis; injected in tests."""
    now = now or _now_utc()
    notify = notify or gate.notify_denis
    conn = famdb.connect()
    try:
        since = _summary_watermark(conn, now)
        rows = conn.execute(
            "SELECT kind, payload FROM audit_log WHERE ts_utc >= ? "
            "AND kind = 'tick.error' "
            "ORDER BY id", (since,)).fetchall()
        problems = []
        for r in rows:
            payload = json.loads(r["payload"])
            problems.append(
                f"тик {payload.get('where','?')}: {payload.get('error','')}")
        for e in run_errors or []:
            problems.append(f"maintenance: {e}")
        probes = health.all_probes(conn, cfg, now=now)
        probe_problems = [f"{p['name']}: {p['detail'] or p['status']}"
                          for p in probes if p["status"] != "ok"]
        all_problems = problems + probe_problems
        famdb.meta_set(conn, "maint_summary_last_run",
                       now.isoformat(timespec="seconds"))
        conn.commit()
    finally:
        conn.close()
    if not all_problems:
        return {"problems": [], "probes": probes, "sent": False, "skipped_clean": True}
    body = "Гермес — сводка за сутки:\n" + "\n".join(f"• {p}" for p in all_problems)
    sent = bool(notify(body))
    return {"problems": all_problems, "probes": probes,
            "sent": sent, "skipped_clean": False}

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

def _sqlite_backup(src, dest):
    # timeout/busy_timeout: under a concurrent minute-tick write against
    # src, scon.backup(dcon) can otherwise raise "database is locked" --
    # give both connections a generous window to wait out the writer
    # instead of failing the nightly backup.
    scon = sqlite3.connect(str(src), timeout=10.0)
    scon.execute("PRAGMA busy_timeout=10000")
    dcon = sqlite3.connect(str(dest), timeout=10.0)
    dcon.execute("PRAGMA busy_timeout=10000")
    try:
        with dcon:
            scon.backup(dcon)   # online backup API -- consistent under a live writer
    finally:
        scon.close(); dcon.close()

def _rotate(dest_dir, stem, keep):
    files = sorted(Path(dest_dir).glob(f"{stem}-*.db"))  # YYYYMMDD sorts chronologically
    if keep > 0:
        for old in files[:-keep]:
            old.unlink()

def _backup_dest(src, dest_dir, now):
    """Single source of truth for the dated backup filename, shared by
    backup_db and run_maintenance's dry-run preview so they cannot drift."""
    return Path(dest_dir) / f"{Path(src).stem}-{now.strftime('%Y%m%d')}.db"

def backup_db(src, dest_dir, keep, now=None):
    now = now or _now_utc()
    src = Path(src); dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_dir.chmod(0o700)
    dest = _backup_dest(src, dest_dir, now)
    _sqlite_backup(src, dest)
    _rotate(dest_dir, src.stem, keep)
    return dest


def verify_backup(path):
    """(ok, detail) for a backup file: PRAGMA integrity_check == 'ok'
    AND a schema_version present in meta. A non-sqlite/corrupt file
    surfaces as ok=False, not an exception."""
    try:
        con = sqlite3.connect(str(path))
    except sqlite3.Error as e:
        return False, {"integrity": f"open-error: {e}", "schema_version": None}
    try:
        integ = con.execute("PRAGMA integrity_check").fetchone()[0]
        row = con.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()
        schema = row[0] if row else None
        return (integ == "ok" and schema is not None,
                {"integrity": integ, "schema_version": schema})
    except sqlite3.DatabaseError as e:
        return False, {"integrity": f"error: {e}", "schema_version": None}
    finally:
        con.close()


def run_maintenance(cfg, dry_run=False, now=None):
    now = now or _now_utc()
    result = {"pruned": 0, "backups": [], "errors": []}
    # 1. retention (own connection, like the cmd_tick_* handlers)
    try:
        conn = famdb.connect()
        try:
            if dry_run:
                cutoff = (now - timedelta(days=cfg["audit_retention_days"])
                          ).isoformat(timespec="seconds")
                result["pruned"] = conn.execute(
                    "SELECT COUNT(*) FROM audit_log WHERE ts_utc < ?", (cutoff,)
                ).fetchone()[0]
            else:
                result["pruned"] = prune_audit_log(
                    conn, cfg["audit_retention_days"], now=now)
        finally:
            conn.close()
    except Exception as e:                       # noqa: BLE001 -- guard: one step failing must not skip the other
        result["errors"].append(f"prune: {e}")
    # 2. backups: assistant.db (resolve_db_path) + state.db if present
    targets = []
    try:
        targets.append(famdb.resolve_db_path())
        state = cfg.get("state_db_path")
        if state and Path(state).exists():
            targets.append(state)
    except Exception as e:                       # noqa: BLE001 -- guard: target resolution must not crash the tick
        result["errors"].append(f"resolve targets: {e}")
    for src in targets:
        try:
            if dry_run:
                result["backups"].append(str(
                    _backup_dest(src, cfg["backup_dir"], now)))
            else:
                result["backups"].append(str(
                    backup_db(src, cfg["backup_dir"], cfg["backup_keep"], now=now)))
        except Exception as e:                   # noqa: BLE001
            result["errors"].append(f"backup {src}: {e}")
    # 3. nightly problem summary sweep (guarded: a summary failure must
    # not prevent the errors-audit block below from seeing prior errors)
    try:
        if not dry_run:
            result["summary"] = problem_summary(cfg, now=now, run_errors=list(result["errors"]))
        else:
            result["summary"] = {"skipped_clean": None, "dry_run": True}
    except Exception as e:                           # noqa: BLE001 -- guard
        result["errors"].append(f"summary: {e}")
    # failures are recorded into the same journal `fam log` reads (design §7);
    # best-effort -- journald + the non-zero CLI exit are the backstop if even
    # this write fails. Skipped on dry-run.
    if result["errors"] and not dry_run:
        try:
            c = famdb.connect()
            try:
                audit.log(c, "tick.maintenance",
                          {"op": "errors", "errors": result["errors"]}, actor="tick")
                c.commit()
            finally:
                c.close()
        except Exception:                        # noqa: BLE001
            pass
    return result
