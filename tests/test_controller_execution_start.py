"""The controller signals a starting execution before the helper, outside its failure capture."""
from types import SimpleNamespace

import pytest

from hermes_cli import pipeline_execution_controller as controller


def _base():
    return controller.PipelineExecutionControllerResult(
        status="blocked", execution_allowed=False, blocked_reason=None, selected_pipeline_id="engineering_review_pipeline",
        would_call=None, actual_execution_invoked=False, execution_mode="autonomous")


def _invoke(monkeypatch, on_execution_start, order):
    def helper(**_kwargs):
        order.append("helper")
        return {"status": "completed"}
    monkeypatch.setattr(controller.pipeline_execution_helpers, "resolve_pipeline_execution_helper",
                        lambda **_: SimpleNamespace(resolved=True, helper=helper, helper_name="engineer",
                                                    status=None, blocked_reason=None))
    return controller._invoke_resolved_helper(
        base=_base(), config={}, session=SimpleNamespace(pipeline_session_id="ps-7"), state_snapshot=None,
        loaded_specs=None, pipeline_spec=None, pipeline_id="engineering_review_pipeline",
        execution_helper=helper, allow_registered_helper_selection=False, helper_execution_context={},
        on_execution_start=on_execution_start)


def test_the_signal_fires_once_with_the_run_id_before_the_helper(monkeypatch):
    order = []
    _invoke(monkeypatch, lambda run_id: order.append(("start", run_id)), order)
    assert order == [("start", "ps-7"), "helper"]


def test_a_refusal_from_the_signal_stops_the_helper_and_is_not_a_helper_failure(monkeypatch):
    class Busy(RuntimeError):
        pass

    order = []

    def refuse(_run_id):
        raise Busy("chat already has a run")
    with pytest.raises(Busy):
        _invoke(monkeypatch, refuse, order)
    assert order == [], "the helper must not run when the gateway refuses the run"


def test_no_signal_when_the_helper_does_not_resolve(monkeypatch):
    fired = []
    monkeypatch.setattr(controller.pipeline_execution_helpers, "resolve_pipeline_execution_helper",
                        lambda **_: SimpleNamespace(resolved=False, helper=None, helper_name=None,
                                                    status="blocked", blocked_reason="no_helper"))
    result = controller._invoke_resolved_helper(
        base=_base(), config={}, session=SimpleNamespace(pipeline_session_id="ps-7"), state_snapshot=None,
        loaded_specs=None, pipeline_spec=None, pipeline_id="engineering_review_pipeline",
        execution_helper=None, allow_registered_helper_selection=True, helper_execution_context={},
        on_execution_start=fired.append)
    assert fired == [] and result.blocked_reason == "no_helper"
