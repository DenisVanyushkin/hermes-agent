"""Phase 6a maintenance: audit_log retention + DB backups + verify."""
from datetime import datetime, timezone, timedelta
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from . import audit
from . import db as famdb
from . import diag
from . import gate, health

def _now_utc():
    return datetime.now(timezone.utc)

def _summary_watermark(conn, now):
    val = famdb.meta_get(conn, "maint_summary_last_run")
    if val:
        return val
    return (now - timedelta(hours=24)).isoformat(timespec="seconds")


def _problem_lines(digest):
    """Human-readable problem lines for the emergency fallback only --
    the LLM report renders from the digest itself. Kept deliberately
    close to the pre-2026-08 format so a fallback message still reads
    like the summary Denis is used to, plus counts and age."""
    sections = digest["sections"]
    lines = []
    for finding in sections.get("errors", {}).get("findings", []):
        where = finding.get("p_where") or finding.get("kind")
        example = (finding.get("examples") or [""])[0]
        lines.append(f"{where}: {example} (×{finding['count']}, "
                     f"{finding['status']}, {finding['age_days']} дн.)")
    collisions = sections.get("calendar", {}).get("collisions", 0)
    if collisions:
        lines.append(f"календарь: {collisions} совпадающих записей, разобрать вручную")
    for probe in sections.get("probes", []):
        if probe["status"] != "ok":
            lines.append(f"{probe['name']}: {probe['detail'] or probe['status']}")
    for rejected in sections.get("backups", {}).get("rejected") or []:
        # Not only through the LLM report: a foreign or corrupt file sitting
        # in the backup directory is exactly the finding that must not
        # depend on that report having been delivered.
        lines.append(f"бэкап отбракован: {rejected['name']} — {rejected['why']}")
    for unit in sections.get("timers", {}).get("failed", []):
        lines.append(f"unit {unit['unit']}: {unit['detail']}")
    for err in sections.get("maintenance_errors", []):
        lines.append(f"maintenance: {err}")
    for name, err in digest.get("section_errors", {}).items():
        lines.append(f"сборщик {name}: {err}")
    return lines


def problem_summary(cfg, now=None, notify=None, run_errors=None):
    """Nightly sweep: collect the digest, publish it for the agent's
    `fam-nightly-report` job, and guard the delivery contract.

    Delivery moved out of fam (design 2026-08-01): the LLM report is
    rendered and delivered by the agent. What stays here is the invariant
    this function has always enforced -- the watermark closes a window
    only once its problems reached Denis. The watermark marks how far
    delivery is CONFIRMED, not when we last looked: on a confirmed report
    it moves to the previous digest's own timestamp, on a delivered raw
    fallback to now, and otherwise it does not move at all -- including
    on a clean night, because a clean window says nothing about an
    earlier digest that never arrived.

    The fallback stays an emergency channel and is silent on a clean
    night: daily cadence is the LLM report's contract, not fam's, and
    making fam chatty while the job is being rolled out would be a
    regression against today's behaviour."""
    now = now or _now_utc()
    notify = notify or gate.notify_denis
    conn = famdb.connect()
    try:
        since = _summary_watermark(conn, now)
        previous_digest_at = famdb.meta_get(conn, "maint_digest_last_written")
        delivered, detail = diag.report_delivery_status(
            cfg.get("report_jobs_path", gate.CONFIG_DEFAULTS["report_jobs_path"]),
            cfg.get("report_job_name", gate.CONFIG_DEFAULTS["report_job_name"]),
            previous_digest_at)
        digest, new_state = diag.build_digest(
            conn, cfg, since, now,
            delivery={"previous_report_ok": delivered,
                      "previous_digest_at": previous_digest_at,
                      "detail": detail},
            state=diag.load_state(conn), verify=verify_backup)
        digest["sections"]["maintenance_errors"] = list(run_errors or [])
        diag.save_state(conn, new_state)
        # `or` not bare `.get(key, default)`: as with report_jobs_path
        # above, `.get` substitutes its default only for an ABSENT key --
        # "diagnostics_dir": null in the live config would otherwise reach
        # Path(None) inside write_digest and raise TypeError, which
        # run_maintenance swallows into result["errors"] silently (no
        # digest, no fallback, frozen watermark).
        path = diag.write_digest(
            digest,
            str(cfg.get("diagnostics_dir") or diag.DEFAULT_DIAGNOSTICS_DIR), now)
        famdb.meta_set(conn, "maint_digest_last_written", digest["generated_at"])
        conn.commit()

        problems = _problem_lines(digest)
        fallback_sent = False
        if delivered:
            # To the CONFIRMED digest's timestamp, not to now. The digest
            # written moments ago is still unconfirmed: closing the window
            # on it would stake this night's problems on a delivery nobody
            # has verified. Consequence -- consecutive digests overlap by
            # one night, which is the safety margin: an undelivered
            # digest's contents reappear in the next one, deduplicated by
            # signature and marked `known`.
            advance_to = previous_digest_at
        elif problems:
            fallback_sent = bool(notify(
                "Гермес — сводка за сутки (LLM-отчёт не дошёл):\n"
                + "\n".join(f"• {p}" for p in problems)))
            advance_to = now.isoformat(timespec="seconds") if fallback_sent else None
        else:
            # Clean window, report unconfirmed: nothing was delivered, so
            # nothing may be closed. Advancing here is what would strand an
            # earlier undelivered digest behind the watermark forever.
            advance_to = None
        if advance_to:
            famdb.meta_set(conn, "maint_summary_last_run", advance_to)
            conn.commit()
        return {"digest_path": str(path), "delivery_ok": delivered,
                "delivery_detail": detail, "fallback_sent": fallback_sent,
                "problems": problems, "skipped_clean": not problems}
    finally:
        conn.close()

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
    os.chmod(dest, 0o600)   # backups carry the same PII as the live DB
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


