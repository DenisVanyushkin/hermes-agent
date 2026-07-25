"""Nightly cleanup for the two stores that grow without bound.

Run worktrees (~200 MB each) and per-session review-debt files. Both were added
this cycle and neither had an owner for its old entries.

The governing rule is the same for both, and it is not a detail: **age is not
permission to delete**. A worktree holding uncommitted work is what an operator
comes looking for later, and a debt file with outstanding findings *is* the
review objection — removing it does not tidy up, it silently discharges the
objection and lets the commit gate pass. Only provably-spent entries go.
"""
import json
import subprocess
from pathlib import Path

import pytest

from scripts.nightly_gc import prune_review_debt, summarize


def _debt(root: Path, session: str, outstanding: dict, mtime: float) -> Path:
    p = root / f"{session}.json"
    p.write_text(json.dumps({"session": session, "outstanding": outstanding}))
    import os

    os.utime(p, (mtime, mtime))
    return p


@pytest.fixture
def debt_root(tmp_path: Path) -> Path:
    root = tmp_path / "review_gate"
    root.mkdir()
    return root


NOW = 1_000_000.0
DAY = 86400.0


def test_a_settled_old_session_is_removed(debt_root: Path):
    p = _debt(debt_root, "old-clean", {}, NOW - 30 * DAY)

    removed, kept = prune_review_debt(debt_root, max_age_seconds=14 * DAY, now=NOW)

    assert [Path(x).name for x in removed] == ["old-clean.json"]
    assert kept == []
    assert not p.exists()


def test_a_recent_session_is_left_alone(debt_root: Path):
    p = _debt(debt_root, "fresh", {}, NOW - 1 * DAY)

    removed, kept = prune_review_debt(debt_root, max_age_seconds=14 * DAY, now=NOW)

    assert removed == []
    assert p.exists()


def test_outstanding_findings_are_never_removed_however_old(debt_root: Path):
    """The rule that matters: deleting this would discharge the review."""
    p = _debt(
        debt_root, "old-indebted",
        {"tools/approval.py": ["cron_mode=smart is undocumented"]},
        NOW - 365 * DAY,
    )

    removed, kept = prune_review_debt(debt_root, max_age_seconds=14 * DAY, now=NOW)

    assert removed == []
    assert [Path(x).name for x in kept] == ["old-indebted.json"]
    assert p.exists()
    # And the debt itself is intact.
    assert json.loads(p.read_text())["outstanding"]


def test_a_stale_unreadable_file_is_removed(debt_root: Path):
    """ReviewGateState.load already treats it as a clean slate, so it is inert."""
    p = debt_root / "corrupt.json"
    p.write_text("not json{{{")
    import os

    os.utime(p, (NOW - 30 * DAY, NOW - 30 * DAY))

    removed, kept = prune_review_debt(debt_root, max_age_seconds=14 * DAY, now=NOW)

    assert [Path(x).name for x in removed] == ["corrupt.json"]
    assert not p.exists()


def test_a_missing_store_is_not_an_error(tmp_path: Path):
    removed, kept = prune_review_debt(
        tmp_path / "never-created", max_age_seconds=DAY, now=NOW
    )
    assert (removed, kept) == ([], [])


def test_non_json_files_are_ignored(debt_root: Path):
    stray = debt_root / "README.txt"
    stray.write_text("hands off")
    import os

    os.utime(stray, (NOW - 99 * DAY, NOW - 99 * DAY))

    removed, _ = prune_review_debt(debt_root, max_age_seconds=DAY, now=NOW)

    assert removed == []
    assert stray.exists()


# ── Reporting ───────────────────────────────────────────────────────────────

def test_a_quiet_night_prints_nothing():
    """The cron job is no-agent and must stay silent unless it did something."""
    assert summarize(worktrees=[], debt_removed=[], debt_kept=[]) == ""


def test_work_done_is_reported():
    out = summarize(
        worktrees=["/tmp/runs/r1"], debt_removed=["/x/s1.json"], debt_kept=[]
    )
    assert "r1" in out
    assert "1" in out


def test_retained_debt_is_surfaced_even_though_nothing_was_deleted():
    """An unresolved review sitting there for weeks is worth saying out loud."""
    out = summarize(worktrees=[], debt_removed=[], debt_kept=["/x/old-indebted.json"])
    assert out != ""
    assert "old-indebted" in out
