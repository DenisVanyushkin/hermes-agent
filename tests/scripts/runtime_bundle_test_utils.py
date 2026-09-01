"""Helpers for building test doubles from the runtime bundle contract."""

from __future__ import annotations

import shutil
from pathlib import Path


def runtime_python_files(repo_root: Path) -> tuple[str, ...]:
    """Read the Python portion of the production ``RUNTIME_FILES`` array."""
    in_runtime_files = False
    names: list[str] = []
    for raw_line in (
        repo_root / "scripts" / "acceptance" / "publish-runtime.sh"
    ).read_text().splitlines():
        line = raw_line.strip()
        if line == "RUNTIME_FILES=(":
            in_runtime_files = True
            continue
        if in_runtime_files and line == ")":
            break
        if in_runtime_files and line and not line.startswith("#"):
            names.append(line)
    if not names:
        raise AssertionError("publish-runtime.sh has no RUNTIME_FILES")
    runtime_names = [name for name in names if name.endswith(".py")]
    # The finalizer also loads host-side fallback helpers intentionally outside
    # the published RUNTIME_FILES contract. Derive those by their shared
    # production naming convention so new helpers reach both stub bundles.
    runtime_names.extend(
        path.name for path in sorted((repo_root / "scripts").glob("upstream_sync_*.py"))
    )
    return tuple(dict.fromkeys(runtime_names))


def copy_runtime_python_files(repo_root: Path, destination: Path) -> None:
    """Copy every Python runtime helper into a stub scripts directory."""
    for name in runtime_python_files(repo_root):
        shutil.copyfile(repo_root / "scripts" / name, destination / name)
