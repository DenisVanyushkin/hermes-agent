"""Observe-mode gateway hook for the Hermes pipeline router."""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from hermes_cli.config import cfg_get
from hermes_cli.pipeline_router import HeuristicPipelineRouter, RouterDecision
from hermes_cli.pipeline_specs import load_pipeline_specs


logger = logging.getLogger(__name__)

_VALID_ROUTER_MODES = {"disabled", "observe"}


def observe_pipeline_router_decision(
    *,
    config: dict[str, Any] | None,
    user_message: str,
    session_id: str | None,
    session_key: str | None = None,
    platform: str | None = None,
    chat_id: str | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
    selected_provider: str | None = None,
    selected_model: str | None = None,
    actual_provider: str | None = None,
    actual_model: str | None = None,
    repo_root: Path | str | None = None,
    logger: logging.Logger | None = None,
) -> RouterDecision | None:
    log = logger or globals()["logger"]
    mode = _pipeline_router_mode(config)
    if mode != "observe":
        return None

    started = time.perf_counter()
    pipeline_session_id = uuid.uuid4().hex

    try:
        loaded_specs = load_pipeline_specs(repo_root=repo_root)
        router = HeuristicPipelineRouter(loaded_specs=loaded_specs, repo_root=repo_root)
        decision = router.route(
            user_message,
            pipeline_session_id=pipeline_session_id,
        )
        if actual_provider is not None:
            decision = _replace_decision(
                decision,
                actual_provider=actual_provider,
                actual_model=actual_model,
            )
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        log.info(
            "pipeline_router_observe %s",
            json.dumps(
                {
                    "event": "pipeline_router_observe_decision",
                    "pipeline_session_id": decision.pipeline_session_id,
                    "status": decision.status,
                    "selected_pipeline_id": decision.selected_pipeline_id,
                    "fallback_pipeline_id": decision.fallback_pipeline_id,
                    "confidence": decision.confidence,
                    "fallback_safe": decision.fallback_safe,
                    "requires_clarification": decision.requires_clarification,
                    "policy_block_reason": decision.policy_block_reason,
                    "routing_failure_reason": decision.routing_failure_reason,
                    "selected_provider": decision.selected_provider or selected_provider,
                    "selected_model": decision.selected_model or selected_model,
                    "actual_provider": decision.actual_provider or actual_provider,
                    "actual_model": decision.actual_model or actual_model,
                    "platform": platform,
                    "session_id": session_id,
                    "session_key": session_key,
                    "chat_id": chat_id,
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "elapsed_ms": elapsed_ms,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return decision
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        exc_summary = _exception_summary(exc)
        log.warning(
            "pipeline_router_observe %s",
            json.dumps(
                {
                    "event": "pipeline_router_observe_failed",
                    "pipeline_session_id": pipeline_session_id,
                    "platform": platform,
                    "session_id": session_id,
                    "session_key": session_key,
                    "chat_id": chat_id,
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "exception": exc_summary,
                    "routing_failure_reason": exc_summary,
                    "elapsed_ms": elapsed_ms,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            exc_info=True,
        )
        return None


def _pipeline_router_mode(config: dict[str, Any] | None) -> str:
    raw = str(cfg_get(config, "pipelines", "router", "mode", default="disabled") or "disabled").strip().lower()
    if raw in _VALID_ROUTER_MODES:
        return raw
    logger.warning(
        "Invalid pipelines.router.mode=%r; treating pipeline router observe hook as disabled",
        raw,
    )
    return "disabled"


def _replace_decision(
    decision: RouterDecision,
    *,
    actual_provider: str | None,
    actual_model: str | None,
    ) -> RouterDecision:
    return RouterDecision(
        pipeline_session_id=decision.pipeline_session_id,
        router_subagent_id=decision.router_subagent_id,
        status=decision.status,
        selected_pipeline_id=decision.selected_pipeline_id,
        fallback_pipeline_id=decision.fallback_pipeline_id,
        confidence=decision.confidence,
        reasoning_summary=decision.reasoning_summary,
        requires_clarification=decision.requires_clarification,
        clarification_question=decision.clarification_question,
        policy_block_reason=decision.policy_block_reason,
        routing_failure_reason=decision.routing_failure_reason,
        alternatives=decision.alternatives,
        fallback_safe=decision.fallback_safe,
        selected_provider=decision.selected_provider,
        selected_model=decision.selected_model,
        actual_provider=actual_provider,
        actual_model=actual_model,
        token_usage=decision.token_usage,
        cache_usage=decision.cache_usage,
    )


def _exception_summary(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return type(exc).__name__
    return f"{type(exc).__name__}: {message}"
