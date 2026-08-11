from pathlib import Path

import yaml

import tools.fitness_tool as fitness_tool
from hermes_cli.role_packages import KNOWN_TOOL_CATEGORIES

REPO = Path(__file__).resolve().parents[2]

EXPECTED = {
    "fitness_schedule",
    "fitness_my_bookings",
    "fitness_book",
    "fitness_cancel",
    "fitness_watch_add",
    "fitness_watch_list",
    "fitness_watch_remove",
}


def _map_tools() -> list[str]:
    raw = yaml.safe_load(
        (REPO / "config" / "hermes-role-tool-map.yaml").read_text(encoding="utf-8")
    )
    return raw["categories"]["fitness_booking"]["tools"]


def test_all_tools_are_registered():
    assert EXPECTED <= set(fitness_tool.REGISTERED_NAMES)


def test_toolset_name_is_declared_in_toolsets():
    from toolsets import TOOLSETS

    assert "fitness_booking" in TOOLSETS


def test_toolset_is_configurable():
    from hermes_cli.tools_config import CONFIGURABLE_TOOLSETS

    assert any(row[0] == "fitness_booking" for row in CONFIGURABLE_TOOLSETS)


def test_role_tool_map_lists_every_tool_exactly_once():
    raw = yaml.safe_load(
        (REPO / "config" / "hermes-role-tool-map.yaml").read_text(encoding="utf-8")
    )
    assert EXPECTED <= set(_map_tools())
    all_names = [n for cat in raw["categories"].values() for n in cat.get("tools", [])]
    for name in EXPECTED:
        assert all_names.count(name) == 1, f"{name} встречается в карте больше одного раза"


def test_category_is_known_to_role_packages():
    assert "fitness_booking" in KNOWN_TOOL_CATEGORIES


def test_schedule_tool_returns_rendered_text(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class FakeClient:
        def schedule(self, day_from, day_to=None, club_id=None):
            from datetime import datetime, timezone

            from fitness.models import ClassSlot

            return [
                ClassSlot(
                    class_id="c1", title="Йога", trainer=None, club_id="abay",
                    starts_at=datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc),
                    ends_at=datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc),
                    capacity=10, taken=3, booking_opens_at=None, my_status="none",
                )
            ]

    monkeypatch.setattr(fitness_tool, "_client", lambda: FakeClient())
    out = fitness_tool.fitness_schedule(date="2026-08-11")
    assert "Йога" in out
    assert "19:00" in out


def test_schedule_tool_rejects_a_malformed_date(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert "YYYY-MM-DD" in fitness_tool.fitness_schedule(date="11.08.2026")


def test_my_bookings_surfaces_an_active_ban(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone

    from fitness.invictus_client import BookingsInfo

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)

    class FakeClient:
        def bookings_info(self):
            return BookingsInfo(bookings=[], banned_till=now + timedelta(days=2),
                                ban_reason="Пропуск тренировки")

    monkeypatch.setattr(fitness_tool, "_client", lambda: FakeClient())
    monkeypatch.setattr(fitness_tool, "_now", lambda: now)

    out = fitness_tool.fitness_my_bookings()
    assert "заблокирована" in out.lower()
    assert "Пропуск тренировки" in out


def test_watch_add_creates_a_rule(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from fitness.rules import RuleStore

    fitness_tool.fitness_watch_add(
        title_pattern="функционал", weekday=1, at_time="19:00", kind="recurring"
    )
    rules = RuleStore().load()
    assert len(rules) == 1
    assert rules[0].title_pattern == "функционал"


def test_watch_add_requires_a_weekday_for_recurring_rules(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert "weekday" in fitness_tool.fitness_watch_add(title_pattern="йога")


def test_watch_list_and_remove_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    fitness_tool.fitness_watch_add(
        title_pattern="функционал", weekday=1, at_time="19:00", kind="recurring"
    )
    listed = fitness_tool.fitness_watch_list()
    assert "функционал" in listed
    rule_id = listed.split(":")[0].replace("•", "").strip()
    assert "удалено" in fitness_tool.fitness_watch_remove(rule_id=rule_id)


def test_watch_remove_reports_unknown_rule(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert "не найдено" in fitness_tool.fitness_watch_remove(rule_id="nope").lower()
