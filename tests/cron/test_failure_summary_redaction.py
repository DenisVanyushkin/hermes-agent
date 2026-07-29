"""Regression: the cron failure summary delivered to a chat must never carry
an exception class name or a filesystem path (2026-07-28/29: an ImportError
naming /home/denis/.hermes/hermes-agent/hermes_cli/review_gate.py was
delivered verbatim to a non-technical recipient's WhatsApp)."""

from __future__ import annotations

from cron.scheduler import _summarize_cron_failure_for_delivery

JOB = {"name": "Вечерний короткий прогноз погоды Алматы — Амина", "id": "150d115fe905"}

IMPORT_ERROR = (
    "ImportError: cannot import name 'ReviewGateState' from "
    "'hermes_cli.review_gate' "
    "(/home/denis/.hermes/hermes-agent/hermes_cli/review_gate.py)"
)


def test_exception_class_name_is_stripped() -> None:
    message = _summarize_cron_failure_for_delivery(JOB, IMPORT_ERROR)

    assert "ImportError" not in message
    assert "cannot import name" in message


def test_filesystem_paths_are_redacted() -> None:
    message = _summarize_cron_failure_for_delivery(JOB, IMPORT_ERROR)

    assert "/home/denis" not in message
    assert ".hermes" not in message
    assert "review_gate.py" not in message


def test_chained_exception_wrappers_are_stripped() -> None:
    message = _summarize_cron_failure_for_delivery(
        JOB, "RuntimeError: ValueError: upstream feed returned nothing"
    )

    assert "RuntimeError" not in message
    assert "ValueError" not in message
    assert "upstream feed returned nothing" in message


def test_stack_frame_lines_are_dropped() -> None:
    error = (
        'Traceback (most recent call last):\n'
        '  File "/home/denis/.hermes/hermes-agent/cron/scheduler.py", line 3599, in run_job\n'
        "OSError: disk quota exceeded"
    )
    message = _summarize_cron_failure_for_delivery(JOB, error)

    assert "File \"" not in message
    assert "line 3599" not in message
    assert "scheduler.py" not in message
    assert "disk quota exceeded" in message


def test_windows_style_paths_are_redacted() -> None:
    message = _summarize_cron_failure_for_delivery(
        JOB, r"OSError: cannot open C:\Users\denis\hermes\state.db"
    )

    assert "C:" not in message
    assert "state.db" not in message


def test_short_prose_error_survives_intact() -> None:
    message = _summarize_cron_failure_for_delivery(
        JOB, "the weather provider returned no data for tomorrow"
    )

    assert "the weather provider returned no data for tomorrow" in message