def _age_encrypt(plain_path, dest_age, recipient):
    """Encrypt plain_path -> dest_age for `recipient`. Shells out to age
    (v1.1.1). Raises CalledProcessError on failure (caught by offsite_backup)."""
    subprocess.run(["age", "-r", recipient, "-o", str(dest_age), str(plain_path)],
                   check=True, capture_output=True)


def _rotate_age(dest_dir, stem, keep):
    files = sorted(Path(dest_dir).glob(f"{stem}-*.db.age"))  # YYYYMMDD sorts chrono
    if keep > 0:
        for old in files[:-keep]:
            old.unlink()


def offsite_backup(cfg, now=None):
    """Weekly offsite: online .backup -> age-encrypt -> atomically publish
    <stem>-YYYYMMDD.db.age onto the NFS mount; rotate to offsite_keep.
    Never raises; failures land in result['errors'] for problem_summary."""
    now = now or _now_utc()
    off = cfg["offsite_dir"]
    recipient = cfg["offsite_age_recipient"]
    result = {"written": [], "pruned": [], "errors": []}
    if not recipient:
        result["errors"].append("offsite: no age recipient configured")
        return result
    if not os.path.ismount(off):
        result["errors"].append(f"offsite: {off} not mounted")
        return result
    targets = []
    try:
        targets.append(famdb.resolve_db_path())
        state = cfg.get("state_db_path")
        if state and Path(state).exists():
            targets.append(Path(state))
    except Exception as e:                       # noqa: BLE001 -- guard
        result["errors"].append(f"offsite resolve: {e}")
    for src in targets:
        src = Path(src)
        try:
            with tempfile.TemporaryDirectory() as td:
                plain = Path(td) / f"{src.stem}.db"
                _sqlite_backup(src, plain)                       # online, consistent
                final = Path(off) / f"{src.stem}-{now.strftime('%Y%m%d')}.db.age"
                tmp_age = Path(off) / (final.name + ".tmp")
                _age_encrypt(plain, tmp_age, recipient)
                os.replace(tmp_age, final)                       # atomic publish on NFS
            result["written"].append(str(final))
            _rotate_age(off, src.stem, cfg["offsite_keep"])
        except Exception as e:                   # noqa: BLE001 -- guard: one DB failing must not skip the other
            result["errors"].append(f"offsite {src.name}: {e}")
    return result
