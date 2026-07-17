"""Cron tests must never write into the operator's real ~/.hermes.

cron/jobs.py resolves HERMES_DIR/CRON_DIR/JOBS_FILE at *import* time, which
under pytest happens during collection — before the global
``_hermetic_environment`` fixture redirects HERMES_HOME. The env var moved; the
already-frozen JOBS_FILE did not.

On 2026-07-16 three runs of tests/cron/ leaked 21 live jobs into the production
jobs.json, including three ``every 5m`` agent jobs that spent a day burning
OpenRouter credits (HTTP 402) until they were found and purged.
"""

from __future__ import annotations

import os
from pathlib import Path

import cron.jobs as jobs_mod
from cron.jobs import create_job


def _real_jobs_file() -> Path:
    """The operator's actual jobs.json — the file that must stay untouched."""
    return Path.home() / ".hermes" / "cron" / "jobs.json"


def test_cron_store_resolves_inside_the_per_test_home() -> None:
    store = jobs_mod._current_cron_store()
    home = Path(os.environ["HERMES_HOME"]).resolve()

    assert store.jobs_file.resolve().is_relative_to(home), (
        f"cron store escaped the test home: {store.jobs_file}"
    )
    assert store.cron_dir.resolve().is_relative_to(home)
    assert store.output_dir.resolve().is_relative_to(home)


def test_module_level_paths_are_redirected() -> None:
    """_current_cron_store() reads these attrs live, so they must be patched."""
    home = Path(os.environ["HERMES_HOME"]).resolve()

    assert Path(jobs_mod.JOBS_FILE).resolve().is_relative_to(home)
    assert Path(jobs_mod.CRON_DIR).resolve().is_relative_to(home)


def test_create_job_does_not_touch_the_real_jobs_file() -> None:
    """The regression itself: creating a job must not reach production."""
    real = _real_jobs_file()
    before = real.read_bytes() if real.exists() else None

    create_job(name="isolation probe", schedule="every 5m", prompt="echo hi")

    after = real.read_bytes() if real.exists() else None
    assert after == before, f"test leaked a job into the real {real}"


def test_created_job_lands_in_the_test_home() -> None:
    """Isolation must not be achieved by silently dropping writes."""
    job = create_job(name="isolation probe", schedule="every 5m", prompt="echo hi")

    stored = jobs_mod.get_job(job["id"])
    assert stored is not None
    assert Path(jobs_mod.JOBS_FILE).exists()
