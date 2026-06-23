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
DEFAULT_ROUTER_STRATEGY = "llm"
DEFAULT_LLM_FALLBACK_STRATEGY = "fail_closed"
DEFAULT_ROUTER_LLM_PROVIDER = "openai-codex"
DEFAULT_ROUTER_LLM_MODEL = "gpt-5.4-mini"
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

_ENGINEERING_MUTATION_KEYWORDS = (
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
    "исправь",
    "исправить",
    "почини",
    "починить",
    "поправь",
    "измени",
    "изменить",
    "обнови",
    "обновить",
    "добавь",
    "добавить",
    "сделай ревью",
    "сделать ревью",
    "ревью изменений",
    "regression test",
)
_ENGINEERING_DEBUG_KEYWORDS = (
    "bug",
    "failing",
    "failure",
    "debug",
    "fix",
    "pytest",
    "ruff",
    "bandit",
    "test",
    "tests",
    "код",
    "тест",
    "тесты",
    "баг",
    "ошиб",
    "падает",
    "регресс",
    "regression",
    "gateway",
    "router",
    "pipeline",
    "config",
    "конфиг",
    "ревью",
)
_ENGINEERING_DOMAIN_KEYWORDS = (
    "hermes",
    "repo",
    "repository",
    "code",
    "config",
    "test",
    "tests",
    "pytest",
    "ruff",
    "bandit",
    "gateway",
    "router",
    "pipeline",
    "orchestrator",
    "review",
    "reviewer",
    "код",
    "конфиг",
    "тест",
    "тесты",
    "репо",
    "пайтест",
    "ревью",
    "баг",
)
_ARCHITECTURE_ONLY_KEYWORDS = (
    "обсудим архитектуру",
    "что думаешь",
    "дай задание агенту",
    "сформулируй план",
    "draft a plan",
    "architecture discussion",
)
_HEURISTIC_ENGINEERING_FALLBACK_TEST_KEYWORDS = (
    "pytest",
    "test file",
    "trivial pytest test",
    "marker file",
    "regression",
    "function",
    "module",
    "tests/",
)
_HEURISTIC_ENGINEERING_FALLBACK_MUTATION_KEYWORDS = (
    "create",
    "add",
    "modify",
    "fix",
    "update",
    "write",
    "edit",
    "patch",
)
_HEURISTIC_ENGINEERING_FALLBACK_ANCHORS = (
    "hermes autonomous pipeline validation",
    "do not modify production behavior",
    "do not touch db persistence",
)
_HEURISTIC_RUNTIME_ANALYSIS_PIPELINE_ANCHORS = (
    "engineering_review_pipeline",
    "autonomous engineering pipeline",
    "autonomous execution controller",
    "runtime smoke",
    "post-fix runtime smoke",
    "helper/subagent bridge",
    "pipeline_execution_report",
    "actual_execution_invoked",
)
_HEURISTIC_RUNTIME_ANALYSIS_TOOL_REPO_ANCHORS = (
    "find_files",
    "read_file",
    "search_files",
    "repo-relative",
    "engineer bridge",
    "commit ",
    "repository",
    "repo",
    "репозитории",
    "репозитории",
)
_HEURISTIC_RUNTIME_ANALYSIS_SAFETY_ANCHORS = (
    "do not change code",
    "не меняй код",
    "do not commit",
    "не делай commit",
    "do not push",
    "не делай push",
)
_HEURISTIC_DEFAULT_CONVERSATION_KEYWORDS = (
    "привет",
    "как дела",
    "что ты умеешь",
    "объясни",
    "напиши текст письма",
    "суммируй",
    "помоги сформулировать",
    "какой следующий шаг",
    "hello",
    "hi",
    "what can you do",
    "explain",
    "summarize",
    "help me phrase",
    "draft an email",
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
    r"[\w./-]+\.py|pyproject\.toml|uv\.lock|"
    r"pipeline_[\w-]+|gateway/run\.py|hermes_cli/[\w./-]+|config/pipelines/[\w./-]+"
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
    invalid_confidence_kind: str | None = None
    invalid_confidence_summary: str | None = None
    invalid_router_contract_kind: str | None = None
    invalid_router_contract_summary: str | None = None
    dropped_alternatives_count: int = 0
    dropped_alternatives_reasons: tuple[str, ...] = field(default_factory=tuple)
    routing_fallback_used: bool = False
    routing_fallback_reason: str | None = None
    router_strategy: str | None = None
    routing_confidence_source: str | None = None


class PipelineRouter:
    """Base interface for registry-constrained routing decisions."""

    def route(
        self,
        user_message: str,
        *,
        pipeline_session_id: str,
        router_subagent_id: str = DEFAULT_ROUTER_SUBAGENT_ID,
        routing_context: dict[str, Any] | None = None,
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
        routing_context: dict[str, Any] | None = None,
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
                matched_signals = self._engineering_matched_signals(normalized)
                return self._decision(
                    pipeline_session_id=pipeline_session_id,
                    router_subagent_id=router_subagent_id,
                    status="selected",
                    selected_pipeline_id=ENGINEERING_PIPELINE_ID,
                    confidence=0.93,
                    reasoning_summary="The request matches engineering domain, mutation/debug intent, or engineering target paths declared by the pipeline registry.",
                    matched_signals=matched_signals,
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
                matched_signals=self._engineering_matched_signals(normalized),
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
        matched_signals: tuple[str, ...] = (),
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
                "matched_signals": list(matched_signals),
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
        return bool(self._engineering_matched_signals(normalized))

    def _engineering_matched_signals(self, normalized: str) -> tuple[str, ...]:
        if _matches_any(normalized, _ARCHITECTURE_ONLY_KEYWORDS):
            return ()

        matched_signals: list[str] = []
        has_path_signal = bool(_ENGINEERING_PATH_PATTERN.search(normalized))
        has_mutation_signal = _matches_any(normalized, _ENGINEERING_MUTATION_KEYWORDS)
        has_domain_signal = _matches_any(normalized, _ENGINEERING_DOMAIN_KEYWORDS)
        has_debug_signal = _matches_any(normalized, _ENGINEERING_DEBUG_KEYWORDS)

        if has_domain_signal and (has_mutation_signal or has_debug_signal):
            matched_signals.append("task_classification.domain == engineering")
        if has_mutation_signal:
            matched_signals.append("task_intent includes code_mutation")
        if has_path_signal:
            matched_signals.append("target_paths match engineering_path_patterns")

        return tuple(matched_signals)

    def candidate_hints(self, user_message: str) -> dict[str, Any]:
        normalized = _normalize_text(user_message.strip())
        matched_signals = self._engineering_matched_signals(normalized)
        return {
            "matched_signals": list(matched_signals),
            "engineering_candidate_pipeline_id": ENGINEERING_PIPELINE_ID if matched_signals else None,
            "default_fallback_pipeline_id": DEFAULT_PIPELINE_ID,
            "architecture_only": _matches_any(normalized, _ARCHITECTURE_ONLY_KEYWORDS),
            "ambiguous_mutation_request": _looks_ambiguous(normalized),
        }


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
        routing_context: dict[str, Any] | None = None,
    ) -> RouterDecision:
        guardrail_decision = self._deterministic_router.route(
            user_message,
            pipeline_session_id=pipeline_session_id,
            router_subagent_id=router_subagent_id,
        )
        if guardrail_decision.status == "blocked_by_policy":
            return parse_router_decision(
                {
                    "pipeline_session_id": guardrail_decision.pipeline_session_id,
                    "router_subagent_id": guardrail_decision.router_subagent_id,
                    "status": guardrail_decision.status,
                    "confidence": guardrail_decision.confidence,
                    "reasoning_summary": guardrail_decision.reasoning_summary,
                    "requires_clarification": False,
                    "policy_block_reason": guardrail_decision.policy_block_reason,
                    "routing_failure_reason": guardrail_decision.routing_failure_reason,
                    "matched_signals": list(guardrail_decision.matched_signals),
                    "alternatives": [alternative.__dict__ for alternative in guardrail_decision.alternatives],
                    "fallback_safe": False,
                    "selected_provider": self._provider,
                    "selected_model": self._model,
                    "actual_provider": None,
                    "actual_model": None,
                },
                loaded_specs=self._loaded_specs,
            )

        try:
            raw = self._llm_call(
                provider=self._provider,
                model=self._model,
                timeout_seconds=self._timeout_seconds,
                messages=_build_router_messages(
                    self._loaded_specs,
                    user_message,
                    routing_context=routing_context,
                    candidate_hints=self._deterministic_router.candidate_hints(user_message),
                ),
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
            invalid_confidence = _invalid_confidence_diagnostic(payload)
            if invalid_confidence is not None:
                return self._fallback_decision(
                    user_message,
                    pipeline_session_id=pipeline_session_id,
                    router_subagent_id=router_subagent_id,
                    reason="llm_invalid_confidence",
                    reasoning_summary=_optional_str(payload.get("reasoning_summary"))
                    or "The LLM router returned an invalid confidence value, so Hermes failed closed.",
                    invalid_confidence_kind=invalid_confidence["kind"],
                    invalid_confidence_summary=invalid_confidence["summary"],
                )
            invalid_router_contract = _invalid_router_contract_diagnostic(payload)
            if invalid_router_contract is not None:
                return self._fallback_decision(
                    user_message,
                    pipeline_session_id=pipeline_session_id,
                    router_subagent_id=router_subagent_id,
                    reason=invalid_router_contract["reason"],
                    reasoning_summary=_optional_str(payload.get("reasoning_summary"))
                    or "The LLM router returned an invalid router contract shape, so Hermes failed closed.",
                    invalid_router_contract_kind=invalid_router_contract["kind"],
                    invalid_router_contract_summary=invalid_router_contract["summary"],
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
            return parse_router_decision(
                _sanitize_router_payload(payload, loaded_specs=self._loaded_specs),
                loaded_specs=self._loaded_specs,
            )
        except Exception as exc:
            failure_reason = _exception_summary(exc)
            fallback_selection = self._heuristic_engineering_timeout_fallback(
                user_message,
                pipeline_session_id=pipeline_session_id,
                router_subagent_id=router_subagent_id,
                failure_reason=failure_reason,
            )
            if fallback_selection is not None:
                return fallback_selection
            fallback_selection = self._heuristic_default_timeout_fallback(
                user_message,
                pipeline_session_id=pipeline_session_id,
                router_subagent_id=router_subagent_id,
                failure_reason=failure_reason,
            )
            if fallback_selection is not None:
                return fallback_selection
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
                    "routing_failure_reason": failure_reason,
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
        invalid_confidence_kind: str | None = None,
        invalid_confidence_summary: str | None = None,
        invalid_router_contract_kind: str | None = None,
        invalid_router_contract_summary: str | None = None,
    ) -> RouterDecision:
        fallback_failure_reason = reason
        if invalid_router_contract_summary:
            fallback_failure_reason = f"{reason}: {invalid_router_contract_summary}"
        elif invalid_confidence_summary:
            fallback_failure_reason = f"{reason}: {invalid_confidence_summary}"
        fallback_selection = self._heuristic_engineering_timeout_fallback(
            user_message,
            pipeline_session_id=pipeline_session_id,
            router_subagent_id=router_subagent_id,
            failure_reason=fallback_failure_reason,
            failure_code=reason,
            invalid_confidence_kind=invalid_confidence_kind,
            invalid_confidence_summary=invalid_confidence_summary,
            invalid_router_contract_kind=invalid_router_contract_kind,
            invalid_router_contract_summary=invalid_router_contract_summary,
        )
        if fallback_selection is not None:
            return fallback_selection
        fallback_selection = self._heuristic_default_timeout_fallback(
            user_message,
            pipeline_session_id=pipeline_session_id,
            router_subagent_id=router_subagent_id,
            failure_reason=fallback_failure_reason,
            failure_code=reason,
            invalid_confidence_kind=invalid_confidence_kind,
            invalid_confidence_summary=invalid_confidence_summary,
            invalid_router_contract_kind=invalid_router_contract_kind,
            invalid_router_contract_summary=invalid_router_contract_summary,
        )
        if fallback_selection is not None:
            return fallback_selection
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
                        "invalid_confidence_kind": invalid_confidence_kind,
                        "invalid_confidence_summary": invalid_confidence_summary,
                        "invalid_router_contract_kind": invalid_router_contract_kind,
                        "invalid_router_contract_summary": invalid_router_contract_summary,
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
                    "invalid_confidence_kind": invalid_confidence_kind,
                    "invalid_confidence_summary": invalid_confidence_summary,
                    "invalid_router_contract_kind": invalid_router_contract_kind,
                    "invalid_router_contract_summary": invalid_router_contract_summary,
                },
                loaded_specs=self._loaded_specs,
            )

        if reason in {"llm_low_confidence", "llm_ambiguous"}:
            return parse_router_decision(
                {
                    "pipeline_session_id": pipeline_session_id,
                    "router_subagent_id": router_subagent_id,
                    "status": "needs_clarification",
                    "confidence": DEFAULT_CONFIDENCE,
                    "reasoning_summary": reasoning_summary,
                    "requires_clarification": True,
                    "clarification_question": "Should Hermes treat this as a code-changing engineering request, or keep it conversational/read-only?",
                    "fallback_pipeline_id": DEFAULT_PIPELINE_ID,
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
                "invalid_confidence_kind": invalid_confidence_kind,
                "invalid_confidence_summary": invalid_confidence_summary,
                "invalid_router_contract_kind": invalid_router_contract_kind,
                "invalid_router_contract_summary": invalid_router_contract_summary,
            },
            loaded_specs=self._loaded_specs,
        )

    def _heuristic_engineering_timeout_fallback(
        self,
        user_message: str,
        *,
        pipeline_session_id: str,
        router_subagent_id: str,
        failure_reason: str,
        failure_code: str | None = None,
        invalid_confidence_kind: str | None = None,
        invalid_confidence_summary: str | None = None,
        invalid_router_contract_kind: str | None = None,
        invalid_router_contract_summary: str | None = None,
    ) -> RouterDecision | None:
        if not self._is_heuristic_engineering_fallback_failure(
            failure_reason,
            failure_code=failure_code,
            invalid_confidence_kind=invalid_confidence_kind,
            invalid_router_contract_kind=invalid_router_contract_kind,
        ):
            return None

        base = self._deterministic_router.route(
            user_message,
            pipeline_session_id=pipeline_session_id,
            router_subagent_id=router_subagent_id,
        )
        if not self._is_strong_engineering_fallback_candidate(user_message, base):
            return None

        return parse_router_decision(
            {
                "pipeline_session_id": base.pipeline_session_id,
                "router_subagent_id": base.router_subagent_id,
                "status": "selected",
                "selected_pipeline_id": ENGINEERING_PIPELINE_ID,
                "fallback_pipeline_id": None,
                "confidence": max(self._min_confidence, 0.74),
                "reasoning_summary": "The LLM router failed, but the request matched a narrow deterministic engineering smoke signature, so Hermes selected the engineering pipeline without opening the normal fallback path.",
                "requires_clarification": False,
                "policy_block_reason": base.policy_block_reason,
                "routing_failure_reason": failure_reason,
                "matched_signals": list(base.matched_signals),
                "alternatives": [alternative.__dict__ for alternative in base.alternatives],
                "fallback_safe": False,
                "selected_provider": self._provider,
                "selected_model": self._model,
                "actual_provider": self._provider,
                "actual_model": self._model,
                "invalid_confidence_kind": invalid_confidence_kind,
                "invalid_confidence_summary": invalid_confidence_summary,
                "invalid_router_contract_kind": invalid_router_contract_kind,
                "invalid_router_contract_summary": invalid_router_contract_summary,
                "routing_fallback_used": True,
                "routing_fallback_reason": failure_reason,
                "router_strategy": "heuristic_timeout_fallback",
                "routing_confidence_source": "heuristic_strict",
            },
            loaded_specs=self._loaded_specs,
        )

    def _heuristic_default_timeout_fallback(
        self,
        user_message: str,
        *,
        pipeline_session_id: str,
        router_subagent_id: str,
        failure_reason: str,
        failure_code: str | None = None,
        invalid_confidence_kind: str | None = None,
        invalid_confidence_summary: str | None = None,
        invalid_router_contract_kind: str | None = None,
        invalid_router_contract_summary: str | None = None,
    ) -> RouterDecision | None:
        if not self._is_heuristic_engineering_fallback_failure(
            failure_reason,
            failure_code=failure_code,
            invalid_confidence_kind=invalid_confidence_kind,
            invalid_router_contract_kind=invalid_router_contract_kind,
        ):
            return None

        base = self._deterministic_router.route(
            user_message,
            pipeline_session_id=pipeline_session_id,
            router_subagent_id=router_subagent_id,
        )
        if base.status != "no_specialized_pipeline" or base.fallback_pipeline_id != DEFAULT_PIPELINE_ID:
            return None
        if not base.fallback_safe or base.requires_clarification:
            return None
        if not self._is_clear_non_engineering_default_candidate(user_message):
            return None

        return parse_router_decision(
            {
                "pipeline_session_id": base.pipeline_session_id,
                "router_subagent_id": base.router_subagent_id,
                "status": "no_specialized_pipeline",
                "selected_pipeline_id": None,
                "fallback_pipeline_id": DEFAULT_PIPELINE_ID,
                "confidence": max(base.confidence, 0.76),
                "reasoning_summary": "The LLM router failed, but the request matched a clear non-engineering conversation pattern, so Hermes used the safe default conversation fallback.",
                "requires_clarification": False,
                "policy_block_reason": base.policy_block_reason,
                "routing_failure_reason": failure_reason,
                "matched_signals": list(base.matched_signals),
                "alternatives": [alternative.__dict__ for alternative in base.alternatives],
                "fallback_safe": True,
                "selected_provider": self._provider,
                "selected_model": self._model,
                "actual_provider": self._provider,
                "actual_model": self._model,
                "invalid_confidence_kind": invalid_confidence_kind,
                "invalid_confidence_summary": invalid_confidence_summary,
                "invalid_router_contract_kind": invalid_router_contract_kind,
                "invalid_router_contract_summary": invalid_router_contract_summary,
                "routing_fallback_used": True,
                "routing_fallback_reason": failure_reason,
                "router_strategy": "heuristic_timeout_default_fallback",
            },
            loaded_specs=self._loaded_specs,
        )

    def _is_clear_non_engineering_default_candidate(self, user_message: str) -> bool:
        normalized = _normalize_text(user_message.strip())
        if _ENGINEERING_PATH_PATTERN.search(normalized):
            return False
        if _matches_any(normalized, _ENGINEERING_MUTATION_KEYWORDS):
            return False
        if _matches_any(normalized, _ENGINEERING_DEBUG_KEYWORDS):
            return False
        return _matches_any(normalized, _HEURISTIC_DEFAULT_CONVERSATION_KEYWORDS)

    def _is_heuristic_engineering_fallback_failure(
        self,
        failure_reason: str,
        *,
        failure_code: str | None,
        invalid_confidence_kind: str | None,
        invalid_router_contract_kind: str | None,
    ) -> bool:
        normalized_reason = (failure_reason or "").strip().lower()
        if normalized_reason.startswith("timeouterror:"):
            return True
        if "jsondecodeerror" in normalized_reason:
            return True
        if failure_code == "llm_invalid_confidence" and invalid_confidence_kind is not None:
            return True
        return False

    def _is_strong_engineering_fallback_candidate(
        self,
        user_message: str,
        base: RouterDecision,
    ) -> bool:
        if base.status != "selected" or base.selected_pipeline_id != ENGINEERING_PIPELINE_ID:
            return False
        normalized = _normalize_text(user_message.strip())
        if not _ENGINEERING_PATH_PATTERN.search(normalized):
            return False
        if not (
            _matches_any(normalized, _ENGINEERING_MUTATION_KEYWORDS)
            or _matches_any(normalized, _HEURISTIC_ENGINEERING_FALLBACK_MUTATION_KEYWORDS)
        ):
            return False
        if _looks_ambiguous(normalized):
            return False

        old_smoke_marker_shape = (
            _matches_any(normalized, _HEURISTIC_ENGINEERING_FALLBACK_TEST_KEYWORDS)
            and _matches_any(normalized, _HEURISTIC_ENGINEERING_FALLBACK_ANCHORS)
        )
        runtime_analysis_shape = (
            _matches_any(normalized, _HEURISTIC_RUNTIME_ANALYSIS_PIPELINE_ANCHORS)
            and _matches_any(normalized, _HEURISTIC_RUNTIME_ANALYSIS_TOOL_REPO_ANCHORS)
            and _matches_any(normalized, _HEURISTIC_RUNTIME_ANALYSIS_SAFETY_ANCHORS)
        )
        return old_smoke_marker_shape or runtime_analysis_shape


def build_pipeline_router(
    *,
    config: dict[str, Any] | None,
    loaded_specs: LoadedPipelineSpecs | None = None,
    repo_root: Path | str | None = None,
) -> PipelineRouter:
    specs = loaded_specs or load_pipeline_specs(repo_root=repo_root)
    strategy = _pipeline_router_strategy(config)
    if strategy == "llm":
        llm_cfg = _router_llm_config(config, loaded_specs=specs)
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
        invalid_confidence_kind=_optional_str(data.get("invalid_confidence_kind")),
        invalid_confidence_summary=_optional_str(data.get("invalid_confidence_summary")),
        invalid_router_contract_kind=_optional_str(data.get("invalid_router_contract_kind")),
        invalid_router_contract_summary=_optional_str(data.get("invalid_router_contract_summary")),
        dropped_alternatives_count=int(data.get("dropped_alternatives_count", 0) or 0),
        dropped_alternatives_reasons=tuple(
            str(entry).strip()
            for entry in data.get("dropped_alternatives_reasons", [])
            if str(entry).strip()
        ),
        routing_fallback_used=bool(data.get("routing_fallback_used", False)),
        routing_fallback_reason=_optional_str(data.get("routing_fallback_reason")),
        router_strategy=_optional_str(data.get("router_strategy")),
        routing_confidence_source=_optional_str(data.get("routing_confidence_source")),
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

    alternatives = data.get("alternatives", [])
    if not isinstance(alternatives, list):
        raise RouterDecisionValidationError("alternatives must be a list")
    if "dropped_alternatives_count" in data and not isinstance(data.get("dropped_alternatives_count"), int):
        raise RouterDecisionValidationError("dropped_alternatives_count must be an integer")
    dropped_alternatives_reasons = data.get("dropped_alternatives_reasons", [])
    if not isinstance(dropped_alternatives_reasons, list):
        raise RouterDecisionValidationError("dropped_alternatives_reasons must be a list")
    for index, alternative in enumerate(alternatives):
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


def _router_llm_config(config: dict[str, Any] | None, loaded_specs: LoadedPipelineSpecs | None = None) -> dict[str, Any]:
    router_model = _router_default_model(loaded_specs) if loaded_specs is not None else {}
    default_provider = str(router_model.get("provider") or DEFAULT_ROUTER_LLM_PROVIDER).strip()
    default_model = str(router_model.get("model") or DEFAULT_ROUTER_LLM_MODEL).strip()
    provider = str(
        _nested_config_value(config, "pipelines", "router", "llm", "provider", default=default_provider)
        or default_provider
    ).strip()
    model = str(
        _nested_config_value(config, "pipelines", "router", "llm", "model", default=default_model)
        or default_model
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
            default=str((loaded_specs.subagent_specs.get(DEFAULT_ROUTER_SUBAGENT_ID, {}) or {}).get("failure_policy", {}).get("model_unavailable", "fail_closed")).replace("fail closed", "fail_closed") if loaded_specs is not None else DEFAULT_LLM_FALLBACK_STRATEGY,
        )
        or (str((loaded_specs.subagent_specs.get(DEFAULT_ROUTER_SUBAGENT_ID, {}) or {}).get("failure_policy", {}).get("model_unavailable", "fail_closed")).replace("fail closed", "fail_closed") if loaded_specs is not None else DEFAULT_LLM_FALLBACK_STRATEGY)
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


def _build_router_messages(
    loaded_specs: LoadedPipelineSpecs,
    user_message: str,
    *,
    routing_context: dict[str, Any] | None = None,
    candidate_hints: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    registry_blob = json.dumps(_router_registry_prompt_context(loaded_specs), ensure_ascii=False, indent=2, sort_keys=True)
    request_context_blob = json.dumps(
        {
            "user_message_safe_summary": _safe_router_message_summary(user_message),
            "platform_context": (routing_context or {}).get("platform_context", {}),
            "session_context": (routing_context or {}).get("session_context", {}),
            "recent_pipeline_state": (routing_context or {}).get("recent_pipeline_state", {}),
            "safety_constraints": (routing_context or {}).get("safety_constraints", {}),
            "candidate_hints": candidate_hints or {},
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return [
        {
            "role": "system",
            "content": (
                "You are the Hermes pipeline router. Select only from the declared pipeline registry. "
                "Use semantic intent, requested mutations, and target paths. "
                "Deterministic keywords are incomplete; rely on the registry semantics instead. "
                "Never invent pipeline ids or statuses. If the request is ambiguous between read-only audit and mutation, "
                "return needs_clarification. If the request asks for unsafe bypass, secret exfiltration, or destructive misuse, "
                "return blocked_by_policy. Return JSON only. confidence must be a JSON number between 0 and 1 inclusive. "
                "Do not return confidence as a string, percentage, labels, null, missing field, or 0..100 scale. "
                "Every alternatives confidence entry must follow the same numeric 0..1 contract. "
                "alternatives are optional; omit alternatives if unsure. Every alternative must use a valid known pipeline_id, and you must never return pipeline_id null in alternatives. "
                "status must be exactly one of: selected, no_specialized_pipeline, needs_clarification, blocked_by_policy, routing_failed. "
                "Pipeline ids must never appear in status. default_conversation_pipeline is a pipeline id or fallback, never a status. "
                "If no specialized pipeline is selected, return status no_specialized_pipeline, selected_pipeline_id null, and fallback_pipeline_id default_conversation_pipeline. "
                "If selecting engineering, return status selected, selected_pipeline_id engineering_review_pipeline, and fallback_pipeline_id null. "
                "Do not place the selected pipeline id into status, pipeline_id, pipeline, selected_pipeline, fallback_pipeline_id, reasoning_summary, or alternatives unless the schema field explicitly requires it."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Pipeline registry and schema:\n{registry_blob}\n\n"
                f"Structured routing request context:\n{request_context_blob}\n\n"
                "Compact examples:\n"
                'Example A - default or ordinary prompt:\n'
                '{\n'
                '  "status": "no_specialized_pipeline",\n'
                '  "selected_pipeline_id": null,\n'
                '  "fallback_pipeline_id": "default_conversation_pipeline",\n'
                '  "confidence": 0.8,\n'
                '  "requires_clarification": false,\n'
                '  "fallback_safe": true,\n'
                '  "reasoning_summary": "Ordinary conversation should use the default pipeline."\n'
                '}\n\n'
                'Example B - engineering prompt:\n'
                '{\n'
                '  "status": "selected",\n'
                '  "selected_pipeline_id": "engineering_review_pipeline",\n'
                '  "fallback_pipeline_id": null,\n'
                '  "confidence": 0.8,\n'
                '  "requires_clarification": false,\n'
                '  "fallback_safe": false,\n'
                '  "reasoning_summary": "The request asks to modify code or tests."\n'
                '}\n\n'
                'Example C - recruiter or career writing prompt:\n'
                '{\n'
                '  "status": "no_specialized_pipeline",\n'
                '  "selected_pipeline_id": null,\n'
                '  "fallback_pipeline_id": "default_conversation_pipeline",\n'
                '  "confidence": 0.8,\n'
                '  "requires_clarification": false,\n'
                '  "fallback_safe": true,\n'
                '  "reasoning_summary": "Career writing is not an engineering code-change pipeline."\n'
                '}\n\n'
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

    # The Codex chat-completions shim may not enforce extra_body.response_format
    # end-to-end, so parse_router_decision remains the safety boundary.
    response = client.chat.completions.create(
        model=resolved_model or model,
        messages=messages,
        timeout=timeout_seconds,
        extra_body=_router_response_format_payload(),
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


def _router_response_format_payload() -> dict[str, Any]:
    return json.loads(json.dumps(_ROUTER_RESPONSE_FORMAT))


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


def _invalid_confidence_diagnostic(payload: dict[str, Any]) -> dict[str, str] | None:
    if "confidence" not in payload:
        return {"kind": "missing", "summary": "missing"}
    raw = payload.get("confidence")
    if raw is None:
        return {"kind": "null", "summary": _summarize_confidence_value(raw)}
    if isinstance(raw, bool):
        return {"kind": "non_numeric", "summary": _summarize_confidence_value(raw)}
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return {"kind": "non_numeric", "summary": _summarize_confidence_value(raw)}
    if value < 0.0:
        return {"kind": "out_of_range_low", "summary": _summarize_confidence_value(raw)}
    if value > 1.0:
        return {"kind": "out_of_range_high", "summary": _summarize_confidence_value(raw)}
    return None


def _invalid_router_contract_diagnostic(payload: dict[str, Any]) -> dict[str, str] | None:
    status = payload.get("status")
    if status not in VALID_ROUTER_STATUSES:
        summary = _summarize_router_contract_value(status)
        if _looks_like_pipeline_id(status):
            summary = f"{summary}, looks_like_pipeline_id=True"
        return {
            "kind": "invalid_status",
            "reason": f"Unknown router status: {status!r}",
            "summary": summary,
        }

    if status == "selected":
        raw_selected_pipeline_id = payload.get("selected_pipeline_id")
        if raw_selected_pipeline_id is None or not isinstance(raw_selected_pipeline_id, str) or not raw_selected_pipeline_id.strip():
            return {
                "kind": "selected_missing_pipeline_id",
                "reason": "Router status 'selected' requires selected_pipeline_id",
                "summary": f"selected_pipeline_id={_summarize_router_contract_value(raw_selected_pipeline_id)}",
            }
    return None


def _sanitize_router_payload(
    payload: dict[str, Any],
    *,
    loaded_specs: LoadedPipelineSpecs,
) -> dict[str, Any]:
    sanitized = dict(payload)
    alternatives = sanitized.get("alternatives", [])
    cleaned_alternatives, dropped_reasons = _sanitize_advisory_alternatives(
        alternatives,
        registered_pipeline_ids=set(loaded_specs.pipeline_specs),
    )
    sanitized["alternatives"] = cleaned_alternatives
    sanitized["dropped_alternatives_count"] = len(dropped_reasons)
    sanitized["dropped_alternatives_reasons"] = dropped_reasons
    return sanitized


def _sanitize_advisory_alternatives(
    alternatives: Any,
    *,
    registered_pipeline_ids: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    if alternatives is None:
        return [], []
    if not isinstance(alternatives, list):
        return [], ["invalid_alternatives_container"]

    cleaned: list[dict[str, Any]] = []
    dropped_reasons: list[str] = []
    for alternative in alternatives:
        if not isinstance(alternative, dict):
            dropped_reasons.append("invalid_alternative_mapping")
            continue
        if "pipeline_id" not in alternative:
            dropped_reasons.append("missing_pipeline_id")
            continue
        pipeline_id = alternative.get("pipeline_id")
        if pipeline_id is None:
            dropped_reasons.append("null_pipeline_id")
            continue
        if not isinstance(pipeline_id, str):
            dropped_reasons.append("non_string_pipeline_id")
            continue
        normalized_pipeline_id = pipeline_id.strip()
        if not normalized_pipeline_id:
            dropped_reasons.append("empty_pipeline_id")
            continue
        if normalized_pipeline_id not in registered_pipeline_ids:
            dropped_reasons.append("unknown_pipeline_id")
            continue
        cleaned.append(
            {
                **alternative,
                "pipeline_id": normalized_pipeline_id,
            }
        )
    return cleaned, dropped_reasons


def _summarize_router_contract_value(raw: Any) -> str:
    if raw is None:
        return "NoneType(null)"
    if isinstance(raw, bool):
        return f"bool({raw})"
    if isinstance(raw, (int, float)):
        return f"{type(raw).__name__}({raw})"
    if isinstance(raw, str):
        category = "pipeline_id_like" if _looks_like_pipeline_id(raw) else ("redacted" if _looks_sensitive_string(raw) else "text")
        return f"str(len={len(raw)}, category={category})"
    return type(raw).__name__


def _looks_like_pipeline_id(raw: Any) -> bool:
    if not isinstance(raw, str):
        return False
    normalized = raw.strip().lower()
    if not normalized:
        return False
    return normalized in {DEFAULT_PIPELINE_ID, ENGINEERING_PIPELINE_ID} or normalized.endswith("_pipeline")


def _summarize_confidence_value(raw: Any) -> str:
    if raw is None:
        return "NoneType(null)"
    if isinstance(raw, bool):
        return f"bool({raw})"
    if isinstance(raw, (int, float)):
        return f"{type(raw).__name__}({raw})"
    if isinstance(raw, str):
        category = "redacted" if _looks_sensitive_string(raw) else "text"
        return f"str(len={len(raw)}, category={category})"
    return type(raw).__name__


def _looks_sensitive_string(raw: str) -> bool:
    normalized = raw.strip().lower()
    if any(marker in normalized for marker in ("sk-", "token", "secret", "bearer", "apikey", "api_key")):
        return True
    return len(normalized) >= 12 and any(ch.isdigit() for ch in normalized) and any(ch.isalpha() for ch in normalized)


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


def _safe_router_message_summary(user_message: str, limit: int = 240) -> str:
    normalized = _normalize_text(user_message.strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def _exception_summary(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return type(exc).__name__
    return f"{type(exc).__name__}: {message}"
