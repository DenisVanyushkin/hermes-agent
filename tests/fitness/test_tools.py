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


def test_module_is_picked_up_by_tool_autodiscovery():
    """Дискавери импортирует файл, только если найдёт ЛИТЕРАЛЬНЫЙ вызов
    registry.register(...) среди statement'ов модуля (_module_registers_tools).

    Обёртка-хелпер прячет вызов внутрь функции — файл молча перестаёт быть
    тулфайлом, и тулсет не появляется ни в одной платформе. Прямой импорт в
    остальных тестах этого не ловит, поэтому проверяем именно дискавери.
    """
    from tools.registry import _module_registers_tools

    path = REPO / "tools" / "fitness_tool.py"
    assert _module_registers_tools(path) is True


def test_autodiscovery_actually_imports_the_module():
    from tools.registry import discover_builtin_tools

    assert "tools.fitness_tool" in discover_builtin_tools()


def test_registry_exposes_the_whole_toolset():
    from tools.registry import registry

    assert EXPECTED <= set(registry.get_tool_names_for_toolset("fitness_booking"))


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


# --- headless-логин --------------------------------------------------------

from datetime import datetime, timezone

from fitness.auth import LoginError, MissingPhoneNumber


def test_login_request_asks_for_number_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class FakeClient:
        def request_otp(self, phone_number=None):
            raise MissingPhoneNumber("нет номера")

    monkeypatch.setattr(fitness_tool, "_client", lambda: FakeClient())
    out = fitness_tool.fitness_login_request()
    assert "номер" in out.lower()


def test_login_request_returns_masked_number(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class FakeClient:
        def request_otp(self, phone_number=None):
            return "77011102626"

    monkeypatch.setattr(fitness_tool, "_client", lambda: FakeClient())
    out = fitness_tool.fitness_login_request(person_name="Амина")
    assert "2626" in out
    assert "Амина" in out
    assert "77011102626" not in out  # номер маскируется


def test_login_confirm_reports_success(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from fitness.session import Session

    class FakeClient:
        def login(self, code):
            return Session(
                access_token="a", refresh_token="r",
                expires_at=datetime(2026, 8, 19, 17, 47, tzinfo=timezone.utc),
                device_headers={"x-device-id": "d"},
            )

    monkeypatch.setattr(fitness_tool, "_client", lambda: FakeClient())
    out = fitness_tool.fitness_login_confirm("9797")
    assert "активна" in out.lower()


def test_login_confirm_reports_bad_code(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class FakeClient:
        def login(self, code):
            raise LoginError("Неверный код")

    monkeypatch.setattr(fitness_tool, "_client", lambda: FakeClient())
    out = fitness_tool.fitness_login_confirm("0000")
    assert "не подошёл" in out.lower()


def test_login_tools_are_registered():
    assert {"fitness_login_request", "fitness_login_confirm"} <= set(
        fitness_tool.REGISTERED_NAMES
    )


def test_login_tools_are_in_role_map():
    assert {"fitness_login_request", "fitness_login_confirm"} <= set(_map_tools())
