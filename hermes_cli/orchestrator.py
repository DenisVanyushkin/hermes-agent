"""Import-light observe/default orchestrator skeleton for gateway turns."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from hermes_cli.config import cfg_get
from hermes_cli.pipeline_gate import PipelineGateDecision, PipelineGateMode, PipelineGateRequest, evaluate_pipeline_gate
from hermes_cli.pipeline_router import DEFAULT_PIPELINE_ID, RouterDecision
from hermes_cli.pipeline_state import (
    ExecutionReport,
    OrchestratorObserveReport,
    PipelineSession,
    PipelineState,
)


logger = logging.getLogger(__name__)

_VALID_ORCHESTRATOR_MODES = {"disabled", "observe"}
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
    actual_provider: str | None = None,
    actual_model: str | None = None,
    logger: logging.Logger | None = None,
) -> OrchestratorObserveReport | None:
    mode = _orchestrator_mode(config)
    if mode != "observe":
        return None

    started = time.perf_counter()
    gateway_logger = logger or globals()["logger"]
    pipeline_session_id = (
        router_decision.pipeline_session_id
        if router_decision is not None and router_decision.pipeline_session_id
        else uuid.uuid4().hex
    )

    session = PipelineSession(
        pipeline_session_id=pipeline_session_id,
        platform=platform,
        session_key=session_key,
        session_id=session_id,
        chat_id=chat_id,
        thread_id=thread_id,
        user_id=user_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        user_message_hash=_hash_user_message(user_message),
        mode=mode,
    )
    state = _build_pipeline_state(
        pipeline_session_id=pipeline_session_id,
        mode=mode,
        router_decision=router_decision,
    )
    pipeline_plan_payload = _build_pipeline_plan_payload(
        config=config,
        user_message=user_message,
        pipeline_session_id=pipeline_session_id,
        router_decision=router_decision,
        selected_provider=selected_provider,
        selected_model=selected_model,
    )
    pipeline_gate = _evaluate_pipeline_gate_safely(
        config=config,
        router_decision=router_decision,
        pipeline_plan_payload=pipeline_plan_payload,
        platform=platform,
        user_message=user_message,
    )
    try:
        pipeline_handoff_payload = _evaluate_pipeline_handoff_safely(
            router_decision=router_decision,
            pipeline_plan_payload=pipeline_plan_payload,
            pipeline_gate=pipeline_gate,
        )
    except Exception as exc:
        pipeline_handoff_payload = _handoff_failure_payload(
            router_decision=router_decision,
            pipeline_gate=pipeline_gate,
            reason_code="handoff_evaluation_failed",
            exception_type=type(exc).__name__,
            elapsed_ms=0.0,
        )
    try:
        pipeline_activation_payload = _evaluate_pipeline_activation_safely(
            config=config,
            router_decision=router_decision,
            pipeline_gate=pipeline_gate,
            pipeline_handoff_payload=pipeline_handoff_payload,
            platform=platform,
        )
    except Exception as exc:
        pipeline_activation_payload = _activation_failure_payload(
            router_decision=router_decision,
            pipeline_gate=pipeline_gate,
            pipeline_handoff_payload=pipeline_handoff_payload,
            reason_code="activation_evaluation_failed",
            exception_type=type(exc).__name__,
            elapsed_ms=0.0,
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
        selected_provider=selected_provider or _UNAVAILABLE,
        selected_model=selected_model or _UNAVAILABLE,
        actual_provider=actual_provider or _UNAVAILABLE,
        actual_model=actual_model or _UNAVAILABLE,
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
    )
    _log_observe_report(
        gateway_logger=gateway_logger,
        report=report,
        orchestrator_mode=mode,
        pipeline_plan_payload=pipeline_plan_payload,
        pipeline_gate_payload=pipeline_gate.to_safe_dict(),
        pipeline_handoff_payload=pipeline_handoff_payload,
        pipeline_activation_payload=pipeline_activation_payload,
    )
    return report


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
        return PipelineGateDecision(
            allowed=False,
            mode=PipelineGateMode.DISABLED,
            pipeline_id=getattr(router_decision, "selected_pipeline_id", None)
            or getattr(router_decision, "fallback_pipeline_id", None),
            pipeline_session_id=getattr(router_decision, "pipeline_session_id", None),
            reason_code="unknown",
            reason="Pipeline gate evaluation failed; treating execution as denied.",
            requirements_met=[],
            requirements_failed=["gate_evaluation_failed"],
            risk_level="high",
            safe_to_log_payload={
                "mode": PipelineGateMode.DISABLED.value,
                "pipeline_session_id": getattr(router_decision, "pipeline_session_id", None),
                "pipeline_id": getattr(router_decision, "selected_pipeline_id", None)
                or getattr(router_decision, "fallback_pipeline_id", None),
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


def _evaluate_pipeline_handoff_safely(
    *,
    router_decision: RouterDecision | None,
    pipeline_plan_payload: dict[str, Any],
    pipeline_gate: PipelineGateDecision,
) -> dict[str, Any]:
    if not _should_plan_engineering_pipeline(config={"pipelines": {"enabled": True, "orchestrator": {"mode": "observe"}}}, router_decision=router_decision):
        pipeline_id = getattr(router_decision, "selected_pipeline_id", None) or getattr(router_decision, "fallback_pipeline_id", None)
        pipeline_session_id = getattr(router_decision, "pipeline_session_id", None)
        return {
            "pipeline_id": pipeline_id,
            "pipeline_session_id": pipeline_session_id,
            "gate_allowed": False,
            "gate_reason_code": "not_applicable",
            "handoff_status": "not_applicable",
            "handoff_reason": "not_applicable",
            "execution_mode": "observe_only",
            "would_execute": False,
            "executed": False,
            "pipeline_executor_status": None,
            "safe_summary": "Pipeline handoff is not applicable for the current observe route.",
            "elapsed_ms": 0.0,
            "error": None,
            "gate_payload": {},
            "pipeline_executor_result": None,
        }

    started = time.perf_counter()
    try:
        handoff_request = _build_pipeline_handoff_request(
            router_decision=router_decision,
            pipeline_gate=pipeline_gate,
        )
        coordinator = _load_pipeline_handoff_coordinator_class()()
        result = coordinator.run(handoff_request)
        payload = result.to_safe_dict()
        payload["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        if payload.get("handoff_status") == "denied":
            payload["handoff_reason"] = payload.get("gate_reason_code") or payload.get("handoff_reason")
        return payload
    except Exception as exc:
        return _handoff_failure_payload(
            router_decision=router_decision,
            pipeline_gate=pipeline_gate,
            reason_code="handoff_evaluation_failed",
            exception_type=type(exc).__name__,
            elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
        )


def _build_pipeline_state(
    *,
    pipeline_session_id: str,
    mode: str,
    router_decision: RouterDecision | None,
) -> PipelineState:
    if router_decision is None:
        return PipelineState(
            pipeline_session_id=pipeline_session_id,
            pipeline_id=DEFAULT_PIPELINE_ID,
            state="response_generation",
            mode=mode,
            router_status=_UNAVAILABLE,
            selected_pipeline_id=DEFAULT_PIPELINE_ID,
            fallback_pipeline_id=DEFAULT_PIPELINE_ID,
            completion_allowed=True,
            completion_blocked_reason=None,
            final_verdict="observe_default_allowed",
        )

    effective_pipeline_id = (
        router_decision.selected_pipeline_id
        or router_decision.fallback_pipeline_id
        or DEFAULT_PIPELINE_ID
    )
    return PipelineState(
        pipeline_session_id=pipeline_session_id,
        pipeline_id=effective_pipeline_id,
        state="response_generation",
        mode=mode,
        router_status=router_decision.status or _UNAVAILABLE,
        selected_pipeline_id=router_decision.selected_pipeline_id,
        fallback_pipeline_id=router_decision.fallback_pipeline_id,
        completion_allowed=True,
        completion_blocked_reason=None,
        final_verdict="observe_only_non_authoritative",
    )


def _hash_user_message(user_message: str) -> str:
    return hashlib.sha256((user_message or "").encode("utf-8")).hexdigest()[:16]


def _log_observe_report(
    *,
    gateway_logger: logging.Logger,
    report: OrchestratorObserveReport,
    orchestrator_mode: str,
    pipeline_plan_payload: dict[str, Any],
    pipeline_gate_payload: dict[str, Any],
    pipeline_handoff_payload: dict[str, Any],
    pipeline_activation_payload: dict[str, Any],
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
        "pipeline_gate": pipeline_gate_payload,
        "pipeline_handoff": pipeline_handoff_payload,
        "pipeline_activation": pipeline_activation_payload,
    }
    payload.update(pipeline_plan_payload)
    gateway_logger.info(
        "pipeline_orchestrator_observe %s",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )


def _build_pipeline_plan_payload(
    *,
    config: dict[str, Any] | None,
    user_message: str,
    pipeline_session_id: str,
    router_decision: RouterDecision | None,
    selected_provider: str | None,
    selected_model: str | None,
) -> dict[str, Any]:
    if not _should_plan_engineering_pipeline(config=config, router_decision=router_decision):
        return {
            "pipeline_plan_status": "not_applicable",
            "pipeline_plan_completion_reason": None,
            "planned_steps_count": 0,
            "planned_subagent_ids": [],
            "reviewer_planned": False,
            "reviewer_condition": None,
            "pipeline_plan_elapsed_ms": 0.0,
            "runtime_plan_failed": False,
            "pipeline_plan_error": None,
            "pipeline_plan": None,
        }

    started = time.perf_counter()
    try:
        loaded_specs = _load_pipeline_specs(repo_root=None)
        runtime_factory_cls = _load_runtime_factory_class()
        executor_cls = _load_pipeline_planning_components()
        subagent_runner_cls = _load_subagent_runner_class()
        request_cls = _load_pipeline_execution_request_class()
        executor = executor_cls(
            runtime_factory=runtime_factory_cls(repo_root=loaded_specs.repo_root),
            engineer_runner=subagent_runner_cls(executor=None),
            reviewer_runner=subagent_runner_cls(executor=None),
        )
        result = executor.execute(
            request_cls(
                loaded_specs=loaded_specs,
                pipeline_session_id=pipeline_session_id,
                task_summary=user_message,
                repo_path=str(loaded_specs.repo_root),
                current_session_provider=selected_provider,
                current_session_model=selected_model,
                mode="plan_only",
            )
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        safe_result = result.to_safe_dict()
        reviewer_step = next((step for step in safe_result["step_records"] if step["step_kind"] == "reviewer"), None)
        return {
            "pipeline_plan_status": safe_result["status"],
            "pipeline_plan_completion_reason": safe_result["completion_reason"],
            "planned_steps_count": len(safe_result["step_records"]),
            "planned_subagent_ids": [step["subagent_id"] for step in safe_result["step_records"]],
            "reviewer_planned": reviewer_step is not None,
            "reviewer_condition": reviewer_step["condition"] if reviewer_step else None,
            "pipeline_plan_elapsed_ms": elapsed_ms,
            "runtime_plan_failed": safe_result["status"] == "failed",
            "pipeline_plan_error": _result_error_payload(safe_result),
            "pipeline_plan": safe_result,
        }
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return {
            "pipeline_plan_status": "failed",
            "pipeline_plan_completion_reason": "planning_failed",
            "planned_steps_count": 0,
            "planned_subagent_ids": [],
            "reviewer_planned": False,
            "reviewer_condition": None,
            "pipeline_plan_elapsed_ms": elapsed_ms,
            "runtime_plan_failed": True,
            "pipeline_plan_error": {
                "error_type": type(exc).__name__,
                "message": _safe_exception_message(exc),
            },
            "pipeline_plan": None,
        }


def _should_plan_engineering_pipeline(
    *,
    config: dict[str, Any] | None,
    router_decision: RouterDecision | None,
) -> bool:
    if _orchestrator_mode(config) != "observe":
        return False
    if router_decision is None:
        return False
    return router_decision.selected_pipeline_id == _ENGINEERING_PIPELINE_ID


def _load_pipeline_specs(*, repo_root: Any):
    from hermes_cli.pipeline_specs import load_pipeline_specs

    return load_pipeline_specs(repo_root=repo_root)


def _load_runtime_factory_class():
    from hermes_cli.runtime_factory import RuntimeFactory

    return RuntimeFactory


def _load_pipeline_planning_components():
    from hermes_cli.pipeline_executor import EngineeringReviewPipelineExecutor

    return EngineeringReviewPipelineExecutor


def _load_subagent_runner_class():
    from hermes_cli.subagent_runner import SubagentRunner

    return SubagentRunner


def _load_pipeline_execution_request_class():
    from hermes_cli.pipeline_executor import PipelineExecutionRequest

    return PipelineExecutionRequest


def _load_pipeline_handoff_coordinator_class():
    from hermes_cli.pipeline_handoff import PipelineHandoffCoordinator

    return PipelineHandoffCoordinator


def _load_pipeline_activation_coordinator_class():
    from hermes_cli.pipeline_activation import PipelineActivationCoordinator

    return PipelineActivationCoordinator


def _load_pipeline_activation_types():
    from hermes_cli.pipeline_activation import PipelineActivationRequest, PipelineActivationResult, PipelineActivationStatus

    return PipelineActivationRequest, PipelineActivationResult, PipelineActivationStatus


def _load_pipeline_handoff_request_class():
    from hermes_cli.pipeline_handoff import PipelineHandoffMode, PipelineHandoffRequest

    return PipelineHandoffMode, PipelineHandoffRequest


def _build_pipeline_handoff_request(
    *,
    router_decision: RouterDecision | None,
    pipeline_gate: PipelineGateDecision,
):
    pipeline_id = getattr(router_decision, "selected_pipeline_id", None) or getattr(router_decision, "fallback_pipeline_id", None)
    pipeline_session_id = getattr(router_decision, "pipeline_session_id", None) or uuid.uuid4().hex
    execution_request_cls = _load_pipeline_execution_request_class()
    pipeline_handoff_mode_cls, pipeline_handoff_request_cls = _load_pipeline_handoff_request_class()
    return pipeline_handoff_request_cls(
        pipeline_id=pipeline_id or DEFAULT_PIPELINE_ID,
        pipeline_session_id=pipeline_session_id,
        router_decision=router_decision,
        gate_decision=pipeline_gate,
        execution_request=execution_request_cls(
            loaded_specs=_load_pipeline_specs(repo_root=None),
            pipeline_session_id=pipeline_session_id,
            task_summary="observe_only_redacted",
            repo_path="observe_only_redacted",
            pipeline_id=pipeline_id or _ENGINEERING_PIPELINE_ID,
            mode="plan_only",
        ),
        mode=pipeline_handoff_mode_cls.OBSERVE_ONLY,
        allow_test_execution=False,
        engineer_executor=None,
        reviewer_executor=None,
    )


def _safe_gate_payload(gate: PipelineGateDecision) -> dict[str, Any]:
    try:
        return gate.to_safe_dict()
    except Exception:
        return {}


def _evaluate_pipeline_activation_safely(
    *,
    config: dict[str, Any] | None,
    router_decision: RouterDecision | None,
    pipeline_gate: PipelineGateDecision,
    pipeline_handoff_payload: dict[str, Any],
    platform: str | None,
) -> dict[str, Any]:
    pipeline_id = getattr(router_decision, "selected_pipeline_id", None) or getattr(router_decision, "fallback_pipeline_id", None)
    pipeline_session_id = getattr(router_decision, "pipeline_session_id", None)
    request_cls, _result_cls, status_cls = _load_pipeline_activation_types()
    coordinator = _load_pipeline_activation_coordinator_class()()

    if pipeline_handoff_payload.get("handoff_status") == "not_applicable":
        return {
            "pipeline_id": pipeline_id,
            "pipeline_session_id": pipeline_session_id,
            "activation_status": status_cls.NOT_APPLICABLE.value,
            "activation_reason": "not_applicable",
            "would_execute": False,
            "executed": False,
            "gate_allowed": False,
            "handoff_would_execute": False,
            "handoff_executed": False,
            "execution_mode": "disabled",
            "requirements_met": [],
            "requirements_failed": ["specialized_pipeline_selected"],
            "error": None,
            "pipeline_executor_status": None,
            "pipeline_executor_result": None,
            "elapsed_ms": 0.0,
        }

    result = coordinator.run(
        request_cls(
            config=config,
            router_decision=router_decision,
            pipeline_id=pipeline_id,
            pipeline_session_id=pipeline_session_id,
            gate_decision=pipeline_gate,
            handoff_decision=_handoff_payload_to_decision(pipeline_handoff_payload),
            executor=None,
            platform=platform,
            platform_allowed=True if platform else None,
            destructive_task=False,
            explicit_approval=False,
            allow_test_execution=False,
        )
    )
    return result.to_safe_dict()


def _handoff_payload_to_decision(payload: dict[str, Any]):
    from hermes_cli.pipeline_handoff import PipelineHandoffDecision, PipelineHandoffError, PipelineHandoffMode, PipelineHandoffStatus

    error_payload = payload.get("error")
    error = None
    if isinstance(error_payload, dict) and error_payload.get("code"):
        error = PipelineHandoffError(
            code=str(error_payload["code"]),
            exception_type=error_payload.get("exception_type"),
        )
    return PipelineHandoffDecision(
        pipeline_id=payload.get("pipeline_id"),
        pipeline_session_id=payload.get("pipeline_session_id"),
        gate_allowed=bool(payload.get("gate_allowed", False)),
        gate_reason_code=str(payload.get("gate_reason_code", "unknown")),
        handoff_status=PipelineHandoffStatus(str(payload.get("handoff_status", "failed"))),
        handoff_reason=str(payload.get("handoff_reason", "unknown")),
        execution_mode=PipelineHandoffMode(str(payload.get("execution_mode", "disabled"))),
        would_execute=bool(payload.get("would_execute", False)),
        executed=bool(payload.get("executed", False)),
        safe_summary=str(payload.get("safe_summary", "")),
        elapsed_ms=float(payload.get("elapsed_ms", 0.0) or 0.0),
        error=error,
        gate_payload=dict(payload.get("gate_payload") or {}),
        pipeline_executor_result=None,
    )


def _activation_failure_payload(
    *,
    router_decision: RouterDecision | None,
    pipeline_gate: PipelineGateDecision,
    pipeline_handoff_payload: dict[str, Any],
    reason_code: str,
    exception_type: str,
    elapsed_ms: float,
) -> dict[str, Any]:
    return {
        "pipeline_id": getattr(router_decision, "selected_pipeline_id", None)
        or getattr(router_decision, "fallback_pipeline_id", None),
        "pipeline_session_id": getattr(router_decision, "pipeline_session_id", None),
        "activation_status": "failed",
        "activation_reason": reason_code,
        "would_execute": False,
        "executed": False,
        "gate_allowed": bool(getattr(pipeline_gate, "allowed", False)),
        "handoff_would_execute": bool(pipeline_handoff_payload.get("would_execute", False)),
        "handoff_executed": bool(pipeline_handoff_payload.get("executed", False)),
        "execution_mode": "observe_only",
        "requirements_met": [],
        "requirements_failed": ["activation_evaluation_failed"],
        "error": {
            "code": reason_code,
            "exception_type": exception_type,
        },
        "pipeline_executor_status": None,
        "pipeline_executor_result": None,
        "elapsed_ms": elapsed_ms,
    }


def _handoff_failure_payload(
    *,
    router_decision: RouterDecision | None,
    pipeline_gate: PipelineGateDecision,
    reason_code: str,
    exception_type: str,
    elapsed_ms: float,
) -> dict[str, Any]:
    return {
        "pipeline_id": getattr(router_decision, "selected_pipeline_id", None)
        or getattr(router_decision, "fallback_pipeline_id", None),
        "pipeline_session_id": getattr(router_decision, "pipeline_session_id", None),
        "gate_allowed": bool(getattr(pipeline_gate, "allowed", False)),
        "gate_reason_code": getattr(pipeline_gate, "reason_code", "unknown"),
        "handoff_status": "failed",
        "handoff_reason": reason_code,
        "execution_mode": "observe_only",
        "would_execute": False,
        "executed": False,
        "pipeline_executor_status": None,
        "safe_summary": "Pipeline handoff evaluation failed closed in observe mode.",
        "elapsed_ms": elapsed_ms,
        "error": {
            "code": reason_code,
            "exception_type": exception_type,
        },
        "gate_payload": _safe_gate_payload(pipeline_gate),
        "pipeline_executor_result": None,
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
