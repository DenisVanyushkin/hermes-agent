"""Юниты гейта upstream-sync: разбор вывода git merge-tree."""

from __future__ import annotations

import subprocess
import json
import sys
from pathlib import Path

import pytest

from scripts import upstream_sync_gate
from scripts.upstream_sync_gate import new_failures, parse_merge_tree


CLEAN = "fa64e4b20356cb615af29bad8ffc5ed5f4e95221\n"

CONFLICTED = (
    "a990a74ae62ce3bc7c5e3e013a38d6ea06a5b4b8\n"
    "f.txt\n"
    "gateway/run.py\n"
    "\n"
    "Auto-merging f.txt\n"
    "CONFLICT (content): Merge conflict in f.txt\n"
)


def test_manifest_universe_is_union():
    """A deletion stays visible even though it exists only before the merge.

    The universe has three independent sources: fork-only paths from the
    before tree, fork-only paths from the after tree, and every test path in
    the before..after diff.  Building it from the after tree alone recreates
    the incident this gate is fixing: the deleted path disappears before its
    ``exists_pre``/``exists_post`` classification can be reported.
    """
    before = "1" * 40
    after = "2" * 40
    boundary = "3" * 40
    deleted = "tests/test_upstream_deleted.py"

    builder = getattr(upstream_sync_gate, "build_selection_manifest", None)
    assert callable(builder), (
        "the selection-manifest universe has no builder, so the deleted path "
        f"{deleted!r} is absent before it can be classified"
    )

    manifest = builder(
        before=before,
        after=after,
        boundary=boundary,
        before_paths=[
            "tests/test_fork_existing.py",
            "tests/test_upstream_kept.py",
            deleted,
        ],
        after_paths=[
            "tests/test_fork_added.py",
            "tests/test_fork_existing.py",
            "tests/test_upstream_added.py",
            "tests/test_upstream_kept.py",
        ],
        boundary_paths=[
            "tests/test_upstream_added.py",
            "tests/test_upstream_kept.py",
        ],
        changed_paths=[deleted, "tests/test_upstream_added.py"],
    )

    assert manifest == {
        "schema_version": "upstream-sync-test-selection/v1",
        "before": before,
        "after": after,
        "boundary": boundary,
        "tests": [
            {
                "path": "tests/test_fork_added.py",
                "exists_pre": False,
                "exists_post": True,
            },
            {
                "path": "tests/test_fork_existing.py",
                "exists_pre": True,
                "exists_post": True,
            },
            {
                "path": "tests/test_upstream_added.py",
                "exists_pre": False,
                "exists_post": True,
            },
            {"path": deleted, "exists_pre": True, "exists_post": False},
        ],
    }


def test_manifest_builder_owns_test_path_filtering():
    """Raw git listings are filtered once, inside the pure builder."""
    builder = getattr(upstream_sync_gate, "build_selection_manifest", None)
    assert callable(builder), "selection-manifest builder is not implemented"

    manifest = builder(
        before="1" * 40,
        after="2" * 40,
        boundary="3" * 40,
        before_paths=[
            "tests/test_kept.py",
            "tests/__init__.py",
            "tests/fixtures/helper.py",
            "tests/._generated.py",
            "tests/README.md",
        ],
        after_paths=[
            "tests/test_kept.py",
            "tests/__init__.py",
            "tests/fixtures/helper.py",
            "tests/._generated.py",
            "tests/README.md",
        ],
        boundary_paths=[],
        changed_paths=[
            "tests/test_kept.py",
            "tests/__init__.py",
            "tests/fixtures/helper.py",
            "tests/._generated.py",
            "tests/README.md",
        ],
    )

    assert [item["path"] for item in manifest["tests"]] == ["tests/test_kept.py"]


def test_manifest_rejects_changed_path_absent_from_both_trees():
    """A correctly bound before..after diff cannot name a path in neither tree."""
    builder = getattr(upstream_sync_gate, "build_selection_manifest", None)
    assert callable(builder), "selection-manifest builder is not implemented"
    poisoned = "tests/test_from_another_candidate.py"

    with pytest.raises(ValueError, match=poisoned):
        builder(
            before="1" * 40,
            after="2" * 40,
            boundary="3" * 40,
            before_paths=[],
            after_paths=[],
            boundary_paths=[],
            changed_paths=[poisoned],
        )


def test_manifest_rejects_identical_before_and_after():
    """A differential manifest must describe two distinct candidate trees."""
    same = "1" * 40

    with pytest.raises(ValueError, match="before.*after.*distinct"):
        upstream_sync_gate.build_selection_manifest(
            before=same,
            after=same,
            boundary="3" * 40,
            before_paths=["tests/test_same.py"],
            after_paths=["tests/test_same.py"],
            boundary_paths=[],
            changed_paths=[],
        )


def test_clean_merge_reports_no_conflicts():
    report = parse_merge_tree(CLEAN)
    assert report.tree_oid == "fa64e4b20356cb615af29bad8ffc5ed5f4e95221"
    assert report.conflicted_paths == []


def test_conflicted_merge_lists_paths_only():
    report = parse_merge_tree(CONFLICTED)
    assert report.tree_oid == "a990a74ae62ce3bc7c5e3e013a38d6ea06a5b4b8"
    assert report.conflicted_paths == ["f.txt", "gateway/run.py"]


def test_informational_lines_are_never_mistaken_for_paths():
    """Auto-merging и CONFLICT идут ПОСЛЕ пустой строки и путями не являются."""
    report = parse_merge_tree(CONFLICTED)
    assert not any("Auto-merging" in p for p in report.conflicted_paths)
    assert not any("CONFLICT" in p for p in report.conflicted_paths)


def test_empty_output_is_a_failure_not_a_clean_merge():
    """Пустой вывод означает, что git не отработал, а не что конфликтов нет."""
    with pytest.raises(ValueError):
        parse_merge_tree("")


def _log(failed: list[str], summary: str = "1 failed, 2 passed in 1.00s") -> str:
    body = "".join(f"FAILED {name} - AssertionError: boom\n" for name in failed)
    return body + summary + "\n"


def _node_run(
    *,
    collected: set[str],
    failed: set[str],
    collect_ok: bool = True,
    probe_ok: bool = True,
) -> dict:
    return {
        "collect_ok": collect_ok,
        "probe_ok": probe_ok,
        "collected_nodeids": collected,
        "failed_nodeids": failed,
    }


