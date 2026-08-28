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
