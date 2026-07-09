from __future__ import annotations

from pathlib import Path
import subprocess
import os

import pytest

from hermes_cli.pipeline_mutations import apply_controlled_mutations


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, text=True, capture_output=True)


def _init_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mut-repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_apply_controlled_mutations_writes_safe_file(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)

    summary = apply_controlled_mutations(
        allow_mutations=True,
        mutation_workspace=repo,
        mutations_payload=[
            {
                "operation": "write_text",
                "path": "tests/test_example.py",
                "content": "def test_ok():\n    assert True\n",
            }
        ],
    )

    assert summary.applied_count == 1
    assert summary.denied_count == 0
    assert (repo / "tests/test_example.py").read_text(encoding="utf-8").startswith("def test_ok")
    assert "def test_ok" not in str(summary.to_safe_dict())


def test_apply_controlled_mutations_denies_when_gate_disabled(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)

    summary = apply_controlled_mutations(
        allow_mutations=False,
        mutation_workspace=repo,
        mutations_payload=[{"operation": "write_text", "path": "safe.txt", "content": "hello\n"}],
    )

    assert summary.applied_count == 0
    assert summary.denied_count == 1
    assert summary.results[0]["reason"] == "mutation_gate_disabled"
    assert not (repo / "safe.txt").exists()


@pytest.mark.parametrize("path_value", ["../secret.env", "/tmp/absolute.txt", ".git/config", ".env"])
def test_apply_controlled_mutations_denies_sensitive_or_outside_paths(tmp_path: Path, path_value: str) -> None:
    repo = _init_git_repo(tmp_path)

    summary = apply_controlled_mutations(
        allow_mutations=True,
        mutation_workspace=repo,
        mutations_payload=[{"operation": "write_text", "path": path_value, "content": "unsafe\n"}],
    )

    assert summary.applied_count == 0
    assert summary.denied_count == 1


def test_apply_controlled_mutations_denies_symlink_escape_without_creating_outside_dirs(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = repo / "link-out"
    os.symlink(outside, link)

    summary = apply_controlled_mutations(
        allow_mutations=True,
        mutation_workspace=repo,
        mutations_payload=[
            {
                "operation": "write_text",
                "path": "link-out/nested/escape.txt",
                "content": "unsafe\n",
            }
        ],
    )

    assert summary.applied_count == 0
    assert summary.denied_count == 1
    assert summary.results[0]["reason"] in {"path_outside_workspace", "symlink_target_denied"}
    assert not (outside / "nested").exists()
    assert not (outside / "nested/escape.txt").exists()


def test_apply_controlled_mutations_mixed_batch_top_level_symlink_no_partial_write(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("external\n", encoding="utf-8")
    os.symlink(outside, repo / "link-out")

    summary = apply_controlled_mutations(
        allow_mutations=True,
        mutation_workspace=repo,
        mutations_payload=[
            {"operation": "write_text", "path": "safe.txt", "content": "hello\n"},
            {"operation": "write_text", "path": "link-out", "content": "attack\n"},
        ],
    )

    assert summary.applied_count == 0
    assert summary.denied_count >= 1
    assert not (repo / "safe.txt").exists()
    assert outside.read_text(encoding="utf-8") == "external\n"


def test_apply_controlled_mutations_denies_oversized_content_by_default(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    summary = apply_controlled_mutations(
        allow_mutations=True,
        mutation_workspace=repo,
        mutations_payload=[
            {"operation": "write_text", "path": "big.txt", "content": "x" * 150_000}
        ],
    )
    assert summary.denied_count == 1
    assert summary.results[0]["reason"] == "content_too_large"


def test_apply_controlled_mutations_allows_oversized_content_when_exempt(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    summary = apply_controlled_mutations(
        allow_mutations=True,
        mutation_workspace=repo,
        mutations_payload=[
            {
                "operation": "write_text",
                "path": "big.txt",
                "content": "x" * 150_000,
                "size_limit_exempt": True,
            }
        ],
    )
    assert summary.applied_count == 1
    assert summary.denied_count == 0
    assert len((repo / "big.txt").read_text(encoding="utf-8")) == 150_000


def test_apply_controlled_mutations_exempt_still_denies_binary_content(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    summary = apply_controlled_mutations(
        allow_mutations=True,
        mutation_workspace=repo,
        mutations_payload=[
            {
                "operation": "write_text",
                "path": "big.bin",
                "content": "x\x00y",
                "size_limit_exempt": True,
            }
        ],
    )
    assert summary.denied_count == 1
    assert summary.results[0]["reason"] == "binary_content_denied"


def test_apply_controlled_mutations_permission_error_becomes_clean_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_git_repo(tmp_path)
    target = repo / "existing.py"
    target.write_text("original\n", encoding="utf-8")

    orig_write_text = Path.write_text

    def boom(self: Path, *args, **kwargs):
        if self.name == "existing.py":
            raise PermissionError(13, "Permission denied")
        return orig_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", boom)

    summary = apply_controlled_mutations(
        allow_mutations=True,
        mutation_workspace=repo,
        mutations_payload=[{"operation": "write_text", "path": "existing.py", "content": "new\n"}],
    )

    assert summary.applied_count == 0
    assert summary.denied_count == 1
    assert summary.results[0]["reason"] == "write_failed_not_writable"
    # original content preserved (write was denied, not partially applied)
    assert target.read_text(encoding="utf-8") == "original\n"


def test_apply_controlled_mutations_normal_write_still_applies(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    summary = apply_controlled_mutations(
        allow_mutations=True,
        mutation_workspace=repo,
        mutations_payload=[{"operation": "write_text", "path": "new.py", "content": "hi\n"}],
    )
    assert summary.applied_count == 1
    assert summary.denied_count == 0
    assert (repo / "new.py").read_text(encoding="utf-8") == "hi\n"
