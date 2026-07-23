"""Step 5B Slice 5B-3a: benchmark artifacts must live OUTSIDE the repo.

Incident 2026-07-20: an upstream-sync/recovery rewrote local/customizations
and the untracked `artifacts/` tree disappeared with it, destroying $0.67 of
paid, non-reproducible LLM recordings (5A smoke + 5B-4 calibration) and the
pinned eligible-corpus snapshot. Repo-relative artifact roots are therefore
forbidden for benchmark output.
"""
from __future__ import annotations

from pathlib import Path

from job_intel.vacancy_understanding.semantic.benchmark import baseline
from job_intel.vacancy_understanding.semantic.benchmark.paths import (
    DEFAULT_ARTIFACT_ROOT,
    ARTIFACT_ROOT_ENV,
    artifact_root,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def test_default_artifact_root_is_absolute_and_outside_the_repo(monkeypatch):
    monkeypatch.delenv(ARTIFACT_ROOT_ENV, raising=False)
    root = artifact_root()
    assert root.is_absolute()
    assert not _is_inside(root, REPO_ROOT), (
        f"{root} is inside the repo — automated sync/recovery can destroy it")
    assert root == DEFAULT_ARTIFACT_ROOT


def test_env_override_is_respected(tmp_path, monkeypatch):
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path / "custom"))
    assert artifact_root() == tmp_path / "custom"


def test_relative_env_override_is_rejected(monkeypatch):
    import pytest
    monkeypatch.setenv(ARTIFACT_ROOT_ENV, "artifacts/whatever")
    with pytest.raises(ValueError, match="absolute"):
        artifact_root()


def test_baseline_default_out_root_lives_under_artifact_root(monkeypatch):
    monkeypatch.delenv(ARTIFACT_ROOT_ENV, raising=False)
    default = baseline.default_out_root()
    assert _is_inside(default, artifact_root())
    assert not _is_inside(default, REPO_ROOT)


def test_gitignore_covers_artifacts_dir():
    """Belt and braces: even with output moved out, a stray repo-relative
    `artifacts/` (Phase I replay defaults still write there) must at least be
    ignored so a plain `git clean -fd` leaves it alone."""
    patterns = {
        line.strip() for line in (REPO_ROOT / ".gitignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert "artifacts/" in patterns
