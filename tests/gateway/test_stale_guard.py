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
