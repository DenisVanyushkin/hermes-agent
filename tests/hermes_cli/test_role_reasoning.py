"""Уровень из роль-политики становится полом, который рантайм соблюдает.

select_model_policy() всегда возвращал reasoning_level, и его никто не применял:
каждый ход с политикой "high" уходил к провайдеру на глобальном medium. Здесь —
чистые части починки: таблица, решающая какие уровни политики вообще называют
усилие, и сравнение, которое поднимает конфиг до пола, никогда его не опуская.
"""
import pytest

from hermes_cli.role_reasoning import (
    POLICY_EFFORT_FLOORS,
    effort_label,
    raise_to_floor,
    resolve_role_effort_floor,
)


# ── таблица ──────────────────────────────────────────────────────────────────

def test_high_is_the_only_level_that_names_an_effort():
    assert resolve_role_effort_floor("high") == "high"
    assert POLICY_EFFORT_FLOORS == {"high": "high"}


@pytest.mark.parametrize("level", ["default", "balanced", "stable"])
def test_posture_words_are_not_efforts(level):
    """Это описания посадки, а не уровни. Приписать им effort — выдумка."""
    assert resolve_role_effort_floor(level) is None


def test_unknown_and_empty_levels_have_no_floor():
    assert resolve_role_effort_floor("") is None
    assert resolve_role_effort_floor(None) is None
    assert resolve_role_effort_floor("banana") is None


def test_the_level_is_matched_case_and_space_tolerantly():
    assert resolve_role_effort_floor("  HIGH ") == "high"


# ── сравнение ────────────────────────────────────────────────────────────────

def test_a_lower_config_is_raised():
    assert raise_to_floor({"enabled": True, "effort": "low"}, "high") == {
        "enabled": True,
        "effort": "high",
    }


def test_a_higher_config_is_left_alone():
    """Оператор попросил больше, чем нужно политике — не срезать."""
    assert raise_to_floor({"enabled": True, "effort": "xhigh"}, "high") == {
        "enabled": True,
        "effort": "xhigh",
    }


def test_an_equal_config_is_left_alone():
    current = {"enabled": True, "effort": "high"}
    assert raise_to_floor(current, "high") is current


def test_no_config_at_all_takes_the_floor():
    """None означает «усилие нигде не задано, решает провайдер»."""
    assert raise_to_floor(None, "high") == {"enabled": True, "effort": "high"}


def test_disabled_thinking_stays_disabled():
    """reasoning_effort: false — явное решение оператора, а не отсутствие его."""
    assert raise_to_floor({"enabled": False}, "high") == {"enabled": False}


def test_no_floor_is_a_no_op():
    current = {"enabled": True, "effort": "low"}
    assert raise_to_floor(current, None) is current


def test_an_unknown_floor_is_a_no_op():
    current = {"enabled": True, "effort": "low"}
    assert raise_to_floor(current, "banana") is current


def test_a_config_without_a_usable_level_takes_the_floor():
    assert raise_to_floor({"enabled": True, "effort": ""}, "high") == {
        "enabled": True,
        "effort": "high",
    }


def test_comparison_uses_the_scale_not_the_alphabet():
    """По алфавиту "low" > "high" — наивное сравнение инвертирует пол."""
    assert raise_to_floor({"enabled": True, "effort": "low"}, "medium") == {
        "enabled": True,
        "effort": "medium",
    }
    assert raise_to_floor({"enabled": True, "effort": "max"}, "medium") == {
        "enabled": True,
        "effort": "max",
    }


def test_extra_keys_in_the_config_survive_a_raise():
    raised = raise_to_floor({"enabled": True, "effort": "low", "summary": "auto"}, "high")
    assert raised == {"enabled": True, "effort": "high", "summary": "auto"}


# ── ярлык для лога ───────────────────────────────────────────────────────────

def test_effort_label_reads_the_three_states():
    assert effort_label({"enabled": True, "effort": "high"}) == "high"
    assert effort_label({"enabled": False}) == "off"
    assert effort_label(None) == "-"
