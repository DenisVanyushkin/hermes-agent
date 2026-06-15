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
    )
    return report


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
    }
    gateway_logger.info(
        "pipeline_orchestrator_observe %s",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )
