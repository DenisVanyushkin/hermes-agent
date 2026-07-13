"""Phase 6a maintenance: audit_log retention + DB backups + verify."""
from datetime import datetime, timezone, timedelta
import sqlite3
from pathlib import Path
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
