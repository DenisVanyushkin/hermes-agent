"""Prove malformed balanced-scan output stays a blocking gate outcome."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts import run_tests_parallel, upstream_sync_gate


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


def test_unpaired_selector_is_unreadable_and_blocking(tmp_path: Path) -> None:
    """Protect the fail-closed direction and the merged-isolated ordering.

    The malformed selector is intentionally accepted by the path-only
    admission checks, then sent through a real runner probe before any
    availability filtering. Its unreadable result must survive the gate and
    prevent landing. This protects the balanced-scan failure direction and
    the order ``merged-isolated probe before upstream availability filter``.
    """
    nodeid = "tests/test_selector.py::test_case[alpha - broken"
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

    # Link 5: model the rejected report at the classifier boundary and prove
    # the persistence decision has non-zero unknown/unreadable counts, which
    # is the finalizer's landing refusal condition.
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


def test_finalizer_keeps_unreadable_probe_fail_closed() -> None:
    source = FINALIZER.read_text(encoding="utf-8")

    # Rollback A (availability filtering before merged-isolated probing) would
    # move this boundary earlier and silently erase the malformed request.
    assert source.index(
        'merged_isolated_nodes="$attempt_dir/gate-merged-isolated.nodes.json"'
    ) < source.index(
        'probe_available_nodeids="$attempt_dir/gate-upstream-probe.available.nodeids.json"'
    )
    # Rollback B (turning an unreadable probe into an empty success) would
    # weaken the exact record that the landing decision counts as unknown.
    assert (
        'Path(sys.argv[1]).write_text(json.dumps({\n'
        '    "collect_ok": False,\n'
        '    "probe_ok": False,\n'
        '    "collected_nodeids": [],\n'
        '    "failed_nodeids": [],\n'
        '    "error_count": 0,\n'
        '    "collection_error_paths": [],\n'
        '    "unreadable_runs": [{"source": sys.argv[2], "stage": "receipt"}],'
    ) in source
