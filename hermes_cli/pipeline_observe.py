"""Observe-mode gateway hook for the Hermes pipeline router."""

from __future__ import annotations

import inspect
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from hermes_cli.config import cfg_get
from hermes_cli.pipeline_router import (
    DEFAULT_LLM_FALLBACK_STRATEGY,
    DEFAULT_ROUTER_STRATEGY,
    VALID_ROUTER_STRATEGIES,
    RouterDecision,
    build_pipeline_router,
)
from hermes_cli.pipeline_specs import load_pipeline_specs


logger = logging.getLogger(__name__)

# Autonomous routing builds metadata first; execution remains gated downstream.
AUTONOMOUS_ROUTER_MODE = "autonomous"
_VALID_ROUTER_MODES = {"disabled", "observe", AUTONOMOUS_ROUTER_MODE}


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
    if mode == "disabled":
        return None

    started = time.perf_counter()
    pipeline_session_id = uuid.uuid4().hex

    try:
        loaded_specs = load_pipeline_specs(repo_root=repo_root)
        strategy = _pipeline_router_strategy(config)
        fallback_strategy = _pipeline_router_fallback_strategy(config)
        router = build_pipeline_router(config=config, loaded_specs=loaded_specs, repo_root=repo_root)
        # Keep routing_context limited to safe metadata; it is sent to the
        # router LLM in observe/LLM mode and must not carry secrets or raw
        # provider payloads.
        routing_context = {
            "platform_context": {
                "platform": platform,
                "chat_id": chat_id,
                "thread_id": thread_id,
                "user_id": user_id,
            },
            "session_context": {
                "session_id": session_id,
                "session_key": session_key,
                "pipeline_session_id": pipeline_session_id,
            },
            "safety_constraints": {
                "execution_mode": cfg_get(config, "pipelines", "execution", "mode", default="disabled"),
                "pipelines_enabled": bool(cfg_get(config, "pipelines", "enabled", default=False)),
                "router_mode": mode,
            },
        }
        route_kwargs = {
            "pipeline_session_id": pipeline_session_id,
            "routing_context": routing_context,
        }
        if _router_accepts_routing_context(router):
            decision = router.route(user_message, **route_kwargs)
        else:
            decision = router.route(user_message, pipeline_session_id=pipeline_session_id)
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
                    "router_strategy": decision.router_strategy or strategy,
                    "router_fallback_strategy": fallback_strategy,
                    "selected_pipeline_id": decision.selected_pipeline_id,
                    "fallback_pipeline_id": decision.fallback_pipeline_id,
                    "fallback_reason": decision.routing_failure_reason,
                    "confidence": decision.confidence,
                    "fallback_safe": decision.fallback_safe,
                    "requires_clarification": decision.requires_clarification,
                    "policy_block_reason": decision.policy_block_reason,
                    "routing_failure_reason": decision.routing_failure_reason,
                    "routing_fallback_used": decision.routing_fallback_used,
                    "routing_fallback_reason": decision.routing_fallback_reason,
                    "routing_confidence_source": decision.routing_confidence_source,
                    "invalid_confidence_kind": decision.invalid_confidence_kind,
                    "invalid_confidence_summary": decision.invalid_confidence_summary,
                    "invalid_router_contract_kind": decision.invalid_router_contract_kind,
                    "invalid_router_contract_summary": decision.invalid_router_contract_summary,
                    "dropped_alternatives_count": decision.dropped_alternatives_count,
                    "dropped_alternatives_reasons": list(decision.dropped_alternatives_reasons),
                    "reasoning_summary": decision.reasoning_summary,
                    "matched_signals": list(decision.matched_signals),
                    "alternatives": [
                        {
                            "pipeline_id": alternative.pipeline_id,
                            "confidence": alternative.confidence,
                            "reasoning_summary": alternative.reasoning_summary,
                        }
                        for alternative in decision.alternatives
                    ],
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


def _pipeline_router_strategy(config: dict[str, Any] | None) -> str:
    raw = str(cfg_get(config, "pipelines", "router", "strategy", default=DEFAULT_ROUTER_STRATEGY) or DEFAULT_ROUTER_STRATEGY).strip().lower()
    if raw in VALID_ROUTER_STRATEGIES:
        return raw
    logger.warning(
        "Invalid pipelines.router.strategy=%r; treating pipeline router strategy as %s",
        raw,
        DEFAULT_ROUTER_STRATEGY,
    )
    return DEFAULT_ROUTER_STRATEGY


def _pipeline_router_fallback_strategy(config: dict[str, Any] | None) -> str:
    raw = str(
        cfg_get(
            config,
            "pipelines",
            "router",
            "llm",
            "fallback_strategy",
            default=DEFAULT_LLM_FALLBACK_STRATEGY,
        )
        or DEFAULT_LLM_FALLBACK_STRATEGY
    ).strip().lower()
    if raw:
        return raw
    return DEFAULT_LLM_FALLBACK_STRATEGY


def _router_accepts_routing_context(router: object) -> bool:
    try:
        signature = inspect.signature(router.route)
    except (TypeError, ValueError):
        return False
    return "routing_context" in signature.parameters


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
        matched_signals=decision.matched_signals,
        alternatives=decision.alternatives,
        fallback_safe=decision.fallback_safe,
        selected_provider=decision.selected_provider,
        selected_model=decision.selected_model,
        actual_provider=actual_provider,
        actual_model=actual_model,
        token_usage=decision.token_usage,
        cache_usage=decision.cache_usage,
        invalid_confidence_kind=decision.invalid_confidence_kind,
        invalid_confidence_summary=decision.invalid_confidence_summary,
        invalid_router_contract_kind=decision.invalid_router_contract_kind,
        invalid_router_contract_summary=decision.invalid_router_contract_summary,
        dropped_alternatives_count=decision.dropped_alternatives_count,
        dropped_alternatives_reasons=decision.dropped_alternatives_reasons,
        routing_fallback_used=decision.routing_fallback_used,
        routing_fallback_reason=decision.routing_fallback_reason,
        router_strategy=decision.router_strategy,
    )


def _exception_summary(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return type(exc).__name__
    return f"{type(exc).__name__}: {message}"
