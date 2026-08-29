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
def classify_nodes(
    nodeids: list[str] | set[str],
    *,
    red_standalone: set[str],
    intra_file_order: set[str],
    host_sensitive: set[str],
    needs_neighbour: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Attach independent evidence states to every supplied node id.

    Missing evidence is deliberately represented as ``not_checked`` rather
    than inferred from another trait.  In particular, order sensitivity and
    neighbour dependence are not mutually exclusive classifications.
    """
    neighbour_nodes = needs_neighbour or set()
    return [
        {
            "nodeid": nodeid,
            "traits": {
                "red_standalone": (
                    "yes" if nodeid in red_standalone else "not_checked"
                ),
                "intra_file_order": (
                    "yes" if nodeid in intra_file_order else "no"
                ),
                "needs_neighbour": (
                    "yes" if nodeid in neighbour_nodes else "not_checked"
                ),
                "cross_process_or_host_state_sensitive": (
                    "yes" if nodeid in host_sensitive else "not_checked"
                ),
            },
        }
        for nodeid in sorted(nodeids)
    ]


def classify_standalone_results(results: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Turn one-node pytest return codes into the required yes/no trait."""
    return {
        nodeid: ("yes" if result["returncode"] != 0 else "no")
        for nodeid, result in sorted(results.items())
    }


def probe_standalone_nodes(
    nodeids: list[str] | set[str], *, tree: Path, python: str
) -> dict[str, dict[str, Any]]:
    """Run every node in its own pytest process and retain concise evidence."""
    results: dict[str, dict[str, Any]] = {}
    for nodeid in sorted(nodeids):
        output, returncode = _run_checked(
            _pytest_command(python, [nodeid]), tree=tree
        )
        results[nodeid] = {
            "returncode": returncode,
            "failed": returncode != 0,
            "output_tail": output[-1000:],
        }
    return results


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


def parse_node_statuses(log: str) -> dict[str, str]:
    """Parse explicit per-node outcome lines from a pytest run log."""
    statuses: dict[str, str] = {}
    for line in log.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or fields[0] not in {
            "PASSED",
            "FAILED",
            "SKIPPED",
            "XFAIL",
            "XPASS",
            "RERUN",
            "ERROR",
        }:
            continue
        nodeid = fields[1].partition(" - ")[0].strip()
        if nodeid.startswith("tests/") and "::" in nodeid:
            statuses[nodeid] = fields[0]
    return statuses


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


def _collect_nodeids(*, tree: Path, python: str, path: str) -> list[str]:
    output, returncode = _run_checked(
        [
            python,
            "-m",
            "pytest",
            path,
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        tree=tree,
    )
    if returncode != 0:
        raise RuntimeError(f"collection failed for {path}: rc={returncode}")
    nodeids: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        nodeid = line.strip()
        if nodeid.startswith("tests/") and "::" in nodeid and nodeid not in seen:
            nodeids.append(nodeid)
            seen.add(nodeid)
    if not nodeids:
        raise RuntimeError(f"collection returned no nodeids for {path}")
    return nodeids


def _probe_file_order(
    *, tree: Path, python: str, path: str
) -> dict[str, Any]:
    """Run one file in explicit collection order and its exact reverse."""
    nodeids = _collect_nodeids(tree=tree, python=python, path=path)
    direct_log, direct_returncode = _run_checked(
        _pytest_command(python, nodeids), tree=tree
    )
    reverse_log, reverse_returncode = _run_checked(
        _pytest_command(python, list(reversed(nodeids))), tree=tree
    )
    direct_statuses = parse_node_statuses(direct_log)
    reverse_statuses = parse_node_statuses(reverse_log)
    missing = (set(nodeids) - direct_statuses.keys()) | (
        set(nodeids) - reverse_statuses.keys()
    )
    if missing:
        raise RuntimeError(
            f"order probe did not produce terminal status for {len(missing)} "
            f"node(s) in {path}"
        )
    changed = sorted(
        nodeid
        for nodeid in nodeids
        if direct_statuses[nodeid] != reverse_statuses[nodeid]
    )
    return {
        "nodeids": nodeids,
        "direct_returncode": direct_returncode,
        "reverse_returncode": reverse_returncode,
        "direct_failed": sorted(
            nodeid
            for nodeid, status in direct_statuses.items()
            if status == "FAILED"
        ),
        "reverse_failed": sorted(
            nodeid
            for nodeid, status in reverse_statuses.items()
            if status == "FAILED"
        ),
        "changed_nodeids": changed,
    }


def classify_failure_report(
    report: dict[str, Any], *, tree: Path, python: str,
    bisection_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Probe all residual files and emit the four-trait node inventory."""
    failed_nodeids = report.get("failed_nodeids")
    if not isinstance(failed_nodeids, list) or not all(
        isinstance(nodeid, str) for nodeid in failed_nodeids
    ):
        raise ValueError("failure report must contain a failed_nodeids list")
    files = sorted({nodeid.split("::", 1)[0] for nodeid in failed_nodeids})
    order_probe = {
        path: _probe_file_order(tree=tree, python=python, path=path)
        for path in files
    }
    if bisection_report is not None:
        for path, result in bisection_report.items():
            if path in order_probe and isinstance(result, dict):
                order_probe[path]["bisection"] = {
                    "conclusion": result.get("bisection"),
                    "minimal_changed_subset": result.get("minimal_changed_subset"),
                    "probe_count": len(result.get("probes", [])),
                }
    order_changed = {
        nodeid
        for result in order_probe.values()
        for nodeid in result["changed_nodeids"]
    }
    standalone_probe = probe_standalone_nodes(
        failed_nodeids, tree=tree, python=python
    )
    standalone_states = classify_standalone_results(standalone_probe)
    standalone = {
        nodeid for nodeid, state in standalone_states.items() if state == "yes"
    }
    host_sensitive = {
        nodeid
        for nodeid in failed_nodeids
        if nodeid
        in {
            "tests/hermes_cli/test_profile_handoff.py::test_preview_mode_writes_nothing",
            "tests/test_baseline_git.py::test_clean_repo_returns_empty",
        }
    }
    nodes = classify_nodes(
        failed_nodeids,
        red_standalone=standalone,
        intra_file_order=order_changed,
        host_sensitive=host_sensitive,
    )
    return {
        "schema": "hermes-order-leakage-classification/v1",
        "source_report": report.get("source_report"),
        "tree": str(tree),
        "head": _git(tree, "rev-parse", "HEAD"),
        "node_count": len(nodes),
        "nodes": nodes,
        "order_probe": order_probe,
        "evidence": {
            "red_standalone": {
                "rule": "yes when pytest <nodeid> fails in its own process with no file or run neighbours",
                "nodeids": sorted(standalone),
                "no_nodeids": sorted(
                    nodeid for nodeid, state in standalone_states.items() if state == "no"
                ),
                "probe_results": standalone_probe,
            },
            "needs_neighbour": {
                "rule": "not probed in task 11; reserved for task 13",
            },
            "cross_process_or_host_state_sensitive": {
                "rule": "yes only when the same node changed result under different external process or shared-state conditions",
                "measurements": [
                    {
                        "node_or_scope": "test_gate_b_record_controls.py",
                        "without_external_process": 1,
                        "with_external_process": 42,
                        "cause": "unresolved between resource pressure and shared state",
                    },
                    {
                        "node_or_scope": "test_profile_handoff.py + test_baseline_git.py",
                        "without_fixture_mutation": 0,
                        "with_tracked_fixture_mutation": 2,
                        "cause": "shared worktree state: tracked legal_research fixtures",
                    },
                ],
                "nodeids": sorted(host_sensitive),
            },
            "fixture_writer": {
                "status": "unresolved",
                "tracked_suite_search": "no direct writer found; legal-research tests read fixtures only",
                "sequential_full_probe": "464 selected files, 6974 passed, 197 failed, no legal_research HTML mutation",
                "parallel_full_probe": "464 selected files, 6124 estimated tests, 119 file-level failures, no legal_research HTML mutation",
                "strace_probe": "legal-research client/review tests: 17 passed, no writes to legal_research HTML",
            },
        },
    }


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
    parser.add_argument("--attempt", type=Path)
    parser.add_argument(
        "--failure-report",
        type=Path,
        help="classify failed_nodeids from a saved node report with order probes",
    )
    parser.add_argument(
        "--standalone-only",
        type=Path,
        help="update an existing classification JSON with one-node probes",
    )
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument(
        "--python", default=os.environ.get("HERMES_PYTHON", sys.executable)
    )
    parser.add_argument(
        "--bisection-report",
        type=Path,
        help="merge a saved direct/reverse bisection report into classification JSON",
    )
    args = parser.parse_args(argv)
    if args.standalone_only is not None:
        result = json.loads(args.standalone_only.read_text(encoding="utf-8"))
        nodeids = [node["nodeid"] for node in result.get("nodes", [])]
        probe = probe_standalone_nodes(nodeids, tree=args.tree, python=args.python)
        states = classify_standalone_results(probe)
        for node in result["nodes"]:
            node["traits"]["red_standalone"] = states[node["nodeid"]]
        evidence = result.setdefault("evidence", {})
        evidence["red_standalone"] = {
            "rule": "yes when pytest <nodeid> fails in its own process with no file or run neighbours",
            "nodeids": sorted(node for node, state in states.items() if state == "yes"),
            "no_nodeids": sorted(node for node, state in states.items() if state == "no"),
            "probe_results": probe,
        }
        result["standalone_probe"] = {
            "node_count": len(probe),
            "yes": sum(state == "yes" for state in states.values()),
            "no": sum(state == "no" for state in states.values()),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.failure_report is not None:
        report = json.loads(args.failure_report.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise SystemExit("failure report must be a JSON object")
        report["source_report"] = str(args.failure_report)
        bisection = None
        if args.bisection_report is not None:
            bisection = json.loads(args.bisection_report.read_text(encoding="utf-8"))
        result = classify_failure_report(
            report, tree=args.tree, python=args.python, bisection_report=bisection
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.attempt is None:
        parser.error("--attempt is required unless --failure-report is given")
    report = measure(attempt=args.attempt, tree=args.tree, python=args.python)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
