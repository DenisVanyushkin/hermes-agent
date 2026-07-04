"""Tests for the recruiter decision-support gateway execution bridge."""

from __future__ import annotations

from types import SimpleNamespace

from hermes_cli.pipeline_execution_controller import evaluate_pipeline_execution_controller
from hermes_cli.pipeline_execution_helpers import (
    RECRUITER_DECISION_HELPER,
    RECRUITER_PIPELINE_ID,
    resolve_pipeline_execution_helper,
)
from hermes_cli.recruiter_decision_execution import (
    build_decision_request_from_message,
    execute_recruiter_decision_support_helper,
)

_PROMPT = "оцени эту вакансию https://hh.ru/vacancy/134606080"


class TestBuildDecisionRequest:
    def test_extracts_vacancy_url(self) -> None:
        request = build_decision_request_from_message(_PROMPT)
        assert request.vacancy_source == {
            "source_type": "vacancy_url",
            "source_id": "https://hh.ru/vacancy/134606080",
            "approved": True,
        }
        assert request.output_mode == "draft_only"
        assert not request.outbound_enabled

    def test_no_url(self) -> None:
        request = build_decision_request_from_message("стоит ли податься в Airwallex?")
        assert request.vacancy_source is None


class TestHelperResolution:
    def test_resolves_recruiter_helper(self) -> None:
        resolution = resolve_pipeline_execution_helper(
            pipeline_id=RECRUITER_PIPELINE_ID,
            allow_registered_helper_selection=True,
        )
        assert resolution.resolved
        assert resolution.helper_name == RECRUITER_DECISION_HELPER

    def test_not_wired_without_selection_flag(self) -> None:
        resolution = resolve_pipeline_execution_helper(
            pipeline_id=RECRUITER_PIPELINE_ID,
            allow_registered_helper_selection=False,
        )
        assert not resolution.resolved
        assert resolution.helper_name == RECRUITER_DECISION_HELPER


class TestExecuteHelper:
    def test_runs_without_provider_and_produces_final_response(self) -> None:
        result = execute_recruiter_decision_support_helper(
            config={"pipelines": {"execution": {"allow_real_provider_execution": False}}},
            user_message=_PROMPT,
        )
        assert result["status"] == "executed"
        assert result["completion_allowed"] is True
        text = result["report"]["final_response"]["text"]
        assert "Decision Support" in text
        assert "manual review" in text.lower()
        for forbidden in ("pipeline", "provider", "subagent", "packet"):
            assert forbidden not in text.lower()

    def test_executor_factory_failure_degrades(self) -> None:
        def broken_factory() -> None:
            raise RuntimeError("no client")

        result = execute_recruiter_decision_support_helper(
            config={"pipelines": {"execution": {"allow_real_provider_execution": True}}},
            user_message=_PROMPT,
            executor_factory=broken_factory,
        )
        assert result["status"] == "executed"
        assert "results are limited" in result["report"]["final_response"]["text"]
        assert result["subagent_runs"] == []


def _autonomous_config() -> dict:
    return {
        "pipelines": {
            "enabled": True,
            "execution": {
                "mode": "autonomous",
                "enable_gateway_execution_controller": True,
                "allow_real_provider_execution": False,
                "allowed_subagents": ["hermes_engineer_core", "hermes_code_reviewer", "general_operator"],
                "allow_pipelines": [RECRUITER_PIPELINE_ID],
            },
        }
    }


class TestControllerRecruiterPath:
    def test_controller_invokes_recruiter_helper(self) -> None:
        session = SimpleNamespace(
            pipeline_id=RECRUITER_PIPELINE_ID,
            pipeline_session_id="ps-1",
            router_status="selected",
        )
        state_snapshot = SimpleNamespace(
            pipeline_id=RECRUITER_PIPELINE_ID,
            pipeline_session_id="ps-1",
            planned_steps=[],
        )
        result = evaluate_pipeline_execution_controller(
            config=_autonomous_config(),
            session=session,
            state_snapshot=state_snapshot,
            allow_test_execution=True,
            allow_registered_helper_selection=True,
            helper_execution_context={
                "runtime_factory": object(),
                "runner": object(),
                "user_message": _PROMPT,
            },
        )
        assert result.resolved_helper_name == RECRUITER_DECISION_HELPER
        assert result.actual_execution_invoked is True
        assert isinstance(result.final_response_text, str)
        assert result.final_response_text.strip()

    def test_controller_blocks_when_operator_not_allowed(self) -> None:
        config = _autonomous_config()
        config["pipelines"]["execution"]["allowed_subagents"] = ["hermes_engineer_core"]
        session = SimpleNamespace(
            pipeline_id=RECRUITER_PIPELINE_ID,
            pipeline_session_id="ps-1",
            router_status="selected",
        )
        state_snapshot = SimpleNamespace(
            pipeline_id=RECRUITER_PIPELINE_ID,
            pipeline_session_id="ps-1",
            planned_steps=[],
        )
        result = evaluate_pipeline_execution_controller(
            config=config,
            session=session,
            state_snapshot=state_snapshot,
            allow_test_execution=True,
            allow_registered_helper_selection=True,
            helper_execution_context={
                "runtime_factory": object(),
                "runner": object(),
                "user_message": _PROMPT,
            },
        )
        assert result.blocked_reason == "required_subagents_not_allowed"
        assert result.actual_execution_invoked is False
