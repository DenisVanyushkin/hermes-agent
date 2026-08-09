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


@pytest.fixture()
def make_cron_provider():
    """Factory for minimal CronScheduler test doubles.

    ``make_cron_provider(register_job=...)`` returns a real ``CronScheduler``
    subclass instance whose ``register_job`` is the given callable — so tests
    exercising the creation-registration contract share one stub instead of
    redefining inline spy/failing classes, and an ABC rename breaks them
    loudly instead of silently passing a duck-type.
    """
    from cron.scheduler_provider import CronScheduler

    def _make(register_job=None, name="stub"):
        class _StubProvider(CronScheduler):
            @property
            def name(self):  # pragma: no cover - trivial
                return name

            def start(self, stop_event, **kw):  # pragma: no cover - unused
                pass

            def register_job(self, job):
                if register_job is not None:
                    return register_job(job)
                return None

        return _StubProvider()

    return _make


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
def _reset_session_context_vars():
    """Restore session ContextVars around cron tests that call run_job directly.

    Production confines each cron run to a copied context, but direct unit tests
    share the pytest context. ``run_job`` intentionally clears ordinary session
    variables to explicit empty values, which would otherwise shadow legacy env
    fallbacks used by later approval tests in the same process.
    """
    from gateway.session_context import _UNSET, _VAR_MAP

    def _reset_all():
        for var in _VAR_MAP.values():
            var.set(_UNSET)

    _reset_all()
    yield
    _reset_all()
