"""Behavioural tests for the checkout tree-state checker.

Each failing state is built in a throwaway repository and the script is
executed, because the property under test is what the script *does* with a
dirty tree, a failing git, and untracked auto-loading code — none of which a
grep over its source can establish.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

CHECKER = Path(__file__).resolve().parents[2] / "scripts/job_intel_tree_state.sh"


def run(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(CHECKER), str(path)], capture_output=True, text=True, check=False
    )


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "checkout"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "module.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-qm", "base"],
        check=True,
    )
    return repo


def test_clean_checkout_passes(tmp_path) -> None:
    result = run(make_repo(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_tracked_modification_is_refused(tmp_path) -> None:
    repo = make_repo(tmp_path)
    (repo / "module.py").write_text("x = 2\n", encoding="utf-8")

    result = run(repo)

    assert result.returncode == 3
    assert "tracked modifications" in result.stderr


def test_git_failure_is_not_read_as_clean(tmp_path) -> None:
    """The earlier version swallowed git errors with `|| true`, so a directory
    that is not a repository at all reported a clean tree."""
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()

    result = run(not_a_repo)

    assert result.returncode == 2, result.stdout
    assert "git status failed" in result.stderr


def test_untracked_sitecustomize_is_refused(tmp_path) -> None:
    """Python executes sitecustomize.py with no import statement, so this code
    runs while every tracked file still matches the pin."""
    repo = make_repo(tmp_path)
    (repo / "sitecustomize.py").write_text("import os\n", encoding="utf-8")

    result = run(repo)

    assert result.returncode == 4
    assert "untracked auto-loading code" in result.stderr


def test_pth_in_the_checkout_root_is_not_this_checks_business(tmp_path) -> None:
    """A .pth only executes inside a site directory, and the virtualenv is
    gitignored, so git never sees it. Claiming .pth coverage here would be a
    guarantee this check cannot deliver; it belongs to the site manifest."""
    repo = make_repo(tmp_path)
    (repo / "00-shim.pth").write_text("import shim\n", encoding="utf-8")

    result = run(repo)

    assert result.returncode == 0, result.stderr


def test_untracked_directory_contents_are_not_collapsed(tmp_path) -> None:
    """--untracked-files=normal reports an untracked directory as one entry and
    hides what is inside it. The assertion is on the listing the checker sees,
    because a return code alone would stay green under either setting."""
    repo = make_repo(tmp_path)
    nested = repo / "scratch"
    nested.mkdir()
    (nested / "payload.py").write_text("import os\n", encoding="utf-8")

    listing = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True, check=True,
    ).stdout
    collapsed = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=normal"],
        capture_output=True, text=True, check=True,
    ).stdout

    assert "scratch/payload.py" in listing
    assert "scratch/payload.py" not in collapsed, "fixture no longer demonstrates the difference"
    assert "--untracked-files=all" in CHECKER.read_text(encoding="utf-8")
