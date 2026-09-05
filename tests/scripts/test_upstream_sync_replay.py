from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "upstream_sync" / "replay_9f3feebcd3.json"
FIXTURE_4D = Path(__file__).resolve().parents[1] / "fixtures" / "upstream_sync" / "replay_4d4dfb04b3.json"


def test_recorded_merge_extracts_object_tree_and_unscoped_measurement_is_clean():
    from upstream_sync_invariants import check_merge
    from upstream_sync_replay import extract_merge_case

    data = json.loads(FIXTURE.read_text())
    repo = Path(__file__).resolve().parents[2]
    case = extract_merge_case(repo, data["merge"], data["path"])

    assert case.base == data["base"]
    assert case.ours_commit == data["ours_commit"]
    assert case.theirs_commit == data["theirs_commit"]
    assert case.path == data["path"]
    assert case.result_text is not None
    report = check_merge(
        [case.path], lambda _p: case.ours_text, lambda _p: case.theirs_text,
        lambda _p: case.result_text, lambda _p: case.base_text,
    )
    assert report.findings == []
    assert data["expected_raw_findings"] == []


def test_recorded_merge_acceptance_uses_the_policy_aware_gate():
    from upstream_sync_invariants import check_merge
    from upstream_sync_replay import extract_merge_case

    data = json.loads(FIXTURE.read_text())
    repo = Path(__file__).resolve().parents[2]
    case = extract_merge_case(repo, data["merge"], data["path"])

    report = check_merge(
        [case.path], lambda _p: case.ours_text, lambda _p: case.theirs_text,
        lambda _p: case.result_text, lambda _p: case.base_text,
        policy_by_path={case.path: "merge-both"},
    )
    assert [(f.kind, f.symbol) for f in report.findings] == [
        (item["kind"], item["symbol"]) for item in data["expected_policy_findings"]
    ]

    keep_local = check_merge(
        [case.path], lambda _p: case.ours_text, lambda _p: case.theirs_text,
        lambda _p: case.result_text, lambda _p: case.base_text,
        policy_by_path={case.path: "keep-local"},
    )
    assert keep_local.findings == []


def test_second_recorded_replay_has_no_policy_aware_findings():
    from upstream_sync_invariants import check_merge
    from upstream_sync_replay import extract_merge_case

    data = json.loads(FIXTURE_4D.read_text())
    repo = Path(__file__).resolve().parents[2]
    case = extract_merge_case(repo, data["merge"], data["path"])
    assert case.path in case.both_sides

    report = check_merge(
        [case.path], lambda _p: case.ours_text, lambda _p: case.theirs_text,
        lambda _p: case.result_text, lambda _p: case.base_text,
        policy_by_path={case.path: "merge-both"},
    )
    assert report.findings == []
    assert data["expected_policy_findings"] == []


def test_replay_fixture_records_the_measurement_method_and_all_five_classifications():
    data = json.loads(FIXTURE.read_text())
    assert data["orientation"].startswith("ours=first-parent")
    assert len(data["manual_classifications"]) == 5
