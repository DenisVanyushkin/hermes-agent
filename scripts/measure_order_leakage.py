#!/usr/bin/env python3
"""Measure cross-file order leakage in the fork test gate.

The command runs the merged selection twice as one pytest process.  For each
run it then reruns only the files that failed in that full run, one process per
file, and compares node-id sets.  It deliberately records host load and the
worktree identity around every child process: this is a measurement, not a
normal pass/fail test.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


FAILED_LINE = re.compile(r"^FAILED (.+?)(?: - .*)?$")
def parse_failed_nodeids(log: str) -> set[str]:
    """Parse pytest's final ``FAILED nodeid`` lines from one log."""
    failed: set[str] = set()
    for line in log.splitlines():
        match = FAILED_LINE.match(line.strip())
        if not match:
            continue
        nodeid = match.group(1).strip()
        if nodeid.startswith("tests/") and "::" in nodeid:
            failed.add(nodeid)
    return failed


def parse_measurement_report(
    full_log: str, isolated_logs: dict[str, str]
) -> dict[str, Any]:
    """Compare one full-run log with per-file isolated-run logs."""
    full = parse_failed_nodeids(full_log)
    isolated_by_file = {
        path: parse_failed_nodeids(log)
        for path, log in isolated_logs.items()
    }
    isolated = set().union(*isolated_by_file.values()) if isolated_by_file else set()
    survive = full & isolated
    vanish = full - isolated
    isolation_only = isolated - full
    by_file: dict[str, dict[str, list[str]]] = {}
    for path in sorted(set(isolated_by_file) | {node.split("::", 1)[0] for node in full}):
        file_full = sorted(node for node in full if node.split("::", 1)[0] == path)
        file_isolated = sorted(isolated_by_file.get(path, set()))
        by_file[path] = {
            "full": file_full,
            "isolated": file_isolated,
            "survive": sorted(set(file_full) & set(file_isolated)),
            "vanish": sorted(set(file_full) - set(file_isolated)),
            "isolation_only": sorted(set(file_isolated) - set(file_full)),
        }
    return {
        "full": len(full),
        "isolated": len(isolated),
        "survive": len(survive),
        "vanish": len(vanish),
        "isolation_only": len(isolation_only),
        "by_file": by_file,
        "bidirectional": {
            path: entries
            for path, entries in by_file.items()
            if entries["vanish"] and entries["isolation_only"]
        },
    }


def _run_checked(command: list[str], *, tree: Path) -> tuple[str, int]:
    before_head = _git(tree, "rev-parse", "HEAD")
    before_status = _git(tree, "status", "--short", "--untracked-files=all")
    process = subprocess.run(
        command,
        cwd=tree,
        capture_output=True,
        text=True,
        errors="replace",
    )
    after_head = _git(tree, "rev-parse", "HEAD")
    after_status = _git(tree, "status", "--short", "--untracked-files=all")
    if before_head != after_head or before_status != after_status:
        raise RuntimeError(
            "tree moved or changed during pytest: "
            f"head {before_head!r}->{after_head!r}, "
            f"status {before_status!r}->{after_status!r}"
        )
    return process.stdout + process.stderr, process.returncode