def _manifest(*entries: tuple[str, bool, bool]) -> dict:
    return {
        "tests": [
            {"path": path, "exists_pre": exists_pre, "exists_post": exists_post}
            for path, exists_pre, exists_post in entries
        ]
    }


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(
            {
                "baseline": _node_run(collected=set(), failed=set()),
                "upstream_parent": _node_run(
                    collected={"tests/test_common.py::test_added_upstream"},
                    failed=set(),
                ),
                "merged": _node_run(
                    collected={"tests/test_common.py::test_added_upstream"},
                    failed={"tests/test_common.py::test_added_upstream"},
                ),
                "manifest": _manifest(("tests/test_common.py", True, True)),
            },
            {
                "common_path": [
                    {
                        "path": "tests/test_common.py",
                        "nodeid": "tests/test_common.py::test_added_upstream",
                        "classification": "fork_compatibility_failure",
                    }
                ],
                "post_only_path": [],
                "pre_existing": [],
                "unknown": [],
            },
            id="upstream-node-pass-merged-node-fail",
        ),
        pytest.param(
            {
                "baseline": _node_run(collected=set(), failed=set()),
                "upstream_parent": _node_run(
                    collected={"tests/test_common.py::test_red_upstream"},
                    failed={"tests/test_common.py::test_red_upstream"},
                ),
                "merged": _node_run(
                    collected={"tests/test_common.py::test_red_upstream"},
                    failed={"tests/test_common.py::test_red_upstream"},
                ),
                "manifest": _manifest(("tests/test_common.py", True, True)),
            },
            {
                "common_path": [
                    {
                        "path": "tests/test_common.py",
                        "nodeid": "tests/test_common.py::test_red_upstream",
                        "classification": "upstream_red_admission_failure",
                    }
                ],
                "post_only_path": [],
                "pre_existing": [],
                "unknown": [],
            },
            id="upstream-node-fail-merged-node-fail",
        ),
        pytest.param(
            {
                "baseline": _node_run(collected=set(), failed=set()),
                "upstream_parent": _node_run(collected=set(), failed=set()),
                "merged": _node_run(
                    collected={"tests/test_post_only.py::test_local_node"},
                    failed={"tests/test_post_only.py::test_local_node"},
                ),
                "manifest": _manifest(("tests/test_post_only.py", False, True)),
            },
            {
                "common_path": [],
                "post_only_path": [
                    {
                        "path": "tests/test_post_only.py",
                        "nodeid": "tests/test_post_only.py::test_local_node",
                        "classification": "merge_resolution_or_local_introduced",
                    }
                ],
                "pre_existing": [],
                "unknown": [],
            },
            id="node-absent-from-baseline-and-upstream",
        ),
        pytest.param(
            {
                "baseline": _node_run(collected=set(), failed=set()),
                "upstream_parent": _node_run(
                    collected=set(), failed=set(), collect_ok=False
                ),
                "merged": _node_run(
                    collected={"tests/test_common.py::test_collect_unknown"},
                    failed={"tests/test_common.py::test_collect_unknown"},
                ),
                "manifest": _manifest(("tests/test_common.py", True, True)),
            },
            {
                "common_path": [],
                "post_only_path": [],
                "pre_existing": [],
                "unknown": [
                    {
                        "path": "tests/test_common.py",
                        "nodeid": "tests/test_common.py::test_collect_unknown",
                        "source": "upstream_parent",
                        "stage": "collect",
                    }
                ],
                "unreadable_runs": [
                    {"source": "upstream_parent", "stage": "collect"}
                ],
            },
            id="upstream-collect-unreadable",
        ),
        pytest.param(
            {
                "baseline": _node_run(collected=set(), failed=set()),
                "upstream_parent": _node_run(
                    collected={"tests/test_common.py::test_probe"},
                    failed=set(),
                    probe_ok=False,
                ),
                "merged": _node_run(
                    collected={"tests/test_common.py::test_probe"},
                    failed={"tests/test_common.py::test_probe"},
                ),
                "manifest": _manifest(("tests/test_common.py", True, True)),
            },
            {
                "common_path": [],
                "post_only_path": [],
                "pre_existing": [],
                "unknown": [
                    {
                        "path": "tests/test_common.py",
                        "nodeid": "tests/test_common.py::test_probe",
                        "source": "upstream_parent",
                        "stage": "probe",
                    }
                ],
                "unreadable_runs": [
                    {"source": "upstream_parent", "stage": "probe"}
                ],
            },
            id="upstream-probe-unreadable",
        ),
        pytest.param(
            {
                "baseline": _node_run(
                    collected={"tests/test_common.py::test_regression"},
                    failed=set(),
                ),
                "upstream_parent": _node_run(
                    collected={"tests/test_common.py::test_regression"},
                    failed=set(),
                ),
                "merged": _node_run(
                    collected={"tests/test_common.py::test_regression"},
                    failed={"tests/test_common.py::test_regression"},
                ),
                "manifest": _manifest(("tests/test_common.py", True, True)),
            },
            {
                "common_path": [
                    {
                        "path": "tests/test_common.py",
                        "nodeid": "tests/test_common.py::test_regression",
                        "classification": "fork_regression",
                    }
                ],
                "post_only_path": [],
                "pre_existing": [],
                "unknown": [],
            },
            id="baseline-pass-merged-fail",
        ),
        pytest.param(
            {
                "baseline": _node_run(
                    collected={"tests/test_common.py::test_preexisting"},
                    failed={"tests/test_common.py::test_preexisting"},
                ),
                "upstream_parent": _node_run(
                    collected={"tests/test_common.py::test_preexisting"},
                    failed=set(),
                ),
                "merged": _node_run(
                    collected={"tests/test_common.py::test_preexisting"},
                    failed={"tests/test_common.py::test_preexisting"},
                ),
                "manifest": _manifest(("tests/test_common.py", True, True)),
            },
            {
                "common_path": [],
                "post_only_path": [],
                "pre_existing": [
                    {
                        "path": "tests/test_common.py",
                        "nodeid": "tests/test_common.py::test_preexisting",
                        "classification": "pre_existing_failure",
                    }
                ],
                "unknown": [],
            },
            id="baseline-fail-merged-fail-is-preexisting",
        ),
        pytest.param(
            {
                "baseline": _node_run(collected=set(), failed=set()),
                "upstream_parent": _node_run(
                    collected={
                        "tests/z.py::test_z",
                        "tests/a.py::test_a",
                    },
                    failed=set(),
                ),
                "merged": _node_run(
                    collected={
                        "tests/z.py::test_z",
                        "tests/a.py::test_a",
                    },
                    failed={
                        "tests/z.py::test_z",
                        "tests/a.py::test_a",
                    },
                ),
                "manifest": _manifest(
                    ("tests/z.py", True, True), ("tests/a.py", True, True)
                ),
            },
            {
                "common_path": [
                    {
                        "path": "tests/a.py",
                        "nodeid": "tests/a.py::test_a",
                        "classification": "fork_compatibility_failure",
                    },
                    {
                        "path": "tests/z.py",
                        "nodeid": "tests/z.py::test_z",
                        "classification": "fork_compatibility_failure",
                    },
                ],
                "post_only_path": [],
                "pre_existing": [],
                "unknown": [],
            },
            id="classification-output-is-sorted",
        ),
        pytest.param(
            {
                "baseline": _node_run(collected=set(), failed=set()),
                "upstream_parent": _node_run(collected=set(), failed=set()),
                "merged": _node_run(
                    collected=set(),
                    failed={"tests/test_common.py::test_not_collected"},
                ),
                "merged_isolated": _node_run(collected=set(), failed=set()),
                "manifest": _manifest(("tests/test_common.py", True, True)),
            },
            {
                "common_path": [],
                "post_only_path": [],
                "pre_existing": [],
                "unknown": [
                    {
                        "path": "tests/test_common.py",
                        "nodeid": "tests/test_common.py::test_not_collected",
                        "source": "merged",
                        "stage": "outcome",
                    }
                ],
            },
            id="failed-node-not-collected-is-unknown",
        ),
    ],
)
def test_classification_matrix(case, expected):
    classifier = getattr(upstream_sync_gate, "classify_node_failures", None)
    assert callable(classifier), (
        "node-aware classifier is not implemented; classification matrix "
        f"case={case!r}"
    )
    assert classifier(
        baseline=case["baseline"],
        upstream_parent=case["upstream_parent"],
        merged=case["merged"],
        merged_isolated=case.get("merged_isolated", case["merged"]),
        manifest=case["manifest"],
    ) == expected


def test_merged_failure_that_passes_in_merged_isolation_is_order_dependent():
    nodeid = "tests/test_common.py::test_order_dependent"
    result = upstream_sync_gate.classify_node_failures(
        baseline=_node_run(collected=set(), failed=set()),
        upstream_parent=_node_run(collected={nodeid}, failed=set()),
        merged=_node_run(collected={nodeid}, failed={nodeid}),
        merged_isolated=_node_run(collected={nodeid}, failed=set()),
        manifest=_manifest(("tests/test_common.py", True, True)),
    )

    assert result["common_path"] == [
        {
            "path": "tests/test_common.py",
            "nodeid": nodeid,
            "classification": "order_dependent_failure",
            "classification_without_isolation": "fork_compatibility_failure",
        }
    ]


def test_order_dependent_failure_is_blocking_without_human_decision():
    node = {
        "path": "tests/test_common.py",
        "nodeid": "tests/test_common.py::test_order_dependent",
        "classification": "order_dependent_failure",
    }

    payload = upstream_sync_gate.build_gate_failures_payload(
        classification={
            "common_path": [node],
            "post_only_path": [],
            "pre_existing": [],
            "unknown": [],
        },
        merge_sha="a" * 40,
        before="b" * 40,
        legacy_failures=[],
    )

    assert payload["blocking_failures"] == [node]


def test_order_dependent_failure_preserves_unshadowed_upstream_classification():
    nodeid = "tests/test_common.py::test_order_dependent_upstream_red"
    result = upstream_sync_gate.classify_node_failures(
        baseline=_node_run(collected=set(), failed=set()),
        upstream_parent=_node_run(collected={nodeid}, failed={nodeid}),
        merged=_node_run(collected={nodeid}, failed={nodeid}),
        merged_isolated=_node_run(collected={nodeid}, failed=set()),
        manifest=_manifest(("tests/test_common.py", True, True)),
    )

    assert result["common_path"] == [
        {
            "path": "tests/test_common.py",
            "nodeid": nodeid,
            "classification": "order_dependent_failure",
            "classification_without_isolation": "upstream_red_admission_failure",
        }
    ]


def test_payload_blocks_exactly_the_explicit_blocking_class_set():
    blocking_classes = sorted(upstream_sync_gate.BLOCKING_CLASSIFICATIONS)
    entries = [
        {
            "path": f"tests/test_{index}.py",
            "nodeid": f"tests/test_{index}.py::test_failure",
            "classification": classification,
        }
        for index, classification in enumerate(blocking_classes)
    ]
    informational = {
        "path": "tests/test_existing.py",
        "nodeid": "tests/test_existing.py::test_failure",
        "classification": "pre_existing_failure",
    }

    payload = upstream_sync_gate.build_gate_failures_payload(
        classification={
            "common_path": entries,
            "post_only_path": [],
            "pre_existing": [informational],
            "unknown": [],
        },
        merge_sha="a" * 40,
        before="b" * 40,
        legacy_failures=[],
    )

    assert payload["blocking_failures"] == entries
    assert payload["blocking_failures_by_class"] == {
        classification: 1 for classification in blocking_classes
    }
    assert informational not in payload["blocking_failures"]


def test_removing_a_class_from_blocking_set_can_make_it_informational(monkeypatch):
    node = {
        "path": "tests/test_upstream.py",
        "nodeid": "tests/test_upstream.py::test_red_admission",
        "classification": "upstream_red_admission_failure",
    }
    monkeypatch.setattr(
        upstream_sync_gate,
        "BLOCKING_CLASSIFICATIONS",
        frozenset({"order_dependent_failure"}),
    )
    monkeypatch.setattr(
        upstream_sync_gate,
        "INFORMATIONAL_CLASSIFICATIONS",
        frozenset({"pre_existing_failure", "upstream_red_admission_failure"}),
        raising=False,
    )

    payload = upstream_sync_gate.build_gate_failures_payload(
        classification={
            "common_path": [node],
            "post_only_path": [],
            "pre_existing": [],
            "unknown": [],
        },
        merge_sha="a" * 40,
        before="b" * 40,
        legacy_failures=[],
    )

    assert payload["blocking_failures"] == []
    assert payload["blocking_failures_by_class"] == {}
    assert payload["unknown_blocking_classifications"] == []


def test_removed_class_without_informational_policy_remains_fail_closed(monkeypatch):
    node = {
        "path": "tests/test_future.py",
        "nodeid": "tests/test_future.py::test_future_policy",
        "classification": "upstream_red_admission_failure",
    }
    monkeypatch.setattr(
        upstream_sync_gate,
        "BLOCKING_CLASSIFICATIONS",
        frozenset({"order_dependent_failure"}),
    )

    payload = upstream_sync_gate.build_gate_failures_payload(
        classification={
            "common_path": [node],
            "post_only_path": [],
            "pre_existing": [],
            "unknown": [],
        },
        merge_sha="a" * 40,
        before="b" * 40,
        legacy_failures=[],
    )

    assert payload["blocking_failures"] == [node]
    assert payload["blocking_failures_by_class"] == {
        "upstream_red_admission_failure": 1
    }
    assert payload["unknown_blocking_classifications"] == [
        "upstream_red_admission_failure"
    ]


