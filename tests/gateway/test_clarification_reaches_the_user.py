"""The router's clarifying question has to be asked, not filed.

The router writes a clarification_question (pipeline_router.py:503),
pipeline_observe stores it in an observation payload, and nothing reads it. The
turn falls through to the default conversation pipeline and the question is never
put to the operator. Both live occurrences were the same shape: "выполни
изменение по этому плану" where the plan itself is not in the message -- exactly
the case where guessing is the wrong move.

Small in volume (2 runs in 97) and deliberately narrow in scope: it fires only
when the router itself said it needs clarification, and only when it actually
wrote a question.
"""
from types import SimpleNamespace

import pytest


def _report(status: str, question: str | None):
    return SimpleNamespace(
        state=SimpleNamespace(router_status=status),
        session=SimpleNamespace(clarification_question=question),
    )


@pytest.fixture
def responder():
    from gateway.run import GatewayRunner

    return GatewayRunner.__dict__["_pipeline_clarification_response"]


def test_a_needs_clarification_turn_asks_the_question(responder):
    out = responder(
        None,
        _report("needs_clarification", "Нужен read-only разбор или подготовить патч?"),
        orchestrator_mode="autonomous",
    )
    assert out is not None
    assert "Нужен read-only разбор или подготовить патч?" in out


def test_a_selected_route_is_left_alone(responder):
    assert responder(
        None, _report("selected", "irrelevant"), orchestrator_mode="autonomous"
    ) is None


def test_no_question_means_no_short_circuit(responder):
    """Stopping a turn with nothing to ask would be strictly worse than proceeding."""
    assert responder(
        None, _report("needs_clarification", None), orchestrator_mode="autonomous"
    ) is None
    assert responder(
        None, _report("needs_clarification", "   "), orchestrator_mode="autonomous"
    ) is None


def test_a_missing_report_is_not_an_error(responder):
    assert responder(None, None, orchestrator_mode="autonomous") is None


def test_the_reply_says_nothing_was_done(responder):
    """The operator must be able to tell a question from a refusal to work."""
    out = responder(
        None, _report("needs_clarification", "Уточните объём"), orchestrator_mode="autonomous"
    )
    assert "not_executed" in out
    assert "needs_clarification" in out
