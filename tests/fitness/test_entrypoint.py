from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "fitness_host_wrapper.sh"
ALERTS_SCRIPT = REPO / "scripts" / "fitness_alerts.sh"


def test_wrapper_exists_and_is_executable():
    assert SCRIPT.exists()
    assert SCRIPT.stat().st_mode & 0o111


def test_wrapper_probes_for_the_package_marker_not_the_directory():
    # ~/.hermes/<pkg>/ как каталог данных делает пакет namespace-пакетом (PEP 420)
    # и затеняет настоящий. Поэтому проверяем маркерный файл, а не каталог.
    text = SCRIPT.read_text(encoding="utf-8")
    assert "fitness/__main__.py" in text
    assert "-d \"$c/fitness\"" not in text
    assert "-d $c/fitness" not in text


def test_wrapper_resolves_repo_from_hermes_home():
    # script-mode cron игнорирует workdir и стартует с cwd=~/.hermes/scripts
    text = SCRIPT.read_text(encoding="utf-8")
    assert "HERMES_HOME" in text
    assert "hermes-agent" in text


def test_wrapper_fails_loudly_when_repo_not_found():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "exit 1" in text


def test_main_dispatches_known_commands():
    from fitness.__main__ import COMMANDS

    assert set(COMMANDS) == {"watch-tick", "digest", "status", "alerts"}


def test_main_rejects_unknown_command(capsys):
    from fitness.__main__ import main

    assert main(["nonsense"]) == 2
    assert "nonsense" in capsys.readouterr().err


# --- поведение команд ------------------------------------------------------

NOW = datetime(2026, 8, 11, 2, 0, tzinfo=timezone.utc)  # 07:00 Алматы


class FakeClient:
    def __init__(self, bookings=None, slots=None):
        self.bookings = bookings or []
        self.slots = slots or []
        self.ranges = []

    def schedule(self, day_from, day_to=None, club_id=None):
        self.ranges.append((day_from, day_to))
        return list(self.slots)

    def my_bookings(self):
        return list(self.bookings)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def _booking(hours_ahead, title="Йога", class_id="c1"):
    from fitness.models import Booking

    return Booking(class_id=class_id, title=title,
                   starts_at=NOW + timedelta(hours=hours_ahead), status="booked")


def test_digest_asks_for_today_and_tomorrow_in_club_dates(home, monkeypatch, capsys):
    import fitness.__main__ as entry

    client = FakeClient(bookings=[_booking(10)])
    monkeypatch.setattr(entry, "_client", lambda: client)
    monkeypatch.setattr(entry, "_now", lambda: NOW)

    entry.cmd_digest()

    assert len(client.ranges) == 1, "диапазон запрашивается одним вызовом"
    day_from, day_to = client.ranges[0]
    assert day_from.isoformat() == "2026-08-11"  # клубная дата, не UTC
    assert day_to.isoformat() == "2026-08-12"
    assert "Йога" in capsys.readouterr().out


def test_digest_prints_nothing_when_there_is_nothing_to_say(home, monkeypatch, capsys):
    import fitness.__main__ as entry

    monkeypatch.setattr(entry, "_client", lambda: FakeClient())
    monkeypatch.setattr(entry, "_now", lambda: NOW)

    entry.cmd_digest()

    assert capsys.readouterr().out == ""


def test_watch_tick_emits_a_reminder_before_the_cancel_deadline(home, monkeypatch, capsys):
    import fitness.__main__ as entry
    from fitness.engine import TickResult

    # дедлайн 2 ч, окно напоминания открывается за 2.5 ч до занятия
    client = FakeClient(bookings=[_booking(2.4)])
    monkeypatch.setattr(entry, "_client", lambda: client)
    monkeypatch.setattr(entry, "_now", lambda: NOW)
    monkeypatch.setattr(entry, "tick", lambda **kw: TickResult())

    entry.cmd_watch_tick()

    assert "Бесплатно отменить" in capsys.readouterr().out


def test_reminder_is_not_repeated_on_the_next_tick(home, monkeypatch, capsys):
    import fitness.__main__ as entry
    from fitness.engine import TickResult

    client = FakeClient(bookings=[_booking(2.4)])
    monkeypatch.setattr(entry, "_client", lambda: client)
    monkeypatch.setattr(entry, "_now", lambda: NOW)
    monkeypatch.setattr(entry, "tick", lambda **kw: TickResult())

    entry.cmd_watch_tick()
    capsys.readouterr()
    entry.cmd_watch_tick()

    assert capsys.readouterr().out == ""


