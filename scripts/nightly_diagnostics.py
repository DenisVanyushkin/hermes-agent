#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DISK_WARN_PCT = 85
MEM_WARN_GB = 2.0


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
        if (path / "job_intel").is_dir():
            return path
    return Path(os.getcwd())


def resolve_hermes_home() -> Path:
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        return Path(env_home)
    return Path("/home/hermes/.hermes")


def clean_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


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
                import re

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

    alarming = mem_low
    disk_parts: list[str] = []
    unique_paths = []
    seen = set()
    for raw in paths:
        if not raw or raw in seen:
            continue
        seen.add(raw)
        if Path(raw).exists():
            unique_paths.append(raw)
    if unique_paths:
        proc = subprocess.run(
            ["df", "-hP", *unique_paths],
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


def main() -> int:
    workdir = resolve_workdir()
    hermes_home = resolve_hermes_home()
    system_line, alarming = system_report([str(hermes_home), str(workdir), "/var/lib/browser-desktop"])
    if not alarming:
        return 0
    print(f"Nightly diagnostics — system — {workdir}")
    print(f"- Hermes home: {hermes_home}")
    print(f"- {system_line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
