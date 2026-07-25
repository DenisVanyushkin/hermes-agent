"""Iteration budgets come from one place, and exhaustion has a name.

The number a turn may spend is currently decided in several unrelated places:
HERMES_MAX_ITERATIONS in the gateway (default 90), agent_init's own default 90,
delegation.max_iterations for subagents (50), pipeline_aiagent_executor's 24, and
max_review_iterations: 3 in the engineering pipeline spec. None of them knows
about the others, and none of them knows the role.

This is the single resolver they can all read from. It is deliberately
behaviour-preserving when no role_budgets block exists: absent config, every
caller gets exactly the number it gets today.
"""
import pytest

from agent.iteration_budget import IterationBudget
from agent.turn_context import resolve_iteration_budget


def test_budget_comes_from_role_config():
    budget = resolve_iteration_budget(role="scribe", config={
        "role_budgets": {"scribe": 16, "engineer": 120, "default": 50},
    })
    assert budget.max_total == 16


def test_unknown_role_uses_default():
    budget = resolve_iteration_budget(role="artist", config={
        "role_budgets": {"scribe": 16, "default": 50},
    })
    assert budget.max_total == 50


def test_exhaustion_is_reported_as_turn_reason():
    budget = IterationBudget(1)
    assert budget.consume()
    assert not budget.consume()
    assert budget.exhausted_reason == "budget_exhausted"


def test_a_budget_with_room_left_has_no_exhaustion_reason():
    budget = IterationBudget(2)
    assert budget.consume()
    assert budget.exhausted_reason is None


def test_a_refund_un_exhausts_the_budget():
    """execute_code turns are refunded; a refunded budget is not exhausted."""
    budget = IterationBudget(1)
    budget.consume()
    assert budget.exhausted_reason == "budget_exhausted"
    budget.refund()
    assert budget.exhausted_reason is None


def test_no_role_budgets_block_keeps_todays_number():
    """Absent config must not silently re-cap every turn."""
    budget = resolve_iteration_budget(role="engineer", config={}, fallback=90)
    assert budget.max_total == 90


def test_the_callers_own_number_wins_over_the_built_in_default():
    budget = resolve_iteration_budget(role="engineer", config=None, fallback=24)
    assert budget.max_total == 24


def test_a_nonsense_role_budget_is_ignored_rather_than_obeyed():
    """A typo must not hand a turn a budget of zero."""
    budget = resolve_iteration_budget(
        role="scribe", config={"role_budgets": {"scribe": "sixteen"}}, fallback=90
    )
    assert budget.max_total == 90
