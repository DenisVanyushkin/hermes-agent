"""Import-light pipeline router core for validated Hermes pipeline specs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Callable

from hermes_cli.pipeline_specs import (
    VALID_ROUTER_STATUSES,
    LoadedPipelineSpecs,
    load_pipeline_specs,
)

DEFAULT_PIPELINE_ID = "default_conversation_pipeline"
ENGINEERING_PIPELINE_ID = "engineering_review_pipeline"
DEFAULT_ROUTER_SUBAGENT_ID = "hermes_pipeline_router"
DEFAULT_CONFIDENCE = 0.2
DEFAULT_ROUTER_STRATEGY = "deterministic"
DEFAULT_LLM_FALLBACK_STRATEGY = "deterministic"
DEFAULT_ROUTER_LLM_PROVIDER = "openrouter"
DEFAULT_ROUTER_LLM_MODEL = "openrouter/owl-alpha"
DEFAULT_ROUTER_LLM_TIMEOUT_SECONDS = 10.0
DEFAULT_ROUTER_LLM_MIN_CONFIDENCE = 0.70
VALID_ROUTER_STRATEGIES = {"deterministic", "llm"}
VALID_LLM_FALLBACK_STRATEGIES = {"deterministic", "fail_closed"}

_ROUTER_RESPONSE_FORMAT = {
    "response_format": {
        "type": "json_schema",
        "json_schema": {
            "name": "pipeline_router_decision",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": sorted(VALID_ROUTER_STATUSES),
                    },
                    "selected_pipeline_id": {
                        "type": ["string", "null"],
                        "enum": [DEFAULT_PIPELINE_ID, ENGINEERING_PIPELINE_ID, None],
                    },
                    "fallback_pipeline_id": {
                        "type": ["string", "null"],
                        "enum": [DEFAULT_PIPELINE_ID, ENGINEERING_PIPELINE_ID, None],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reasoning_summary": {"type": "string"},
                    "requires_clarification": {"type": "boolean"},
                    "clarification_question": {"type": ["string", "null"]},
                    "policy_block_reason": {"type": ["string", "null"]},
                    "routing_failure_reason": {"type": ["string", "null"]},
                    "fallback_safe": {"type": "boolean"},
                    "alternatives": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "pipeline_id": {
                                    "type": "string",
                                    "enum": [DEFAULT_PIPELINE_ID, ENGINEERING_PIPELINE_ID],
                                },
                                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                "reasoning_summary": {"type": ["string", "null"]},
                            },
                            "required": ["pipeline_id", "confidence", "reasoning_summary"],
                        },
                    },
                },
                "required": [
                    "status",
                    "confidence",
                    "reasoning_summary",
                    "requires_clarification",
                    "fallback_safe",
                    "alternatives",
                ],
            },
        },
    }
}

RouterLlmCall = Callable[..., dict[str, Any]]

_ENGINEERING_KEYWORDS = (
    "implement slice",
    "patch",
    "write file",
    "write_file",
    "modify code",
    "modify config",
    "modify tests",
    "fix test",
    "refactor",
    "git commit",
    "update test",
    "run pytest",
    "run ruff",
    "run bandit",
)
_ARCHITECTURE_ONLY_KEYWORDS = (
    "обсудим архитектуру",
    "что думаешь",
    "дай задание агенту",
    "сформулируй план",
    "draft a plan",
    "architecture discussion",
)
_AMBIGUOUS_KEYWORDS = (
    "посмотри это и реши, надо ли чинить",
    "look at this and decide if it needs fixing",
    "надо ли чинить",
    "should we fix",
)
_POLICY_BLOCK_PATTERNS = (
    "bypass review",
    "skip review",
    "disable gates",
    "force push",
    "exfiltrate",
    "dump secrets",
    "print auth.json",
    "show me the tokens",
    "delete production data",
    "drop the database",
)
_ENGINEERING_PATH_PATTERN = re.compile(
    r"(?i)\b("
    r"agent/|gateway/|hermes_cli/|job_intel/|scripts/|tests/|config/|cron/|"
    r"[\w./-]+\.py|pyproject\.toml|uv\.lock"
    r")\b"
)


class RouterDecisionValidationError(ValueError):
    """Raised when a router decision violates the registry-constrained contract."""


@dataclass(frozen=True)
class RouterAlternative:
    pipeline_id: str
    confidence: float
    reasoning_summary: str | None = None


@dataclass(frozen=True)
class RouterDecision:
    pipeline_session_id: str
    router_subagent_id: str
    status: str
    selected_pipeline_id: str | None = None
    fallback_pipeline_id: str | None = None
    confidence: float = DEFAULT_CONFIDENCE
    reasoning_summary: str = ""
    requires_clarification: bool = False
    clarification_question: str | None = None
    policy_block_reason: str | None = None
    routing_failure_reason: str | None = None
    matched_signals: tuple[str, ...] = field(default_factory=tuple)
    alternatives: tuple[RouterAlternative, ...] = field(default_factory=tuple)
    fallback_safe: bool = False
    selected_provider: str | None = None
    selected_model: str | None = None
    actual_provider: str | None = None
    actual_model: str | None = None
    token_usage: dict[str, Any] | None = None
    cache_usage: dict[str, Any] | None = None


class PipelineRouter:
    """Base interface for registry-constrained routing decisions."""

    def route(
        self,
        user_message: str,
        *,
        pipeline_session_id: str,
        router_subagent_id: str = DEFAULT_ROUTER_SUBAGENT_ID,
    ) -> RouterDecision:
        raise NotImplementedError


class HeuristicPipelineRouter(PipelineRouter):
    """Deterministic B1 router with heuristics isolated from future LLM routing."""

    def __init__(
        self,
        *,
        loaded_specs: LoadedPipelineSpecs | None = None,
        repo_root: Path | str | None = None,
    ) -> None:
        self._loaded_specs = loaded_specs or load_pipeline_specs(repo_root=repo_root)
        self._registered_pipeline_ids = set(self._loaded_specs.pipeline_specs)
        self._router_model = _router_default_model(self._loaded_specs)

    def route(
        self,
        user_message: str,
        *,
        pipeline_session_id: str,
        router_subagent_id: str = DEFAULT_ROUTER_SUBAGENT_ID,
    ) -> RouterDecision:
        message = user_message.strip()
        normalized = _normalize_text(message)

        if _matches_any(normalized, _POLICY_BLOCK_PATTERNS):
            return self._decision(
                pipeline_session_id=pipeline_session_id,
                router_subagent_id=router_subagent_id,
                status="blocked_by_policy",
                confidence=0.98,
                reasoning_summary="The request asks for an unsafe bypass, secret exfiltration, or destructive action.",
                policy_block_reason="Request bypasses required review or asks for unsafe/destructive behavior.",
            )

        if _looks_ambiguous(normalized):
            return self._decision(
                pipeline_session_id=pipeline_session_id,
                router_subagent_id=router_subagent_id,
                status="needs_clarification",
                confidence=0.45,
                reasoning_summary="The request could mean a read-only audit or a code change, so the router cannot safely classify it yet.",
                requires_clarification=True,
                clarification_question="Do you want a read-only assessment first, or should Hermes prepare and apply a patch?",
                fallback_pipeline_id=DEFAULT_PIPELINE_ID,
                fallback_safe=False,
            )

        if self._should_route_to_engineering(normalized):
            if ENGINEERING_PIPELINE_ID in self._registered_pipeline_ids:
                return self._decision(
                    pipeline_session_id=pipeline_session_id,
                    router_subagent_id=router_subagent_id,
                    status="selected",
                    selected_pipeline_id=ENGINEERING_PIPELINE_ID,
                    confidence=0.93,
                    reasoning_summary="The request explicitly asks for code/config/test/script changes or references engineering paths.",
                    alternatives=(
                        RouterAlternative(
                            pipeline_id=DEFAULT_PIPELINE_ID,
                            confidence=0.18,
                            reasoning_summary="Fallback only if the implementation request is re-scoped to non-mutating discussion.",
                        ),
                    ),
                )
            return self._decision(
                pipeline_session_id=pipeline_session_id,
                router_subagent_id=router_subagent_id,
                status="no_specialized_pipeline",
                confidence=0.35,
                reasoning_summary="The request looks engineering-related, but no registered engineering pipeline is available.",
                fallback_pipeline_id=DEFAULT_PIPELINE_ID,
                fallback_safe=True,
            )

        return self._decision(
            pipeline_session_id=pipeline_session_id,
            router_subagent_id=router_subagent_id,
            status="no_specialized_pipeline",
            confidence=0.82 if _matches_any(normalized, _ARCHITECTURE_ONLY_KEYWORDS) else 0.75,
            reasoning_summary="No specialized pipeline is required for this conversational or discussion-only request.",
            fallback_pipeline_id=DEFAULT_PIPELINE_ID,
            fallback_safe=True,
            alternatives=self._default_alternatives(),
        )

    def _decision(
        self,
        *,
        pipeline_session_id: str,
        router_subagent_id: str,
        status: str,
        confidence: float,
        reasoning_summary: str,
        selected_pipeline_id: str | None = None,
        fallback_pipeline_id: str | None = None,
        requires_clarification: bool = False,
        clarification_question: str | None = None,
        policy_block_reason: str | None = None,
        routing_failure_reason: str | None = None,
        alternatives: tuple[RouterAlternative, ...] = (),
        fallback_safe: bool = False,
    ) -> RouterDecision:
        provider = self._router_model.get("provider")
        model = self._router_model.get("model")
        return parse_router_decision(
            {
                "pipeline_session_id": pipeline_session_id,
                "router_subagent_id": router_subagent_id,
                "status": status,
                "selected_pipeline_id": selected_pipeline_id,
                "fallback_pipeline_id": fallback_pipeline_id,
                "confidence": confidence,
                "reasoning_summary": reasoning_summary,
                "requires_clarification": requires_clarification,
                "clarification_question": clarification_question,
                "policy_block_reason": policy_block_reason,
                "routing_failure_reason": routing_failure_reason,
                "alternatives": [alternative.__dict__ for alternative in alternatives],
                "fallback_safe": fallback_safe,
                "selected_provider": provider,
                "selected_model": model,
                "actual_provider": None,
                "actual_model": None,
                "token_usage": None,
                "cache_usage": None,
            },
            loaded_specs=self._loaded_specs,
        )

    def _default_alternatives(self) -> tuple[RouterAlternative, ...]:
        if ENGINEERING_PIPELINE_ID not in self._registered_pipeline_ids:
            return ()
        return (
            RouterAlternative(
                pipeline_id=ENGINEERING_PIPELINE_ID,
                confidence=0.12,
                reasoning_summary="Could become specialized only if the user explicitly asks for repo changes.",
            ),
        )

    def _should_route_to_engineering(self, normalized: str) -> bool:
        if _matches_any(normalized, _ARCHITECTURE_ONLY_KEYWORDS):
            return False
        if _matches_any(normalized, _ENGINEERING_KEYWORDS):
            return True
        return bool(_ENGINEERING_PATH_PATTERN.search(normalized))


class LlmPipelineRouter(PipelineRouter):
    """Registry-constrained router that asks an LLM to classify the request."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        timeout_seconds: float = DEFAULT_ROUTER_LLM_TIMEOUT_SECONDS,
        fallback_strategy: str = DEFAULT_LLM_FALLBACK_STRATEGY,
        min_confidence: float = DEFAULT_ROUTER_LLM_MIN_CONFIDENCE,
        llm_call: RouterLlmCall | None = None,
        loaded_specs: LoadedPipelineSpecs | None = None,
        repo_root: Path | str | None = None,
        deterministic_router: HeuristicPipelineRouter | None = None,
    ) -> None:
        self._loaded_specs = loaded_specs or load_pipeline_specs(repo_root=repo_root)
        self._provider = provider.strip()
        self._model = model.strip()
        self._timeout_seconds = float(timeout_seconds)
        self._fallback_strategy = fallback_strategy.strip().lower() or DEFAULT_LLM_FALLBACK_STRATEGY
        self._min_confidence = _coerce_min_confidence(min_confidence)
        self._llm_call = llm_call or _default_router_llm_call
        self._deterministic_router = deterministic_router or HeuristicPipelineRouter(
            loaded_specs=self._loaded_specs,
            repo_root=repo_root,
        )

    def route(
        self,
        user_message: str,
        *,
        pipeline_session_id: str,
        router_subagent_id: str = DEFAULT_ROUTER_SUBAGENT_ID,
    ) -> RouterDecision:
        try:
            raw = self._llm_call(
                provider=self._provider,
                model=self._model,
                timeout_seconds=self._timeout_seconds,
                messages=_build_router_messages(self._loaded_specs, user_message),
            )
            payload = _coerce_llm_router_payload(raw)
            payload["pipeline_session_id"] = pipeline_session_id
            payload["router_subagent_id"] = router_subagent_id
            payload.setdefault("fallback_safe", payload.get("status") == "no_specialized_pipeline")
            payload.setdefault("alternatives", [])
            payload.setdefault("selected_provider", self._provider)
            payload.setdefault("selected_model", self._model)
            payload.setdefault("actual_provider", self._provider)
            payload.setdefault("actual_model", self._model)
            if payload.get("status") == "ambiguous":
                return self._fallback_decision(
                    user_message,
                    pipeline_session_id=pipeline_session_id,
                    router_subagent_id=router_subagent_id,
                    reason="llm_ambiguous",
                    reasoning_summary=_optional_str(payload.get("reasoning_summary"))
                    or "The LLM router marked the request as ambiguous, so Hermes failed closed.",
                )
            confidence_error = _confidence_error(payload.get("confidence"))
            if confidence_error:
                return self._fallback_decision(
                    user_message,
                    pipeline_session_id=pipeline_session_id,
                    router_subagent_id=router_subagent_id,
                    reason="llm_invalid_confidence",
                    reasoning_summary=_optional_str(payload.get("reasoning_summary"))
                    or "The LLM router returned an invalid confidence value, so Hermes failed closed.",
                )
            confidence_value = float(payload["confidence"])
            if payload.get("status") == "selected" and confidence_value < self._min_confidence:
                return self._fallback_decision(
                    user_message,
                    pipeline_session_id=pipeline_session_id,
                    router_subagent_id=router_subagent_id,
                    reason="llm_low_confidence",
                    reasoning_summary=_optional_str(payload.get("reasoning_summary"))
                    or "The LLM router selected a specialized pipeline below the configured confidence threshold.",
                )
            if payload.get("status") == "no_specialized_pipeline":
                payload.setdefault("fallback_pipeline_id", DEFAULT_PIPELINE_ID)
            return parse_router_decision(payload, loaded_specs=self._loaded_specs)
        except Exception as exc:
            if self._fallback_strategy == "deterministic":
                return self._deterministic_router.route(
                    user_message,
                    pipeline_session_id=pipeline_session_id,
                    router_subagent_id=router_subagent_id,
                )
            return parse_router_decision(
                {
                    "pipeline_session_id": pipeline_session_id,
                    "router_subagent_id": router_subagent_id,
                    "status": "routing_failed",
                    "confidence": DEFAULT_CONFIDENCE,
                    "reasoning_summary": "The LLM pipeline router failed before it could produce a valid registry-constrained decision.",
                    "requires_clarification": False,
                    "routing_failure_reason": _exception_summary(exc),
                    "fallback_safe": False,
                    "alternatives": [],
                    "selected_provider": self._provider,
                    "selected_model": self._model,
                    "actual_provider": self._provider,
                    "actual_model": self._model,
                },
                loaded_specs=self._loaded_specs,
            )

    def _fallback_decision(
        self,
        user_message: str,
        *,
        pipeline_session_id: str,
        router_subagent_id: str,
        reason: str,
        reasoning_summary: str,
    ) -> RouterDecision:
        if self._fallback_strategy == "deterministic":
            base = self._deterministic_router.route(
                user_message,
                pipeline_session_id=pipeline_session_id,
                router_subagent_id=router_subagent_id,
            )
            if base.status == "selected":
                return parse_router_decision(
                    {
                        "pipeline_session_id": base.pipeline_session_id,
                        "router_subagent_id": base.router_subagent_id,
                        "status": "no_specialized_pipeline",
                        "selected_pipeline_id": None,
                        "fallback_pipeline_id": DEFAULT_PIPELINE_ID,
                        "confidence": max(base.confidence, self._min_confidence),
                        "reasoning_summary": reasoning_summary,
                        "requires_clarification": False,
                        "policy_block_reason": base.policy_block_reason,
                        "routing_failure_reason": reason,
                        "matched_signals": list(base.matched_signals),
                        "alternatives": [alternative.__dict__ for alternative in base.alternatives],
                        "fallback_safe": True,
                        "selected_provider": self._provider,
                        "selected_model": self._model,
                        "actual_provider": self._provider,
                        "actual_model": self._model,
                    },
                    loaded_specs=self._loaded_specs,
                )
            return parse_router_decision(
                {
                    "pipeline_session_id": base.pipeline_session_id,
                    "router_subagent_id": base.router_subagent_id,
                    "status": base.status,
                    "selected_pipeline_id": base.selected_pipeline_id,
                    "fallback_pipeline_id": base.fallback_pipeline_id or DEFAULT_PIPELINE_ID,
                    "confidence": base.confidence,
                    "reasoning_summary": reasoning_summary,
                    "requires_clarification": base.requires_clarification,
                    "clarification_question": base.clarification_question,
                    "policy_block_reason": base.policy_block_reason,
                    "routing_failure_reason": reason,
                    "matched_signals": list(base.matched_signals),
                    "alternatives": [alternative.__dict__ for alternative in base.alternatives],
                    "fallback_safe": base.fallback_safe or base.fallback_pipeline_id == DEFAULT_PIPELINE_ID,
                    "selected_provider": self._provider,
                    "selected_model": self._model,
                    "actual_provider": self._provider,
                    "actual_model": self._model,
                },
                loaded_specs=self._loaded_specs,
            )

        return parse_router_decision(
            {
                "pipeline_session_id": pipeline_session_id,
                "router_subagent_id": router_subagent_id,
                "status": "routing_failed",
                "confidence": DEFAULT_CONFIDENCE,
                "reasoning_summary": reasoning_summary,
                "requires_clarification": False,
                "routing_failure_reason": reason,
                "matched_signals": [],
                "alternatives": [],
                "fallback_safe": False,
                "selected_provider": self._provider,
                "selected_model": self._model,
                "actual_provider": self._provider,
                "actual_model": self._model,
            },
            loaded_specs=self._loaded_specs,
        )