def test_unknown_classification_is_blocking_and_explicitly_reported():
    unknown = {
        "path": "tests/test_future.py",
        "nodeid": "tests/test_future.py::test_future_failure",
        "classification": "future_failure_class",
    }

    payload = upstream_sync_gate.build_gate_failures_payload(
        classification={
            "common_path": [],
            "post_only_path": [unknown],
            "pre_existing": [],
            "unknown": [],
        },
        merge_sha="a" * 40,
        before="b" * 40,
        legacy_failures=[],
    )

    assert payload["blocking_failures"] == [unknown]
    assert payload["blocking_failures_by_class"] == {"future_failure_class": 1}
    assert payload["unknown_blocking_classifications"] == ["future_failure_class"]


def test_payload_records_same_file_failure_rename_with_both_traces():
    old = "tests/test_pricing.py::test_old_pricing"
    new = "tests/test_pricing.py::test_new_pricing"
    unrelated = {
        "tests/test_windows.py::test_a",
        "tests/test_windows.py::test_b",
        "tests/test_windows.py::test_c",
    }
    classification = {
        "common_path": [{
            "path": "tests/test_pricing.py",
            "nodeid": new,
            "classification": "merge_resolution_or_local_introduced",
        }],
        "post_only_path": [],
        "pre_existing": [],
        "unknown": [],
    }
    payload = upstream_sync_gate.build_gate_failures_payload(
        classification=classification,
        merge_sha="a" * 40,
        before="b" * 40,
        legacy_failures=[new],
        baseline=_node_run(collected={old}, failed={old}),
        merged=_node_run(collected={new, *unrelated}, failed={new, *unrelated}),
        baseline_log=(
            "_______ test_old_pricing _______\n"
            "E   old assertion trace\n"
            f"FAILED {old} - AssertionError: old\n"
        ),
        merged_log=(
            "_______ test_new_pricing _______\n"
            "E   new assertion trace\n"
            f"FAILED {new} - AssertionError: new\n"
        ),
    )

    assert payload["suspected_rename"] == [{
        "path": "tests/test_pricing.py",
        "disappeared": {
            "nodeid": old,
            "trace": (
                "_______ test_old_pricing _______\n"
                "E   old assertion trace\n"
                f"FAILED {old} - AssertionError: old"
            ),
            "trace_source": "baseline",
        },
        "appeared": {
            "nodeid": new,
            "trace": (
                "_______ test_new_pricing _______\n"
                "E   new assertion trace\n"
                f"FAILED {new} - AssertionError: new"
            ),
            "trace_source": "merged",
        },
    }]
    assert payload["common_path"] == classification["common_path"]


def test_collected_green_old_node_does_not_create_rename_hint():
    old = "tests/test_pricing.py::test_old_pricing"
    new = "tests/test_pricing.py::test_new_pricing"
    payload = upstream_sync_gate.build_gate_failures_payload(
        classification={"common_path": [], "post_only_path": [], "pre_existing": [], "unknown": []},
        merge_sha="a" * 40,
        before="b" * 40,
        legacy_failures=[new],
        baseline=_node_run(collected={old}, failed={old}),
        merged=_node_run(collected={old, new}, failed={new}),
        baseline_log=f"FAILED {old} - AssertionError: old\n",
        merged_log=f"FAILED {new} - AssertionError: new\n",
    )

    assert payload["suspected_rename"] == []


def test_suspected_rename_requires_exactly_one_candidate_per_file():
    def hint(old_nodes: set[str], new_nodes: set[str]) -> list[dict]:
        return upstream_sync_gate.build_suspected_renames(
            baseline=_node_run(collected=old_nodes, failed=old_nodes),
            merged=_node_run(collected=new_nodes, failed=new_nodes),
            baseline_log="",
            merged_log="",
        )

    assert hint(
        {
            "tests/test_same.py::test_old_a",
            "tests/test_same.py::test_old_b",
        },
        {
            "tests/test_same.py::test_new_a",
            "tests/test_same.py::test_new_b",
        },
    ) == []
    assert hint(
        {"tests/test_same.py::test_old"},
        {
            "tests/test_same.py::test_new_a",
            "tests/test_same.py::test_new_b",
        },
    ) == []


@pytest.mark.parametrize(
    ("bad_side", "bad_flag"),
    [("baseline", "collect_ok"), ("merged", "probe_ok")],
)
def test_suspected_rename_requires_trustworthy_runs(bad_side, bad_flag):
    old = "tests/test_same.py::test_old"
    new = "tests/test_same.py::test_new"
    baseline = _node_run(collected={old}, failed={old})
    merged = _node_run(collected={new}, failed={new})
    (baseline if bad_side == "baseline" else merged)[bad_flag] = False

    assert upstream_sync_gate.build_suspected_renames(
        baseline=baseline,
        merged=merged,
        baseline_log="",
        merged_log="",
    ) == []


def test_persist_cli_includes_suspected_rename_inputs(tmp_path):
    old = "tests/test_pricing.py::test_old_pricing"
    new = "tests/test_pricing.py::test_new_pricing"
    classification = tmp_path / "classification.json"
    classification.write_text(json.dumps({
        "common_path": [{
            "path": "tests/test_pricing.py",
            "nodeid": new,
            "classification": "merge_resolution_or_local_introduced",
        }],
        "post_only_path": [],
        "pre_existing": [],
        "unknown": [],
    }))
    baseline = tmp_path / "baseline.nodes.json"
    baseline.write_text(json.dumps({
        "collect_ok": True,
        "probe_ok": True,
        "collected_nodeids": [old],
        "failed_nodeids": [old],
    }))
    merged = tmp_path / "merged.nodes.json"
    merged.write_text(json.dumps({
        "collect_ok": True,
        "probe_ok": True,
        "collected_nodeids": [new],
        "failed_nodeids": [new],
    }))
    baseline_log = tmp_path / "baseline.log"
    baseline_log.write_text(f"_______ test_old_pricing _______\nFAILED {old} - old\n")
    merged_log = tmp_path / "merged.log"
    merged_log.write_text(f"_______ test_new_pricing _______\nFAILED {new} - new\n")
    legacy = tmp_path / "legacy.txt"
    legacy.write_text(f"{new}\n")
    output = tmp_path / "gate-failures.json"

    result = _cli(
        "persist-gate-failures",
        "--classification", str(classification),
        "--merge-sha", "a" * 40,
        "--before", "b" * 40,
        "--legacy-failures", str(legacy),
        "--baseline-nodes", str(baseline),
        "--merged-nodes", str(merged),
        "--baseline-log", str(baseline_log),
        "--merged-log", str(merged_log),
        "--output", str(output),
    )

    assert result.returncode == 0, result.stderr
    assert len(json.loads(output.read_text())["suspected_rename"]) == 1


def test_failure_that_remains_in_merged_isolation_is_not_order_dependent():
    nodeid = "tests/test_common.py::test_still_fails"
    result = upstream_sync_gate.classify_node_failures(
        baseline=_node_run(collected=set(), failed=set()),
        upstream_parent=_node_run(collected={nodeid}, failed=set()),
        merged=_node_run(collected={nodeid}, failed={nodeid}),
        merged_isolated=_node_run(collected={nodeid}, failed={nodeid}),
        manifest=_manifest(("tests/test_common.py", True, True)),
    )

    assert result["common_path"] == [
        {
            "path": "tests/test_common.py",
            "nodeid": nodeid,
            "classification": "fork_compatibility_failure",
        }
    ]


@pytest.mark.parametrize(
    ("merged_failed", "merged_isolated_failed", "expected_classification"),
    [
        (False, False, None),
        (False, True, None),
        (True, False, "order_dependent_failure"),
        (True, True, "fork_compatibility_failure"),
    ],
    ids=[
        "merged-pass-isolated-pass",
        "merged-pass-isolated-fail",
        "merged-fail-isolated-pass",
        "merged-fail-isolated-fail",
    ],
)
def test_classification_covers_merged_and_isolated_failure_matrix(
    merged_failed, merged_isolated_failed, expected_classification
):
    nodeid = "tests/test_common.py::test_matrix"
    result = upstream_sync_gate.classify_node_failures(
        baseline=_node_run(collected=set(), failed=set()),
        upstream_parent=_node_run(collected={nodeid}, failed=set()),
        merged=_node_run(
            collected={nodeid}, failed={nodeid} if merged_failed else set()
        ),
        merged_isolated=_node_run(
            collected={nodeid},
            failed={nodeid} if merged_isolated_failed else set(),
        ),
        manifest=_manifest(("tests/test_common.py", True, True)),
    )

    entries = result["common_path"]
    if expected_classification is None:
        assert entries == []
    else:
        assert entries[0]["classification"] == expected_classification


def test_unreadable_merged_run_with_no_failures_is_not_clean():
    result = upstream_sync_gate.classify_node_failures(
        baseline=_node_run(
            collected={"tests/test_common.py::test_existing"}, failed=set()
        ),
        upstream_parent=_node_run(
            collected={"tests/test_common.py::test_existing"}, failed=set()
        ),
        merged=_node_run(
            collected=set(), failed=set(), collect_ok=False
        ),
        merged_isolated=_node_run(collected=set(), failed=set()),
        manifest=_manifest(("tests/test_common.py", True, True)),
    )

    assert result["common_path"] == []
    assert result["post_only_path"] == []
    assert result["pre_existing"] == []
    assert result["unknown"] == []
    assert result["unreadable_runs"] == [{"source": "merged", "stage": "collect"}]


def test_classifier_leaves_blocking_aggregate_to_persistence():
    result = upstream_sync_gate.classify_node_failures(
        baseline=_node_run(collected=set(), failed=set()),
        upstream_parent=_node_run(
            collected={"tests/test_post_only.py::test_new"}, failed=set()
        ),
        merged=_node_run(
            collected={"tests/test_post_only.py::test_new"},
            failed={"tests/test_post_only.py::test_new"},
        ),
        merged_isolated=_node_run(
            collected={"tests/test_post_only.py::test_new"}, failed=set()
        ),
        manifest=_manifest(("tests/test_post_only.py", False, True)),
    )

    assert result["post_only_path"]
    assert "blocking_failures" not in result


