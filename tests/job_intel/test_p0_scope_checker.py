"""Behavioral tests for the P0 authorization scope checker."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parents[2]
CHECKER = REPO_ROOT / "scripts" / "check_p0_scope.sh"
AUTH_REL = Path("docs/evidence/product-search-gate-a/authorization.md")
PLAN_REL = Path("docs/plans/p0-plan.md")
ALLOWED_PATHS = (
    "allowed.txt",
    str(AUTH_REL),
    str(PLAN_REL),
)
REAL_AUTH_REL = Path(
    "docs/evidence/product-search-gate-a/2026-08-26-p0-authorization.md"
)
NOISE_PATH = Path("tests/fixtures/legal_research/small_act.html")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _write_authorization(repo: Path, authorized_base: str) -> None:
    canonical = ("\n".join(sorted(set(ALLOWED_PATHS))) + "\n").encode()
    table_hash = hashlib.sha256(canonical).hexdigest()
    tick = chr(96)
    rows = "\n".join(f"| {tick}{path}{tick} | test | test |" for path in ALLOWED_PATHS)
    authorization = f"""\
---
authorized_base: {authorized_base}
scope_table_sha256: {table_hash}
scope_table_path_count: {len(set(ALLOWED_PATHS))}
scope_table_canonicalization: "sorted unique backticked paths from the section 4 table, one per line, LF-terminated"
---

# Test authorization

## 4. Full path list

