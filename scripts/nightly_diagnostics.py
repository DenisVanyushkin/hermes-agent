#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

DOCTOR_TIMEOUT = 180
REPORT_LIMIT = 6
DISK_WARN_PCT = 85
MEM_WARN_GB = 2.0


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


def python_works(python_bin: str, workdir: Path) -> bool:
    try:
        proc = subprocess.run(
            [python_bin, "-c", "import job_intel"],
            cwd=str(workdir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def resolve_python(workdir: Path) -> str:
    candidates = [
        os.environ.get("JOB_INTEL_BROWSER_PYTHON", ""),
        "/var/lib/browser-desktop/playwright-venv/bin/python",
        os.environ.get("JOB_INTEL_PYTHON", ""),
        str(workdir / "venv" / "bin" / "python"),
        str(workdir / ".venv" / "bin" / "python"),
        "python3",
        "python",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = candidate
        if "/" not in candidate:
            resolved = shutil.which(candidate) or ""
        if resolved and python_works(resolved, workdir):
            return resolved
    for candidate in candidates:
        if not candidate:
            continue
        if "/" in candidate and Path(candidate).exists():
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise RuntimeError("Could not resolve a usable Python interpreter for job_intel")


def resolve_hermes(workdir: Path) -> str:
    candidates = [
        os.environ.get("HERMES_BIN", ""),
        str(Path.home() / ".local" / "bin" / "hermes"),
        str(workdir / "venv" / "bin" / "hermes"),
        str(workdir / ".venv" / "bin" / "hermes"),
        "hermes",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = candidate
        if "/" not in candidate:
            resolved = shutil.which(candidate) or ""
        if not resolved:
            continue
        try:
            proc = subprocess.run(
                [resolved, "--version"],
                cwd=str(workdir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
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


def interesting_lines(text: str) -> list[str]:
    keep = []
    patterns = re.compile(
        r"(error|warn|fail|missing|not found|permission denied|unhealthy|outdated|broken|invalid|timeout)",
        re.IGNORECASE,
    )
    for line in clean_lines(text):
        if patterns.search(line):
            keep.append(line)
    return keep


def summarize(text: str, limit: int = REPORT_LIMIT) -> list[str]:
    lines = interesting_lines(text)
    if lines:
        return lines[:limit]
    cleaned = clean_lines(text)
    return cleaned[:limit]


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
        lines = interesting_lines(output)
        if not lines:
            return []
        return [f"{label} emitted warnings:"] + [f"  - {line}" for line in lines[:REPORT_LIMIT]]
    lines = summarize(output)
    prefix = f"{label} failed (exit {code})"
    if lines:
        return [prefix + ":"] + [f"  - {line}" for line in lines]
    return [prefix]


def main() -> int:
    workdir = resolve_workdir()
    hermes_bin = resolve_hermes(workdir)
    python_bin = resolve_python(workdir)

    env = os.environ.copy()
    env.setdefault("JOB_INTEL_WORKDIR", str(workdir))
    env.setdefault("JOB_INTEL_DB_PATH", str(Path.home() / ".hermes" / "job_intel" / "job_intel.sqlite3"))
    env.setdefault("JOB_INTEL_ENVIRONMENT", "production")
    env["JOB_INTEL_SCRIPTS_DIR"] = str(Path(__file__).resolve().parent)

    issues: list[str] = []

    if hermes_bin:
        hermes_code, hermes_out = run_command([hermes_bin, "doctor"], workdir, env)
        issues.extend(maybe_issue("hermes doctor", hermes_code, hermes_out))
    else:
        issues.append("hermes doctor skipped: hermes binary not found")

    job_code, job_out = run_command([python_bin, "-m", "job_intel", "doctor"], workdir, env)
    issues.extend(maybe_issue("job_intel doctor", job_code, job_out))

    disk_paths = unique_paths(
        [
            str(Path.home()),
            str(workdir),
            "/var/lib/browser-desktop",
        ]
    )
    system_line, system_alarm = system_report(disk_paths)
    if issues or system_alarm:
        issues.append(system_line)

    if not issues:
        return 0

    print(f"Nightly diagnostics — {workdir}")
    for line in issues:
        print(f"- {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
