"""The cron store used by tests can never be the operator's real one.

On 2026-07-16 three runs of this suite leaked 21 live jobs into production
(three ``every 5m`` agent jobs burned OpenRouter credits for a day): cron/jobs.py
froze ``JOBS_FILE`` at import time, before the home was redirected.

Upstream v0.22.5 resolves the store from the active ``HERMES_HOME`` on every
call (``_current_cron_store``) and its conftest refuses any I/O against the real
home, so the fork's per-test rebinding fixture is gone. These tests pin the
property without touching the real file.
"""

from __future__ import annotations

import os
from pathlib import Path

import cron.jobs as jobs_mod
from cron.jobs import create_job


def _real_home() -> Path:
    return (Path(os.path.expanduser("~")) / ".hermes").resolve()


def test_cron_store_resolves_inside_the_per_test_home() -> None:
    store = jobs_mod._current_cron_store()
    home = Path(os.environ["HERMES_HOME"]).resolve()

    assert store.jobs_file.resolve().is_relative_to(home), store.jobs_file
    assert store.cron_dir.resolve().is_relative_to(home)
    assert store.output_dir.resolve().is_relative_to(home)


def test_no_store_path_points_into_the_real_home() -> None:
    store = jobs_mod._current_cron_store()
    for value in (store.jobs_file, jobs_mod.JOBS_FILE, jobs_mod.CRON_DIR):
        assert not Path(value).resolve().is_relative_to(_real_home()), value


def test_a_created_job_lands_in_the_test_store() -> None:
    job = create_job(name="isolation probe", schedule="every 5m", prompt="echo hi")

    jobs_file = jobs_mod._current_cron_store().jobs_file
    assert job["id"] in jobs_file.read_text()