def build_pipeline_router(
    *,
    config: dict[str, Any] | None,
    loaded_specs: LoadedPipelineSpecs | None = None,
    repo_root: Path | str | None = None,
) -> PipelineRouter:
    specs = loaded_specs or load_pipeline_specs(repo_root=repo_root)
    strategy = _pipeline_router_strategy(config)
    if strategy == "llm":
        llm_cfg = _router_llm_config(config)
        return LlmPipelineRouter(
            loaded_specs=specs,
            repo_root=repo_root,
            provider=llm_cfg["provider"],
            model=llm_cfg["model"],
            timeout_seconds=llm_cfg["timeout_seconds"],
            fallback_strategy=llm_cfg["fallback_strategy"],
            min_confidence=llm_cfg["min_confidence"],
        )
    return HeuristicPipelineRouter(loaded_specs=specs, repo_root=repo_root)


def parse_router_decision(
    data: dict[str, Any],
    *,
    loaded_specs: LoadedPipelineSpecs | None = None,
    repo_root: Path | str | None = None,
) -> RouterDecision:
    specs = loaded_specs or load_pipeline_specs(repo_root=repo_root)
    _validate_router_decision_dict(data, specs)

    alternatives = tuple(
        RouterAlternative(
            pipeline_id=entry["pipeline_id"],
            confidence=float(entry["confidence"]),
            reasoning_summary=entry.get("reasoning_summary"),
        )
        for entry in data.get("alternatives", [])
    )

    return RouterDecision(
        pipeline_session_id=str(data["pipeline_session_id"]),
        router_subagent_id=str(data["router_subagent_id"]),
        status=str(data["status"]),
        selected_pipeline_id=_optional_str(data.get("selected_pipeline_id")),
        fallback_pipeline_id=_optional_str(data.get("fallback_pipeline_id")),
        confidence=float(data["confidence"]),
        reasoning_summary=str(data["reasoning_summary"]),
        requires_clarification=bool(data["requires_clarification"]),
        clarification_question=_optional_str(data.get("clarification_question")),
        policy_block_reason=_optional_str(data.get("policy_block_reason")),
        routing_failure_reason=_optional_str(data.get("routing_failure_reason")),
        matched_signals=tuple(str(entry) for entry in data.get("matched_signals", []) if str(entry).strip()),
        alternatives=alternatives,
        fallback_safe=bool(data.get("fallback_safe", False)),
        selected_provider=_optional_str(data.get("selected_provider")),
        selected_model=_optional_str(data.get("selected_model")),
        actual_provider=_optional_str(data.get("actual_provider")),
        actual_model=_optional_str(data.get("actual_model")),
        token_usage=data.get("token_usage"),
        cache_usage=data.get("cache_usage"),
    )


