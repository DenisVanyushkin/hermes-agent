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


# --- Происхождение измерения ---------------------------------------------


def test_report_names_the_namespace_the_probe_ran_in() -> None:
    """exit_ip измеряет выход того процесса, который запустил readout. Запуск
    снаружи namespace даёт адрес хоста, который оператор прочитает как «выход
    браузера» — правдоподобное число вместо ошибки. Число обязано носить своё
    происхождение с собой."""
    report = build_report(exit_ip="203.0.113.7", inventory=[], now=NOW, netns="ln-eg")

    assert report["netns"] == "ln-eg"
    assert report["exit_ip_attributable"] is True


def test_measurement_from_the_host_is_marked_unattributable() -> None:
    report = build_report(exit_ip="203.0.113.7", inventory=[], now=NOW, netns=None)

    assert report["netns"] is None
    assert report["exit_ip_attributable"] is False


def test_render_shows_the_namespace_next_to_the_address() -> None:
    host = render_report(build_report(exit_ip="75.119.154.183", inventory=[], now=NOW, netns=None))
    inside = render_report(build_report(exit_ip="213.211.83.79", inventory=[], now=NOW, netns="ln-eg"))

    assert "netns=host" in host.splitlines()[0]
    assert "netns=ln-eg" in inside.splitlines()[0]


def test_report_names_the_chrome_profile_it_read() -> None:
    """session_missing_cookie, полученное из чужого профиля, — не факт о
    сессии. Отчёт обязан называть каталог, который прочитан."""
    report = build_report(
        exit_ip="203.0.113.7", inventory=[], now=NOW, netns="ln-eg", profile_dir="Profile 1"
    )

    assert report["profile_dir"] == "Profile 1"
    assert "profile=Profile 1" in render_report(report).splitlines()[0]


def test_report_carries_how_the_profile_was_chosen() -> None:
    """`session_missing_cookie`, полученное после того, как профиль с сессией
    не удалось прочитать, — не факт о сессии, а факт о правах доступа."""
    report = build_report(
        exit_ip="203.0.113.7", inventory=[], now=NOW, netns="ln-eg",
        profile_dir="Default", profile_reason="default", unreadable_profiles=("Profile 1",),
    )

    assert report["profile_reason"] == "default"
    assert report["unreadable_profiles"] == ["Profile 1"]