def _git(tree: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(tree), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _host_load() -> dict[str, Any]:
    uptime = subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()
    ps = subprocess.run(
        ["ps", "-eo", "comm=,args="], capture_output=True, text=True
    )
    processes = []
    for line in ps.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2:
            continue
        command, args = fields
        executable = Path(command).name
        if executable in {"pytest", "python", "python3"} and re.search(
            r"(^|\s)(?:-m\s+)?pytest(?:\s|$)", args
        ):
            processes.append(line)
    return {"uptime": uptime, "pytest_processes": len(processes)}


def _attempt_selection(attempt: Path) -> list[str]:
    payload = json.loads((attempt / "gate-selection.json").read_text(encoding="utf-8"))
    tests = payload.get("tests")
    if not isinstance(tests, list):
        raise ValueError("attempt selection has no tests list")
    selected = [
        item["path"]
        for item in tests
        if isinstance(item, dict)
        and item.get("exists_post") is True
        and isinstance(item.get("path"), str)
    ]
    if not selected:
        raise ValueError("attempt selection is empty")
    return sorted(set(selected))


def _pytest_command(python: str, paths: list[str]) -> list[str]:
    return [
        python,
        "-m",
        "pytest",
        *paths,
        "-q",
        "-p",
        "no:cacheprovider",
        "--timeout=90",
        "-rA",
        "--continue-on-collection-errors",
    ]


def _one_measurement(
    *, attempt: Path, tree: Path, python: str, repeat: int
) -> dict[str, Any]:
    selected = _attempt_selection(attempt)
    host_load: list[dict[str, Any]] = []
    host_load.append({"phase": "full", "load": _host_load()})
    full_log, full_rc = _run_checked(_pytest_command(python, selected), tree=tree)
    full_failed = parse_failed_nodeids(full_log)
    files = sorted({node.split("::", 1)[0] for node in full_failed})
    isolated_logs: dict[str, str] = {}
    isolated_rc: dict[str, int] = {}
    for path in files:
        host_load.append({"phase": "isolated", "file": path, "load": _host_load()})
        isolated_logs[path], isolated_rc[path] = _run_checked(
            _pytest_command(python, [path]), tree=tree
        )
    report = parse_measurement_report(full_log, isolated_logs)
    report.update({
        "repeat": repeat,
        "full_returncode": full_rc,
        "isolated_returncodes": isolated_rc,
        "files": files,
        "host_load": host_load,
    })
    return report


def measure(*, attempt: Path, tree: Path, python: str) -> dict[str, Any]:
    target_head = _git(tree, "rev-parse", "HEAD")
    runs = [
        _one_measurement(attempt=attempt, tree=tree, python=python, repeat=1),
        _one_measurement(attempt=attempt, tree=tree, python=python, repeat=2),
    ]
    first, second = runs
    first_by_file = first["by_file"]
    second_by_file = second["by_file"]
    first_full = {
        node
        for entries in first_by_file.values()
        for node in entries["full"]
    }
    second_full = {
        node
        for entries in second_by_file.values()
        for node in entries["full"]
    }
    first_isolated = {
        node
        for entries in first_by_file.values()
        for node in entries["isolated"]
    }
    second_isolated = {
        node
        for entries in second_by_file.values()
        for node in entries["isolated"]
    }
    return {
        "schema": "hermes-order-leakage/v1",
        "attempt": str(attempt),
        "tree": str(tree),
        "head": target_head,
        "full": first["full"],
        "isolated": first["isolated"],
        "survive": first["survive"],
        "vanish": first["vanish"],
        "isolation_only": first["isolation_only"],
        "by_file": first_by_file,
        "bidirectional": first["bidirectional"],
        "runs": runs,
        "repeatability": {
            "identical_counts": all(
                first[key] == second[key]
                for key in ("full", "isolated", "survive", "vanish", "isolation_only")
            ),
            "count_delta": {
                key: second[key] - first[key]
                for key in ("full", "isolated", "survive", "vanish", "isolation_only")
            },
            "full_added": sorted(set(second_by_file) - set(first_by_file)),
            "full_removed": sorted(set(first_by_file) - set(second_by_file)),
            "full_nodeids_added": sorted(second_full - first_full),
            "full_nodeids_removed": sorted(first_full - second_full),
            "isolated_nodeids_added": sorted(second_isolated - first_isolated),
            "isolated_nodeids_removed": sorted(first_isolated - second_isolated),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument(
        "--python", default=os.environ.get("HERMES_PYTHON", sys.executable)
    )
    args = parser.parse_args(argv)
    report = measure(attempt=args.attempt, tree=args.tree, python=args.python)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