| Path | Section | Basis |
|---|---|---|
{rows}
"""
    auth_path = repo / AUTH_REL
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(authorization, encoding="utf-8")
    plan_path = repo / PLAN_REL
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# P0 test plan\n", encoding="utf-8")


@pytest.fixture
def authorized_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "P0 Scope Test")
    _git(repo, "config", "user.email", "p0-scope-test@example.invalid")

    (repo / "allowed.txt").write_text("base\n", encoding="utf-8")
    (repo / "outside.txt").write_text("base\n", encoding="utf-8")
    noise_path = repo / NOISE_PATH
    noise_path.parent.mkdir(parents=True, exist_ok=True)
    noise_path.write_text("fixture\n", encoding="utf-8")
    base = _commit(repo, "base")

    _write_authorization(repo, base)
    _commit(repo, "owner authorization")
    return repo, base


def _run_checker(
    repo: Path, *, ref: str = "HEAD", authorization: Path | None = None
) -> subprocess.CompletedProcess[str]:
    auth_path = authorization or (repo / AUTH_REL)
    return subprocess.run(
        [
            "bash",
            str(CHECKER),
            "--authorization",
            str(auth_path),
            "--ref",
            ref,
        ],
        cwd=repo,
        text=True,
        capture_output=True,
    )


def test_rejects_a_committed_path_outside_the_authorization_table(
    authorized_repo: tuple[Path, str],
) -> None:
    repo, _ = authorized_repo
    (repo / "outside.txt").write_text("unauthorized\n", encoding="utf-8")
    offending_commit = _commit(repo, "touch unauthorized path")

    result = _run_checker(repo)

    assert result.returncode != 0
    assert "outside.txt" in result.stderr
    assert offending_commit in result.stderr


def test_rejects_intermediate_touch_even_after_a_later_revert(
    authorized_repo: tuple[Path, str],
) -> None:
    repo, base = authorized_repo
    (repo / "outside.txt").write_text(
        "temporary unauthorized change\n", encoding="utf-8"
    )
    offending_commit = _commit(repo, "touch unauthorized path")
    (repo / "outside.txt").write_text("base\n", encoding="utf-8")
    _commit(repo, "revert unauthorized path")

    assert "outside.txt" not in _git(repo, "diff", "--name-only", f"{base}..HEAD")
    result = _run_checker(repo)

    assert result.returncode != 0
    assert "outside.txt" in result.stderr
    assert offending_commit in result.stderr


def test_rejects_eol_only_change_outside_the_named_fixture_noise_list(
    authorized_repo: tuple[Path, str],
) -> None:
    repo, _ = authorized_repo
    (repo / "outside.txt").write_text("base \n", encoding="utf-8")

    result = _run_checker(repo)

    assert result.returncode != 0
    assert "outside.txt" in result.stderr
    assert "working-tree" in result.stderr


def test_rejects_content_change_in_a_named_fixture_noise_path(
    authorized_repo: tuple[Path, str],
) -> None:
    repo, _ = authorized_repo
    (repo / NOISE_PATH).write_text("fixture changed\n", encoding="utf-8")

    result = _run_checker(repo)

    assert result.returncode != 0
    assert str(NOISE_PATH) in result.stderr
    assert "working-tree" in result.stderr


def test_rejects_an_untracked_working_tree_path(
    authorized_repo: tuple[Path, str],
) -> None:
    repo, _ = authorized_repo
    (repo / "untracked.txt").write_text("unauthorized\n", encoding="utf-8")

    result = _run_checker(repo)

    assert result.returncode != 0
    assert "untracked.txt" in result.stderr
    assert "untracked-working-tree" in result.stderr


def test_rejects_a_merge_commit_even_when_all_paths_are_allowed(
    authorized_repo: tuple[Path, str],
) -> None:
    repo, _ = authorized_repo
    _git(repo, "checkout", "-b", "side")
    (repo / PLAN_REL).write_text("# side plan change\n", encoding="utf-8")
    _commit(repo, "side allowed change")
    _git(repo, "checkout", "main")
    (repo / "allowed.txt").write_text("main change\n", encoding="utf-8")
    _commit(repo, "main allowed change")
    _git(repo, "merge", "--no-ff", "side", "-m", "merge allowed side")
    merge_commit = _git(repo, "rev-parse", "HEAD")

    result = _run_checker(repo)

    assert result.returncode != 0
    assert "merge commit rejected" in result.stderr
    assert merge_commit in result.stderr
    assert "return to a linear history" in result.stderr


def test_rejects_a_merge_commit_that_carries_a_forbidden_path(
    authorized_repo: tuple[Path, str],
) -> None:
    repo, _ = authorized_repo
    _git(repo, "checkout", "-b", "side")
    (repo / "allowed.txt").write_text("side change\n", encoding="utf-8")
    _commit(repo, "side allowed change")
    _git(repo, "checkout", "main")
    (repo / PLAN_REL).write_text("# main plan change\n", encoding="utf-8")
    _commit(repo, "main allowed change")
    _git(repo, "merge", "--no-ff", "side", "-m", "merge forbidden side")
    (repo / "outside.txt").write_text("merged unauthorized change\n", encoding="utf-8")
    _git(repo, "add", "outside.txt")
    _git(repo, "commit", "--amend", "--no-edit")
    merge_commit = _git(repo, "rev-parse", "HEAD")

    result = _run_checker(repo)

    assert result.returncode != 0
    assert "merge commit rejected" in result.stderr
    assert merge_commit in result.stderr


def test_accepts_the_real_authorized_range_in_a_temporary_clone(
    tmp_path: Path,
) -> None:
    clone = tmp_path / "real-range"
    subprocess.run(
        ["git", "clone", "--shared", str(REPO_ROOT), str(clone)],
        check=True,
        text=True,
        capture_output=True,
    )
    _git(clone, "checkout", "--detach", "203cad8206")

    result = _run_checker(
        clone,
        ref="203cad8206",
        authorization=clone / REAL_AUTH_REL,
    )

    assert result.returncode == 0, result.stderr
    assert "scope check passed" in result.stdout


def test_uses_the_first_commit_that_added_the_authorization_artifact(
    authorized_repo: tuple[Path, str],
) -> None:
    repo, _ = authorized_repo
    auth_path = repo / AUTH_REL
    auth_path.write_text(
        auth_path.read_text(encoding="utf-8") + "\n# later metadata touch\n",
        encoding="utf-8",
    )
    _commit(repo, "touch authorization metadata")
    (repo / "allowed.txt").write_text("allowed work\n", encoding="utf-8")
    _commit(repo, "allowed work")

    result = _run_checker(repo)

    assert result.returncode == 0, result.stderr
    assert "scope check passed" in result.stdout


def test_rejects_a_corrupted_scope_table_hash(
    authorized_repo: tuple[Path, str],
) -> None:
    repo, _ = authorized_repo
    auth_path = repo / AUTH_REL
    lines = auth_path.read_text(encoding="utf-8").splitlines()
    auth_path.write_text(
        "\n".join(
            "scope_table_sha256: " + ("0" * 64)
            if line.startswith("scope_table_sha256:")
            else line
            for line in lines
        )
        + "\n",
        encoding="utf-8",
    )

    result = _run_checker(repo)

    assert result.returncode != 0
    assert "scope table hash mismatch" in result.stderr


def test_rejects_a_missing_authorization_file(
    authorized_repo: tuple[Path, str],
) -> None:
    repo, _ = authorized_repo
    (repo / AUTH_REL).unlink()

    result = _run_checker(repo)

    assert result.returncode != 0
    assert "missing authorization file" in result.stderr
