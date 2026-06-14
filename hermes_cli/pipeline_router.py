"""Import-light pipeline router core for validated Hermes pipeline specs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from hermes_cli.pipeline_specs import (
    VALID_ROUTER_STATUSES,
    LoadedPipelineSpecs,
    load_pipeline_specs,
)

DEFAULT_PIPELINE_ID = "default_conversation_pipeline"
ENGINEERING_PIPELINE_ID = "engineering_review_pipeline"
DEFAULT_ROUTER_SUBAGENT_ID = "hermes_pipeline_router"
DEFAULT_CONFIDENCE = 0.2

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
