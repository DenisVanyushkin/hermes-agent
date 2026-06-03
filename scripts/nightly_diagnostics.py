#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

DOCTOR_TIMEOUT = 40
REPORT_LIMIT = 6
DISK_WARN_PCT = 85
MEM_WARN_GB = 2.0
LOG_LOOKBACK_HOURS = 24
LOG_SCAN_LIMIT_LINES = 6000

TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d{3}\b")
NOISE_PATTERNS = [
    re.compile(r"\.hermes/\.env", re.IGNORECASE),
    re.compile(r"~/.local/bin/hermes not found", re.IGNORECASE),
    re.compile(r"ripgrep .*not found|\(rg\) not found|\brg not found\b", re.IGNORECASE),
    re.compile(r"missing DISCORD_BOT_TOKEN", re.IGNORECASE),
    re.compile(r"Docker storage driver does not support per-container disk limits", re.IGNORECASE),
    re.compile(r"Config version outdated", re.IGNORECASE),
    re.compile(r"missing OPENROUTER_API_KEY|missing EXA_API_KEY|missing XAI_API_KEY", re.IGNORECASE),
    re.compile(r"Auxiliary: marking openrouter unhealthy|Auxiliary: marking nous unhealthy", re.IGNORECASE),
    re.compile(r"Missing ~/.local/bin/hermes symlink|configure missing API keys for full tool access", re.IGNORECASE),
]
LOG_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("email IMAP timeout", re.compile(r"IMAP fetch error:.*timeout|The read operation timed out|cannot read from timed out object", re.IGNORECASE)),
    ("cron missing skill", re.compile(r"skill not found", re.IGNORECASE)),
    ("delivery thread/topic lost", re.compile(r"delivery target lost it|Thread .* not found in _send_telegram|reply anchor", re.IGNORECASE)),
    ("Codex stream stalled", re.compile(r"Codex stream produced no bytes|Codex stream produced no SSE events|TTFB cutoff", re.IGNORECASE)),
    ("job_intel missing playwright", re.compile(r"No module named 'playwright'|Playwright is not installed", re.IGNORECASE)),
    ("job_intel missing pydantic", re.compile(r"No module named 'pydantic'", re.IGNORECASE)),
    ("Telegram transient network error", re.compile(r"Telegram network error", re.IGNORECASE)),
]


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def log_event(message: str, started_at: float | None = None) -> None:
    prefix = f"[{utc_timestamp()}]"
    if started_at is not None:
        prefix += f" (+{time.monotonic() - started_at:.1f}s)"
    print(f"{prefix} {message}", file=sys.stderr, flush=True)