def test_status_reports_a_missing_session(home, capsys):
    import fitness.__main__ as entry

    entry.cmd_status()

    assert "не захвачена" in capsys.readouterr().out


def test_watch_tick_survives_a_dead_session_without_traceback(home, monkeypatch, capsys):
    import fitness.__main__ as entry
    from fitness.engine import TickResult
    from fitness.invictus_client import SessionDead

    class DeadClient:
        def my_bookings(self):
            raise SessionDead("сессия не захвачена")

    monkeypatch.setattr(entry, "_client", lambda: DeadClient())
    monkeypatch.setattr(entry, "_now", lambda: NOW)
    monkeypatch.setattr(entry, "tick", lambda **kw: TickResult())

    assert entry.cmd_watch_tick() == 0


# --- две аудитории: stdout крона = сообщение Амине -------------------------
# cron/scheduler.py при returncode == 0 отдаёт stdout прямо на `deliver`
# джобы, а deliver у fitness-джоб — WhatsApp Амины. Значит любая лишняя
# строка здесь — это сообщение живому человеку.


def test_alerts_entry_script_exists_and_is_executable():
    assert ALERTS_SCRIPT.exists()
    assert ALERTS_SCRIPT.stat().st_mode & 0o111
    assert "alerts" in ALERTS_SCRIPT.read_text(encoding="utf-8")


def test_watch_tick_does_not_print_engineering_text(home, monkeypatch, capsys):
    import fitness.__main__ as entry
    from fitness import alerts
    from fitness.engine import TickResult

    monkeypatch.setattr(entry, "tick", lambda **kw: TickResult(
        messages=["✅ Записал: «HIIT» 21.08 10:00"],
        alerts=["⚠️ Сессия Invictus недействительна — автозапись остановлена."]))
    monkeypatch.setattr(entry, "_emit_reminders", lambda client, now: None)
    monkeypatch.setattr(entry, "_client", lambda: object())
    monkeypatch.setattr(entry, "_now", lambda: NOW)

    assert entry.cmd_watch_tick() == 0
    out = capsys.readouterr().out
    assert "Записал" in out
    assert "Сессия" not in out
    assert alerts.pending(NOW) == [
        "⚠️ Сессия Invictus недействительна — автозапись остановлена."]


def test_alerts_command_prints_and_clears(home, monkeypatch, capsys):
    import fitness.__main__ as entry
    from fitness import alerts

    monkeypatch.setattr(entry, "_now", lambda: NOW)
    alerts.push("⚠️ Сессия Invictus недействительна.", NOW)

    assert entry.cmd_alerts() == 0
    assert "Сессия Invictus" in capsys.readouterr().out
    assert entry.cmd_alerts() == 0
    assert capsys.readouterr().out == ""      # пустая очередь молчит


def test_alerts_command_on_empty_queue_is_silent(home, monkeypatch, capsys):
    import fitness.__main__ as entry

    monkeypatch.setattr(entry, "_now", lambda: NOW)

    assert entry.cmd_alerts() == 0
    assert capsys.readouterr().out == ""


def test_digest_session_error_goes_to_the_queue(home, monkeypatch, capsys):
    import fitness.__main__ as entry
    from fitness import alerts
    from fitness.invictus_client import SessionDead

    class _Dead:
        def schedule(self, *a, **kw):
            raise SessionDead("refresh 401")

        def my_bookings(self):
            raise SessionDead("refresh 401")

    monkeypatch.setattr(entry, "_client", lambda: _Dead())
    monkeypatch.setattr(entry, "_now", lambda: NOW)

    assert entry.cmd_digest() == 0
    assert capsys.readouterr().out == ""       # Амине — ничего
    assert alerts.pending(NOW)                 # оператору — есть


def test_digest_alert_is_not_repeated_every_morning_run(home, monkeypatch, capsys):
    import fitness.__main__ as entry
    from fitness import alerts
    from fitness.invictus_client import SessionDead

    class _Dead:
        def schedule(self, *a, **kw):
            raise SessionDead("refresh 401")

        def my_bookings(self):
            raise SessionDead("refresh 401")

    monkeypatch.setattr(entry, "_client", lambda: _Dead())
    monkeypatch.setattr(entry, "_now", lambda: NOW)

    entry.cmd_digest()
    entry.cmd_digest()

    assert len(alerts.pending(NOW)) == 1
    assert capsys.readouterr().out == ""