def test_node_probe_scope_selects_exact_newly_seen_failing_nodeids():
    selector = getattr(upstream_sync_gate, "build_upstream_probe_request", None)
    assert callable(selector), "node probe request builder is not implemented"
    post_only_node = (
        "tests/hermes_cli/test_linux_desktop_entry.py::"
        "test_exec_prefixes_interpreter_for_env_shebang_python_script"
    )
    common_node = "tests/tests.py::test_common_new_failure"
    baseline = _node_run(
        collected={"tests/tests.py::test_existing"},
        failed=set(),
    )
    merged = _node_run(
        collected={
            "tests/tests.py::test_existing",
            common_node,
            post_only_node,
        },
        failed={common_node, post_only_node},
    )
    request = selector(
        baseline=baseline,
        merged=merged,
        manifest=_manifest(
            ("tests/hermes_cli/test_linux_desktop_entry.py", False, True),
            ("tests/tests.py", True, True),
        ),
    )
    assert request == {
        "nodeids": [post_only_node, common_node],
        "paths": [
            "tests/hermes_cli/test_linux_desktop_entry.py",
            "tests/tests.py",
        ],
    }


def test_node_probe_scope_excludes_paths_absent_from_upstream_boundary():
    selector = getattr(upstream_sync_gate, "build_upstream_probe_request", None)
    assert callable(selector), "node probe request builder is not implemented"
    absent_path_node = "tests/local_only.py::test_added_by_merge"
    common_node = "tests/tests.py::test_common_new_failure"
    request = selector(
        baseline=_node_run(collected=set(), failed=set()),
        merged=_node_run(
            collected={absent_path_node, common_node},
            failed={absent_path_node, common_node},
        ),
        manifest=_manifest(
            ("tests/local_only.py", False, True),
            ("tests/tests.py", True, True),
        ),
        available_paths={"tests/tests.py"},
    )
    assert request == {
        "nodeids": [common_node],
        "paths": ["tests/tests.py"],
    }


def test_probe_request_can_be_restricted_to_nodes_collected_upstream():
    filter_request = getattr(upstream_sync_gate, "filter_probe_request", None)
    assert callable(filter_request), "probe request node filter is not implemented"
    absent_node = "tests/tests.py::test_removed_upstream"
    present_node = "tests/tests.py::test_existing_upstream"
    assert filter_request(
        {
            "nodeids": [absent_node, present_node],
            "paths": ["tests/tests.py"],
        },
        {present_node},
    ) == {
        "nodeids": [present_node],
        "paths": ["tests/tests.py"],
    }


def test_same_failures_before_and_after_mean_no_regression():
    log = _log(["tests/a.py::test_one", "tests/b.py::test_two"])
    assert new_failures(log, log) == []


def test_a_failure_that_appeared_after_the_merge_is_reported():
    before = _log(["tests/a.py::test_one"])
    after = _log(["tests/a.py::test_one", "tests/b.py::test_two"])
    assert new_failures(before, after) == ["tests/b.py::test_two"]


def test_a_failure_that_disappeared_is_not_reported():
    before = _log(["tests/a.py::test_one", "tests/b.py::test_two"])
    after = _log(["tests/a.py::test_one"])
    assert new_failures(before, after) == []


def test_trailing_detail_after_the_dash_is_stripped_from_the_id():
    before = _log([], summary="0 failed, 3 passed in 1.00s")
    after = (
        "FAILED tests/b.py::test_two - TypeError: 'NoneType' object\n"
        "2 passed, 1 failed in 1.00s\n"
    )
    assert new_failures(before, after) == ["tests/b.py::test_two"]


def test_outcomes_parser_preserves_plural_collection_errors():
    parser = getattr(upstream_sync_gate, "parse_test_outcomes", None)
    assert callable(parser), "outcomes parser is not implemented"

    outcome = parser(
        "ERROR tests/broken.py - RuntimeError: boom\n"
        "2 errors in 0.05s\n"
    )

    assert outcome == {
        "collected_nodeids": [],
        "failed_nodeids": [],
        "error_count": 2,
        "collection_error_paths": ["tests/broken.py"],
    }


def test_outcomes_parser_collects_passed_nodes_for_classification():
    parser = getattr(upstream_sync_gate, "parse_test_outcomes", None)
    assert callable(parser), "outcomes parser is not implemented"

    outcome = parser(
        "PASSED tests/fork.py::test_regression\n"
        "PASSED tests/fork.py::test_untouched\n"
        "FAILED tests/fork.py::test_regression_after_merge - AssertionError\n"
        "2 passed, 1 failed in 0.05s\n"
    )

    assert len(outcome["collected_nodeids"]) == 3
    assert len(outcome["failed_nodeids"]) == 1
    assert set(outcome["failed_nodeids"]).issubset(outcome["collected_nodeids"])


def test_outcomes_parser_preserves_parameterized_dashes():
    parser = getattr(upstream_sync_gate, "parse_test_outcomes", None)
    assert callable(parser), "outcomes parser is not implemented"
    old = "tests/test_dash.py::test_case[alpha - old]"
    new = "tests/test_dash.py::test_case[alpha - new]"

    outcome = parser(
        f"FAILED {old} - RuntimeError: setup boom\n"
        f"FAILED {new} - RuntimeError: setup boom\n"
        "2 failed in 0.05s\n"
    )

    assert outcome["failed_nodeids"] == [new, old]


def test_aggregate_outcomes_parser_preserves_framed_parameterized_dashes():
    parser = getattr(upstream_sync_gate, "parse_test_outcomes", None)
    assert callable(parser), "outcomes parser is not implemented"
    old = "tests/test_dash.py::test_case[alpha - old]"
    new = "tests/test_dash.py::test_case[alpha - new]"

    outcome = parser(
        "--- tests/test_dash.py ---\n"
        f"║ FAILED {old} - RuntimeError: setup boom\n"
        f"║ FAILED {new} - RuntimeError: setup boom\n"
        "2 failed in 0.05s\n"
        "=== Summary: 1 files, 0 tests passed, 2 failed "
        "(100% complete) in 0.1s (1 workers) ===\n",
        aggregate=True,
    )

    assert outcome["failed_nodeids"] == [new, old]


def test_aggregate_parser_accepts_real_runner_failure_block():
    parser = getattr(upstream_sync_gate, "parse_test_outcomes", None)
    assert parser is not None
    nodeid = "tests/test_dash.py::test_case[alpha - new]"
    log = (
        "=== Failure output ===\n"
        "--- tests/test_dash.py ---\n"
        f"FAILED {nodeid}\n"
        "============================== 1 failed in 0.1s ==============================\n"
        "=== Summary: 1 files, 0 tests passed, 1 failed (100% complete) in 0.1s (1 workers) ===\n"
    )

    outcome = parser(log, aggregate=True)

    assert outcome["failed_nodeids"] == [nodeid]


@pytest.mark.parametrize(
    "line",
    [
        "FAILED tests/test.py - setup boom",
        "FAILED tests/test.txt::test_case - setup boom",
        "FAILED tests/test.py:drive::test_case - setup boom",
        "SKIPPED tests/test.py::test_case - skipped",
    ],
)
def test_gate_status_filters_stay_at_the_call_site(line):
    assert upstream_sync_gate._nodeid_from_status_line(line) is None


def test_gate_still_accepts_rerun_status():
    assert upstream_sync_gate._nodeid_from_status_line(
        "RERUN tests/test.py::test_case - transient"
    ) == "tests/test.py::test_case"


def test_passed_baseline_node_is_classified_as_fork_regression(tmp_path):
    nodeid = "tests/test_scheduler.py::test_delivery_targets"
    pre_existing_nodeid = "tests/test_scheduler.py::test_already_broken"
    baseline_log = tmp_path / "baseline.log"
    merged_log = tmp_path / "merged.log"
    baseline_log.write_text(
        f"PASSED {nodeid}\n"
        f"FAILED {pre_existing_nodeid} - AssertionError: old\n"
        "1 passed, 1 failed in 0.01s\n"
    )
    merged_log.write_text(
        f"FAILED {nodeid} - AssertionError: boom\n"
        f"FAILED {pre_existing_nodeid} - AssertionError: old\n"
        "2 failed in 0.01s\n"
    )

    baseline_result = _cli("node-outcome", "--log", str(baseline_log))
    merged_result = _cli("node-outcome", "--log", str(merged_log))
    assert baseline_result.returncode == 0, baseline_result.stderr
    assert merged_result.returncode == 0, merged_result.stderr

    classification = upstream_sync_gate.classify_node_failures(
        baseline=json.loads(baseline_result.stdout),
        upstream_parent=_node_run(collected=set(), failed=set()),
        merged=json.loads(merged_result.stdout),
        merged_isolated=_node_run(
            collected=json.loads(merged_result.stdout)["collected_nodeids"],
            failed=json.loads(merged_result.stdout)["failed_nodeids"],
        ),
        manifest=_manifest(("tests/test_scheduler.py", True, True)),
    )

    assert classification["common_path"] == [
        {
            "path": "tests/test_scheduler.py",
            "nodeid": nodeid,
            "classification": "fork_regression",
        }
    ]
    assert classification["pre_existing"] == [
        {
            "path": "tests/test_scheduler.py",
            "nodeid": pre_existing_nodeid,
            "classification": "pre_existing_failure",
        }
    ]


