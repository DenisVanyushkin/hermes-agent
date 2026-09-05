"""Prove malformed balanced-scan output stays a blocking gate outcome."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts import run_tests_parallel, upstream_sync_gate

from scripts.pytest_status_lines import parse_status_line

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts" / "run_tests_parallel.py"
GATE = REPO_ROOT / "scripts" / "upstream_sync_gate.py"
FINALIZER = REPO_ROOT / "scripts" / "upstream-sync-finalize.sh"


def _run(nodeid: str, *, collected: set[str], failed: set[str]) -> dict:
    return {
        "collect_ok": True,
        "probe_ok": True,
        "collected_nodeids": collected,
        "failed_nodeids": failed,
    }


def test_unpaired_selector_is_unreadable_at_gate_boundary(tmp_path: Path) -> None:
    """Execute the malformed-selector path through the gate boundary.

    The malformed selector is intentionally accepted by the path-only
    admission checks, then sent through a real runner probe before any
    availability filtering. Links 1–4 execute the admission, request, real
    runner, and report-door behavior. The classifier/payload assertions then
    prove the persisted unreadable input, but do not claim a landing decision;
    the actual ``run_gate outcome=unknown`` and no-landing behavior executes in
    ``test_unreadable_merged_isolated_probe_refuses_to_land`` in the real
    finalizer harness. The ordering assertion below is source evidence only.
    """
    raw_status = "FAILED tests/test_selector.py::test_case[alpha - broken - boom"
    parsed_status = parse_status_line(raw_status)
    assert parsed_status is not None
    assert parsed_status.nodeid is not None
    nodeid = parsed_status.nodeid
    repo = tmp_path / "probe-repo"
    test_file = repo / "tests" / "test_selector.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "import pytest\n\n"
        "@pytest.mark.parametrize('label', ['alpha - broken'])\n"
        "def test_case(label):\n"
        "    assert True\n",
        encoding="utf-8",
    )

    # Link 1: the runner's path-only check admits the malformed nodeid.
    assert run_tests_parallel._looks_like_nodeid(nodeid)

    # Link 2: the gate's path-bound manifest check admits it into the exact
    # merged probe request; no availability filter runs before this point.
    baseline = _run(nodeid, collected=set(), failed=set())
    merged = _run(nodeid, collected={nodeid}, failed={nodeid})
    manifest = {
        "tests": [
            {"path": "tests/test_selector.py", "exists_pre": True, "exists_post": True}
        ]
    }
    request = upstream_sync_gate.build_upstream_probe_request(
        baseline=baseline,
        merged=merged,
        manifest=manifest,
    )
    assert request == {"nodeids": [nodeid], "paths": ["tests/test_selector.py"]}

    # Link 3: use the actual runner against the malformed selector. Do not
    # replace this with a hand-written node-report: this is the trust boundary
    # whose unreadable result makes the regression fail closed.
    request_file = tmp_path / "probe-request.json"
    request_file.write_text(json.dumps(request), encoding="utf-8")
    report = tmp_path / "merged-isolated.nodes.json"
    runner = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--repo-root",
            str(repo),
            "--nodeids-file",
            str(request_file),
            "--node-report",
            str(report),
            "--file-retries",
            "0",
            "--file-timeout",
            "30",
            "--jobs",
            "1",
            "-q",
            "-p",
            "no:cacheprovider",
            "-rA",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert runner.returncode == 1, runner.stdout + runner.stderr
    assert "not found" in (runner.stdout + runner.stderr).lower()
    report_payload = json.loads(report.read_text(encoding="utf-8"))
    assert report_payload["tests_collected"] == 0
    assert report_payload["readable"] is False

    # Link 4: the report door refuses unreadable evidence instead of turning
    # the probe into an empty successful outcome.
    outcome = subprocess.run(
        [
            sys.executable,
            str(GATE),
            "node-outcome",
            "--node-report",
            str(report),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert outcome.returncode == 2
    assert "run_tests_parallel node report is unreadable" in outcome.stderr

    # Classifier boundary: prove the persisted unreadable input that the real
    # finalizer test consumes for its landing refusal.
    unreadable_isolated = {
        "collect_ok": False,
        "probe_ok": False,
        "collected_nodeids": report_payload["collected_nodeids"],
        "failed_nodeids": report_payload["failed_nodeids"],
    }
    classification = upstream_sync_gate.classify_node_failures(
        baseline=baseline,
        upstream_parent=_run(nodeid, collected=set(), failed=set()),
        merged=merged,
        merged_isolated=unreadable_isolated,
        manifest=manifest,
    )
    assert classification["unreadable_runs"] == [
        {"source": "merged_isolated", "stage": "collect"}
    ]
    assert classification["unknown"]
    payload = upstream_sync_gate.build_gate_failures_payload(
        classification=classification,
        merge_sha="a" * 40,
        before="b" * 40,
        legacy_failures=[],
    )
    assert len(payload["unknown"]) + len(payload["unreadable_runs"]) > 0


def test_finalizer_probes_before_availability_filter() -> None:
    """Source-order evidence for rollback A; landing behavior is tested in
    ``TestApplyMergeFromScratchClone`` with the real finalizer harness.
    """
    source = FINALIZER.read_text(encoding="utf-8")
    # Rollback A (availability filtering before merged-isolated probing) would
    # move this boundary earlier and silently erase the malformed request.
    assert source.index(
        'merged_isolated_nodes="$attempt_dir/gate-merged-isolated.nodes.json"'
    ) < source.index(
        'probe_available_nodeids="$attempt_dir/gate-upstream-probe.available.nodeids.json"'
    )
