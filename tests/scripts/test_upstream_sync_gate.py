"""Юниты гейта upstream-sync: разбор вывода git merge-tree."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

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
