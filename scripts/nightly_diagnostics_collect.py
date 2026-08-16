#!/usr/bin/env python3
"""Collect a 24h diagnostics digest for the morning reporter agent.

Writes ~/.hermes/diagnostics/digest-latest.json (+ dated copy, 14-day
rotation) and maintains known-issues.json across nights. Prints nothing on
success so the no-agent cron job stays silent in Slack.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

WINDOW_HOURS = 24
DOCTOR_TIMEOUT = 180
ROTATE_DAYS = 14
MAX_EXAMPLES = 3
MAX_TAIL_CHARS = 700
STATUS_SCHEMA_VERSION = "collector-status.v1"
STATUS_FILENAME = "collector-status.json"

LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+(?P<level>[A-Z]+)\s+(?P<rest>.*)$"
)
MEMORY_RE = re.compile(r"\[MEMORY\] rss=(\d+)mb", re.IGNORECASE)

# Known-benign log lines that are WARNING-level but carry no actionable signal —
# routine startup narration or deliberately-unfixed config gaps. They are dropped
# from log findings so the nightly report stops re-surfacing them every night.
# Each entry is a substring matched against the message text (after the
# "TIMESTAMP LEVEL " prefix). Keep this list curated and commented — add a line
# ONLY when the warning is confirmed cosmetic or an accepted, deliberate state.
NOISE_PATTERNS = (
    # Telegram: normal startup path, logged at WARNING. Adapter resolves backup
    # API IPs via DoH then connects on attempt 1/8 — always followed by an INFO
    # "Connected to Telegram". No impact. (2026-07-06)
    "Discovering Telegram API fallback IPs via DNS-over-HTTPS",
    "Connecting to Telegram (attempt",
    # Slack: multi-person-DM (mpim) support is deliberately NOT enabled — the app
    # lacks mpim:history/message.mpim and we chose not to add them. Regular
    # channels and 1:1 DMs are unaffected. Accepted gap, not a defect. (2026-07-06)
    "Group DMs (multi-person DMs) will not work",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _as_aware_utc(value).isoformat(timespec="seconds")


def _new_run_id(started_at: datetime) -> str:
    stamp = _as_aware_utc(started_at).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:12]}"


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON through a same-directory fsynced temporary file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        directory_flags = getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(path.parent, os.O_RDONLY | directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_collector_status(diagnostics_dir: Path, status: dict) -> None:
    payload = {"schema_version": STATUS_SCHEMA_VERSION, **status}
    _atomic_write_json(Path(diagnostics_dir) / STATUS_FILENAME, payload)


def _is_noise(text: str) -> bool:
    return any(pattern in text for pattern in NOISE_PATTERNS)


def parse_log_line(line: str) -> dict | None:
    match = LOG_LINE_RE.match(line.strip())
    if not match:
        return None
    try:
        ts = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return {"ts": ts, "level": match.group("level"), "rest": match.group("rest")}


def normalize_signature(text: str) -> str:
    text = re.sub(r"[0-9a-fA-F]{8,}", "<hex>", text)
    text = re.sub(r"\d+", "<n>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:300]


def extract_log_findings(lines, since: datetime) -> list[dict]:
    since_utc = _as_aware_utc(since)
    buckets: dict[str, dict] = {}
    for raw in lines:
        parsed = parse_log_line(raw)
        parsed_ts = _as_aware_utc(parsed["ts"]) if parsed else None
        if not parsed or parsed_ts < since_utc:
            continue
        if parsed["level"] not in ("ERROR", "WARNING", "CRITICAL"):
            continue
        if _is_noise(parsed["rest"]):
            continue
        sig = normalize_signature(parsed["rest"])
        bucket = buckets.setdefault(
            sig, {"level": parsed["level"], "signature": sig, "count": 0, "examples": []}
        )
        bucket["count"] += 1
        if len(bucket["examples"]) < MAX_EXAMPLES:
            bucket["examples"].append(raw.strip()[:300])
    return sorted(buckets.values(), key=lambda b: -b["count"])


def memory_trend(lines, since: datetime) -> dict | None:
    since_utc = _as_aware_utc(since)
    values: list[int] = []
    for raw in lines:
        parsed = parse_log_line(raw)
        parsed_ts = _as_aware_utc(parsed["ts"]) if parsed else None
        if not parsed or parsed_ts < since_utc:
            continue
        match = MEMORY_RE.search(parsed["rest"])
        if match:
            values.append(int(match.group(1)))
    if not values:
        return None
    return {
        "min_mb": min(values),
        "max_mb": max(values),
        "last_mb": values[-1],
        "delta_mb": values[-1] - values[0],
        "samples": len(values),
    }


def latest_output_tail(job_dir: Path) -> str | None:
    if not job_dir.is_dir():
        return None
    files = sorted(job_dir.glob("*.md"))
    if not files:
        return None
    text = files[-1].read_text(encoding="utf-8", errors="replace")
    return text[-MAX_TAIL_CHARS:]


def summarize_cron_jobs(jobs: list[dict], output_root: Path) -> dict:
    ok: list[dict] = []
    failed: list[dict] = []
    paused: list[dict] = []
    for job in jobs:
        entry = {
            "name": job.get("name") or job.get("id", "?"),
            "id": job.get("id"),
            "last_run_at": job.get("last_run_at"),
            "schedule": job.get("schedule_display"),
        }
        if not job.get("enabled", True):
            paused.append(entry)
            continue
        status = (job.get("last_status") or "").lower()
        if status and status != "ok":
            entry["last_status"] = job.get("last_status")
            entry["last_error"] = job.get("last_error")
            entry["output_tail"] = latest_output_tail(output_root / str(job.get("id")))
            failed.append(entry)
        else:
            entry["last_status"] = status or "never-ran"
            ok.append(entry)
    return {"ok": ok, "failed": failed, "paused": paused}


def diff_known_issues(state: dict, findings: list[dict], now: datetime) -> tuple[list[dict], list[dict], dict]:
    now_iso = now.isoformat(timespec="seconds")
    annotated: list[dict] = []
    new_state: dict[str, dict] = {}
    for finding in findings:
        sig = finding["signature"]
        prior = state.get(sig)
        if prior and prior.get("first_seen"):
            first_seen = prior["first_seen"]
            try:
                age_days = max(0, (now.date() - datetime.fromisoformat(first_seen).date()).days)
            except ValueError:
                first_seen, age_days = now_iso, 0
            status = "known"
        else:
            first_seen, age_days, status = now_iso, 0, "new"
        annotated.append({**finding, "status": status, "first_seen": first_seen, "age_days": age_days})
        new_state[sig] = {"first_seen": first_seen, "last_seen": now_iso, "count": finding["count"]}
    resolved = [
        {"signature": sig, "first_seen": meta.get("first_seen"), "last_seen": meta.get("last_seen")}
        for sig, meta in state.items()
        if sig not in new_state
    ]
    return annotated, resolved, new_state


def job_intel_summary(db_path: Path) -> dict:
    if not db_path.exists():
        return {"error": f"db not found: {db_path}"}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        tables = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        if "vacancy_observability" not in tables:
            return {"error": "no runs recorded"}

        # `created_at` is sourced from the vacancy scrape timestamp, which can
        # predate a later re-evaluation. Select the newest completed production
        # daily run from the canonical run ledger instead of treating the newest
        # scrape timestamp as the newest pipeline execution.
        selection = "observability_timestamp_fallback"
        run_id = None
        run_at = None
        run_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(runs)").fetchall()
        } if "runs" in tables else set()
        if {"id", "mode", "status", "started_at", "finished_at"}.issubset(run_columns):
            run_type_filter = (
                "AND COALESCE(r.run_type, 'production') = 'production'"
                if "run_type" in run_columns
                else ""
            )
            run_row = conn.execute(
                f"""
                SELECT r.id AS run_id, COALESCE(r.finished_at, r.started_at) AS run_at
                FROM runs r
                WHERE r.mode = 'daily'
                  AND r.status = 'ok'
                  {run_type_filter}
                  AND EXISTS (
                      SELECT 1 FROM vacancy_observability v WHERE v.run_id = r.id
                  )
                ORDER BY COALESCE(r.finished_at, r.started_at) DESC, r.id DESC
                LIMIT 1
                """
            ).fetchone()
            if run_row is not None:
                run_id = run_row["run_id"]
                run_at = run_row["run_at"]
                selection = "daily_production_run"

        if run_id is None:
            run_row = conn.execute(
                """
                SELECT run_id, MAX(created_at) AS run_at
                FROM vacancy_observability
                GROUP BY run_id
                ORDER BY run_at DESC, run_id DESC
                LIMIT 1
                """
            ).fetchone()
            if run_row is None or run_row["run_id"] is None:
                return {"error": "no runs recorded"}
            run_id = run_row["run_id"]
            run_at = run_row["run_at"]

        run = conn.execute(
            """
            SELECT ? AS run_id, ? AS run_at, COUNT(*) AS found,
                   SUM(accepted) AS accepted, SUM(notified) AS notified
            FROM vacancy_observability
            WHERE run_id = ?
            """,
            (run_id, run_at, run_id),
        ).fetchone()

        # Data gaps (salary_unknown, low_confidence, ...) are emitted per
        # vacancy no matter why it was rejected, so ranking every label
        # together buries the blockers that actually decided the outcome.
        top = {}
        for key, predicate in (
            ("top_blockers", "reason_type = 'blocker'"),
            ("top_data_gaps", "(reason_type IS NULL OR reason_type != 'blocker')"),
        ):
            top[key] = [
                dict(row)
                for row in conn.execute(
                    "SELECT rejection_reason AS reason, COUNT(*) AS count"
                    " FROM vacancy_rejection_events"
                    f" WHERE run_id = ? AND {predicate}"
                    " GROUP BY rejection_reason"
                    " ORDER BY COUNT(*) DESC, rejection_reason LIMIT 5",
                    (run_id,),
                ).fetchall()
            ]

        notification_diagnostics: dict[str, dict[str, int]] = {}
        card_decision_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(vacancy_card_decisions)").fetchall()
        } if "vacancy_card_decisions" in tables else set()
        if {"run_id", "decision"}.issubset(card_decision_columns):
            notification_diagnostics["card_decisions"] = {
                str(row["decision"]): int(row["count"] or 0)
                for row in conn.execute(
                    """
                    SELECT decision, COUNT(*) AS count
                    FROM vacancy_card_decisions
                    WHERE run_id = ?
                    GROUP BY decision
                    """,
                    (run_id,),
                ).fetchall()
            }
        notification_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(notifications)").fetchall()
        } if "notifications" in tables else set()
        if {"run_id", "vacancy_id", "delivery_status"}.issubset(notification_columns):
            notification_diagnostics["delivery_statuses"] = {
                str(row["delivery_status"]): int(row["count"] or 0)
                for row in conn.execute(
                    """
                    SELECT delivery_status, COUNT(*) AS count
                    FROM notifications
                    WHERE run_id = ? AND vacancy_id IS NOT NULL
                    GROUP BY delivery_status
                    """,
                    (run_id,),
                ).fetchall()
            }

        return {
            "run_id": run["run_id"],
            "run_at": run["run_at"],
            "run_selection": selection,
            "found": run["found"],
            "accepted": run["accepted"] or 0,
            "notified": run["notified"] or 0,
            "notification_diagnostics": notification_diagnostics,
            **top,
        }
    finally:
        conn.close()


INTERESTING_RE = re.compile(
    r"(error|warn|fail|missing|not found|permission denied|unhealthy|outdated|broken|invalid|timeout)",
    re.IGNORECASE,
)


def interesting_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and INTERESTING_RE.search(line)]


def run_command(cmd: list[str], cwd=None, env=None, timeout: int = DOCTOR_TIMEOUT) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout, check=False,
        )
        return proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        return 124, out + "\nTIMEOUT"
    except OSError as exc:
        return 127, str(exc)


def _read_log_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def collect_logs(hermes_home: Path, since: datetime, now: datetime) -> dict:
    gateway_lines = _read_log_lines(hermes_home / "logs" / "gateway.log")
    error_lines = _read_log_lines(hermes_home / "logs" / "errors.log")
    findings = extract_log_findings(gateway_lines + error_lines, since)
    diagnostics_dir = hermes_home / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    state_path = diagnostics_dir / "known-issues.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}
    annotated, resolved, new_state = diff_known_issues(state, findings, now)
    state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "findings": annotated,
        "resolved": resolved,
        "memory": memory_trend(gateway_lines, since),
    }


def collect_cron_jobs(hermes_home: Path) -> dict:
    jobs_path = hermes_home / "cron" / "jobs.json"
    raw = json.loads(jobs_path.read_text(encoding="utf-8"))
    jobs = raw if isinstance(raw, list) else raw.get("jobs", [])
    return summarize_cron_jobs(jobs, hermes_home / "cron" / "output")


def collect_systemd() -> dict:
    units: dict[str, dict] = {}
    for unit in ("job-intel-daily.service", "job-intel-weekly-kpi.service"):
        code, out = run_command(
            ["systemctl", "show", unit, "--property=Result,ExecMainStatus,ExecMainExitTimestamp"]
        )
        units[unit] = {"exit_code": code, "detail": out.strip()[:400]}
    return units


def collect_system_health(paths: list[str]) -> dict:
    load = None
    try:
        one, five, fifteen = os.getloadavg()
        load = f"{one:.2f}/{five:.2f}/{fifteen:.2f}"
    except OSError:
        pass
    _, free_out = run_command(["free", "-h"])
    existing = [p for p in dict.fromkeys(paths) if Path(p).exists()]
    disks: list[str] = []
    if existing:
        _, df_out = run_command(["df", "-hP", *existing])
        disks = [line.strip() for line in df_out.splitlines()[1:] if line.strip()]
    return {"load": load, "free": free_out.strip()[:400], "disks": disks}


def collect_docker() -> dict:
    code, out = run_command(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"])
    if code != 0:
        return {"error": out.strip()[:300]}
    lines = [line for line in out.splitlines() if line.strip()]
    exited = [line for line in lines if "\tExited" in line]
    monitoring_down = [
        line for line in exited
        if any(name in line for name in ("prometheus", "grafana", "loki", "promtail", "alertmanager", "cadvisor", "job-intel-exporter"))
    ]
    return {"total": len(lines), "exited": len(exited), "monitoring_down": monitoring_down[:10]}


def _sqlite_runtime_vulnerable(hermes_home: Path, version_info) -> tuple[bool | None, str | None]:
    """(vulnerable, detector_error) via upstream's own predicate.

    Never a local copy of the affected version range — a second copy would
    drift the day upstream learns something new.
    """
    try:
        sys.path.insert(0, str(hermes_home / "hermes-agent"))
        from hermes_cli.sqlite_runtime import is_sqlite_wal_reset_vulnerable

        return bool(is_sqlite_wal_reset_vulnerable(version_info)), None
    except Exception as exc:  # noqa: BLE001 - report, never crash the digest
        return None, f"{type(exc).__name__}: {exc}"


def _collect_exporter_sqlite_health(hermes_home: Path) -> dict:
    """Probe the job-intel-exporter container's effective SQLite, the same
    way collect_docker() elsewhere in this module shells out to docker.

    Degrades explicitly when docker or the container is absent: "cannot
    tell" is reported as its own attention-worthy state, not folded into a
    silent "clean" reading.
    """
    code, out = run_command(
        ["docker", "exec", "monitoring-job-intel-exporter", "python", "-c",
         "import sqlite3; print(sqlite3.sqlite_version, sqlite3.__name__)"],
        timeout=30,
    )
    if code != 0:
        return {
            "reachable": False,
            "error": out.strip()[:300],
            "needs_attention": True,
        }
    parts = out.strip().split()
    version = parts[0] if parts else ""
    module_name = parts[1] if len(parts) > 1 else ""
    shim_active = "pysqlite3" in module_name
    vulnerable = None
    if version:
        try:
            version_info = tuple(int(x) for x in version.split("."))
        except ValueError:
            version_info = None
        if version_info is not None:
            vulnerable, _ = _sqlite_runtime_vulnerable(hermes_home, version_info)
    return {
        "reachable": True,
        "version": version,
        "module": module_name,
        "shim_active": shim_active,
        "wal_reset_vulnerable": vulnerable,
        "needs_attention": bool(vulnerable or not shim_active or vulnerable is None),
    }


def _autoupdate_ts_stale(report: dict, max_days: int = 10) -> bool:
    """A result file nobody has refreshed in ~10 days can't be trusted as
    "clean" even if its last recorded action looked fine."""
    ts = report.get("ts")
    if not ts:
        return False
    try:
        when = datetime.fromisoformat(ts)
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).days > max_days


def collect_sqlite_health(hermes_home: Path) -> dict:
    """Report the SQLite this deployment actually runs — the gateway venv
    AND the job-intel-exporter image — plus the updater result.

    The gateway venv and the job-intel-exporter image run a statically compiled
    SQLite via a .pth shim, because no supplier ships a version without the
    WAL-reset corruption bug: Ubuntu pins libsqlite3 at 3.45.1 for the life of
    the LTS and uv's CPython bundles 3.50.4, both inside the affected range.
    That took those processes out of apt's update path, so this section is the
    only thing that reports drift.

    Fitness is judged by upstream's own detector (hermes_cli.sqlite_runtime),
    never by a local copy of the affected version range — a second copy would
    drift the day upstream learns something new.
    """
    module_file = getattr(sqlite3, "__file__", "") or ""
    info: dict = {
        "version": sqlite3.sqlite_version,
        "module": module_file,
        "shim_active": "pysqlite3" in module_file,
    }
    info["wal_reset_vulnerable"], detector_error = _sqlite_runtime_vulnerable(
        hermes_home, sqlite3.sqlite_version_info
    )
    if detector_error:
        info["detector_error"] = detector_error

    result_path = hermes_home / "state" / "sqlite_autoupdate_last.json"
    try:
        info["autoupdate"] = json.loads(result_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        info["autoupdate"] = {"action": "never_ran"}
    except (OSError, ValueError) as exc:
        info["autoupdate"] = {"action": "unreadable", "error": str(exc)}

    report = info["autoupdate"] or {}
    info["exporter"] = _collect_exporter_sqlite_health(hermes_home)

    # detector_error is its own attention trigger: it is the one condition
    # where nobody can vouch for vulnerability status, and a bare "not
    # vulnerable" boolean check would silently pass right through it.
    info["needs_attention"] = bool(
        info["wal_reset_vulnerable"]
        or info.get("detector_error")
        or not info["shim_active"]
        or report.get("action")
        in {"failed", "rejected", "rolled_back", "never_ran", "unreadable"}
        or info["exporter"].get("needs_attention")
        or _autoupdate_ts_stale(report)
    )
    # Informational only: "the shim is now retirable" is good news, not an
    # alarm — kept out of needs_attention so it doesn't latch the digest's
    # alarm permanently on once uv's CPython finally catches up.
    info["retirement_available"] = bool(report.get("upstream_runtime_viable"))
    return info


def collect_doctors(workdir: Path, hermes_home: Path, db_path: Path) -> dict:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    env["PYTHONPATH"] = str(workdir)
    result: dict[str, dict] = {}
    code, out = run_command([sys.executable, "-m", "hermes_cli.main", "doctor"], cwd=workdir, env=env)
    result["hermes_doctor"] = {"exit_code": code, "issues": interesting_lines(out)[:10]}
    ji_env = env.copy()
    ji_env["JOB_INTEL_DB_PATH"] = str(db_path)
    ji_env["JOB_INTEL_DOCTOR_SKIP_LIVE_COLLECTION"] = "1"
    code, out = run_command([sys.executable, "-m", "job_intel", "doctor"], cwd=workdir, env=ji_env)
    result["job_intel_doctor"] = {"exit_code": code, "issues": interesting_lines(out)[:10]}
    return result


def build_digest(
    hermes_home: Path,
    workdir: Path,
    db_path: Path,
    now: datetime,
    *,
    run_id: str | None = None,
) -> dict:
    since = now - timedelta(hours=WINDOW_HOURS)
    digest: dict = {
        "generated_at": _timestamp(now),
        "window_hours": WINDOW_HOURS,
        "sections": {},
        "section_errors": {},
    }
    if run_id:
        digest["run_id"] = run_id

    def section(name, fn):
        try:
            digest["sections"][name] = fn()
        except Exception as exc:  # noqa: BLE001 — one bad section must not kill the digest
            digest["section_errors"][name] = f"{type(exc).__name__}: {exc}"

    section("logs", lambda: collect_logs(hermes_home, since, now))
    section("cron_jobs", lambda: collect_cron_jobs(hermes_home))
    section("systemd", collect_systemd)
    section("job_intel", lambda: job_intel_summary(db_path))
    section("system", lambda: collect_system_health([str(hermes_home), str(workdir), "/", "/var/lib/browser-desktop"]))
    section("docker", collect_docker)
    section("sqlite", lambda: collect_sqlite_health(hermes_home))
    section("doctors", lambda: collect_doctors(workdir, hermes_home, db_path))
    return digest


def write_digest(digest: dict, diagnostics_dir: Path, now: datetime) -> None:
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(digest, ensure_ascii=False, indent=1, default=str)
    (diagnostics_dir / "digest-latest.json").write_text(payload, encoding="utf-8")
    dated = diagnostics_dir / f"digest-{now.date().isoformat()}.json"
    dated.write_text(payload, encoding="utf-8")
    cutoff = _as_aware_utc(now) - timedelta(days=ROTATE_DAYS)
    for old in diagnostics_dir.glob("digest-????-??-??.json"):
        try:
            stamp = datetime.strptime(old.stem, "digest-%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if stamp < cutoff:
            old.unlink(missing_ok=True)


def run_collection(
    hermes_home: Path,
    workdir: Path,
    db_path: Path,
    *,
    now: datetime | None = None,
    run_id: str | None = None,
) -> int:
    """Run one collector cycle and publish an atomic lifecycle status."""
    started_at = _as_aware_utc(now or _utc_now())
    run_id = run_id or _new_run_id(started_at)
    diagnostics_dir = Path(hermes_home) / "diagnostics"

    try:
        write_collector_status(
            diagnostics_dir,
            {
                "state": "running",
                "run_id": run_id,
                "started_at": _timestamp(started_at),
                "exit_code": None,
                "reason_code": None,
            },
        )
    except Exception:
        # Do not overwrite a previous valid status when the status path itself
        # is unavailable. A non-zero exit makes the scheduler surface this.
        return 1

    try:
        digest = build_digest(
            Path(hermes_home), Path(workdir), Path(db_path), started_at, run_id=run_id
        )
        write_digest(digest, diagnostics_dir, started_at)
    except Exception:
        finished_at = _utc_now()
        try:
            write_collector_status(
                diagnostics_dir,
                {
                    "state": "failed",
                    "run_id": run_id,
                    "started_at": _timestamp(started_at),
                    "finished_at": _timestamp(finished_at),
                    "exit_code": 1,
                    "reason_code": "collector_exception",
                },
            )
        except Exception:
            pass
        return 1

    finished_at = _utc_now()
    try:
        write_collector_status(
            diagnostics_dir,
            {
                "state": "ok",
                "run_id": run_id,
                "started_at": _timestamp(started_at),
                "finished_at": _timestamp(finished_at),
                "exit_code": 0,
                "reason_code": "section_errors" if digest.get("section_errors") else None,
                "digest_generated_at": digest.get("generated_at"),
            },
        )
    except Exception:
        return 1
    return 0


def resolve_hermes_home() -> Path:
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        return Path(env_home)
    return Path("/home/hermes/.hermes")


def main() -> int:
    hermes_home = resolve_hermes_home()
    workdir = Path(os.environ.get("DIAG_WORKDIR", "") or hermes_home / "hermes-agent")
    db_path = Path(os.environ.get("JOB_INTEL_DB_PATH", "") or "/var/lib/job-intel/state/job_intel.sqlite3")
    return run_collection(hermes_home, workdir, db_path)


if __name__ == "__main__":
    sys.exit(main())
