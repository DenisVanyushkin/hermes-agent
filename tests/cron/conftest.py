"""Cron-test fixtures.

Provides a default ``HERMES_MODEL`` for cron run_job tests so each one
doesn't have to spell out a model. The global conftest blanks
HERMES_MODEL hermetically; without this autouse fixture every cron test
that exercises ``run_job`` would hit the fail-fast guard added in
``cron/scheduler.py`` (see issue #23979) and have to be rewritten.

Tests that specifically need ``HERMES_MODEL`` unset — model-resolution
edge cases — call ``monkeypatch.delenv("HERMES_MODEL", raising=False)``
inside the test, which overrides this fixture's value for that scope.
"""

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _default_cron_test_model(monkeypatch):
    """Pin a default HERMES_MODEL so cron run_job tests have a resolvable model."""
    monkeypatch.setenv("HERMES_MODEL", "test-cron-default-model")
    yield


@pytest.fixture(autouse=True)
def _isolate_cron_store(monkeypatch):
    """Point cron storage at this test's HERMES_HOME instead of the real one.

    ``cron/jobs.py`` resolves ``HERMES_DIR``/``CRON_DIR``/``JOBS_FILE`` at
    *import* time, which under pytest happens during collection — before the
    global ``_hermetic_environment`` fixture redirects ``HERMES_HOME``. The env
    var moves; the already-frozen ``JOBS_FILE`` does not, so every test calling
    ``create_job()``/``save_jobs()`` wrote straight into the operator's real
    ``~/.hermes/cron/jobs.json``.

    That was not theoretical: on 2026-07-16 three runs of this suite leaked 21
    live jobs into production, including three ``every 5m`` agent jobs that
    burned OpenRouter credits (HTTP 402) for a day before being found.

    ``_current_cron_store()`` reads these module attributes on every call, so
    rebinding them here is sufficient, and monkeypatch restores them after each
    test. Tests needing a different profile still use ``use_cron_store()``.
    """
    import cron.jobs as jobs_mod

    home = Path(os.environ["HERMES_HOME"]).resolve()
    cron_dir = home / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(jobs_mod, "HERMES_DIR", home)
    monkeypatch.setattr(jobs_mod, "CRON_DIR", cron_dir)
    monkeypatch.setattr(jobs_mod, "JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr(jobs_mod, "OUTPUT_DIR", cron_dir / "output")
    monkeypatch.setattr(
        jobs_mod, "TICKER_HEARTBEAT_FILE", cron_dir / "ticker_heartbeat"
    )
    monkeypatch.setattr(
        jobs_mod, "TICKER_SUCCESS_FILE", cron_dir / "ticker_last_success"
    )
    yield