def _validate_router_decision_dict(data: dict[str, Any], specs: LoadedPipelineSpecs) -> None:
    status = data.get("status")
    if status not in VALID_ROUTER_STATUSES:
        raise RouterDecisionValidationError(f"Unknown router status: {status!r}")

    registered_pipeline_ids = set(specs.pipeline_specs)
    selected_pipeline_id = _optional_str(data.get("selected_pipeline_id"))
    fallback_pipeline_id = _optional_str(data.get("fallback_pipeline_id"))

    if selected_pipeline_id and selected_pipeline_id not in registered_pipeline_ids:
        raise RouterDecisionValidationError(f"Unknown selected pipeline id: {selected_pipeline_id!r}")
    if fallback_pipeline_id and fallback_pipeline_id not in registered_pipeline_ids:
        raise RouterDecisionValidationError(f"Unknown fallback pipeline id: {fallback_pipeline_id!r}")

    if status == "selected" and not selected_pipeline_id:
        raise RouterDecisionValidationError("Router status 'selected' requires selected_pipeline_id")
    if status == "no_specialized_pipeline" and selected_pipeline_id:
        raise RouterDecisionValidationError("Router status 'no_specialized_pipeline' cannot set selected_pipeline_id")

    if status == "no_specialized_pipeline":
        if fallback_pipeline_id != DEFAULT_PIPELINE_ID:
            raise RouterDecisionValidationError(
                "Router status 'no_specialized_pipeline' must use fallback_pipeline_id='default_conversation_pipeline'"
            )
        if data.get("fallback_safe") is not True:
            raise RouterDecisionValidationError(
                "Router status 'no_specialized_pipeline' must set fallback_safe=True"
            )

    if status == "routing_failed" and fallback_pipeline_id == DEFAULT_PIPELINE_ID and data.get("fallback_safe") is not True:
        raise RouterDecisionValidationError(
            "Router status 'routing_failed' may only use the default fallback when fallback_safe=True"
        )

    for index, alternative in enumerate(data.get("alternatives", [])):
        if not isinstance(alternative, dict):
            raise RouterDecisionValidationError(f"Alternative at index {index} must be a mapping")
        pipeline_id = alternative.get("pipeline_id")
        if pipeline_id not in registered_pipeline_ids:
            raise RouterDecisionValidationError(f"Unknown alternative pipeline id: {pipeline_id!r}")

    if "pipeline_session_id" not in data or not str(data["pipeline_session_id"]).strip():
        raise RouterDecisionValidationError("pipeline_session_id is required")
    if "router_subagent_id" not in data or not str(data["router_subagent_id"]).strip():
        raise RouterDecisionValidationError("router_subagent_id is required")
    if "confidence" not in data:
        raise RouterDecisionValidationError("confidence is required")
    if "reasoning_summary" not in data or not str(data["reasoning_summary"]).strip():
        raise RouterDecisionValidationError("reasoning_summary is required")
    if "requires_clarification" not in data:
        raise RouterDecisionValidationError("requires_clarification is required")


