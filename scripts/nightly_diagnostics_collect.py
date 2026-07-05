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
from datetime import datetime, timedelta
from pathlib import Path

WINDOW_HOURS = 24
DOCTOR_TIMEOUT = 90
ROTATE_DAYS = 14
MAX_EXAMPLES = 3
MAX_TAIL_CHARS = 700

LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+(?P<level>[A-Z]+)\s+(?P<rest>.*)$"
)
MEMORY_RE = re.compile(r"\[MEMORY\] rss=(\d+)mb", re.IGNORECASE)


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
    buckets: dict[str, dict] = {}
    for raw in lines:
        parsed = parse_log_line(raw)
        if not parsed or parsed["ts"] < since:
            continue
        if parsed["level"] not in ("ERROR", "WARNING", "CRITICAL"):
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
    values: list[int] = []
    for raw in lines:
        parsed = parse_log_line(raw)
        if not parsed or parsed["ts"] < since:
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
        run = conn.execute(
            "SELECT run_id, MAX(created_at) AS run_at, COUNT(*) AS found,"
            " SUM(accepted) AS accepted, SUM(notified) AS notified"
            " FROM vacancy_observability WHERE run_id = ("
            "  SELECT run_id FROM vacancy_observability ORDER BY created_at DESC LIMIT 1)"
        ).fetchone()
        if run is None or run["run_id"] is None:
            return {"error": "no runs recorded"}
        reasons = conn.execute(
            "SELECT rejection_reason AS reason, COUNT(*) AS count"
            " FROM vacancy_rejection_events WHERE run_id = ?"
            " GROUP BY rejection_reason ORDER BY count DESC LIMIT 5",
            (run["run_id"],),
        ).fetchall()
        return {
            "run_id": run["run_id"],
            "run_at": run["run_at"],
            "found": run["found"],
            "accepted": run["accepted"] or 0,
            "notified": run["notified"] or 0,
            "top_rejections": [dict(r) for r in reasons],
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


def build_digest(hermes_home: Path, workdir: Path, db_path: Path, now: datetime) -> dict:
    since = now - timedelta(hours=WINDOW_HOURS)
    digest: dict = {
        "generated_at": now.isoformat(timespec="seconds"),
        "window_hours": WINDOW_HOURS,
        "sections": {},
        "section_errors": {},
    }

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
    section("doctors", lambda: collect_doctors(workdir, hermes_home, db_path))
    return digest


def write_digest(digest: dict, diagnostics_dir: Path, now: datetime) -> None:
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(digest, ensure_ascii=False, indent=1, default=str)
    (diagnostics_dir / "digest-latest.json").write_text(payload, encoding="utf-8")
    dated = diagnostics_dir / f"digest-{now.date().isoformat()}.json"
    dated.write_text(payload, encoding="utf-8")
    cutoff = now - timedelta(days=ROTATE_DAYS)
    for old in diagnostics_dir.glob("digest-????-??-??.json"):
        try:
            stamp = datetime.strptime(old.stem, "digest-%Y-%m-%d")
        except ValueError:
            continue
        if stamp < cutoff:
            old.unlink(missing_ok=True)


def resolve_hermes_home() -> Path:
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        return Path(env_home)
    return Path("/home/hermes/.hermes")


def main() -> int:
    hermes_home = resolve_hermes_home()
    workdir = Path(os.environ.get("DIAG_WORKDIR", "") or hermes_home / "hermes-agent")
    db_path = Path(os.environ.get("JOB_INTEL_DB_PATH", "") or "/var/lib/job-intel/state/job_intel.sqlite3")
    now = datetime.now()
    digest = build_digest(hermes_home, workdir, db_path, now)
    write_digest(digest, hermes_home / "diagnostics", now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