def test_fixture_error_is_a_failed_collected_node_not_a_collection_error(tmp_path):
    suite = tmp_path / "test_fixture_error.py"
    suite.write_text(
        "import pytest\n"
        "\n"
        "@pytest.fixture\n"
        "def broken_fixture():\n"
        "    raise RuntimeError('fixture boom')\n"
        "\n"
        "def test_uses_broken_fixture(broken_fixture):\n"
        "    pass\n"
        "\n"
        "def test_passed():\n"
        "    assert True\n"
    )
    log = tmp_path / "fixture-error.log"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(suite),
            "-q",
            "-p",
            "no:cacheprovider",
            "-rA",
            "--continue-on-collection-errors",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    log.write_text(proc.stdout + proc.stderr, encoding="utf-8")
    assert proc.returncode != 0

    nodeid = "test_fixture_error.py::test_uses_broken_fixture"
    outcome = upstream_sync_gate.parse_test_outcomes(log.read_text(encoding="utf-8"))
    assert nodeid in outcome["collected_nodeids"]
    assert outcome["failed_nodeids"] == [nodeid]
    assert outcome["collection_error_paths"] == []

    result = _cli("node-outcome", "--log", str(log))
    assert result.returncode == 0, result.stderr
    node_outcome = json.loads(result.stdout)
    assert node_outcome["collect_ok"] is True
    assert node_outcome["probe_ok"] is True
    assert node_outcome["collected_nodeids"] == sorted(
        ["test_fixture_error.py::test_passed", nodeid]
    )
    assert node_outcome["failed_nodeids"] == [nodeid]


def test_outcomes_parser_reads_real_pytest_collection_summary(tmp_path):
    """The parser consumes pytest's real short summary, not its banner."""
    broken = tmp_path / "test_broken.py"
    broken.write_text('raise RuntimeError("boom")\n')

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(broken),
            "-q",
            "-p",
            "no:cacheprovider",
            "-rA",
            "--continue-on-collection-errors",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode != 0
    outcome = upstream_sync_gate.parse_test_outcomes(proc.stdout + proc.stderr)
    assert outcome["error_count"] == 1
    assert outcome["collection_error_paths"] == ["test_broken.py"]


