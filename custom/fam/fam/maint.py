"""Phase 6a maintenance: audit_log retention + DB backups + verify."""
from datetime import datetime, timezone, timedelta
import sqlite3
from pathlib import Path
from . import audit
from . import db as famdb

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

def _sqlite_backup(src, dest):
    scon = sqlite3.connect(str(src))
    dcon = sqlite3.connect(str(dest))
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

def backup_db(src, dest_dir, keep, now=None):
    now = now or _now_utc()
    src = Path(src); dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_dir.chmod(0o700)
    dest = dest_dir / f"{src.stem}-{now.strftime('%Y%m%d')}.db"
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
    targets = [famdb.resolve_db_path()]
    state = cfg.get("state_db_path")
    if state and Path(state).exists():
        targets.append(state)
    for src in targets:
        try:
            if dry_run:
                result["backups"].append(str(
                    Path(cfg["backup_dir"]) / f"{Path(src).stem}-{now.strftime('%Y%m%d')}.db"))
            else:
                result["backups"].append(str(
                    backup_db(src, cfg["backup_dir"], cfg["backup_keep"], now=now)))
        except Exception as e:                   # noqa: BLE001
            result["errors"].append(f"backup {src}: {e}")
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