def resolve_workdir() -> Path:
    candidates = [
        os.environ.get("JOB_INTEL_WORKDIR", ""),
        "/home/hermes/.hermes/hermes-agent",
        "/workspace/live-hermes",
        os.getcwd(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if (path / "job_intel").is_dir():
            return path
    return Path(os.getcwd())


def resolve_python(workdir: Path) -> str:
    candidates = [
        os.environ.get("JOB_INTEL_PYTHON", ""),
        str(workdir / "venv" / "bin" / "python"),
        str(workdir / ".venv" / "bin" / "python"),
        "python3",
        "python",
        os.environ.get("JOB_INTEL_BROWSER_PYTHON", ""),
        "/var/lib/browser-desktop/playwright-venv/bin/python",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        if "/" in candidate:
            if not Path(candidate).exists():
                continue
            resolved = candidate
        else:
            resolved = shutil.which(candidate) or ""
        if not resolved:
            continue
        try:
            proc = subprocess.run(
                [resolved, "-c", "import job_intel"],
                cwd=str(workdir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            return resolved
    raise RuntimeError("Could not resolve a usable Python interpreter for job_intel")


def resolve_hermes(workdir: Path) -> str:
    candidates = [
        os.environ.get("HERMES_BIN", ""),
        str(workdir / "venv" / "bin" / "hermes"),
        str(workdir / ".venv" / "bin" / "hermes"),
        str(Path.home() / ".local" / "bin" / "hermes"),
        "hermes",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        if "/" in candidate:
            if not Path(candidate).exists():
                continue
            resolved = candidate
        else:
            resolved = shutil.which(candidate) or ""
        if not resolved:
            continue
        try:
            proc = subprocess.run(
                [resolved, "--version"],
                cwd=str(workdir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            return resolved
    return ""


def run_command(cmd: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=DOCTOR_TIMEOUT,
            check=False,
        )
        return proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        return 124, out + ("\nTIMEOUT" if out else "TIMEOUT")
    except OSError as exc:
        return 127, str(exc)


def clean_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            lines.append(line)
    return lines


def is_noise_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in NOISE_PATTERNS)


def interesting_lines(text: str) -> list[str]:
    keep = []
    patterns = re.compile(
        r"(error|warn|fail|missing|not found|permission denied|unhealthy|outdated|broken|invalid|timeout)",
        re.IGNORECASE,
    )
    for line in clean_lines(text):
        if is_noise_line(line):
            continue
        if patterns.search(line):
            keep.append(line)
    return keep


def summarize(text: str, limit: int = REPORT_LIMIT) -> list[str]:
    lines = interesting_lines(text)
    if lines:
        return lines[:limit]
    cleaned = clean_lines(text)
    return cleaned[:limit]


def parse_log_timestamp(line: str) -> datetime | None:
    match = TIMESTAMP_RE.match(line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1) + " " + match.group(2), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def recent_lines(path: Path, cutoff: datetime, limit: int = LOG_SCAN_LIMIT_LINES) -> list[str]:
    lines: list[str] = []
    recent_started = False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.rstrip("\n")
                if len(lines) >= limit:
                    break
                ts = parse_log_timestamp(line)
                if ts is None:
                    if recent_started:
                        lines.append(line)
                    continue
                if ts >= cutoff:
                    recent_started = True
                    lines.append(line)
                elif recent_started:
                    break
    except OSError:
        return []
    return lines


def recent_log_incidents(log_paths: list[Path]) -> list[str]:
    cutoff = datetime.now() - timedelta(hours=LOG_LOOKBACK_HOURS)
    counts: Counter[str] = Counter()
    samples: dict[str, str] = {}
    for path in log_paths:
        if not path.exists():
            continue
        for line in recent_lines(path, cutoff):
            if is_noise_line(line):
                continue
            if parse_log_timestamp(line) is None:
                continue
            for label, pattern in LOG_RULES:
                if pattern.search(line):
                    counts[label] += 1
                    samples.setdefault(label, f"{path.name}: {line}")
                    break
    if not counts:
        return []
    report = [f"Recent log incidents (last {LOG_LOOKBACK_HOURS}h):"]
    for label, count in counts.most_common(REPORT_LIMIT):
        sample = samples.get(label, "")
        if len(sample) > 220:
            sample = sample[:217] + "..."
        report.append(f"  - {label} ({count}x) — sample: {sample}")
    return report


def unique_paths(paths: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        if not path:
            continue
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def disk_report(paths: list[str]) -> list[str]:
    if not paths:
        return []
    proc = subprocess.run(
        ["df", "-hP", *paths],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    lines = clean_lines(proc.stdout)
    if len(lines) <= 1:
        return []
    report = []
    for line in lines[1:]:
        cols = line.split()
        if len(cols) < 6:
            continue
        fs, size, used, avail, pct, mount = cols[:6]
        try:
            pct_num = int(pct.rstrip("%"))
        except ValueError:
            continue
        if pct_num >= DISK_WARN_PCT:
            report.append(
                f"Disk {mount}: {pct_num}% used ({avail} free of {size}) — clean logs/cache or expand storage."
            )
    return report


def _system_load_average() -> str | None:
    try:
        one, five, fifteen = os.getloadavg()
    except (AttributeError, OSError):
        return None
    return f"{one:.2f}/{five:.2f}/{fifteen:.2f}"


def _system_memory_status() -> tuple[str | None, bool]:
    proc = subprocess.run(
        ["free", "-h"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    for line in clean_lines(proc.stdout):
        if line.lower().startswith("mem:"):
            cols = line.split()
            if len(cols) >= 7:
                avail = cols[6]
                match = re.match(r"([0-9.]+)([KMGTP]?)", avail)
                if not match:
                    return avail, False
                value = float(match.group(1))
                unit = match.group(2) or "G"
                scale = {"K": 1 / 1024 / 1024, "M": 1 / 1024, "G": 1.0, "T": 1024.0, "P": 1024.0 * 1024.0}.get(unit, 1.0)
                low = (value * scale) < MEM_WARN_GB
                return avail, low
    return None, False


def system_report(paths: list[str]) -> tuple[str, bool]:
    load_value = _system_load_average() or "n/a"
    mem_value, mem_low = _system_memory_status()
    mem_text = mem_value or "n/a"

    disk_parts: list[str] = []
    alarming = mem_low
    unique = [p for p in unique_paths(paths) if Path(p).exists()]
    if unique:
        proc = subprocess.run(
            ["df", "-hP", *unique],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        lines = clean_lines(proc.stdout)
        for line in lines[1:]:
            cols = line.split()
            if len(cols) < 6:
                continue
            _, size, used, avail, pct, mount = cols[:6]
            try:
                pct_num = int(pct.rstrip("%"))
            except ValueError:
                pct_num = 0
            if pct_num >= DISK_WARN_PCT:
                alarming = True
            disk_parts.append(f"{mount} {avail} free of {size} ({pct} used)")
    disk_value = "; ".join(disk_parts) if disk_parts else "n/a"
    return f"System: load={load_value} | disk={disk_value} | mem={mem_text} available", alarming


def maybe_issue(label: str, code: int, output: str) -> list[str]:
    if code == 0:
        lines = list(dict.fromkeys([line for line in interesting_lines(output) if not is_noise_line(line)]))
        if not lines:
            return []
        return [f"{label} emitted warnings:"] + [f"  - {line}" for line in lines[:REPORT_LIMIT]]
    lines = list(dict.fromkeys([line for line in summarize(output) if not is_noise_line(line)]))
    prefix = f"{label} failed (exit {code})"
    if lines:
        return [prefix + ":"] + [f"  - {line}" for line in lines]
    return [prefix]


def main() -> int:
    run_started = time.monotonic()
    workdir = resolve_workdir()
    hermes_bin = resolve_hermes(workdir)
    python_bin = resolve_python(workdir)

    log_event(f"starting nightly diagnostics in {workdir}", run_started)
    log_event(f"resolved runtimes: hermes={hermes_bin or 'missing'} python={python_bin}", run_started)

    env = os.environ.copy()
    env.setdefault("JOB_INTEL_WORKDIR", str(workdir))
    env.setdefault("JOB_INTEL_DB_PATH", str(Path.home() / ".hermes" / "job_intel" / "job_intel.sqlite3"))
    env.setdefault("JOB_INTEL_ENVIRONMENT", "production")
    env["JOB_INTEL_SCRIPTS_DIR"] = str(Path(__file__).resolve().parent)

    issues: list[str] = []

    if hermes_bin:
        log_event("running hermes doctor", run_started)
        step_started = time.monotonic()
        hermes_code, hermes_out = run_command([hermes_bin, "doctor"], workdir, env)
        log_event(f"finished hermes doctor exit={hermes_code} in {time.monotonic() - step_started:.1f}s", run_started)
        issues.extend(maybe_issue("hermes doctor", hermes_code, hermes_out))
    else:
        log_event("skipping hermes doctor: hermes binary not found", run_started)
        issues.append("hermes doctor skipped: hermes binary not found")

    log_event("running job_intel doctor", run_started)
    step_started = time.monotonic()
    job_code, job_out = run_command([python_bin, "-m", "job_intel", "doctor"], workdir, env)
    log_event(f"finished job_intel doctor exit={job_code} in {time.monotonic() - step_started:.1f}s", run_started)
    issues.extend(maybe_issue("job_intel doctor", job_code, job_out))

    log_event("scanning recent logs", run_started)
    log_paths = [
        Path("/root/.hermes/logs/errors.log"),
        Path("/root/.hermes/logs/agent.log"),
        Path("/root/.hermes/logs/gateway.log"),
    ]
    issues.extend(recent_log_incidents(log_paths))
    log_event("finished recent log scan", run_started)

    log_event("collecting system pressure", run_started)
    disk_paths = unique_paths(
        [
            str(Path.home()),
            str(workdir),
            "/var/lib/browser-desktop",
        ]
    )
    system_line, system_alarm = system_report(disk_paths)
    log_event("collected system pressure", run_started)
    if issues or system_alarm:
        issues.append(system_line)

    if not issues:
        log_event("finished nightly diagnostics with no issues", run_started)
        return 0

    print(f"Nightly diagnostics — {workdir}")
    for line in issues:
        print(f"- {line}")
    log_event(f"finished nightly diagnostics with {len(issues)} issue(s)", run_started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
