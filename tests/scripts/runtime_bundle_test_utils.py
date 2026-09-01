"""Helpers for building test doubles from the runtime bundle contract."""

from __future__ import annotations

import shutil
from pathlib import Path


PYTHON_BUNDLE_HELPERS = (
    "run_tests_parallel.py",
    "pytest_status_lines.py",
    "upstream_sync_apply.py",
    "upstream_sync_cron.py",
    "upstream_sync_decisions.py",
    "upstream_sync_findings.py",
    "upstream_sync_gate.py",
    "upstream_sync_index.py",
    "upstream_sync_invariants.py",
    "upstream_sync_llm.py",
    "upstream_sync_policy.py",
    "upstream_sync_receipts.py",
    "upstream_sync_replay.py",
    "upstream_sync_slack.py",
    "upstream_sync_triage.py",
)


def runtime_python_files(repo_root: Path) -> tuple[str, ...]:
    """Return the explicit Python helper set shared by both stub bundles."""
    return PYTHON_BUNDLE_HELPERS


def copy_runtime_python_files(repo_root: Path, destination: Path) -> None:
    """Copy every Python runtime helper into a stub scripts directory."""
    for name in runtime_python_files(repo_root):
        shutil.copyfile(repo_root / "scripts" / name, destination / name)
