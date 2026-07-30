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


def test_non_leading_exception_wrapper_is_stripped() -> None:
    # 2026-07-29 review finding: _EXC_WRAPPER_RE was ^-anchored, so a
    # wrapper survives whenever it isn't at character zero. This is a real
    # production shape from cron/scheduler.py's subprocess-failure path
    # (run_job wraps a script's own failure text with "Script execution
    # failed: ").
    message = _summarize_cron_failure_for_delivery(
        JOB, "Script execution failed: PermissionError: [Errno 13] denied"
    )

    assert "PermissionError" not in message
    assert "denied" in message


def test_wrapper_after_stderr_traceback_block_is_stripped() -> None:
    message = _summarize_cron_failure_for_delivery(
        JOB,
        "Script exited with code 1\nstderr:\nsome traceback noise\nValueError: bad thing",
    )

    assert "ValueError" not in message
    assert "bad thing" in message


def test_chained_exception_wrapper_mid_text_is_stripped() -> None:
    # Only the first wrapper was being stripped after lines are joined —
    # a chained exception's second wrapper (after the "During handling of
    # the above exception..." separator) must be stripped too.
    message = _summarize_cron_failure_for_delivery(
        JOB,
        "During handling of the above exception, another exception "
        "occurred:\n\nRuntimeError: second boom",
    )

    assert "RuntimeError" not in message
    assert "second boom" in message


def test_lowercase_exception_name_is_stripped() -> None:
    # socket.gaierror and similar stdlib/C-extension exceptions are
    # conventionally lowercase and were slipping through the
    # capitalized-only alternation.
    message = _summarize_cron_failure_for_delivery(
        JOB, "gaierror: [Errno -2] Name or service not known"
    )

    assert "gaierror" not in message
    assert "Name or service not known" in message


def test_provider_url_survives_redaction() -> None:
    # 2026-07-29 review finding: the failing endpoint is the most useful
    # diagnostic token in an operator alert. _FS_PATH_RE was mangling
    # scheme://host/path into "https:/… " and eating the host.
    message = _summarize_cron_failure_for_delivery(
        JOB, "provider call to https://api.open-meteo.com/v1/forecast returned 500"
    )

    assert "https://api.open-meteo.com/v1/forecast" in message


def test_yrno_provider_url_survives_redaction() -> None:
    message = _summarize_cron_failure_for_delivery(
        JOB, "provider call to https://api.met.no/weatherapi/locationforecast/2.0/ failed"
    )

    assert "https://api.met.no/weatherapi/locationforecast/2.0/" in message


def test_prose_with_colon_and_capitalized_word_is_not_mangled() -> None:
    # Guard against over-redaction from the broadened, non-anchored,
    # case-insensitive wrapper rule: ordinary prose with a colon and a
    # capitalized word — but no Error/Exception/Warning/Interrupt suffix —
    # must survive intact.
    message = _summarize_cron_failure_for_delivery(
        JOB, "Reminder: bring the umbrella tomorrow, Almaty forecast is uncertain"
    )

    assert "Reminder: bring the umbrella tomorrow" in message
    assert "Almaty forecast is uncertain" in message


def test_generic_error_prefix_without_a_class_name_survives() -> None:
    # "Error:" alone (no prefixed identifier) is not a Python exception
    # class name and must not be treated as one.
    message = _summarize_cron_failure_for_delivery(
        JOB, "Error: no data returned for the requested day"
    )

    assert "Error: no data returned for the requested day" in message
