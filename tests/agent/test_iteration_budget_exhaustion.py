"""IterationBudget names its own exhaustion with the turn's exit reason."""
from agent.iteration_budget import IterationBudget


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
