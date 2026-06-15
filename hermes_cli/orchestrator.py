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
    logger: logging.Logger | None = None,
) -> OrchestratorObserveReport | None:
    del selected_provider, selected_model

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
        selected_provider=None,
        selected_model=None,
    )
    pipeline_preflight = _evaluate_pipeline_gate_safely(
        config=config,
        router_decision=router_decision,
        pipeline_plan_payload=pipeline_plan_payload,
        platform=platform,
        user_message=user_message,
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
        executed=False,
        would_execute=False,
        execution_mode="observe",
        runtime_status="not_observed",
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
        pipeline_preflight_payload=pipeline_preflight.to_safe_dict(),
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
    pipeline_preflight_payload: dict[str, Any],
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
        "pipeline_preflight": pipeline_preflight_payload,
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
    del user_message, selected_provider, selected_model

    if not _should_plan_engineering_pipeline(config=config, router_decision=router_decision):
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
        result = _build_lightweight_engineering_plan_summary(
            loaded_specs=loaded_specs,
            pipeline_session_id=pipeline_session_id,
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
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

def _build_lightweight_engineering_plan_summary(
    *,
    loaded_specs: Any,
    pipeline_session_id: str,
) -> dict[str, Any]:
    pipeline_spec = loaded_specs.pipeline_specs[_ENGINEERING_PIPELINE_ID]
    subagents = pipeline_spec.get("subagents") or {}
    engineer_id = str(subagents.get("engineer") or "")
    reviewer_id = str(subagents.get("reviewer") or "")
    if not engineer_id or not reviewer_id:
        raise ValueError("engineering pipeline spec is missing engineer/reviewer subagent ids")

    step_records = [
        {
            "step_kind": "engineer",
            "subagent_id": engineer_id,
            "condition": None,
            "execution_status": "planned",
            "planning_mode": "metadata_only",
        },
        {
            "step_kind": "reviewer",
            "subagent_id": reviewer_id,
            "condition": "code_changes_require_review",
            "execution_status": "planned",
            "planning_mode": "metadata_only",
        },
    ]
    return {
        "pipeline_id": _ENGINEERING_PIPELINE_ID,
        "pipeline_session_id": pipeline_session_id,
        "status": "planned",
        "completion_reason": "plan_only",
        "mode": "observe_plan_only",
        "step_records": step_records,
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
