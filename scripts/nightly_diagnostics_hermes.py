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
        "/home/hermes/.hermes/hermes-agent",
        "/workspace/live-hermes",
        os.getcwd(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if (path / "job_intel").is_dir() or (path / "gateway").is_dir():
            return path
    return Path(os.getcwd())


def resolve_hermes_home() -> Path:
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        return Path(env_home)

    candidates = [
        "/home/hermes/.hermes",
        "/root/.hermes",
        str(Path.home() / ".hermes"),
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path
    return Path.home() / ".hermes"


def resolve_hermes(workdir: Path, hermes_home: Path) -> str:
    candidates = [
        os.environ.get("HERMES_BIN", ""),
        str(hermes_home / "hermes-agent" / ".venv" / "bin" / "hermes"),
        str(hermes_home / "hermes-agent" / "venv" / "bin" / "hermes"),
        str(workdir / ".venv" / "bin" / "hermes"),
        "/workspace/live-hermes/.venv/bin/hermes",
        str(workdir / "venv" / "bin" / "hermes"),
        str(hermes_home / ".local" / "bin" / "hermes"),
        "hermes",
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
    hermes_home = resolve_hermes_home()
    hermes_bin = resolve_hermes(workdir, hermes_home)
    if not hermes_bin:
        print("nightly-diagnostics-hermes: hermes binary not found")
        return 1

    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)

    code, output = run_command([hermes_bin, "doctor"], workdir, env=env)
    issues = maybe_issue("hermes doctor", code, output)
    if not issues:
        return 0

    print(f"Nightly diagnostics — hermes — {workdir}")
    print(f"- Hermes home: {hermes_home}")
    print(f"- Hermes binary: {hermes_bin}")
    for line in issues:
        print(f"- {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