def _router_default_model(loaded_specs: LoadedPipelineSpecs) -> dict[str, Any]:
    router_spec = loaded_specs.subagent_specs.get(DEFAULT_ROUTER_SUBAGENT_ID, {})
    models = router_spec.get("models")
    if not isinstance(models, dict):
        return {}
    default = models.get("default")
    return default if isinstance(default, dict) else {}


def _pipeline_router_strategy(config: dict[str, Any] | None) -> str:
    raw = str(_nested_config_value(config, "pipelines", "router", "strategy", default=DEFAULT_ROUTER_STRATEGY) or DEFAULT_ROUTER_STRATEGY)
    normalized = raw.strip().lower()
    if normalized in VALID_ROUTER_STRATEGIES:
        return normalized
    return DEFAULT_ROUTER_STRATEGY


def _router_llm_config(config: dict[str, Any] | None) -> dict[str, Any]:
    provider = str(
        _nested_config_value(config, "pipelines", "router", "llm", "provider", default=DEFAULT_ROUTER_LLM_PROVIDER)
        or DEFAULT_ROUTER_LLM_PROVIDER
    ).strip()
    model = str(
        _nested_config_value(config, "pipelines", "router", "llm", "model", default=DEFAULT_ROUTER_LLM_MODEL)
        or DEFAULT_ROUTER_LLM_MODEL
    ).strip()
    timeout_raw = _nested_config_value(
        config,
        "pipelines",
        "router",
        "llm",
        "timeout_seconds",
        default=DEFAULT_ROUTER_LLM_TIMEOUT_SECONDS,
    )
    try:
        timeout_seconds = float(timeout_raw)
    except (TypeError, ValueError):
        timeout_seconds = DEFAULT_ROUTER_LLM_TIMEOUT_SECONDS
    if timeout_seconds <= 0:
        timeout_seconds = DEFAULT_ROUTER_LLM_TIMEOUT_SECONDS

    fallback_strategy = str(
        _nested_config_value(
            config,
            "pipelines",
            "router",
            "llm",
            "fallback_strategy",
            default=DEFAULT_LLM_FALLBACK_STRATEGY,
        )
        or DEFAULT_LLM_FALLBACK_STRATEGY
    ).strip().lower()
    if fallback_strategy not in VALID_LLM_FALLBACK_STRATEGIES:
        fallback_strategy = DEFAULT_LLM_FALLBACK_STRATEGY

    return {
        "provider": provider or DEFAULT_ROUTER_LLM_PROVIDER,
        "model": model or DEFAULT_ROUTER_LLM_MODEL,
        "timeout_seconds": timeout_seconds,
        "fallback_strategy": fallback_strategy,
        "min_confidence": _coerce_min_confidence(
            _nested_config_value(
                config,
                "pipelines",
                "router",
                "llm",
                "min_confidence",
                default=DEFAULT_ROUTER_LLM_MIN_CONFIDENCE,
            )
        ),
    }


