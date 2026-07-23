"""Daily session reset must fire on the USER's wall clock, not the host's.

Session timestamps are naive host-clock values (``_now()`` ==
``datetime.now()``), so ``session_reset.at_hour`` used to be interpreted in
the host timezone. On a UTC VM serving an Asia/Almaty user that put the
"nightly" 04:00 reset at 09:00 local -- on top of the morning medication
reminder, wiping the session between the reminder and the user's "done".
"""

import time
from datetime import datetime

import pytest

import hermes_time
from gateway.session import _daily_reset_boundary


@pytest.fixture
def utc_host(monkeypatch):
    """Pin the host clock to UTC, like the hermes-home VM."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()


@pytest.fixture
def almaty_tz(monkeypatch):
    monkeypatch.setenv("HERMES_TIMEZONE", "Asia/Almaty")
    hermes_time.reset_cache()
    yield
    monkeypatch.delenv("HERMES_TIMEZONE", raising=False)
    hermes_time.reset_cache()


def test_boundary_uses_user_timezone_not_host(utc_host, almaty_tz):
    """at_hour=4 means 04:00 Almaty (= 23:00 UTC), not 04:00 UTC."""
    now = datetime(2026, 7, 23, 4, 4)  # 09:04 Almaty, on the UTC host clock
    assert _daily_reset_boundary(now, 4) == datetime(2026, 7, 22, 23, 0)


def test_session_active_this_morning_survives_boundary(utc_host, almaty_tz):
    """A session touched after 04:00 Almaty is NOT past the daily boundary.

    This is the regression: the 09:00 medication reminder and the user's
    09:25 "done" must land in the same session.
    """
    now = datetime(2026, 7, 23, 4, 25)  # 09:25 Almaty -- the "Готово" reply
    updated_at = datetime(2026, 7, 23, 4, 0)  # 09:00 Almaty -- the reminder
    assert updated_at >= _daily_reset_boundary(now, 4)


def test_before_local_boundary_rolls_back_a_day(utc_host, almaty_tz):
    """Local 02:00 (21:00 UTC prev day) is before 04:00 -> yesterday's boundary."""
    now = datetime(2026, 7, 22, 21, 0)  # 02:00 Almaty on the 23rd
    assert _daily_reset_boundary(now, 4) == datetime(2026, 7, 21, 23, 0)


def test_no_configured_timezone_falls_back_to_host_clock(utc_host, monkeypatch):
    monkeypatch.setattr(hermes_time, "get_timezone", lambda: None)
    now = datetime(2026, 7, 23, 4, 4)
    assert _daily_reset_boundary(now, 4) == datetime(2026, 7, 23, 4, 0)


def test_invalid_timezone_does_not_raise(utc_host, monkeypatch):
    monkeypatch.setenv("HERMES_TIMEZONE", "Not/AZone")
    hermes_time.reset_cache()
    try:
        now = datetime(2026, 7, 23, 4, 4)
        assert _daily_reset_boundary(now, 4) == datetime(2026, 7, 23, 4, 0)
    finally:
        monkeypatch.delenv("HERMES_TIMEZONE", raising=False)
        hermes_time.reset_cache()
