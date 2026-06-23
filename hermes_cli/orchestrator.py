"""Import-light observe/default orchestrator skeleton for gateway turns."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, replace
from typing import Any

from hermes_cli.config import cfg_get
from hermes_cli.pipeline_execution_controller import evaluate_pipeline_execution_controller
from hermes_cli.pipeline_autonomous_execution import AUTONOMOUS_MODE, build_autonomous_helper_context
from hermes_cli.pipeline_gate import PipelineGateDecision, PipelineGateMode, PipelineGateRequest, evaluate_pipeline_gate
from hermes_cli.pipeline_router import DEFAULT_PIPELINE_ID, RouterDecision
from hermes_cli.pipeline_report import build_pipeline_execution_report
from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
from hermes_cli.pipeline_state import (
    ExecutionReport,
    OrchestratorObserveReport,
    PipelineSession,
    PipelineState,
)
from hermes_cli.pipeline_state_machine import PipelineStateSnapshot, build_pipeline_state_snapshot


logger = logging.getLogger(__name__)

_VALID_ORCHESTRATOR_MODES = {"disabled", "observe", AUTONOMOUS_MODE}
_UNAVAILABLE = "unavailable"
_ENGINEERING_PIPELINE_ID = "engineering_review_pipeline"


def observe_gateway_turn(
    *,
    config: dict[str, Any] | None,
    user_message: str,
    session_id: str | None,
    session_key: str | None = None,
    platform: str | None = None,
    chat_id: str | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
    router_decision: RouterDecision | None = None,
    selected_provider: str | None = None,
    selected_model: str | None = None,
    logger: logging.Logger | None = None,
) -> OrchestratorObserveReport | None:
    del selected_provider, selected_model

    mode = _orchestrator_mode(config)
    if mode == "disabled":
        return None

    started = time.perf_counter()
    gateway_logger = logger or globals()["logger"]
    session = create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=router_decision,
            execution_mode=mode,
            user_message=user_message,
            session_id=session_id,
            session_key=session_key,
            platform=platform,
            chat_id=chat_id,
            thread_id=thread_id,
            user_id=user_id,
        )
    )
    pipeline_session_id = session.pipeline_session_id
    state = _build_pipeline_state(
        session=session,
        config=config,
        router_decision=router_decision,
    )
    if _router_failed_fail_closed(router_decision):
        pipeline_execution_controller = _routing_failed_controller_result(mode=mode)
        pipeline_execution_report_payload = _routing_failed_execution_report_payload(
            session=session,
            router_decision=router_decision,
            execution_mode=mode,
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        execution_report = ExecutionReport(
            pipeline_session_id=pipeline_session_id,
            pipeline_id=state.pipeline_id,
            router_status=state.router_status,
            selected_pipeline_id=state.selected_pipeline_id,
            fallback_pipeline_id=state.fallback_pipeline_id,
            completion_allowed=state.completion_allowed,
            completion_reason="routing_failed_fail_closed",
            executed=False,
            would_execute=False,
            execution_mode=mode,
            runtime_status="not_executed",
            token_usage=getattr(router_decision, "token_usage", None) if router_decision is not None else _UNAVAILABLE,
            cache_usage=getattr(router_decision, "cache_usage", None) if router_decision is not None else _UNAVAILABLE,
            tool_call_summary=[],
            warnings=[],
            elapsed_ms=elapsed_ms,
        )
        report = OrchestratorObserveReport(
            session=session,
            state=state,
            execution_report=execution_report,
            pipeline_execution_controller=pipeline_execution_controller,
        )
        _log_observe_report(
            gateway_logger=gateway_logger,
            report=report,
            router_decision=router_decision,
            orchestrator_mode=mode,
            pipeline_plan_payload=_routing_failed_plan_payload(),
            pipeline_preflight_payload=_routing_failed_preflight_payload(router_decision),
            pipeline_execution_controller_payload=pipeline_execution_controller.to_safe_dict(),
            pipeline_execution_report_payload=pipeline_execution_report_payload,
        )
        return report
    pipeline_plan_payload = _build_pipeline_plan_payload(
        config=config,
        session=session,
    )
    pipeline_preflight = _evaluate_pipeline_gate_safely(
        config=config,
        router_decision=router_decision,
        pipeline_plan_payload=pipeline_plan_payload,
        platform=platform,
        user_message=user_message,
    )
    state_snapshot = _build_state_snapshot(config=config, session=session)
    helper_execution_context = None
    allow_test_execution = False
    allow_registered_helper_selection = False
    if mode == AUTONOMOUS_MODE and pipeline_preflight.allowed:
        helper_execution_context = build_autonomous_helper_context(
            config=config,
            user_message=user_message,
            session_id=session_id,
            pipeline_session_id=pipeline_session_id,
        )
        allow_test_execution = True
        allow_registered_helper_selection = True
    pipeline_execution_controller = evaluate_pipeline_execution_controller(
        config=config,
        session=session,
        state_snapshot=state_snapshot,
        allow_test_execution=allow_test_execution,
        allow_registered_helper_selection=allow_registered_helper_selection,
        helper_execution_context=helper_execution_context,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    execution_report = ExecutionReport(
        pipeline_session_id=pipeline_session_id,
        pipeline_id=state.pipeline_id,
        router_status=state.router_status,
        selected_pipeline_id=state.selected_pipeline_id,
        fallback_pipeline_id=state.fallback_pipeline_id,
        completion_allowed=state.completion_allowed,
        completion_reason="observe_only_default_path",
        executed=pipeline_execution_controller.actual_execution_invoked,
        would_execute=(pipeline_execution_controller.status == "would_execute"),
        execution_mode=mode,
        runtime_status="executed" if pipeline_execution_controller.actual_execution_invoked else "not_observed",
        token_usage=getattr(router_decision, "token_usage", None) if router_decision is not None else _UNAVAILABLE,
        cache_usage=getattr(router_decision, "cache_usage", None) if router_decision is not None else _UNAVAILABLE,
        tool_call_summary=[],
        warnings=[],
        elapsed_ms=elapsed_ms,
    )
    pipeline_execution_report_payload = build_pipeline_execution_report(
        session=session,
        state_snapshot=state_snapshot,
        preflight_result=pipeline_preflight.to_safe_dict(),
    ).to_safe_dict()
    if mode == AUTONOMOUS_MODE:
        helper_report = None
        if isinstance(pipeline_execution_controller.helper_result, dict):
            candidate_report = _helper_runtime_report_payload(pipeline_execution_controller.helper_result)
            if isinstance(candidate_report, dict):
                helper_report = dict(candidate_report)
                helper_report.setdefault("execution_mode", mode)
                controller_payload = helper_report.setdefault("controller", {})
                if isinstance(controller_payload, dict):
                    controller_payload["executed"] = bool(pipeline_execution_controller.actual_execution_invoked)
                    controller_payload["execution_mode"] = mode
        if helper_report is not None:
            pipeline_execution_report_payload = helper_report
        pipeline_execution_controller = replace(pipeline_execution_controller, report_artifacts=None)
    report = OrchestratorObserveReport(
        session=session,
        state=state,
        execution_report=execution_report,
        pipeline_execution_controller=pipeline_execution_controller,
    )
    _log_observe_report(
        gateway_logger=gateway_logger,
        report=report,
        router_decision=router_decision,
        orchestrator_mode=mode,
        pipeline_plan_payload=pipeline_plan_payload,
        pipeline_preflight_payload=pipeline_preflight.to_safe_dict(),
        pipeline_execution_controller_payload=pipeline_execution_controller.to_safe_dict(),
        pipeline_execution_report_payload=pipeline_execution_report_payload,
    )
    return report


def _helper_runtime_report_payload(helper_result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(helper_result, dict):
        return None
    for key in ("report", "execution_report"):
        value = helper_result.get(key)
        if isinstance(value, dict):
            return value
    return None


def _evaluate_pipeline_gate_safely(
    *,
    config: dict[str, Any] | None,
    router_decision: RouterDecision | None,
    pipeline_plan_payload: dict[str, Any],
    platform: str | None,
    user_message: str,
) -> PipelineGateDecision:
    try:
        return evaluate_pipeline_gate(
            PipelineGateRequest(
                config=config,
                router_decision=router_decision,
                pipeline_plan_payload=pipeline_plan_payload,
                platform=platform,
                user_message=user_message,
            )
        )
    except Exception as exc:
        selected_pipeline_id = getattr(router_decision, "selected_pipeline_id", None)
        pipeline_id = selected_pipeline_id or getattr(router_decision, "fallback_pipeline_id", None)
        return PipelineGateDecision(
            allowed=False,
            mode=PipelineGateMode.DISABLED,
            pipeline_id=pipeline_id,
            pipeline_session_id=getattr(router_decision, "pipeline_session_id", None),
            selected_pipeline_id=selected_pipeline_id,
            planned_steps_count=int(pipeline_plan_payload.get("planned_steps_count") or 0),
            reason_code="unknown",
            reason="Pipeline preflight evaluation failed; treating execution as denied.",
            would_execute=False,
            executed=False,
            requirements_met=[],
            requirements_failed=["preflight_evaluation_failed"],
            risk_level="high",
            safe_to_log_payload={
                "mode": PipelineGateMode.DISABLED.value,
                "pipeline_session_id": getattr(router_decision, "pipeline_session_id", None),
                "pipeline_id": pipeline_id,
                "platform": platform,
                "user_message_length": len(user_message or ""),
                "user_message_hash": _hash_user_message(user_message),
                "plan_status": pipeline_plan_payload.get("pipeline_plan_status"),
                "plan_completion_reason": pipeline_plan_payload.get("pipeline_plan_completion_reason"),
                "exception_type": type(exc).__name__,
            },
        )


def _orchestrator_mode(config: dict[str, Any] | None) -> str:
    enabled = bool(cfg_get(config, "pipelines", "enabled", default=False))
    if not enabled:
        return "disabled"
    raw = str(cfg_get(config, "pipelines", "orchestrator", "mode", default="disabled") or "disabled").strip().lower()
    if raw in _VALID_ORCHESTRATOR_MODES:
        return raw
    logger.warning(
        "Invalid pipelines.orchestrator.mode=%r; treating orchestrator observe hook as disabled",
        raw,
    )
    return "disabled"


def _execution_mode(config: dict[str, Any] | None) -> str:
    return str(cfg_get(config, "pipelines", "execution", "mode", default="disabled") or "disabled").strip().lower()


def _build_pipeline_state(
    *,
    session: PipelineSession,
    config: dict[str, Any] | None,
    router_decision: RouterDecision | None = None,
) -> PipelineState:
    if _router_failed_fail_closed(router_decision):
        return PipelineState(
            pipeline_session_id=session.pipeline_session_id,
            pipeline_id=session.pipeline_id,
            state="safe_default_fallback",
            mode=session.mode,
            router_status=session.router_status or _UNAVAILABLE,
            selected_pipeline_id=getattr(router_decision, "selected_pipeline_id", None),
            fallback_pipeline_id=DEFAULT_PIPELINE_ID,
            completion_allowed=False,
            completion_blocked_reason="autonomous_not_selected",
            final_verdict="safe_default_fallback_used",
        )
    snapshot = _build_state_snapshot(config=config, session=session)
    return PipelineState(
        pipeline_session_id=session.pipeline_session_id,
        pipeline_id=session.pipeline_id,
        state=snapshot.state,
        mode=session.mode,
        router_status=session.router_status or _UNAVAILABLE,
        selected_pipeline_id=session.pipeline_id if session.pipeline_id != DEFAULT_PIPELINE_ID else None,
        fallback_pipeline_id=DEFAULT_PIPELINE_ID,
        completion_allowed=snapshot.completion_allowed,
        completion_blocked_reason=snapshot.completion_blocked_reason,
        final_verdict=snapshot.final_verdict,
    )


def _router_failed_fail_closed(router_decision: RouterDecision | None) -> bool:
    if router_decision is None:
        return False
    return (
        str(getattr(router_decision, "status", "") or "").strip().lower() == "routing_failed"
        and not getattr(router_decision, "selected_pipeline_id", None)
        and not bool(getattr(router_decision, "fallback_safe", False))
    )


def _routing_failed_controller_result(*, mode: str):
    from hermes_cli.pipeline_execution_controller import PipelineExecutionControllerResult

    return PipelineExecutionControllerResult(
        status="blocked",
        execution_allowed=False,
        blocked_reason="autonomous_not_selected",
        selected_pipeline_id=None,
        would_call=None,
        actual_execution_invoked=False,
        execution_mode=mode,
        subagent_execution_invoked=False,
        real_provider_bridge_invoked=False,
        resolved_helper_name=None,
        helper_result_status="not_invoked",
        helper_result=None,
        helper_error=None,
        final_response_text=None,
        workspace_basename=None,
        report_artifacts=None,
    )


def _routing_failed_plan_payload() -> dict[str, Any]:
    return {
        "pipeline_plan_status": "not_applicable",
        "pipeline_plan_completion_reason": "safe_default_fallback_used",
        "pipeline_plan_mode": "not_applicable",
        "planned_steps_count": 0,
        "planned_subagent_ids": [],
        "engineer_step_present": False,
        "reviewer_planned": False,
        "reviewer_step_present": False,
        "reviewer_condition": None,
        "pipeline_plan_elapsed_ms": 0.0,
        "runtime_plan_failed": False,
        "pipeline_plan_error": None,
        "pipeline_plan": None,
    }


def _routing_failed_preflight_payload(router_decision: RouterDecision | None) -> dict[str, Any]:
    return {
        "allowed": False,
        "blocked": True,
        "executed": False,
        "would_execute": False,
        "selected_pipeline_id": getattr(router_decision, "selected_pipeline_id", None),
        "pipeline_id": DEFAULT_PIPELINE_ID,
        "pipeline_session_id": getattr(router_decision, "pipeline_session_id", None),
        "planned_steps_count": 0,
        "reason_code": "safe_default_fallback_used",
        "reason": "Router did not safely select an autonomous pipeline; using no-tools default fallback.",
        "requirements_met": [],
        "requirements_failed": ["autonomous_pipeline_not_selected"],
        "risk_level": "medium",
        "mode": PipelineGateMode.DISABLED.value,
        "tools_enabled": False,
        "safe_default_fallback": True,
    }


def _routing_failed_execution_report_payload(
    *,
    session: PipelineSession,
    router_decision: RouterDecision | None,
    execution_mode: str,
) -> dict[str, Any]:
    models_used = []
    providers_used = []
    actual_model = str(getattr(router_decision, "actual_model", "") or "").strip()
    actual_provider = str(getattr(router_decision, "actual_provider", "") or "").strip()
    if actual_model:
        models_used.append(actual_model)
    if actual_provider:
        providers_used.append(actual_provider)
    return {
        "schema_version": "pipeline_execution_report.v1",
        "status": "not_executed",
        "summary": {
            "pipeline_session_id": session.pipeline_session_id,
            "trace_id": session.trace_id,
            "pipeline_id": DEFAULT_PIPELINE_ID,
            "router_status": "routing_failed",
            "router_confidence": session.router_confidence,
            "execution_mode": execution_mode,
            "route_status": "safe_default_fallback_used",
            "selected_subagents": [],
            "blockers": ["autonomous_not_selected"],
            "user_action_required": False,
        },
        "subagents": [],
        "models": [],
        "gate": {
            "preflight_allowed": False,
            "preflight_reason_code": "safe_default_fallback_used",
            "evaluation_statuses": [],
            "review_required": False,
            "escalation_required": False,
            "disagreement_present": False,
            "control_statuses": ["not_invoked"],
            "loop_limit_statuses": [],
            "tools_enabled": False,
        },
        "safety": {
            "executed": False,
            "execution_enabled": False,
            "policy_notes": ["safe_default_fallback_used", "tools_enabled=false", "mutation=none"],
            "secrets_redacted": True,
            "prompts_redacted": True,
            "environment_redacted": True,
        },
        "usage": {
            "input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "total_tokens": None,
            "cache_hit": None,
            "cache_write": None,
            "tool_calls": 0,
            "usage_known": False,
            "token_sources": [],
            "cache_sources": [],
            "planned_subagent_count": 0,
            "executed_subagent_count": 0,
            "subagent_run_instance_count": 0,
            "execution_round_count": 0,
            "subagent_count": 0,
            "models_used": models_used,
            "providers_used": providers_used,
        },
        "completion": {
            "completion_allowed": False,
            "candidate_complete": False,
            "blocked_reason": "autonomous_not_selected",
            "final_verdict": "safe_default_fallback_used",
            "review_required": False,
            "escalation_required": False,
            "disagreement_present": False,
            "user_action_required": False,
        },
        "final_response": {
            "text": None,
            "summary_lines": [
                "status: not_executed",
                "execution_mode: autonomous",
                "final_verdict: safe_default_fallback_used",
                "blocked_reason: autonomous_not_selected",
                "safe_default_fallback_used: true",
                "tools_enabled: false",
                "controller_invoked: false",
                "report_execution_invoked: false",
                "mutation: none",
                "tests: not_run",
            ],
        },
    }


def _hash_user_message(user_message: str) -> str:
    from hermes_cli.pipeline_session import _hash_user_message as _session_hash_user_message

    return _session_hash_user_message(user_message)


def _log_observe_report(
    *,
    gateway_logger: logging.Logger,
    report: OrchestratorObserveReport,
    router_decision: RouterDecision | None,
    orchestrator_mode: str,
    pipeline_plan_payload: dict[str, Any],
    pipeline_preflight_payload: dict[str, Any],
    pipeline_execution_controller_payload: dict[str, Any],
    pipeline_execution_report_payload: dict[str, Any],
) -> None:
    payload = {
        "event": "pipeline_orchestrator_observe_report",
        "pipeline_session_id": report.session.pipeline_session_id,
        "platform": report.session.platform,
        "session_id": report.session.session_id,
        "session_key": report.session.session_key,
        "chat_id": report.session.chat_id,
        "thread_id": report.session.thread_id,
        "user_id": report.session.user_id,
        "router_status": report.state.router_status,
        "router_confidence": getattr(router_decision, "confidence", report.session.router_confidence),
        "routing_confidence_source": getattr(router_decision, "routing_confidence_source", None),
        "router_reasoning_summary": getattr(router_decision, "reasoning_summary", "") or "",
        "selected_pipeline_id": report.state.selected_pipeline_id,
        "fallback_pipeline_id": report.state.fallback_pipeline_id,
        "effective_pipeline_id": report.state.pipeline_id,
        "orchestrator_mode": orchestrator_mode,
        "completion_allowed": report.state.completion_allowed,
        "completion_reason": report.execution_report.completion_reason,
        "elapsed_ms": report.execution_report.elapsed_ms,
        "session": asdict(report.session),
        "state": asdict(report.state),
        "execution_report": asdict(report.execution_report),
        "pipeline_execution_report": pipeline_execution_report_payload,
        "pipeline_preflight": pipeline_preflight_payload,
        "pipeline_execution_controller": pipeline_execution_controller_payload,
    }
    payload.update(pipeline_plan_payload)
    gateway_logger.info(
        "pipeline_orchestrator_observe %s",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )


def _build_pipeline_plan_payload(
    *,
    config: dict[str, Any] | None,
    session: PipelineSession,
) -> dict[str, Any]:
    if not _should_plan_pipeline(config=config, session=session):
        return {
            "pipeline_plan_status": "not_applicable",
            "pipeline_plan_completion_reason": None,
            "pipeline_plan_mode": "not_applicable",
            "planned_steps_count": 0,
            "planned_subagent_ids": [],
            "engineer_step_present": False,
            "reviewer_planned": False,
            "reviewer_step_present": False,
            "reviewer_condition": None,
            "pipeline_plan_elapsed_ms": 0.0,
            "runtime_plan_failed": False,
            "pipeline_plan_error": None,
            "pipeline_plan": None,
        }

    started = time.perf_counter()
    try:
        loaded_specs = _load_pipeline_specs(repo_root=None)
        pipeline_spec = loaded_specs.pipeline_specs[session.pipeline_id]
        snapshot = build_pipeline_state_snapshot(
            session=session,
            pipeline_spec=pipeline_spec,
            execution_mode=AUTONOMOUS_MODE if _orchestrator_mode(config) == AUTONOMOUS_MODE else "observe_plan_only",
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        result = _snapshot_to_plan_payload(snapshot)
        reviewer_step = next((step for step in result["step_records"] if step["step_kind"] == "reviewer"), None)
        engineer_step = next((step for step in result["step_records"] if step["step_kind"] == "engineer"), None)
        return {
            "pipeline_plan_status": result["status"],
            "pipeline_plan_completion_reason": result["completion_reason"],
            "pipeline_plan_mode": result["mode"],
            "planned_steps_count": len(result["step_records"]),
            "planned_subagent_ids": [step["subagent_id"] for step in result["step_records"]],
            "engineer_step_present": engineer_step is not None,
            "reviewer_planned": reviewer_step is not None,
            "reviewer_step_present": reviewer_step is not None,
            "reviewer_condition": reviewer_step["condition"] if reviewer_step else None,
            "pipeline_plan_elapsed_ms": elapsed_ms,
            "runtime_plan_failed": result["status"] == "failed",
            "pipeline_plan_error": _result_error_payload(result),
            "pipeline_plan": result,
        }
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return {
            "pipeline_plan_status": "failed",
            "pipeline_plan_completion_reason": "planning_failed",
            "pipeline_plan_mode": "observe_plan_only",
            "planned_steps_count": 0,
            "planned_subagent_ids": [],
            "engineer_step_present": False,
            "reviewer_planned": False,
            "reviewer_step_present": False,
            "reviewer_condition": None,
            "pipeline_plan_elapsed_ms": elapsed_ms,
            "runtime_plan_failed": True,
            "pipeline_plan_error": {
                "error_type": type(exc).__name__,
                "message": _safe_exception_message(exc),
            },
            "pipeline_plan": None,
        }


def _should_plan_pipeline(
    *,
    config: dict[str, Any] | None,
    session: PipelineSession,
) -> bool:
    if _orchestrator_mode(config) not in {"observe", AUTONOMOUS_MODE}:
        return False
    return session.pipeline_id in {_ENGINEERING_PIPELINE_ID, DEFAULT_PIPELINE_ID}


def _load_pipeline_specs(*, repo_root: Any):
    from hermes_cli.pipeline_specs import load_pipeline_specs

    return load_pipeline_specs(repo_root=repo_root)

def _build_state_snapshot(
    *,
    config: dict[str, Any] | None,
    session: PipelineSession,
) -> PipelineStateSnapshot:
    mode = _orchestrator_mode(config)
    if mode not in {"observe", AUTONOMOUS_MODE}:
        raise ValueError("pipeline state snapshot requested while orchestrator is not in observe or autonomous mode")

    loaded_specs = _load_pipeline_specs(repo_root=None)
    pipeline_spec = loaded_specs.pipeline_specs[session.pipeline_id]
    return build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=pipeline_spec,
        execution_mode=AUTONOMOUS_MODE if mode == AUTONOMOUS_MODE else "observe_plan_only",
    )


def _snapshot_to_plan_payload(snapshot: PipelineStateSnapshot) -> dict[str, Any]:
    step_records = [
        {
            "step_kind": step.step_kind,
            "subagent_id": step.subagent_id,
            "condition": step.condition,
            "execution_status": step.execution_status,
            "planning_mode": step.planning_mode,
            "runtime_factory_plan": runtime_factory_plan,
        }
        for step, runtime_factory_plan in zip(snapshot.planned_steps, snapshot.runtime_factory_plans)
    ]
    return {
        "pipeline_id": snapshot.pipeline_id,
        "pipeline_session_id": snapshot.pipeline_session_id,
        "status": snapshot.status,
        "completion_reason": snapshot.completion_reason,
        "mode": snapshot.execution_mode,
        "transition_path": list(snapshot.transition_path),
        "step_records": step_records,
        "loop_policy": dict(snapshot.loop_policy),
    }


def _result_error_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    if result.get("status") != "failed":
        return None
    return {
        "error_type": "PipelineExecutionResult",
        "error_code": result.get("error_code"),
        "message": result.get("error_message"),
    }


def _safe_exception_message(exc: Exception) -> str:
    message = str(exc).strip()
    return message[:240] if message else type(exc).__name__
