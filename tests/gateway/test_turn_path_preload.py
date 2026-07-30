"""Regression: a deploy under a live gateway must not be able to introduce a
first-time import mid-run. On 2026-07-27 the working tree was replaced 87
minutes after the gateway started; agent/turn_finalizer.py was then imported
for the first time from the NEW source while hermes_cli.review_gate was
still cached from the OLD one, raising ImportError on every turn
finalization for two days."""

from __future__ import annotations

import importlib
import logging
import sys

import pytest

from gateway.run import preload_turn_path_modules

# Modules imported lazily on the turn-completion path. Each one is a place a
# mid-flight deploy could desynchronize sys.modules from disk.
#
# hermes_cli.plugins and hermes_cli.kanban_db are included alongside the
# original two: their call sites in agent/turn_finalizer.py already
# try/except the import, so a desync there doesn't crash a turn — but it
# silently stops transform_llm_output/post_llm_call/on_session_end hooks
# and kanban failure-recording from firing for the rest of the process's
# life, marked only by a logger.warning nobody is watching. Confirmed
# side-effect-free at import time (no plugin discovery, no DB connection,
# no filesystem writes) before adding them to the preload set.
LAZY_TURN_PATH_MODULES = (
    "agent.turn_finalizer",
    "hermes_cli.review_gate",
    "hermes_cli.plugins",
    "hermes_cli.kanban_db",
)


def test_preload_reports_the_turn_path_modules() -> None:
    loaded = preload_turn_path_modules()

    for name in LAZY_TURN_PATH_MODULES:
        assert name in loaded


def test_turn_path_modules_are_in_sys_modules_after_preload() -> None:
    preload_turn_path_modules()

    for name in LAZY_TURN_PATH_MODULES:
        assert name in sys.modules


@pytest.mark.parametrize("exc_type", [ImportError, RuntimeError])
def test_preload_survives_one_module_failing_and_still_loads_the_rest(
    monkeypatch, exc_type
) -> None:
    """A single module's import failure must not raise out of
    preload_turn_path_modules(), and must not prevent the other modules in
    the list from loading — that second property is the whole point of
    calling this at startup: one bad/renamed module can't take the others
    down with it, so the call is safe to make unconditionally.

    Parametrized over ImportError and a non-ImportError: the ``except
    Exception`` in the implementation is deliberately broad (a preload miss
    must never block startup, whatever the failure mode), so this pins
    that breadth rather than assuming only ImportError can occur.
    """
    failing, *surviving = LAZY_TURN_PATH_MODULES
    real_import_module = importlib.import_module

    def _fake_import_module(name, *args, **kwargs):
        if name == failing:
            raise exc_type(f"boom: {name}")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", _fake_import_module)

    loaded = preload_turn_path_modules()

    assert failing not in loaded
    for name in surviving:
        assert name in loaded


def test_preload_logs_a_warning_when_a_module_fails(monkeypatch, caplog) -> None:
    """A preload failure must be visible in the logs, not swallowed
    silently — an invisible failure here reopens the exact exposure this
    task exists to close, just with nobody aware it happened."""
    failing = LAZY_TURN_PATH_MODULES[0]
    real_import_module = importlib.import_module

    def _fake_import_module(name, *args, **kwargs):
        if name == failing:
            raise ImportError(f"boom: {name}")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", _fake_import_module)

    with caplog.at_level(logging.WARNING, logger="gateway.run"):
        preload_turn_path_modules()

    assert any(failing in record.getMessage() for record in caplog.records)
