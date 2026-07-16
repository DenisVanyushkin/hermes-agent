"""Workdir resolution for the job-intel shell entrypoints.

Regression coverage for the 2026-07-15 enrichment failure: the runtime copy of
the scripts lives in ``$HERMES_HOME/scripts``, whose parent (``$HERMES_HOME``)
contains a *data* directory named ``job_intel``.  The resolver probed candidates
with ``[[ -d $candidate/job_intel ]]`` -- a name-only check -- so the data
directory shadowed the real package and python failed with::

    No module named job_intel.__main__; 'job_intel' is a package and
    cannot be directly executed
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"

# Both entrypoints share the resolver.  Only the enrichment script is driven
# end-to-end here: job_intel_host_wrapper.sh sources /etc/job-intel/job-intel.env
# and demands a git checkout plus a writable state dir, so an end-to-end run
# would exercise the scaffolding rather than the resolver.  The wrapper's copy
# of the fix is pinned by test_entrypoints_probe_for_package_marker below.
ENTRYPOINTS = ("job_intel_enrichment.sh",)

ALL_ENTRYPOINTS = ("job_intel_enrichment.sh", "job_intel_host_wrapper.sh")


def _build_runtime_layout(tmp_path: Path, entrypoint: str) -> tuple[Path, Path, Path]:
    """Mirror the production layout: runtime scripts copy + decoy data dir."""
    home = tmp_path / "home"
    hermes_home = home / ".hermes"

    # The real package, as synced by the rebase/deploy flow.
    repo = hermes_home / "hermes-agent"
    package = repo / "job_intel"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text("from .cli import main\n")

    # The decoy: $HERMES_HOME/job_intel holds databases, not python.
    decoy = hermes_home / "job_intel"
    decoy.mkdir(parents=True)
    (decoy / "job_intel.sqlite3").write_text("not python")

    # The runtime scripts copy (sync-runtime-scripts.sh drops these here).
    scripts_dir = hermes_home / "scripts"
    scripts_dir.mkdir(parents=True)
    for name in (entrypoint, "job_intel_service_user.sh"):
        shutil.copy(SCRIPTS / name, scripts_dir / name)

    # The stale database copy that must never be targeted again.
    (decoy / "job_intel.sqlite3").write_text("stale copy, frozen 2026-05-30")

    # Stub interpreter: report the cwd and database the entrypoint chose.
    stub = tmp_path / "stub_python"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'CWD=%s\\n' \"$PWD\"\n"
        "printf 'DB=%s\\n' \"$JOB_INTEL_DB_PATH\"\n"
    )
    stub.chmod(0o755)

    return hermes_home, scripts_dir, stub


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
def test_resolves_real_package_over_data_dir(tmp_path: Path, entrypoint: str) -> None:
    """The data dir named job_intel must not shadow the importable package."""
    hermes_home, scripts_dir, stub = _build_runtime_layout(tmp_path, entrypoint)

    env = dict(os.environ)
    env.pop("JOB_INTEL_WORKDIR", None)  # the bug is masked when this is set
    env.update(
        HOME=str(hermes_home.parent),
        HERMES_HOME=str(hermes_home),
        JOB_INTEL_SERVICE_USER=getpass.getuser(),
        JOB_INTEL_BROWSER_PYTHON=str(stub),
        JOB_INTEL_PYTHON=str(stub),
    )

    result = subprocess.run(
        ["bash", str(scripts_dir / entrypoint), "enrichment"],
        capture_output=True,
        text=True,
        # cron/scheduler.py runs script-mode jobs with cwd=<script dir>.
        cwd=str(scripts_dir),
        env=env,
        timeout=60,
    )

    assert "cannot be directly executed" not in result.stderr
    assert result.returncode == 0, result.stderr
    assert f"CWD={hermes_home / 'hermes-agent'}" in result.stdout, (
        f"resolved the wrong workdir\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_db_path_targets_live_state_db(tmp_path: Path) -> None:
    """Enrichment must write to the live state DB, not the stale ~/.hermes copy.

    job_intel_host_wrapper.sh already resolves the DB via JOB_INTEL_STATE_DIR;
    the enrichment script defaulted to $HOME/.hermes/job_intel/job_intel.sqlite3,
    a 753KB copy frozen in May 2026.  Because the workdir bug crashed the script
    before it ever opened the DB, this never surfaced -- fixing only the workdir
    would have turned a loud failure into a silent no-op against a dead DB.
    """
    hermes_home, scripts_dir, stub = _build_runtime_layout(
        tmp_path, "job_intel_enrichment.sh"
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    env = dict(os.environ)
    env.pop("JOB_INTEL_DB_PATH", None)
    env.pop("JOB_INTEL_WORKDIR", None)
    env.update(
        HOME=str(hermes_home.parent),
        HERMES_HOME=str(hermes_home),
        JOB_INTEL_STATE_DIR=str(state_dir),
        JOB_INTEL_SERVICE_USER=getpass.getuser(),
        JOB_INTEL_BROWSER_PYTHON=str(stub),
    )

    result = subprocess.run(
        ["bash", str(scripts_dir / "job_intel_enrichment.sh")],
        capture_output=True,
        text=True,
        cwd=str(scripts_dir),
        env=env,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert f"DB={state_dir / 'job_intel.sqlite3'}" in result.stdout, result.stdout
    assert str(hermes_home / "job_intel" / "job_intel.sqlite3") not in result.stdout


@pytest.mark.parametrize("entrypoint", ALL_ENTRYPOINTS)
def test_entrypoints_probe_for_package_marker(entrypoint: str) -> None:
    """Neither entrypoint may go back to probing by directory name alone."""
    text = (SCRIPTS / entrypoint).read_text()

    assert '[[ -f "$1/job_intel/__main__.py" ]]' in text
    assert "_job_intel_is_package" in text
    assert 'if [[ -d "$candidate/job_intel" ]]; then' not in text