def _build_router_messages(loaded_specs: LoadedPipelineSpecs, user_message: str) -> list[dict[str, str]]:
    registry_blob = json.dumps(_router_registry_prompt_context(loaded_specs), ensure_ascii=False, indent=2, sort_keys=True)
    return [
        {
            "role": "system",
            "content": (
                "You are the Hermes pipeline router. Select only from the declared pipeline registry. "
                "Use semantic intent, requested mutations, and target paths. "
                "Deterministic keywords are incomplete; rely on the registry semantics instead. "
                "Never invent pipeline ids or statuses. If the request is ambiguous between read-only audit and mutation, "
                "return needs_clarification. If the request asks for unsafe bypass, secret exfiltration, or destructive misuse, "
                "return blocked_by_policy. Return JSON only."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Pipeline registry and schema:\n{registry_blob}\n\n"
                f"User message:\n{user_message.strip()}\n"
            ),
        },
    ]


def _router_registry_prompt_context(loaded_specs: LoadedPipelineSpecs) -> dict[str, Any]:
    pipeline_purposes = {
        pipeline_id: {
            "purpose": spec.get("purpose"),
            "entry_conditions": spec.get("entry_conditions"),
        }
        for pipeline_id, spec in sorted(loaded_specs.pipeline_specs.items())
    }
    return {
        "valid_statuses": sorted(VALID_ROUTER_STATUSES),
        "default_pipeline_id": DEFAULT_PIPELINE_ID,
        "registry": loaded_specs.registry.get("registry", []),
        "pipeline_specs": pipeline_purposes,
        "contract_notes": {
            "selected_requires_pipeline_id": True,
            "no_specialized_pipeline_requires_default_fallback": DEFAULT_PIPELINE_ID,
            "routing_failed_default_fallback_requires_fallback_safe": True,
        },
    }


