"""Tests must never write into the operator's real ~/.hermes/logs.

``cli.py`` calls ``setup_logging()`` at *module* scope, so the first test module
that transitively imports it attaches RotatingFileHandlers to the root logger —
resolved via ``get_hermes_home()`` at that moment, which under a plain
``pytest tests/...`` invocation is the real home. ``setup_logging()`` then
latches on ``_logging_initialized``, so the handlers stick for the whole session
and every later test logs into production.

On 2026-07-16 that noise — ``poison``, ``RuntimeError: boom``, ``Too late``,
``exfil``, ``../../../etc/passwd``, MagicMock thread ids — was ingested by the
nightly diagnostics collector and reported as production incidents.
"""

from __future__ import annotations

import logging
from pathlib import Path


def _real_logs_dir() -> Path:
    return (Path.home() / ".hermes" / "logs").resolve()


def _all_live_handlers() -> list[logging.Handler]:
    """Every handler that can reach a file, including queue-hidden ones.

    hermes_logging routes records through a QueueHandler on the root logger and
    keeps the real RotatingFileHandlers on a QueueListener thread. Walking
    ``logger.handlers`` alone therefore sees a QueueHandler with no
    ``baseFilename`` and reports a clean bill of health while records are being
    written to disk behind it.
    """
    handlers: list[logging.Handler] = []
    manager = logging.Logger.manager
    loggers: list[logging.Logger] = [logging.getLogger()]
    loggers += [
        obj for obj in manager.loggerDict.values() if isinstance(obj, logging.Logger)
    ]
    for logger in loggers:
        handlers.extend(getattr(logger, "handlers", []))

    import hermes_logging

    listener = getattr(hermes_logging, "_queue_listener", None)
    if listener is not None:
        handlers.extend(getattr(listener, "handlers", ()))
    return handlers


def _handlers_pointing_at(directory: Path) -> list[str]:
    """Every live file handler writing anywhere under *directory*."""
    offenders: list[str] = []
    for handler in _all_live_handlers():
        base = getattr(handler, "baseFilename", None)
        if not base:
            continue
        try:
            resolved = Path(base).resolve()
        except OSError:  # pragma: no cover - defensive
            continue
        if resolved.is_relative_to(directory):
            offenders.append(str(resolved))
    return sorted(set(offenders))


def test_no_handler_writes_to_the_real_hermes_logs() -> None:
    offenders = _handlers_pointing_at(_real_logs_dir())
    assert not offenders, f"log handlers escaped to the real home: {offenders}"


def test_importing_cli_does_not_attach_real_log_handlers() -> None:
    """cli.py configures logging on import — the exact regression."""
    import cli  # noqa: F401

    offenders = _handlers_pointing_at(_real_logs_dir())
    assert not offenders, f"importing cli attached real handlers: {offenders}"


def test_emitting_a_warning_does_not_touch_the_real_errors_log() -> None:
    """The leak as the operator experiences it: bytes appended to errors.log."""
    import cli  # noqa: F401  (ensure logging is configured however it will be)

    real_errors = _real_logs_dir() / "errors.log"
    before = real_errors.stat().st_size if real_errors.exists() else None

    logging.getLogger("tests.logging_isolation").warning(
        "isolation probe — must not reach production errors.log"
    )
    # Records go through a queue; drain it before measuring or the write may
    # simply not have happened yet.
    import hermes_logging

    try:
        hermes_logging.drain_log_queue(timeout=2.0)
    except Exception:  # pragma: no cover - defensive
        pass
    for handler in _all_live_handlers():
        try:
            handler.flush()
        except Exception:  # pragma: no cover - defensive
            pass

    after = real_errors.stat().st_size if real_errors.exists() else None
    assert after == before, f"test logging leaked into {real_errors}"
