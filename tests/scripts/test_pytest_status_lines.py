from __future__ import annotations

import pytest

from scripts.pytest_status_lines import StatusLine, parse_status_line


SUPPORTED_CASES = [
    pytest.param(
        "PASSED tests/test_status.py::test_plain",
        StatusLine("PASSED", "tests/test_status.py::test_plain", None, "tests/test_status.py::test_plain"),
        id="passed_plain",
    ),
    pytest.param(
        "FAILED tests/test_dash.py::test_case[alpha - old] - RuntimeError: boom",
        StatusLine("FAILED", "tests/test_dash.py::test_case[alpha - old]", "RuntimeError: boom", "tests/test_dash.py::test_case[alpha - old] - RuntimeError: boom"),
        id="failed_parameter_with_dash",
    ),
    pytest.param(
        "SKIPPED [1] tests/test_status.py:12: reason",
        StatusLine("SKIPPED", None, None, "[1] tests/test_status.py:12: reason"),
        id="skipped_summary",
    ),
    pytest.param(
        "XFAIL tests/test_status.py::test_expected - expected",
        StatusLine("XFAIL", "tests/test_status.py::test_expected", "expected", "tests/test_status.py::test_expected - expected"),
        id="xfail",
    ),
    pytest.param(
        "XPASS tests/test_status.py::test_unexpected - unexpected pass",
        StatusLine("XPASS", "tests/test_status.py::test_unexpected", "unexpected pass", "tests/test_status.py::test_unexpected - unexpected pass"),
        id="xpass",
    ),
    pytest.param(
        "RERUN tests/test_status.py::test_flaky - rerun",
        StatusLine("RERUN", "tests/test_status.py::test_flaky", "rerun", "tests/test_status.py::test_flaky - rerun"),
        id="rerun",
    ),
    pytest.param(
        "ERROR tests/test_status.py::test_error - ValueError: boom",
        StatusLine("ERROR", "tests/test_status.py::test_error", "ValueError: boom", "tests/test_status.py::test_error - ValueError: boom"),
        id="error",
    ),
    pytest.param(
        "FAILED tests/test_status.py::test_nested[a[b] - inner] - RuntimeError",
        StatusLine("FAILED", "tests/test_status.py::test_nested[a[b] - inner]", "RuntimeError", "tests/test_status.py::test_nested[a[b] - inner] - RuntimeError"),
        id="nested_brackets",
    ),
    pytest.param(
        "ERROR tests/test_status.py::test_range[range[0:3] - lo] - RuntimeError",
        StatusLine("ERROR", "tests/test_status.py::test_range[range[0:3] - lo]", "RuntimeError", "tests/test_status.py::test_range[range[0:3] - lo] - RuntimeError"),
        id="range_brackets",
    ),
    pytest.param(
        "PASSED tests/test_status.py::test_no_diagnostic[a - b]",
        StatusLine("PASSED", "tests/test_status.py::test_no_diagnostic[a - b]", None, "tests/test_status.py::test_no_diagnostic[a - b]"),
        id="parameter_without_detail",
    ),
    pytest.param(
        "ERROR tests/test_status.py::test_assert[a] - AssertionError: assert [1] == [2]",
        StatusLine("ERROR", "tests/test_status.py::test_assert[a]", "AssertionError: assert [1] == [2]", "tests/test_status.py::test_assert[a] - AssertionError: assert [1] == [2]"),
        id="brackets_in_detail",
    ),
    pytest.param(
        "FAILED tests/test_status.py::test_extra[a]b] - boom",
        StatusLine("FAILED", "tests/test_status.py::test_extra[a]b]", "boom", "tests/test_status.py::test_extra[a]b] - boom"),
        id="unmatched_close_bracket",
    ),
    pytest.param(
        "PASSED tests/test_status.py::test_colon[http://example]",
        StatusLine("PASSED", "tests/test_status.py::test_colon[http://example]", None, "tests/test_status.py::test_colon[http://example]"),
        id="colon_in_payload",
    ),
    pytest.param(
        "ERROR package/test_status.py::TestClass::test_method[param] - setup failed",
        StatusLine("ERROR", "package/test_status.py::TestClass::test_method[param]", "setup failed", "package/test_status.py::TestClass::test_method[param] - setup failed"),
        id="class_method_nodeid",
    ),
]


@pytest.mark.parametrize("line, expected", SUPPORTED_CASES)
def test_supported_status_lines_are_parsed_as_complete_nodes(
    line: str, expected: StatusLine
) -> None:
    assert parse_status_line(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "INFO tests/test_status.py::test_plain",
        "FAILED",
        "",
    ],
)
def test_invalid_status_lines_do_not_match(line: str) -> None:
    assert parse_status_line(line) is None


def test_hyphen_without_spaces_is_part_of_the_nodeid() -> None:
    parsed = parse_status_line("FAILED tests/test_status.py::test_case[alpha]-boom")
    assert parsed is not None
    assert parsed.nodeid == "tests/test_status.py::test_case[alpha]-boom"
    assert parsed.detail is None


def test_unpaired_open_bracket_grows_the_nodeid() -> None:
    parsed = parse_status_line("ERROR tests/test_status.py::test_x[a[b] - boom")
    assert parsed is not None
    assert parsed.nodeid == "tests/test_status.py::test_x[a[b] - boom"
    assert parsed.detail is None


def test_bracket_inside_id_payload_is_grammatically_ambiguous() -> None:
    parsed = parse_status_line("ERROR tests/test_status.py::test_case[value] - inner]")
    assert parsed is not None
    assert parsed.nodeid == "tests/test_status.py::test_case[value]"
    assert parsed.detail == "inner]"


def test_zero_depth_dash_is_not_a_supported_nodeid() -> None:
    parsed = parse_status_line(
        "FAILED tests/test - old.py::test_case - boom"
    )

    assert parsed is not None
    assert parsed.nodeid is None
    assert parsed.detail == "old.py::test_case - boom"