def test_outcomes_parser_reads_real_passed_and_failed_nodeids(tmp_path):
    suite = tmp_path / "test_real_outcomes.py"
    suite.write_text(
        "def test_passed():\n"
        "    assert True\n"
        "\n"
        "def test_failed():\n"
        "    assert False\n"
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(suite),
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

    assert proc.returncode != 0
    outcome = upstream_sync_gate.parse_test_outcomes(proc.stdout + proc.stderr)
    assert outcome["collected_nodeids"] == [
        "test_real_outcomes.py::test_failed",
        "test_real_outcomes.py::test_passed",
    ]
    assert outcome["failed_nodeids"] == ["test_real_outcomes.py::test_failed"]


@pytest.mark.parametrize(
    ("summary", "error_count"),
    [
        ("2 passed, 1 error in 0.12s\n", 1),
        ("76 failed, 6259 passed, 2 skipped, 6 warnings in 679.63s\n", 0),
        ("164 failed, 7154 passed, 5 skipped, 9 warnings in 815.76s (0:13:35)\n", 0),
    ],
)
def test_outcomes_parser_accepts_known_pytest_summary_forms(summary, error_count):
    outcome = upstream_sync_gate.parse_test_outcomes(summary)
    assert outcome["error_count"] == error_count


def test_outcomes_parser_sums_explicit_aggregate_file_summaries():
    parser = getattr(upstream_sync_gate, "parse_test_outcomes", None)
    aggregate = (
        "--- tests/a.py ---\n"
        "FAILED tests/a.py::test_a - AssertionError\n"
        "2 failed, 3 passed in 1.00s\n"
        "--- tests/b.py ---\n"
        "no tests ran in 0.01s\n"
        "--- tests/c.py ---\n"
        "FAILED tests/c.py::test_c - AssertionError\n"
        "1 failed, 1 passed in 0.20s\n"
        "=== Summary: 3 files, 4 tests passed, 3 failed ===\n"
    )

    outcome = parser(aggregate, aggregate=True)

    assert outcome["summary"] == {"failed": 3, "passed": 4}


def test_aggregate_parser_rejects_a_file_without_a_final_summary():
    aggregate = (
        "--- tests/a.py ---\n"
        "1 passed in 0.10s\n"
        "--- tests/b.py ---\n"
        "FAILED tests/b.py::test_b - AssertionError\n"
    )

    with pytest.raises(ValueError, match="tests/b.py"):
        upstream_sync_gate.parse_test_outcomes(aggregate, aggregate=True)


def test_aggregate_parser_accepts_the_real_runner_overall_summary():
    outcome = upstream_sync_gate.parse_test_outcomes(
        "Running 2 test files (~10 tests) with -j 1\n"
        "=== Summary: 2 files, 8 tests passed, 1 failed, 1 skipped "
        "(100% complete) in 1.0s (1 workers) ===\n",
        aggregate=True,
    )

    assert outcome["summary"] == {"failed": 1, "passed": 8, "skipped": 1}


def test_aggregate_parser_rejects_a_whole_run_with_no_tests(tmp_path):
    log = tmp_path / "aggregate-empty.log"
    log.write_text(
        "Running 2 test files (~0 tests) with -j 1\n"
        "=== Summary: 2 files, 0 tests passed, 0 failed "
        "(100% complete) in 0.1s (1 workers) ===\n"
    )

    with pytest.raises(ValueError, match="no tests ran"):
        upstream_sync_gate.parse_test_outcomes(
            log.read_text(encoding="utf-8"), aggregate=True
        )


def test_aggregate_parser_rejects_nonzero_runner_exit_without_node_failure():
    log = (
        "--- tests/test_hook.py ---\n"
        "PASSED tests/test_hook.py::test_passes\n"
        "1 passed in 0.10s\n"
        "=== 1 file where all tests passed but pytest exited non-zero "
        "(warnings-as-errors, hook failures, etc.) ===\n"
        "=== Summary: 1 files, 1 tests passed, 0 failed "
        "(100% complete) in 0.1s (1 workers) ===\n"
    )

    with pytest.raises(ValueError, match="non-zero"):
        upstream_sync_gate.parse_test_outcomes(log, aggregate=True)


def test_aggregate_parser_ignores_human_dash_sections_inside_failure_output():
    log = (
        "=== Failure output ===\n"
        "--- tests/hermes_cli/test_debug.py ---\n"
        "FAILED tests/hermes_cli/test_debug.py::test_debug - AssertionError\n"
        "--- hermes dump ---\n"
        "captured stdout\n"
        "1 failed in 0.10s\n"
        "=== Summary: 1 files, 0 tests passed, 1 failed "
        "(100% complete) in 0.1s (1 workers) ===\n"
    )

    outcome = upstream_sync_gate.parse_test_outcomes(log, aggregate=True)

    assert outcome["failed_nodeids"] == [
        "tests/hermes_cli/test_debug.py::test_debug"
    ]


def _run_real_aggregate_runner(
    tmp_path: Path,
    files: dict[str, str],
    *,
    node_report: Path | None = None,
    selected_files: list[str] | None = None,
    pytest_args: tuple[str, ...] = (),
    pytest_config: str | None = None,
) -> subprocess.CompletedProcess[str]:
    repo = tmp_path / "runner-worktree"
    tests = repo / "tests"
    tests.mkdir(parents=True)
    for relative, source in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    if pytest_config is not None:
        (repo / "pyproject.toml").write_text(pytest_config, encoding="utf-8")
    selected = ":".join(selected_files or files)
    runner = Path(__file__).resolve().parents[2] / "scripts" / "run_tests_parallel.py"
    command = [
            sys.executable,
            str(runner),
            "--repo-root",
            str(repo),
            "--files",
            selected,
            "--no-duration-cache",
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
    ]
    command.extend(pytest_args)
    if node_report is not None:
        command.extend(["--node-report", str(node_report)])
    return subprocess.run(
        command,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )


_TEARDOWN_ERROR_FILE = (
    "import pytest\n\n"
    "@pytest.fixture\n"
    "def failing_teardown():\n"
    "    yield\n"
    "    raise RuntimeError('teardown boom')\n\n"
    "def test_passes():\n"
    "    pass\n\n"
    "def test_errors_in_teardown(failing_teardown):\n"
    "    pass\n"
)
_ERROR_FILE = (
    "import pytest\n\n"
    "@pytest.fixture(autouse=True)\n"
    "def fail_before_test():\n"
    "    raise RuntimeError('fixture boom')\n\n"
    "def test_reaches_fixture():\n"
    "    pass\n"
)
_PARAMETERIZED_SETUP_ERROR_FILE = (
    "import pytest\n\n"
    "@pytest.fixture\n"
    "def broken_setup():\n"
    "    raise RuntimeError('setup boom')\n\n"
    "@pytest.mark.parametrize('label', ['alpha - old', 'alpha - new'])\n"
    "def test_case(label, broken_setup):\n"
    "    pass\n"
)
_INFRA_CONFTST = (
    "def pytest_sessionfinish(session, exitstatus):\n"
    "    if exitstatus == 0:\n"
    "        session.exitstatus = 3\n"
)


def _matrix_case(
    name: str,
    files: dict[str, str],
    *,
    selected_files: list[str] | None = None,
    pytest_args: tuple[str, ...] = (),
    pytest_config: str | None = None,
) -> tuple[str, dict[str, str], list[str] | None, tuple[str, ...], str | None]:
    return name, files, selected_files, pytest_args, pytest_config


_READABILITY_MATRIX = [
    _matrix_case(
        "green",
        {"tests/test_green.py": "def test_green():\n    assert True\n"},
    ),
    _matrix_case(
        "ordinary-red",
        {"tests/test_red.py": "def test_red():\n    assert False\n"},
    ),
    _matrix_case(
        "nonzero-without-node-evidence",
        {"tests/conftest.py": _INFRA_CONFTST, "tests/test_pass.py": "def test_pass():\n    pass\n"},
        selected_files=["tests/test_pass.py"],
    ),
    _matrix_case("teardown-error", {"tests/test_teardown.py": _TEARDOWN_ERROR_FILE}),
    _matrix_case(
        "empty-beside-measured",
        {
            "tests/test_empty.py": "# deliberately empty\n",
            "tests/test_pass.py": "def test_pass():\n    pass\n",
        },
    ),
    _matrix_case(
        "deselected-beside-measured",
        {
            "tests/test_integration.py": (
                "import pytest\n\n"
                "pytestmark = pytest.mark.integration\n\n"
                "def test_integration():\n    pass\n"
            ),
            "tests/test_regular.py": "def test_regular():\n    pass\n",
        },
        selected_files=["tests/test_integration.py", "tests/test_regular.py"],
        pytest_config="[tool.pytest.ini_options]\naddopts = \"-m 'not integration'\"\n",
    ),
    _matrix_case(
        "all-skipped",
        {
            "tests/test_skipped.py": (
                "import pytest\n\n"
                "@pytest.mark.skip(reason='not for this run')\n"
                "def test_skipped_a():\n    pass\n\n"
                "@pytest.mark.skip(reason='not for this run')\n"
                "def test_skipped_b():\n    pass\n"
            )
        },
    ),
    _matrix_case(
        "xfail-and-xpass",
        {
            "tests/test_expected.py": (
                "import pytest\n\n"
                "@pytest.mark.xfail(reason='expected')\n"
                "def test_expected_failure():\n    assert False\n\n"
                "@pytest.mark.xfail(reason='unexpected', strict=False)\n"
                "def test_unexpected_pass():\n    assert True\n"
            )
        },
    ),
    _matrix_case(
        "errors-in-two-files",
        {"tests/test_error_a.py": _ERROR_FILE, "tests/test_error_b.py": _ERROR_FILE},
    ),
    _matrix_case(
        "error-nodeid-with-space",
        {
            "tests/test_param.py": (
                "import pytest\n\n"
                "@pytest.fixture\n"
                "def broken_fixture():\n"
                "    raise RuntimeError('fixture boom')\n\n"
                "@pytest.mark.parametrize('value', ['foo bar'])\n"
                "def test_case(value, broken_fixture):\n"
                "    pass\n"
            )
        },
    ),
    _matrix_case(
        "parameterized-setup-failure",
        {"tests/test_parameterized_setup.py": _PARAMETERIZED_SETUP_ERROR_FILE},
    ),
    _matrix_case(
        "mixed-infrastructure-and-test-failure",
        {
            "tests/infra/conftest.py": _INFRA_CONFTST,
            "tests/infra/test_infra.py": "def test_infra_passes():\n    pass\n",
            "tests/real/test_real.py": "def test_real_failure():\n    assert False\n",
        },
        selected_files=["tests/infra/test_infra.py", "tests/real/test_real.py"],
    ),
    _matrix_case(
        "skipped-only-with-infrastructure",
        {
            "tests/infra/conftest.py": _INFRA_CONFTST,
            "tests/infra/test_skipped.py": (
                "import pytest\n\n"
                "@pytest.mark.skip(reason='not for this run')\n"
                "def test_skipped_a():\n    pass\n\n"
                "@pytest.mark.skip(reason='not for this run')\n"
                "def test_skipped_b():\n    pass\n"
            ),
        },
        selected_files=["tests/infra/test_skipped.py"],
    ),
    _matrix_case(
        "xfail-only-with-infrastructure",
        {
            "tests/infra/conftest.py": _INFRA_CONFTST,
            "tests/infra/test_xfail.py": (
                "import pytest\n\n"
                "@pytest.mark.xfail(reason='expected')\n"
                "def test_expected_failure():\n    assert False\n"
            ),
        },
        selected_files=["tests/infra/test_xfail.py"],
    ),
    _matrix_case(
        "all-deselected",
        {
            "tests/test_deselected.py": "def test_one():\n    pass\n\ndef test_two():\n    pass\n"
        },
        pytest_args=("-k", "no_such_test_name_xyz"),
    ),
    _matrix_case(
        "all-empty",
        {
            "tests/test_empty_a.py": "# intentionally empty\n",
            "tests/test_empty_b.py": "# intentionally empty\n",
        },
    ),
    _matrix_case(
        "worker-crash",
        {
            "tests/test_survivor.py": "def test_survivor():\n    pass\n",
            "tests/test_crashed.py": "import os\nos._exit(137)\n",
        },
    ),
]


@pytest.mark.parametrize(
    "name, files, selected_files, pytest_args, pytest_config",
    _READABILITY_MATRIX,
    ids=[case[0] for case in _READABILITY_MATRIX],
)
def test_real_runner_log_and_node_report_readability_matrix(
    tmp_path, name, files, selected_files, pytest_args, pytest_config
):
    """Every runner outcome category must have one verdict through both doors."""
    assert len(_READABILITY_MATRIX) == 17
    report = tmp_path / f"{name}-nodes.json"
    runner = _run_real_aggregate_runner(
        tmp_path,
        files,
        node_report=report,
        selected_files=selected_files,
        pytest_args=pytest_args,
        pytest_config=pytest_config,
    )
    log = tmp_path / f"{name}.log"
    log.write_text(runner.stdout, encoding="utf-8")
    log_result = _cli("node-outcome", "--log", str(log), "--aggregate")
    report_result = _cli("node-outcome", "--node-report", str(report))
    report_payload = json.loads(report.read_text(encoding="utf-8"))
    expected_counts = {
        "all-skipped": 2,
        "all-deselected": 0,
        "all-empty": 0,
        "empty-beside-measured": 1,
    }
    if name in expected_counts:
        assert report_payload["tests_collected"] == expected_counts[name]

    assert (log_result.returncode == 0) == (report_result.returncode == 0), (
        name,
        log_result.stderr,
        report_result.stderr,
        runner.stdout,
    )
    if name == "parameterized-setup-failure":
        log_outcome = json.loads(log_result.stdout)
        report_outcome = json.loads(report_result.stdout)
        assert log_outcome["failed_nodeids"] == report_outcome["failed_nodeids"]
        assert len(log_outcome["failed_nodeids"]) == 2


def test_deselected_file_beside_measured_file_is_readable_through_both_doors(
    tmp_path,
):
    report = tmp_path / "deselected-mixed-nodes.json"
    runner = _run_real_aggregate_runner(
        tmp_path,
        {
            "tests/test_integration.py": (
                "import pytest\n\n"
                "pytestmark = pytest.mark.integration\n\n"
                "def test_integration():\n    pass\n"
            ),
            "tests/test_regular.py": "def test_regular():\n    pass\n",
        },
        node_report=report,
        selected_files=["tests/test_integration.py", "tests/test_regular.py"],
        pytest_config="[tool.pytest.ini_options]\naddopts = \"-m 'not integration'\"\n",
    )
    assert runner.returncode == 0, runner.stdout + runner.stderr
    log = tmp_path / "deselected-mixed.log"
    log.write_text(runner.stdout, encoding="utf-8")

    log_result = _cli("node-outcome", "--log", str(log), "--aggregate")
    report_result = _cli("node-outcome", "--node-report", str(report))

    assert log_result.returncode == 0, log_result.stderr
    assert report_result.returncode == 0, report_result.stderr


def test_real_runner_error_nodeid_with_space_is_named_by_log_door(tmp_path):
    runner = _run_real_aggregate_runner(
        tmp_path,
        {
            "tests/test_param.py": (
                "import pytest\n\n"
                "@pytest.fixture\n"
                "def broken_fixture():\n"
                "    raise RuntimeError('fixture boom')\n\n"
                "@pytest.mark.parametrize('value', ['foo bar'])\n"
                "def test_case(value, broken_fixture):\n"
                "    pass\n"
            )
        },
    )
    assert runner.returncode != 0, runner.stdout + runner.stderr
    log = tmp_path / "param-space.log"
    log.write_text(runner.stdout, encoding="utf-8")

    result = _cli("node-outcome", "--log", str(log), "--aggregate")

    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert "tests/test_param.py::test_case[foo bar]" in outcome["failed_nodeids"]


def test_node_outcome_accepts_real_runner_green_aggregate_output(tmp_path):
    runner = _run_real_aggregate_runner(
        tmp_path,
        {"tests/test_green.py": "def test_green():\n    assert True\n"},
    )
    assert runner.returncode == 0, runner.stdout + runner.stderr
    assert "=== Summary: 1 files, 1 tests passed, 0 failed" in runner.stdout
    log = tmp_path / "green.log"
    log.write_text(runner.stdout, encoding="utf-8")

    result = _cli("node-outcome", "--log", str(log), "--aggregate")

    assert result.returncode == 0, result.stderr


def test_node_outcome_rejects_real_runner_nonzero_without_node_failure(tmp_path):
    runner = _run_real_aggregate_runner(
        tmp_path,
        {
            "tests/conftest.py": (
                "def pytest_sessionfinish(session, exitstatus):\n"
                "    if exitstatus == 0:\n"
                "        session.exitstatus = 3\n"
            ),
            "tests/test_hook.py": "def test_passes():\n    assert True\n",
        },
    )
    assert runner.returncode != 0, runner.stdout + runner.stderr
    log = tmp_path / "nonzero.log"
    log.write_text(runner.stdout, encoding="utf-8")

    result = _cli("node-outcome", "--log", str(log), "--aggregate")

    assert result.returncode == 2
    assert "non-zero exit without test failures" in result.stderr


def test_node_outcome_rejects_real_runner_with_no_tests(tmp_path):
    runner = _run_real_aggregate_runner(
        tmp_path,
        {
            "tests/test_empty_a.py": "# intentionally empty\n",
            "tests/test_empty_b.py": "# intentionally empty\n",
        },
    )
    assert runner.returncode != 0, runner.stdout + runner.stderr
    assert "NO TESTS RAN" in runner.stdout
    log = tmp_path / "empty.log"
    log.write_text(runner.stdout, encoding="utf-8")

    result = _cli("node-outcome", "--log", str(log), "--aggregate")

    assert result.returncode == 2
    assert "no tests ran" in result.stderr


def test_node_outcome_rejects_real_runner_with_no_tests_through_node_report(
    tmp_path,
):
    report = tmp_path / "empty-nodes.json"
    runner = _run_real_aggregate_runner(
        tmp_path,
        {
            "tests/test_empty_a.py": "# intentionally empty\n",
            "tests/test_empty_b.py": "# intentionally empty\n",
        },
        node_report=report,
    )
    assert runner.returncode != 0, runner.stdout + runner.stderr

    log = tmp_path / "empty.log"
    log.write_text(runner.stdout, encoding="utf-8")
    log_result = _cli("node-outcome", "--log", str(log), "--aggregate")
    report_result = _cli("node-outcome", "--node-report", str(report))

    assert log_result.returncode == 2, log_result.stdout
    assert report_result.returncode == 2, report_result.stdout


def test_node_outcome_counts_errors_across_real_runner_files(tmp_path):
    error_file = (
        "import pytest\n\n"
        "@pytest.fixture(autouse=True)\n"
        "def fail_before_test():\n"
        "    raise RuntimeError('fixture boom')\n\n"
        "def test_reaches_fixture():\n"
        "    pass\n"
    )
    runner = _run_real_aggregate_runner(
        tmp_path,
        {
            "tests/test_error_a.py": error_file,
            "tests/test_error_b.py": error_file,
        },
    )
    assert runner.returncode != 0, runner.stdout + runner.stderr
    log = tmp_path / "errors.log"
    log.write_text(runner.stdout, encoding="utf-8")

    result = _cli("node-outcome", "--log", str(log), "--aggregate")

    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["error_count"] == 2


def test_node_outcome_reads_real_runner_teardown_error_as_failed_node(tmp_path):
    runner = _run_real_aggregate_runner(
        tmp_path,
        {
            "tests/test_teardown.py": (
                "import pytest\n\n"
                "@pytest.fixture\n"
                "def failing_teardown():\n"
                "    yield\n"
                "    raise RuntimeError('teardown boom')\n\n"
                "def test_passes():\n"
                "    pass\n\n"
                "def test_errors_in_teardown(failing_teardown):\n"
                "    pass\n"
            ),
        },
    )
    assert runner.returncode != 0, runner.stdout + runner.stderr

    log = tmp_path / "teardown.log"
    log.write_text(runner.stdout, encoding="utf-8")
    result = _cli("node-outcome", "--log", str(log), "--aggregate")

    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert "tests/test_teardown.py::test_errors_in_teardown" in outcome[
        "failed_nodeids"
    ]
    assert outcome["error_count"] == 1


def test_node_outcome_reads_real_runner_xfail_and_xpass(tmp_path):
    runner = _run_real_aggregate_runner(
        tmp_path,
        {
            "tests/test_expected.py": (
                "import pytest\n\n"
                "@pytest.mark.xfail(reason='expected')\n"
                "def test_expected_failure():\n"
                "    assert False\n\n"
                "@pytest.mark.xfail(reason='unexpected', strict=False)\n"
                "def test_unexpected_pass():\n"
                "    assert True\n"
            ),
        },
    )
    assert runner.returncode == 0, runner.stdout + runner.stderr
    assert "xfailed" in runner.stdout
    assert "xpassed" in runner.stdout

    log = tmp_path / "expected.log"
    log.write_text(runner.stdout, encoding="utf-8")
    result = _cli("node-outcome", "--log", str(log), "--aggregate")

    assert result.returncode == 0, result.stderr
    parsed = upstream_sync_gate.parse_test_outcomes(
        log.read_text(encoding="utf-8"), aggregate=True
    )
    assert parsed["summary"]["xfailed"] == 1
    assert parsed["summary"]["xpassed"] == 1


def test_node_outcome_reads_real_runner_all_skipped(tmp_path):
    runner = _run_real_aggregate_runner(
        tmp_path,
        {
            "tests/test_skipped.py": (
                "import pytest\n\n"
                "@pytest.mark.skip(reason='not for this run')\n"
                "def test_skipped():\n"
                "    pass\n"
            ),
        },
    )
    assert runner.returncode == 0, runner.stdout + runner.stderr
    assert "1 skipped" in runner.stdout

    log = tmp_path / "skipped.log"
    log.write_text(runner.stdout, encoding="utf-8")
    result = _cli("node-outcome", "--log", str(log), "--aggregate")

    assert result.returncode == 0, result.stderr


def test_node_outcome_skipped_only_infrastructure_is_unreadable_with_parity(
    tmp_path,
):
    report = tmp_path / "skipped-infra-nodes.json"
    runner = _run_real_aggregate_runner(
        tmp_path,
        {
            "tests/infra/conftest.py": (
                "def pytest_sessionfinish(session, exitstatus):\n"
                "    if exitstatus == 0:\n"
                "        session.exitstatus = 3\n"
            ),
            "tests/infra/test_skipped.py": (
                "import pytest\n\n"
                "@pytest.mark.skip(reason='not for this run')\n"
                "def test_skipped_a():\n"
                "    pass\n\n"
                "@pytest.mark.skip(reason='not for this run')\n"
                "def test_skipped_b():\n"
                "    pass\n"
            ),
        },
        node_report=report,
        selected_files=["tests/infra/test_skipped.py"],
    )
    assert runner.returncode != 0, runner.stdout + runner.stderr

    log = tmp_path / "skipped-infra.log"
    log.write_text(runner.stdout, encoding="utf-8")
    log_result = _cli("node-outcome", "--log", str(log), "--aggregate")
    report_result = _cli("node-outcome", "--node-report", str(report))

    assert log_result.returncode == 2, log_result.stdout
    assert report_result.returncode == 2, report_result.stdout


def test_node_outcome_xfail_only_infrastructure_is_unreadable_with_parity(tmp_path):
    report = tmp_path / "xfail-infra-nodes.json"
    runner = _run_real_aggregate_runner(
        tmp_path,
        {
            "tests/infra/conftest.py": (
                "def pytest_sessionfinish(session, exitstatus):\n"
                "    if exitstatus == 0:\n"
                "        session.exitstatus = 3\n"
            ),
            "tests/infra/test_xfail.py": (
                "import pytest\n\n"
                "@pytest.mark.xfail(reason='expected')\n"
                "def test_expected_failure():\n"
                "    assert False\n"
            ),
        },
        node_report=report,
        selected_files=["tests/infra/test_xfail.py"],
    )
    assert runner.returncode != 0, runner.stdout + runner.stderr

    log = tmp_path / "xfail-infra.log"
    log.write_text(runner.stdout, encoding="utf-8")
    log_result = _cli("node-outcome", "--log", str(log), "--aggregate")
    report_result = _cli("node-outcome", "--node-report", str(report))

    assert log_result.returncode == 2, log_result.stdout
    assert report_result.returncode == 2, report_result.stdout


def test_node_outcome_all_deselected_is_unreadable_through_both_doors(tmp_path):
    report = tmp_path / "deselected-nodes.json"
    runner = _run_real_aggregate_runner(
        tmp_path,
        {
            "tests/test_deselected.py": (
                "def test_one():\n    pass\n\n"
                "def test_two():\n    pass\n"
            ),
        },
        node_report=report,
        pytest_args=("-k", "no_such_test_name_xyz"),
    )
    assert runner.returncode != 0, runner.stdout + runner.stderr

    log = tmp_path / "deselected.log"
    log.write_text(runner.stdout, encoding="utf-8")
    log_result = _cli("node-outcome", "--log", str(log), "--aggregate")
    report_result = _cli("node-outcome", "--node-report", str(report))

    assert log_result.returncode == 2, log_result.stdout
    assert report_result.returncode == 2, report_result.stdout


def test_node_outcome_reads_real_runner_failed_node(tmp_path):
    runner = _run_real_aggregate_runner(
        tmp_path,
        {
            "tests/test_failed.py": (
                "def test_failure():\n"
                "    assert False\n"
            ),
        },
    )
    assert runner.returncode != 0, runner.stdout + runner.stderr

    log = tmp_path / "failed.log"
    log.write_text(runner.stdout, encoding="utf-8")
    result = _cli("node-outcome", "--log", str(log), "--aggregate")

    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["failed_nodeids"] == ["tests/test_failed.py::test_failure"]


def test_node_outcome_log_and_node_report_have_same_readability_verdict(tmp_path):
    report = tmp_path / "nodes.json"
    runner = _run_real_aggregate_runner(
        tmp_path,
        {
            "tests/test_teardown.py": (
                "import pytest\n\n"
                "@pytest.fixture\n"
                "def failing_teardown():\n"
                "    yield\n"
                "    raise RuntimeError('teardown boom')\n\n"
                "def test_passes():\n"
                "    pass\n\n"
                "def test_errors_in_teardown(failing_teardown):\n"
                "    pass\n"
            ),
        },
        node_report=report,
    )
    assert runner.returncode != 0, runner.stdout + runner.stderr

    log = tmp_path / "teardown-parity.log"
    log.write_text(runner.stdout, encoding="utf-8")
    log_result = _cli("node-outcome", "--log", str(log), "--aggregate")
    report_result = _cli("node-outcome", "--node-report", str(report))

    assert (log_result.returncode == 0) == (report_result.returncode == 0)
    assert log_result.returncode == 0, log_result.stderr


def test_node_outcome_mixed_infrastructure_and_test_failure_keeps_parity(
    tmp_path,
):
    report = tmp_path / "mixed-nodes.json"
    runner = _run_real_aggregate_runner(
        tmp_path,
        {
            "tests/infra/conftest.py": (
                "def pytest_sessionfinish(session, exitstatus):\n"
                "    if exitstatus == 0:\n"
                "        session.exitstatus = 3\n"
            ),
            "tests/infra/test_infra.py": "def test_infra_passes():\n    pass\n",
            "tests/real/test_real.py": (
                "def test_real_failure():\n"
                "    assert False\n"
            ),
        },
        node_report=report,
    )
    assert runner.returncode != 0, runner.stdout + runner.stderr
    assert "  tests/infra/test_infra.py  (1 passed)" in runner.stdout

    log = tmp_path / "mixed.log"
    log.write_text(runner.stdout, encoding="utf-8")
    log_result = _cli("node-outcome", "--log", str(log), "--aggregate")
    report_result = _cli("node-outcome", "--node-report", str(report))

    assert log_result.returncode == 2, log_result.stdout
    assert report_result.returncode == 2, report_result.stdout


def test_node_outcome_reads_the_runner_machine_report(tmp_path):
    report = tmp_path / "nodes.json"
    report.write_text(
        json.dumps(
            {
                "schema_version": "run-tests-parallel/node-report/v1",
                "files": {},
                "collected_nodeids": ["tests/green.py::test_ok", "tests/red.py::test_bad"],
                "failed_nodeids": ["tests/red.py::test_bad"],
                "collection_error_paths": [],
                "error_count": 0,
                "tests_collected": 2,
                "collect_ok": True,
                "probe_ok": True,
                "readable": True,
            }
        ),
        encoding="utf-8",
    )

    result = _cli("node-outcome", "--node-report", str(report))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "collect_ok": True,
        "probe_ok": True,
        "collected_nodeids": ["tests/green.py::test_ok", "tests/red.py::test_bad"],
        "failed_nodeids": ["tests/red.py::test_bad"],
        "error_count": 0,
        "collection_error_paths": [],
    }


