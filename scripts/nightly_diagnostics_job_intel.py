#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

DOCTOR_TIMEOUT = 90
REPORT_LIMIT = 6


def resolve_workdir() -> Path:
    candidates = [
        os.environ.get("JOB_INTEL_WORKDIR", ""),
        "/workspace/live-hermes",
        "/home/hermes/.hermes/hermes-agent",
        os.getcwd(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if (path / "job_intel").is_dir() or (path / "scripts").is_dir():
            return path
    return Path(os.getcwd())


def resolve_hermes_home() -> Path:
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        return Path(env_home)
    return Path("/home/hermes/.hermes")


def resolve_python(workdir: Path) -> str:
    candidates = [
        os.environ.get("JOB_INTEL_PYTHON", ""),
        str(workdir / "venv" / "bin" / "python"),
        str(workdir / ".venv" / "bin" / "python"),
        "/usr/bin/python3",
        "python3",
        "python",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        if "/" in candidate:
            if Path(candidate).exists():
                return candidate
        else:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
    return ""


def clean_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def interesting_lines(text: str) -> list[str]:
    import re

    pattern = re.compile(r"(error|warn|fail|missing|not found|permission denied|unhealthy|outdated|broken|invalid|timeout)", re.IGNORECASE)
    return [line for line in clean_lines(text) if pattern.search(line)]


def summarize(text: str, limit: int = REPORT_LIMIT) -> list[str]:
    lines = interesting_lines(text)
    if lines:
        return lines[:limit]
    return clean_lines(text)[:limit]


def run_command(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
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


def maybe_issue(label: str, code: int, output: str) -> list[str]:
    if code == 0:
        lines = interesting_lines(output)
        if not lines:
            return []
        return [f"{label} emitted warnings:"] + [f"  - {line}" for line in lines[:REPORT_LIMIT]]
    lines = summarize(output)
    if lines:
        return [f"{label} failed (exit {code}):"] + [f"  - {line}" for line in lines]
    return [f"{label} failed (exit {code})"]


def main() -> int:
    workdir = resolve_workdir()
    python_bin = resolve_python(workdir)
    if not python_bin:
        print("nightly-diagnostics-job-intel: python binary not found")
        return 1

    env = os.environ.copy()
    env["JOB_INTEL_WORKDIR"] = str(workdir)
    runtime_base = resolve_hermes_home() / "job_intel"
    state_dir = runtime_base / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    env["JOB_INTEL_STATE_DIR"] = str(state_dir)
    env["JOB_INTEL_DB_PATH"] = str(state_dir / "job_intel.sqlite3")
    env["JOB_INTEL_ENVIRONMENT"] = "production"
    env["JOB_INTEL_SCRIPTS_DIR"] = str(Path(__file__).resolve().parent)
    env["JOB_INTEL_DOCTOR_SKIP_LIVE_COLLECTION"] = "1"

    code, output = run_command([python_bin, "-m", "job_intel", "doctor"], workdir, env=env)
    issues = maybe_issue("job_intel doctor", code, output)
    if not issues:
        return 0

    print(f"Nightly diagnostics — job-intel — {workdir}")
    for line in issues:
        print(f"- {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
