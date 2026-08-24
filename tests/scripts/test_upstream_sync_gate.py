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
        manifest=case["manifest"],
    ) == expected


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
        "failed_nodeids": [],
        "error_count": 2,
        "collection_error_paths": ["tests/broken.py"],
    }


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
            "-rEf",
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


@pytest.mark.parametrize(
    ("summary", "error_count"),
    [
        ("2 passed, 1 error in 0.12s\n", 1),
        ("76 failed, 6259 passed, 2 skipped, 6 warnings in 679.63s\n", 0),
    ],
)
def test_outcomes_parser_accepts_known_pytest_summary_forms(summary, error_count):
    outcome = upstream_sync_gate.parse_test_outcomes(summary)
    assert outcome["error_count"] == error_count


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


def test_cli_exits_two_on_a_killed_run(tmp_path):
    before, after = tmp_path / "b.log", tmp_path / "a.log"
    before.write_text(_log(["tests/a.py::test_one"]))
    after.write_text("FAILED tests/a.py::test_one - boom\n")
    r = _cli("new-failures", "--baseline", str(before), "--post", str(after))
    assert r.returncode == 2
    assert "killed" in r.stderr
