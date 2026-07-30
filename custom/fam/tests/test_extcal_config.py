"""Task 3 (external calendar sync, schema v12): config surface only --
transport/parser/tick/CLI wiring land in later tasks. These tests just
pin the 6 extcal_* CONFIG_DEFAULTS keys, their default values, the
default-merge-doesn't-clobber-prod-keys behavior (pattern 6a, see
test_car_config.py), and that the iCloud app password never becomes a
config key (controller decision #5 -- it's env-only, ICLOUD_APP_PASSWORD).

(Task 6 fix-round 2: `extcal_all_day_as` was removed -- dead config,
all-day -> `plans` is a fixed design decision, not a runtime switch
anything ever read; the key count below dropped from 7 to 6.)"""
from fam import gate


def test_extcal_defaults_merged_into_old_config(tmp_path):
    live = tmp_path / "fam-config.json"
    live.write_text('{"target": "whatsapp:+1", "quiet_start": "21:30", '
                    '"quiet_end": "07:30"}', encoding="utf-8")
    cfg = gate.load_config(config_path=str(live))
    assert cfg["extcal_enabled"] is False
    assert cfg["extcal_username"] == ""
    assert cfg["extcal_read_calendars"] == []
    assert cfg["extcal_write_calendar"] == ""
    assert cfg["extcal_horizon_weeks"] == 8
    assert cfg["extcal_stale_hours"] == 6


def test_extcal_defaults_do_not_clobber_existing_prod_keys(tmp_path):
    # pattern 6a: a prod config that already set some extcal_* keys (e.g.
    # after a manual setup step ahead of the tick module landing) must
    # keep its own values -- default-merge only fills in what's missing.
    live = tmp_path / "fam-config.json"
    live.write_text(
        '{"target": "whatsapp:+1", "quiet_start": "21:30", '
        '"quiet_end": "07:30", "extcal_enabled": true, '
        '"extcal_username": "denis@icloud.com", '
        '"extcal_write_calendar": "https://caldav.icloud.com/x/hermes/"}',
        encoding="utf-8")
    cfg = gate.load_config(config_path=str(live))
    assert cfg["extcal_enabled"] is True
    assert cfg["extcal_username"] == "denis@icloud.com"
    assert cfg["extcal_write_calendar"] == "https://caldav.icloud.com/x/hermes/"
    # sibling keys the prod config never set are still default-merged
    assert cfg["extcal_read_calendars"] == []
    assert cfg["extcal_horizon_weeks"] == 8
    assert cfg["extcal_stale_hours"] == 6
    # and unrelated prod keys from other phases survive untouched
    assert cfg["target"] == "whatsapp:+1"


def test_extcal_config_defaults_shape():
    assert gate.CONFIG_DEFAULTS["extcal_enabled"] is False
    assert gate.CONFIG_DEFAULTS["extcal_username"] == ""
    assert gate.CONFIG_DEFAULTS["extcal_read_calendars"] == []
    assert gate.CONFIG_DEFAULTS["extcal_write_calendar"] == ""
    assert gate.CONFIG_DEFAULTS["extcal_horizon_weeks"] == 8
    assert gate.CONFIG_DEFAULTS["extcal_stale_hours"] == 6
    assert "extcal_all_day_as" not in gate.CONFIG_DEFAULTS  # removed, fix-round 2


def test_extcal_app_password_is_not_a_config_key():
    # controller decision #5: ICLOUD_APP_PASSWORD is read from env only
    # (~/.hermes/.env, chmod 600, same pattern as TOMTOM_API_KEY) -- it
    # must never appear in CONFIG_DEFAULTS or the example config, so it
    # can't leak into fam-config.json, audit rows, or test fixtures.
    assert not any("password" in k.lower() for k in gate.CONFIG_DEFAULTS)


def test_extcal_app_password_not_in_example_config():
    example = gate.CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8")
    assert "password" not in example.lower()
    assert "ICLOUD_APP_PASSWORD" not in example


def test_extcal_example_config_has_all_six_keys():
    import json
    cfg = json.loads(gate.CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8"))
    for key in ("extcal_enabled", "extcal_username", "extcal_read_calendars",
                "extcal_write_calendar", "extcal_horizon_weeks",
                "extcal_stale_hours"):
        assert key in cfg, key
    assert "extcal_all_day_as" not in cfg  # removed, fix-round 2
