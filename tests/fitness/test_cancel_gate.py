from datetime import datetime, timedelta, timezone

import pytest

import tools.fitness_tool as fitness_tool
from fitness.models import Booking, ClubRules

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, bookings):
        self.bookings = bookings
        self.cancelled = []

    def my_bookings(self):
        return list(self.bookings)

    def cancel(self, class_id):
        self.cancelled.append(class_id)


@pytest.fixture
def patched(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(fitness_tool, "_now", lambda: NOW)
    return monkeypatch


def _booking(hours_ahead):
    return Booking(
        class_id="c1",
        title="Йога",
        starts_at=NOW + timedelta(hours=hours_ahead),
        status="booked",
    )


def test_cancel_before_the_deadline_runs_immediately(patched):
    client = FakeClient([_booking(10)])
    patched.setattr(fitness_tool, "_client", lambda: client)
    patched.setattr(fitness_tool, "load_club_rules", lambda: ClubRules(cancel_deadline_hours=4))

    result = fitness_tool.fitness_cancel(class_id="c1")

    assert client.cancelled == ["c1"]
    assert "отменил" in result.lower()


def test_cancel_after_the_deadline_requires_explicit_confirmation(patched):
    client = FakeClient([_booking(2)])
    patched.setattr(fitness_tool, "_client", lambda: client)
    patched.setattr(
        fitness_tool,
        "load_club_rules",
        lambda: ClubRules(cancel_deadline_hours=4, no_show_penalty="блокировка записи на 3 дня"),
    )

    result = fitness_tool.fitness_cancel(class_id="c1")

    assert client.cancelled == []
    assert "блокировка записи на 3 дня" in result
    assert "confirm_penalty" in result


def test_confirmed_late_cancel_runs(patched):
    client = FakeClient([_booking(2)])
    patched.setattr(fitness_tool, "_client", lambda: client)
    patched.setattr(fitness_tool, "load_club_rules", lambda: ClubRules(cancel_deadline_hours=4))

    fitness_tool.fitness_cancel(class_id="c1", confirm_penalty=True)

    assert client.cancelled == ["c1"]


def test_unknown_deadline_is_treated_as_penalty_fail_closed(patched):
    client = FakeClient([_booking(100)])
    patched.setattr(fitness_tool, "_client", lambda: client)
    patched.setattr(fitness_tool, "load_club_rules", lambda: ClubRules())

    result = fitness_tool.fitness_cancel(class_id="c1")

    assert client.cancelled == []
    assert "неизвест" in result.lower()


def test_cancel_of_unknown_booking_is_reported(patched):
    client = FakeClient([])
    patched.setattr(fitness_tool, "_client", lambda: client)
    patched.setattr(fitness_tool, "load_club_rules", lambda: ClubRules(cancel_deadline_hours=4))

    result = fitness_tool.fitness_cancel(class_id="c1")

    assert client.cancelled == []
    assert "не найдена" in result.lower()


def test_gate_message_states_how_long_is_left(patched):
    client = FakeClient([_booking(2)])
    patched.setattr(fitness_tool, "_client", lambda: client)
    patched.setattr(fitness_tool, "load_club_rules", lambda: ClubRules(cancel_deadline_hours=4))

    result = fitness_tool.fitness_cancel(class_id="c1")

    assert "2.0" in result and "4" in result


def test_cancel_is_registered_as_a_tool():
    assert "fitness_cancel" in fitness_tool.REGISTERED_NAMES
