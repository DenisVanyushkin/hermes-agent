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
    # Realistic traceback.format_exc() shape: a "File ..." line is usually
    # followed by an indented source-context line when source is available.
    error = (
        'Traceback (most recent call last):\n'
        '  File "/home/denis/.hermes/hermes-agent/cron/scheduler.py", line 3599, in run_job\n'
        '      state = load_state(path)\n'
        "OSError: disk quota exceeded"
    )
    message = _summarize_cron_failure_for_delivery(JOB, error)

    assert "File \"" not in message
    assert "line 3599" not in message
    assert "scheduler.py" not in message
    assert "state = load_state(path)" not in message
    assert "disk quota exceeded" in message


def test_traceback_source_context_line_is_dropped() -> None:
    # 2026-07-29 review finding: traceback.format_exc() (the standard
    # capture used in logging) prints the source line under a "File ..."
    # frame when source is available. That line must not survive either.
    error = (
        'Traceback (most recent call last):\n'
        '  File "/home/denis/.hermes/hermes-agent/cron/scheduler.py", line 3599, in run_job\n'
        '      state = load_state(path)\n'
        "FileNotFoundError: [Errno 2] No such file or directory: "
        "'/home/denis/.hermes/state/cron.json'"
    )
    message = _summarize_cron_failure_for_delivery(JOB, error)

    assert "FileNotFoundError" not in message
    assert "state = load_state(path)" not in message
    assert "/home/denis" not in message
    assert "No such file or directory" in message


def test_relative_filesystem_path_is_redacted() -> None:
    # FileNotFoundError conventionally quotes a relative path (no leading
    # slash) — the common shape this must also catch, not just absolute
    # paths.
    message = _summarize_cron_failure_for_delivery(
        JOB,
        "FileNotFoundError: [Errno 2] No such file or directory: "
        "'config/settings.yaml'",
    )

    assert "config/settings.yaml" not in message
    assert "No such file or directory" in message


def test_relative_module_path_is_redacted() -> None:
    message = _summarize_cron_failure_for_delivery(
        JOB, "ModuleNotFoundError: No module named 'hermes_cli/review_gate.py'"
    )

    assert "hermes_cli/review_gate.py" not in message
    assert "No module named" in message


def test_multi_segment_relative_path_is_fully_redacted() -> None:
    # A leading directory name must not survive just because it precedes
    # the first slash the path regex anchors on.
    message = _summarize_cron_failure_for_delivery(
        JOB, "OSError: cannot read cron_output/2026-07-29/run.log"
    )

    assert "cron_output" not in message
    assert "run.log" not in message
    assert "cannot read" in message


def test_prose_with_slashes_is_not_mangled() -> None:
    # Guard against over-redaction: ordinary prose containing slashes must
    # survive intact, even though the path regex was broadened to catch
    # relative paths.
    message = _summarize_cron_failure_for_delivery(
        JOB,
        "the weather provider returned data 5/hour, refreshed 24/7, "
        "sourced from yr.no/Open-Meteo, and/or a cached fallback",
    )

    assert "5/hour" in message
    assert "24/7" in message
    assert "yr.no/Open-Meteo" in message
    assert "and/or" in message


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
