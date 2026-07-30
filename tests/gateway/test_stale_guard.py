"""Конфиг-гейт и бюджет авторестартов (спека 2026-07-30)."""


def test_absent_block_disables_the_feature():
    from gateway.stale_guard import get_stale_guard_config

    assert get_stale_guard_config({}) is None
    assert get_stale_guard_config({"gateway": {}}) is None


def test_enabled_false_disables_the_feature():
    from gateway.stale_guard import get_stale_guard_config

    cfg = {"gateway": {"stale_code_guard": {"enabled": False}}}
    assert get_stale_guard_config(cfg) is None


def test_enabled_true_yields_documented_defaults():
    from gateway.stale_guard import get_stale_guard_config

    cfg = {"gateway": {"stale_code_guard": {"enabled": True}}}
    out = get_stale_guard_config(cfg)

    assert out == {
        "check_every_minutes": 5,
        "idle_timeout_minutes": 10,
        "max_auto_restarts_per_hour": 2,
        "watch_files": [],
    }


def test_explicit_values_override_defaults():
    from gateway.stale_guard import get_stale_guard_config

    cfg = {
        "gateway": {
            "stale_code_guard": {
                "enabled": True,
                "check_every_minutes": 7,
                "idle_timeout_minutes": 3,
                "max_auto_restarts_per_hour": 1,
                "watch_files": ["config/hermes-model-policy.yaml"],
            }
        }
    }
    out = get_stale_guard_config(cfg)

    assert out["check_every_minutes"] == 7
    assert out["idle_timeout_minutes"] == 3
    assert out["max_auto_restarts_per_hour"] == 1
    assert out["watch_files"] == ["config/hermes-model-policy.yaml"]


def test_malformed_values_fall_back_to_defaults():
    from gateway.stale_guard import get_stale_guard_config

    cfg = {
        "gateway": {
            "stale_code_guard": {
                "enabled": True,
                "check_every_minutes": "часто",
                "watch_files": "не список",
            }
        }
    }
    out = get_stale_guard_config(cfg)

    assert out["check_every_minutes"] == 5
    assert out["watch_files"] == []


def test_non_positive_check_every_minutes_falls_back_to_default():
    from gateway.stale_guard import get_stale_guard_config

    cfg_zero = {
        "gateway": {"stale_code_guard": {"enabled": True, "check_every_minutes": 0}}
    }
    cfg_negative = {
        "gateway": {"stale_code_guard": {"enabled": True, "check_every_minutes": -3}}
    }

    assert get_stale_guard_config(cfg_zero)["check_every_minutes"] == 5
    assert get_stale_guard_config(cfg_negative)["check_every_minutes"] == 5


def test_truthy_string_enabled_does_not_arm_the_feature():
    from gateway.stale_guard import get_stale_guard_config

    cfg = {"gateway": {"stale_code_guard": {"enabled": "true"}}}
    assert get_stale_guard_config(cfg) is None


def test_truthy_int_enabled_does_not_arm_the_feature():
    from gateway.stale_guard import get_stale_guard_config

    cfg = {"gateway": {"stale_code_guard": {"enabled": 1}}}
    assert get_stale_guard_config(cfg) is None


def test_real_bool_true_enabled_arms_the_feature():
    from gateway.stale_guard import get_stale_guard_config

    cfg = {"gateway": {"stale_code_guard": {"enabled": True}}}
    assert get_stale_guard_config(cfg) is not None


def test_budget_allows_until_limit_then_blocks(tmp_path):
    from gateway.stale_guard import auto_restart_allowed, record_auto_restart

    now = 1_000_000.0
    assert auto_restart_allowed(tmp_path, now, max_per_hour=2) is True

    record_auto_restart(tmp_path, now)
    assert auto_restart_allowed(tmp_path, now + 60, max_per_hour=2) is True

    record_auto_restart(tmp_path, now + 60)
    assert auto_restart_allowed(tmp_path, now + 120, max_per_hour=2) is False


def test_budget_is_a_rolling_hour(tmp_path):
    from gateway.stale_guard import auto_restart_allowed, record_auto_restart

    now = 1_000_000.0
    record_auto_restart(tmp_path, now)
    record_auto_restart(tmp_path, now + 60)
    assert auto_restart_allowed(tmp_path, now + 120, max_per_hour=2) is False

    # спустя час обе метки вышли из окна
    assert auto_restart_allowed(tmp_path, now + 3700, max_per_hour=2) is True


def test_budget_survives_a_restart(tmp_path):
    """Метки лежат в файле — иначе рестарт обнулял бы собственный бюджет."""
    from gateway.stale_guard import auto_restart_allowed, budget_path, record_auto_restart

    now = 1_000_000.0
    record_auto_restart(tmp_path, now)
    record_auto_restart(tmp_path, now + 10)

    assert budget_path(tmp_path).exists()
    assert auto_restart_allowed(tmp_path, now + 20, max_per_hour=2) is False


def test_corrupt_budget_file_is_treated_as_empty(tmp_path):
    from gateway.stale_guard import auto_restart_allowed, budget_path

    budget_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    budget_path(tmp_path).write_text("не json", encoding="utf-8")

    assert auto_restart_allowed(tmp_path, 1_000_000.0, max_per_hour=2) is True


def test_alert_text_names_the_changed_files():
    from gateway.stale_guard import format_skew_alert

    text = format_skew_alert(
        ["hermes_state.py", "run_agent.py", "a.py", "b.py", "c.py"], "09:27:31"
    )

    assert "hermes_state.py" in text
    assert "09:27:31" in text
    assert "+3" in text  # первые два названы, остальные посчитаны


def test_record_auto_restart_reports_success(tmp_path):
    from gateway.stale_guard import auto_restart_allowed, record_auto_restart

    assert record_auto_restart(tmp_path, 1_000.0) is True
    assert auto_restart_allowed(tmp_path, 1_001.0, 1) is False


def test_record_auto_restart_reports_failure_when_unwritable(tmp_path):
    """I3: потеря бюджета — единственная защита от петли, значит fail closed."""
    import os

    from gateway.stale_guard import budget_writable, record_auto_restart

    home = tmp_path / "ro"
    home.mkdir()
    os.chmod(home, 0o500)
    try:
        assert budget_writable(home) is False
        assert record_auto_restart(home, 1_000.0) is False
    finally:
        os.chmod(home, 0o700)


def test_budget_writable_true_on_a_normal_home(tmp_path):
    from gateway.stale_guard import budget_writable

    assert budget_writable(tmp_path) is True


def test_budget_write_is_atomic(tmp_path, monkeypatch):
    """M7: os.replace — иначе рестарт посреди write_text обнуляет бюджет."""
    import os

    from gateway import stale_guard

    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(
        os, "replace", lambda a, b: replaced.append((a, b)) or real_replace(a, b)
    )

    assert stale_guard.record_auto_restart(tmp_path, 1_000.0) is True
    assert replaced and str(replaced[0][1]) == str(stale_guard.budget_path(tmp_path))
    assert list(stale_guard._read_marks(tmp_path)) == [1_000.0]
    # никакого мусора рядом: временный файл убран за собой
    assert not [f.name for f in tmp_path.iterdir() if f.name.endswith(".tmp")]
