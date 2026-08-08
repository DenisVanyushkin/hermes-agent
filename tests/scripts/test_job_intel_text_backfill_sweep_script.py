"""Coverage for job_intel_text_backfill_sweep.sh that the shared glob test in
test_job_intel_enrichment_script.py does not provide: python interpreter
resolution (no bare python3/python fallback) and the SQLite WAL-reset version
guard.

These tests never touch the live database and never run the real sweep --
the resolved interpreter is always a fake shell-script stub.
"""
from __future__ import annotations

import getpass
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
ENTRYPOINT = "job_intel_text_backfill_sweep.sh"


def _write_stub(path: Path, body: str) -> Path:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _build_runtime_layout(tmp_path: Path) -> tuple[Path, Path]:
    """Mirror the production layout: a repo checkout with the real package.

    Only the workdir-resolution test needs the $HERMES_HOME/job_intel decoy
    (a data dir named like the package); the interpreter/sqlite tests run
    with JOB_INTEL_WORKDIR pinned directly and don't need the full layout.
    """
    home = tmp_path / "home"
    hermes_home = home / ".hermes"

    repo = hermes_home / "hermes-agent"
    package = repo / "job_intel"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text("from .cli import main\n")
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPTS / ENTRYPOINT, repo / "scripts" / ENTRYPOINT)
    shutil.copy(
        SCRIPTS / "job_intel_service_user.sh",
        repo / "scripts" / "job_intel_service_user.sh",
    )
    (repo / "scripts" / "job_intel_text_backfill_sweep.py").write_text(
        "print('should never run in a test')\n"
    )

    # The decoy: $HERMES_HOME/job_intel holds databases, not python.
    decoy = hermes_home / "job_intel"
    decoy.mkdir(parents=True)
    (decoy / "job_intel.sqlite3").write_text("stale copy, frozen 2026-05-30")

    scripts_dir = hermes_home / "scripts"
    scripts_dir.mkdir(parents=True)
    for name in (ENTRYPOINT, "job_intel_service_user.sh"):
        shutil.copy(SCRIPTS / name, scripts_dir / name)

    return hermes_home, scripts_dir


def _base_env(hermes_home: Path, stub_python: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("JOB_INTEL_WORKDIR", None)
    env.pop("JOB_INTEL_PYTHON", None)
    env.update(
        HOME=str(hermes_home.parent),
        HERMES_HOME=str(hermes_home),
        JOB_INTEL_SERVICE_USER=getpass.getuser(),
        JOB_INTEL_WORKDIR=str(hermes_home / "hermes-agent"),
    )
    if stub_python is not None:
        env["JOB_INTEL_PYTHON"] = str(stub_python)
    return env


def _run(scripts_dir: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(scripts_dir / ENTRYPOINT)],
        capture_output=True,
        text=True,
        cwd=str(scripts_dir),
        env=env,
        timeout=60,
    )


# --- interpreter resolution -------------------------------------------------


def test_uses_explicit_job_intel_python_over_bare_python3(tmp_path: Path) -> None:
    """JOB_INTEL_PYTHON, when set and executable, wins -- and it must be used
    instead of falling back to a bare python3/python found on PATH."""
    hermes_home, scripts_dir = _build_runtime_layout(tmp_path)
    workdir = hermes_home / "hermes-agent"

    stub = _write_stub(
        tmp_path / "stub_new_python",
        'echo "3.53.4"\nexit 0\n',
    )
    env = _base_env(hermes_home, stub_python=stub)

    result = _run(scripts_dir, env)

    assert result.returncode == 0, result.stderr
    # Proves our stub ran (the real backfill script would not print this).
    assert str(stub) in result.stderr or "sqlite" in result.stderr.lower()


