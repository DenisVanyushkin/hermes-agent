"""Regression: a deploy under a live gateway must not be able to introduce a
first-time import mid-run. On 2026-07-27 the working tree was replaced 87
minutes after the gateway started; agent/turn_finalizer.py was then imported
for the first time from the NEW source while hermes_cli.review_gate was
still cached from the OLD one, raising ImportError on every turn
finalization for two days."""

from __future__ import annotations

import sys

from gateway.run import preload_turn_path_modules

# Modules imported lazily on the turn-completion path. Each one is a place a
# mid-flight deploy could desynchronize sys.modules from disk.
LAZY_TURN_PATH_MODULES = (
    "agent.turn_finalizer",
    "hermes_cli.review_gate",
)


def test_preload_reports_the_turn_path_modules() -> None:
    loaded = preload_turn_path_modules()

    for name in LAZY_TURN_PATH_MODULES:
        assert name in loaded


def test_turn_path_modules_are_in_sys_modules_after_preload() -> None:
    preload_turn_path_modules()

    for name in LAZY_TURN_PATH_MODULES:
        assert name in sys.modules


def test_preload_is_idempotent_and_never_raises(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "agent.turn_finalizer", sys.modules.get("agent.turn_finalizer"))

    assert preload_turn_path_modules() == preload_turn_path_modules()