def test_node_report_door_ignores_a_corrupt_human_log(
    monkeypatch, tmp_path, capsys
):
    report = tmp_path / "nodes.json"
    report.write_text(
        json.dumps(
            {
                "schema_version": "run-tests-parallel/node-report/v1",
                "files": {},
                "collected_nodeids": ["tests/green.py::test_ok"],
                "failed_nodeids": [],
                "collection_error_paths": [],
                "error_count": 0,
                "tests_collected": 1,
                "collect_ok": True,
                "probe_ok": True,
                "readable": True,
            }
        ),
        encoding="utf-8",
    )
    log = tmp_path / "spoiled.log"
    log.write_text("not pytest output\n", encoding="utf-8")

    def fail_if_log_is_parsed(*args, **kwargs):
        raise AssertionError("node-report door parsed the human log")

    monkeypatch.setattr(
        upstream_sync_gate, "parse_test_outcomes", fail_if_log_is_parsed
    )
    first_rc = upstream_sync_gate._main(
        ["node-outcome", "--node-report", str(report)]
    )
    first_output = capsys.readouterr().out
    log.write_text("ERROR tests/not-the-report.py::test_bad - boom\n", encoding="utf-8")
    second_rc = upstream_sync_gate._main(
        ["node-outcome", "--node-report", str(report)]
    )
    second_output = capsys.readouterr().out

    assert first_rc == second_rc == 0
    assert first_output == second_output