def _default_router_llm_call(
    *,
    provider: str,
    model: str,
    timeout_seconds: float,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    from agent.auxiliary_client import extract_content_or_reasoning, resolve_provider_client

    client, resolved_model = resolve_provider_client(provider, model)
    if client is None:
        raise RuntimeError(f"No credentials or client available for router provider={provider!r}")

    response = client.chat.completions.create(
        model=resolved_model or model,
        messages=messages,
        timeout=timeout_seconds,
        extra_body=_ROUTER_RESPONSE_FORMAT,
    )
    raw_text = extract_content_or_reasoning(response).strip()
    if not raw_text:
        raise RuntimeError("Router LLM returned an empty response body")
    parsed = json.loads(raw_text)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Router LLM returned non-object JSON: {type(parsed).__name__}")
    parsed.setdefault("actual_provider", provider)
    parsed.setdefault("actual_model", resolved_model or model)
    usage = getattr(response, "usage", None)
    parsed.setdefault("token_usage", _coerce_usage_dict(usage))
    return parsed


def _coerce_llm_router_payload(raw: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise TypeError(f"Router LLM payload must be a mapping, got {type(raw).__name__}")

    decision = raw.get("decision", raw)
    if isinstance(decision, str):
        decision = json.loads(decision)
    if not isinstance(decision, dict):
        raise TypeError(f"Router decision must be a mapping, got {type(decision).__name__}")

    payload = dict(decision)
    for key in ("actual_provider", "actual_model", "token_usage", "cache_usage"):
        if key in raw and key not in payload:
            payload[key] = raw[key]
    return payload


def _coerce_min_confidence(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_ROUTER_LLM_MIN_CONFIDENCE
    if 0.0 <= value <= 1.0:
        return value
    return DEFAULT_ROUTER_LLM_MIN_CONFIDENCE


def _confidence_error(raw: Any) -> str | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return "non_numeric"
    if not 0.0 <= value <= 1.0:
        return "out_of_range"
    return None


def _coerce_usage_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    model_dump = getattr(usage, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, dict) else None
    return None


def _nested_config_value(config: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    current: Any = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def _matches_any(message: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in message for pattern in patterns)


def _looks_ambiguous(message: str) -> bool:
    if _matches_any(message, _AMBIGUOUS_KEYWORDS):
        return True
    return (
        ("посмотри" in message or "look at" in message)
        and ("реши" in message or "decide" in message)
        and ("чин" in message or "fix" in message)
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _exception_summary(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return type(exc).__name__
    return f"{type(exc).__name__}: {message}"