def test_no_venv_interpreter_found_fails_loudly(tmp_path: Path) -> None:
    """No JOB_INTEL_PYTHON, no venv/.venv under the workdir -- must fail with
    a clear message, never silently fall back to a bare python3/python."""
    hermes_home, scripts_dir = _build_runtime_layout(tmp_path)
    env = _base_env(hermes_home)
    env.pop("JOB_INTEL_PYTHON", None)
    # No venv/.venv directory exists under the resolved workdir.

    result = _run(scripts_dir, env)

    assert result.returncode != 0
    assert "no venv python interpreter found" in result.stderr
    assert "bare python3/python" in result.stderr


def test_prefers_workdir_venv_python_when_no_override(tmp_path: Path) -> None:
    """Falls through to $workdir/venv/bin/python when JOB_INTEL_PYTHON unset."""
    hermes_home, scripts_dir = _build_runtime_layout(tmp_path)
    workdir = hermes_home / "hermes-agent"
    venv_bin = workdir / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    _write_stub(venv_bin / "python", 'echo "3.53.4"\nexit 0\n')

    env = _base_env(hermes_home)
    env.pop("JOB_INTEL_PYTHON", None)

    result = _run(scripts_dir, env)

    assert result.returncode == 0, result.stderr
    assert str(venv_bin / "python") in result.stderr


# --- sqlite WAL-reset version guard -----------------------------------------


def test_sqlite_guard_aborts_on_old_version(tmp_path: Path) -> None:
    """An interpreter reporting a vulnerable SQLite must abort the run."""
    hermes_home, scripts_dir = _build_runtime_layout(tmp_path)
    stub = _write_stub(
        tmp_path / "stub_old_python",
        'echo "sqlite 3.45.1 is vulnerable to the WAL-reset bug"\nexit 1\n',
    )
    env = _base_env(hermes_home, stub_python=stub)

    result = _run(scripts_dir, env)

    assert result.returncode != 0
    assert "refusing to run" in result.stderr
    assert "3.45.1" in result.stderr
    assert "did not pass the sqlite safety check" in result.stderr


def test_sqlite_guard_proceeds_on_new_version(tmp_path: Path) -> None:
    """An interpreter reporting a fixed SQLite must be allowed to proceed
    (up to the point of exec'ing the sweep, which we stub out)."""
    hermes_home, scripts_dir = _build_runtime_layout(tmp_path)
    stub = _write_stub(
        tmp_path / "stub_new_python",
        'if [[ "$1" == "-" ]]; then echo "3.53.4"; exit 0; fi\n'
        "cat \"$@\" >/dev/null\n"  # the final exec invocation: swallow it
        'echo "SWEPT cwd=$PWD db=$JOB_INTEL_DB_PATH"\n',
    )
    env = _base_env(hermes_home, stub_python=stub)

    result = _run(scripts_dir, env)

    assert result.returncode == 0, result.stderr
    assert "sqlite 3.53.4 ok" in result.stderr
    assert "SWEPT" in result.stdout


# --- workdir resolution (decoy dir must not shadow the real package) -------


def test_resolves_real_package_over_data_dir(tmp_path: Path) -> None:
    hermes_home, scripts_dir = _build_runtime_layout(tmp_path)
    workdir = hermes_home / "hermes-agent"
    venv_bin = workdir / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    stub = _write_stub(
        venv_bin / "python",
        'if [[ "$1" == "-" ]]; then echo "3.53.4"; exit 0; fi\n'
        'echo "CWD=$PWD"\n',
    )

    env = dict(os.environ)
    env.pop("JOB_INTEL_WORKDIR", None)  # the bug is masked when this is set
    env.pop("JOB_INTEL_PYTHON", None)
    env.update(
        HOME=str(hermes_home.parent),
        HERMES_HOME=str(hermes_home),
        JOB_INTEL_SERVICE_USER=getpass.getuser(),
    )

    result = _run(scripts_dir, env)

    assert result.returncode == 0, result.stderr
    assert f"CWD={workdir}" in result.stdout, (
        f"resolved the wrong workdir\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    del stub  # referenced only to keep the stub alive for clarity
