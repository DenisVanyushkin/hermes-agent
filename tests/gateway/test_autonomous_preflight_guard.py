"""Guard: autonomous engineering pipeline preflight blocks must fail closed.

When the router selects the engineering pipeline but the controller blocks
before any execution is invoked (e.g. workspace_dirty_baseline), the turn
must terminate with a controlled response instead of falling through to the
normal conversational agent with full tools.
"""
from __future__ import annotations

from types import SimpleNamespace

from gateway.run import GatewayRunner


def _runner() -> GatewayRunner:
    return object.__new__(GatewayRunner)


def _report(*, router_status: str = "selected", blocked_reason: str | None = "workspace_dirty_baseline", invoked: bool = False):
    controller = SimpleNamespace(
        actual_execution_invoked=invoked,
        subagent_execution_invoked=invoked,
        real_provider_bridge_invoked=invoked,
        blocked_reason=blocked_reason,
        final_response_text=None,
    )
    state = SimpleNamespace(router_status=router_status, pipeline_id="engineering_review_pipeline")
    return SimpleNamespace(state=state, pipeline_execution_controller=controller)


def test_preflight_block_terminates_turn_for_selected_pipeline() -> None:
    runner = _runner()
    response = runner._pipeline_autonomous_preflight_block_response(
        _report(),
        orchestrator_mode="autonomous",
    )
    assert response is not None
    assert "workspace_dirty_baseline" in response
    assert "normal_agent_fallback_blocked: true" in response


def test_preflight_guard_skips_when_execution_was_invoked() -> None:
    runner = _runner()
    assert (
        runner._pipeline_autonomous_preflight_block_response(
            _report(invoked=True),
            orchestrator_mode="autonomous",
        )
        is None
    )


def test_preflight_guard_skips_without_blocked_reason() -> None:
    runner = _runner()
    assert (
        runner._pipeline_autonomous_preflight_block_response(
            _report(blocked_reason=None),
            orchestrator_mode="autonomous",
        )
        is None
    )


def test_preflight_guard_skips_routing_failed_and_non_autonomous() -> None:
    runner = _runner()
    assert (
        runner._pipeline_autonomous_preflight_block_response(
            _report(router_status="routing_failed"),
            orchestrator_mode="autonomous",
        )
        is None
    )
    assert (
        runner._pipeline_autonomous_preflight_block_response(
            _report(),
            orchestrator_mode="controlled_manual",
        )
        is None
    )