def test_node_outcome_rejects_an_expected_node_missing_from_measured_collection(
    tmp_path,
):
    report = tmp_path / "nodes.json"
    report.write_text(
        json.dumps(
            {
                "schema_version": "run-tests-parallel/node-report/v1",
                "files": {},
                "collected_nodeids": ["tests/a.py::test_present"],
                "failed_nodeids": [],
                "collection_error_paths": [],
                "error_count": 0,
                "tests_collected": 1,
                "collect_ok": True,
                "probe_ok": True,
                "readable": True,
            }
        ),
        encoding="utf-8",
    )
    expected = tmp_path / "expected.json"
    expected.write_text(
        json.dumps(["tests/a.py::test_present", "tests/a.py::test_missing"]),
        encoding="utf-8",
    )

    result = _cli(
        "node-outcome",
        "--node-report",
        str(report),
        "--expected-nodeids",
        str(expected),
    )

    assert result.returncode != 0, result.stdout
    assert "not collected" in result.stderr.lower(), result.stderr


def test_outcomes_parser_keeps_skipped_summary_out_of_nodeids():
    outcome = upstream_sync_gate.parse_test_outcomes(
        "SKIPPED [1] tests/hermes_cli/test_gateway_service.py:931: macOS-only test\n"
        "1 skipped in 0.01s\n"
    )

    assert outcome["collected_nodeids"] == []
    assert outcome["failed_nodeids"] == []
    assert outcome["skipped_paths"] == [
        "tests/hermes_cli/test_gateway_service.py"
    ]


def test_outcomes_comparator_reports_new_collection_error():
    comparator = getattr(upstream_sync_gate, "compare_test_outcomes", None)
    assert callable(comparator), "outcomes comparator is not implemented"

    result = comparator(
        "0 failed, 2 passed in 0.05s\n",
        "ERROR tests/broken.py - RuntimeError: boom\n2 errors in 0.05s\n",
    )

    assert result == {
        "new_failures": [],
        "new_collection_errors": ["tests/broken.py"],
    }


def test_outcomes_no_tests_ran_is_unreadable_not_clean():
    parser = getattr(upstream_sync_gate, "parse_test_outcomes", None)
    assert callable(parser), "outcomes parser is not implemented"

    with pytest.raises(ValueError, match="no tests ran"):
        parser("no tests ran in 0.01s\n")


def test_outcomes_node_run_marks_collection_error_unreadable(tmp_path):
    log = tmp_path / "collection-error.log"
    log.write_text("ERROR tests/broken.py - RuntimeError: boom\n2 errors in 0.05s\n")

    result = _cli("node-outcome", "--log", str(log))

    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["collect_ok"] is False
    assert outcome["probe_ok"] is False
    assert outcome["error_count"] == 2


def test_node_outcome_reads_a_green_aggregate_log_explicitly(tmp_path):
    log = tmp_path / "aggregate-green.log"
    log.write_text(
        "Running 2 test files (~2 tests) with -j 1\n"
        "=== Summary: 2 files, 2 tests passed, 0 failed "
        "(100% complete) in 1.0s (1 workers) ===\n"
    )

    result = _cli("node-outcome", "--log", str(log), "--aggregate")

    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["collect_ok"] is True
    assert outcome["probe_ok"] is True


def test_node_outcome_sums_collection_errors_across_an_aggregate_log(tmp_path):
    log = tmp_path / "aggregate-errors.log"
    log.write_text(
        "--- tests/a.py ---\n"
        "ERROR tests/a.py - RuntimeError: a\n"
        "1 error in 0.10s\n"
        "--- tests/b.py ---\n"
        "ERROR tests/b.py - RuntimeError: b\n"
        "1 error in 0.10s\n"
        "=== Summary: 2 files, 0 tests passed, 0 failed "
        "(100% complete) in 0.2s (1 workers) ===\n"
    )

    result = _cli("node-outcome", "--log", str(log), "--aggregate")

    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["error_count"] == 2
    assert outcome["collection_error_paths"] == [
        "tests/a.py",
        "tests/b.py",
    ]


def test_a_log_without_a_summary_line_is_a_killed_run_not_a_clean_one():
    """Прогон без итоговой строки убит (os._exit, OOM, вотчдог).

    Трактовать его как «падений нет» — ровно тот способ пропустить регрессию
    в прод, ради которого этот гейт и существует.
    """
    before = _log(["tests/a.py::test_one"])
    killed = "FAILED tests/a.py::test_one - AssertionError: boom\n"
    with pytest.raises(ValueError):
        new_failures(before, killed)
    with pytest.raises(ValueError):
        new_failures(killed, before)


GATE = Path(__file__).resolve().parents[2] / "scripts" / "upstream_sync_gate.py"


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), *args], capture_output=True, text=True
    )


def test_cli_merge_tree_exits_zero_and_prints_nothing_when_clean(tmp_path):
    f = tmp_path / "mt.txt"
    f.write_text(CLEAN)
    r = _cli("merge-tree", "--output", str(f))
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_cli_merge_tree_exits_one_and_lists_paths_when_conflicted(tmp_path):
    f = tmp_path / "mt.txt"
    f.write_text(CONFLICTED)
    r = _cli("merge-tree", "--output", str(f))
    assert r.returncode == 1
    assert r.stdout.split() == ["f.txt", "gateway/run.py"]


def test_cli_new_failures_exits_one_and_lists_them(tmp_path):
    before, after = tmp_path / "b.log", tmp_path / "a.log"
    before.write_text(_log(["tests/a.py::test_one"]))
    after.write_text(_log(["tests/a.py::test_one", "tests/b.py::test_two"]))
    r = _cli("new-failures", "--baseline", str(before), "--post", str(after))
    assert r.returncode == 1
    assert r.stdout.split() == ["tests/b.py::test_two"]


def test_cli_new_failures_exits_zero_when_the_sets_match(tmp_path):
    before, after = tmp_path / "b.log", tmp_path / "a.log"
    same = _log(["tests/a.py::test_one"])
    before.write_text(same)
    after.write_text(same)
    r = _cli("new-failures", "--baseline", str(before), "--post", str(after))
    assert r.returncode == 0


def test_cli_new_failures_accepts_a_green_aggregate_run(tmp_path):
    before, after = tmp_path / "b.log", tmp_path / "a.log"
    green = (
        "Running 2 test files (~2 tests) with -j 1\n"
        "=== Summary: 2 files, 2 tests passed, 0 failed "
        "(100% complete) in 1.0s (1 workers) ===\n"
    )
    before.write_text(green)
    after.write_text(green)

    r = _cli(
        "new-failures",
        "--baseline",
        str(before),
        "--post",
        str(after),
        "--aggregate",
    )

    assert r.returncode == 0, r.stderr


def test_cli_new_failures_reports_a_failure_from_an_aggregate_run(tmp_path):
    before, after = tmp_path / "b.log", tmp_path / "a.log"
    before.write_text(
        "--- tests/a.py ---\n"
        "PASSED tests/a.py::test_a\n"
        "1 passed in 0.10s\n"
        "=== Summary: 1 files, 1 tests passed, 0 failed "
        "(100% complete) in 0.1s (1 workers) ===\n"
    )
    after.write_text(
        "--- tests/a.py ---\n"
        "FAILED tests/a.py::test_a - AssertionError\n"
        "1 failed in 0.10s\n"
        "=== Summary: 1 files, 0 tests passed, 1 failed "
        "(100% complete) in 0.1s (1 workers) ===\n"
    )

    r = _cli(
        "new-failures",
        "--baseline",
        str(before),
        "--post",
        str(after),
        "--aggregate",
    )

    assert r.returncode == 1, r.stderr
    assert r.stdout.split() == ["tests/a.py::test_a"]


def test_cli_exits_two_on_a_killed_run(tmp_path):
    before, after = tmp_path / "b.log", tmp_path / "a.log"
    before.write_text(_log(["tests/a.py::test_one"]))
    after.write_text("FAILED tests/a.py::test_one - boom\n")
    r = _cli("new-failures", "--baseline", str(before), "--post", str(after))
    assert r.returncode == 2
    assert "killed" in r.stderr
