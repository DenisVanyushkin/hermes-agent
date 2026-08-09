from __future__ import annotations

from datetime import datetime, timedelta, timezone

from job_intel.linkedin_session import CookieRecord
from job_intel.linkedin_readout import build_report, render_report

NOW = datetime(2026, 8, 9, tzinfo=timezone.utc)


def test_report_carries_exit_ip_and_session_state() -> None:
    inventory = [CookieRecord(".linkedin.com", "li_at", NOW + timedelta(days=30), True)]

    report = build_report(exit_ip="203.0.113.7", inventory=inventory, now=NOW)

    assert report["exit_ip"] == "203.0.113.7"
    assert report["session_state"] == "session_ok"
    assert report["cookies"] == [
        {"name": "li_at", "host": ".linkedin.com", "expires_at": "2026-09-08T00:00:00+00:00"}
    ]


def test_unreachable_exit_is_reported_as_such_not_as_empty() -> None:
    """Недостижимый выход обязан отличаться от пустой строки: при поднятом
    fail-closed namespace отсутствие ответа — это нормальный признак упавшего
    туннеля, а не отсутствие данных."""
    report = build_report(exit_ip=None, inventory=[], now=NOW)

    assert report["exit_ip"] is None
    assert report["exit_reachable"] is False


def test_render_names_the_session_state_on_the_first_line() -> None:
    report = build_report(exit_ip="203.0.113.7", inventory=[], now=NOW)

    first_line = render_report(report).splitlines()[0]

    assert "session_missing_cookie" in first_line
    assert "203.0.113.7" in first_line
