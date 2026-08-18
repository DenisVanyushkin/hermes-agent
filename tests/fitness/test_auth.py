import stat

import pytest

from fitness import auth


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def test_device_file_is_created_with_uuid_and_mode_600(home):
    device = auth.load_or_create_device()
    assert len(device["device_id"]) == 36  # UUID с дефисами
    assert device["device_id"] == device["device_id"].upper()
    assert device["phone_number"] is None
    path = home / "state" / "fitness" / "device.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_device_id_is_reused_on_second_call(home):
    first = auth.load_or_create_device()["device_id"]
    second = auth.load_or_create_device()["device_id"]
    assert first == second


def test_set_phone_number_persists_and_overwrites(home):
    auth.set_phone_number("77011102626")
    assert auth.load_or_create_device()["phone_number"] == "77011102626"
    auth.set_phone_number("77770000000")
    assert auth.load_or_create_device()["phone_number"] == "77770000000"


def test_set_phone_number_keeps_device_id(home):
    device_id = auth.load_or_create_device()["device_id"]
    auth.set_phone_number("77011102626")
    assert auth.load_or_create_device()["device_id"] == device_id


def test_device_headers_has_all_required_keys(home):
    headers = auth.device_headers("F9150314-C260-4F9F-B634-14A2A61BE181")
    assert headers["x-device-id"] == "F9150314-C260-4F9F-B634-14A2A61BE181"
    assert headers["x-platform"] == "ios"
    assert headers["x-app-version"] == "4.2.1"
    assert "Invictus/616" in headers["user-agent"]
    assert "accept" in headers and "accept-language" in headers
