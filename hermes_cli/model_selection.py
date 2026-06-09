"""Deterministic model-policy selection for Hermes role execution.

This module is intentionally pure and import-light. It selects a model policy
and debug metadata from role/execution context without mutating provider
resolution, fallback chains, or runtime model state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any


_DEFAULT_PROVIDER = "openai-codex"
_DEFAULT_MODEL = "gpt-5.4-mini"
_CODING_MODEL = "gpt-5.3-codex"
_HIGH_REASONING_MODEL = "gpt-5.5"

_TRADING_ROLES = {"trading_observer_trader", "trading_observer_trader_deferred"}
_SIMPLE_RESEARCH_HINTS = {
    "weather",
    "news",
    "digest",
    "report",
    "btc",
    "bitcoin",
    "binance",
    "coinbase",
    "fees",
    "commissions",
    "lookup",
    "compare",
}
_COMPLEX_RESEARCH_HINTS = {
    "conflicting sources",
    "synthesis",
    "synthesize",
    "due diligence",
    "deep research",
    "high impact",
}


@dataclass(frozen=True)
class ModelSelectionDecision:
    selected_role: str
    canonical_role: str | None
    effective_role: str
    policy_name: str
    policy_class: str
    preferred_provider: str
    preferred_model: str
    fallback_chain_key: str
    allow_fallback: bool
    reasoning_level: str
    selection_reason: str
    debug_metadata: dict[str, Any]


def _normalize_role(role: str | None) -> str:
    return (role or "").strip().lower()


def _normalize_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _research_prefers_fast_lookup(task_text: str) -> bool:
    normalized = _normalize_text(task_text)
    if not normalized:
        return True
    if any(marker in normalized for marker in _COMPLEX_RESEARCH_HINTS):
        return False
    return any(marker in normalized for marker in _SIMPLE_RESEARCH_HINTS)


def _decision(
    *,
    selected_role: str,
    canonical_role: str | None,
    effective_role: str,
    policy_name: str,
    policy_class: str,
    preferred_model: str,
    fallback_chain_key: str,
    allow_fallback: bool,
    reasoning_level: str,
    selection_reason: str,
    task_text: str,
    critical_approval_required: bool,
) -> ModelSelectionDecision:
    return ModelSelectionDecision(
        selected_role=selected_role,
        canonical_role=canonical_role,
        effective_role=effective_role,
        policy_name=policy_name,
        policy_class=policy_class,
        preferred_provider=_DEFAULT_PROVIDER,
        preferred_model=preferred_model,
        fallback_chain_key=fallback_chain_key,
        allow_fallback=allow_fallback,
        reasoning_level=reasoning_level,
        selection_reason=selection_reason,
        debug_metadata={
            "live_model_mutation": False,
            "provider_refresh": False,
            "selected_via": "role_context",
            "critical_approval_required": critical_approval_required,
            "task_preview": str(task_text or "")[:120],
        },
    )


def select_model_policy(
    *,
    selected_role: str,
    canonical_role: str | None = None,
    task_text: str = "",
    critical_approval_required: bool = False,
) -> ModelSelectionDecision:
    """Return deterministic model-policy metadata for the current task."""
    normalized_selected = _normalize_role(selected_role)
    normalized_canonical = _normalize_role(canonical_role) or None
    effective_role = normalized_canonical or normalized_selected or "general_operator"

    if effective_role in _TRADING_ROLES or normalized_selected in _TRADING_ROLES:
        return _decision(
            selected_role=normalized_selected or selected_role,
            canonical_role=normalized_canonical,
            effective_role="general_operator",
            policy_name="general_default",
            policy_class="general_operator",
            preferred_model=_DEFAULT_MODEL,
            fallback_chain_key="configured_runtime_fallback_chain",
            allow_fallback=True,
            reasoning_level="default",
            selection_reason="Trading remains deferred/inactive, so Hermes falls back to the general operator policy.",
            task_text=task_text,
            critical_approval_required=critical_approval_required,
        )

    if critical_approval_required or effective_role == "security_auditor":
        reason = (
            "Critical approval is required before any sensitive mutation-capable action."
            if critical_approval_required
            else "Security review tasks use the conservative approval-critical policy."
        )
        return _decision(
            selected_role=normalized_selected or selected_role,
            canonical_role=normalized_canonical,
            effective_role=effective_role,
            policy_name="approval_critical",
            policy_class="approval_critical",
            preferred_model=_HIGH_REASONING_MODEL,
            fallback_chain_key="stop_and_escalate",
            allow_fallback=False,
            reasoning_level="high",
            selection_reason=reason,
            task_text=task_text,
            critical_approval_required=critical_approval_required,
        )

    if effective_role == "engineer":
        return _decision(
            selected_role=normalized_selected or selected_role,
            canonical_role=normalized_canonical,
            effective_role=effective_role,
            policy_name="coding_high_reasoning",
            policy_class="coding",
            preferred_model=_CODING_MODEL,
            fallback_chain_key="configured_runtime_fallback_chain",
            allow_fallback=True,
            reasoning_level="high",
            selection_reason="Engineer tasks use the coding/engineering policy for repo work, debugging, and tests.",
            task_text=task_text,
            critical_approval_required=critical_approval_required,
        )

    if effective_role == "researcher":
        prefers_fast_lookup = _research_prefers_fast_lookup(task_text)
        return _decision(
            selected_role=normalized_selected or selected_role,
            canonical_role=normalized_canonical,
            effective_role=effective_role,
            policy_name="research_fast_lookup" if prefers_fast_lookup else "research_reasoning",
            policy_class="research",
            preferred_model=_DEFAULT_MODEL if prefers_fast_lookup else _HIGH_REASONING_MODEL,
            fallback_chain_key="configured_runtime_fallback_chain",
            allow_fallback=True,
            reasoning_level="balanced" if prefers_fast_lookup else "high",
            selection_reason="Research tasks use a fast lookup path for simple fact gathering and stronger reasoning for synthesis.",
            task_text=task_text,
            critical_approval_required=critical_approval_required,
        )

    if effective_role == "scribe":
        return _decision(
            selected_role=normalized_selected or selected_role,
            canonical_role=normalized_canonical,
            effective_role=effective_role,
            policy_name="scribe_stable_text",
            policy_class="scribe",
            preferred_model=_DEFAULT_MODEL,
            fallback_chain_key="configured_runtime_fallback_chain",
            allow_fallback=True,
            reasoning_level="stable",
            selection_reason="Scribe tasks prefer the stable text policy instead of the highest-reasoning path.",
            task_text=task_text,
            critical_approval_required=critical_approval_required,
        )

    if effective_role == "career_strategist":
        return _decision(
            selected_role=normalized_selected or selected_role,
            canonical_role=normalized_canonical,
            effective_role=effective_role,
            policy_name="career_balanced_reasoning",
            policy_class="career_strategist",
            preferred_model=_DEFAULT_MODEL,
            fallback_chain_key="configured_runtime_fallback_chain",
            allow_fallback=True,
            reasoning_level="balanced",
            selection_reason="Career strategy tasks use the writing/reasoning balanced policy.",
            task_text=task_text,
            critical_approval_required=critical_approval_required,
        )

    return _decision(
        selected_role=normalized_selected or selected_role or "general_operator",
        canonical_role=normalized_canonical,
        effective_role="general_operator",
        policy_name="general_default",
        policy_class="general_operator",
        preferred_model=_DEFAULT_MODEL,
        fallback_chain_key="configured_runtime_fallback_chain",
        allow_fallback=True,
        reasoning_level="default",
        selection_reason="General operator tasks use the default general model policy.",
        task_text=task_text,
        critical_approval_required=critical_approval_required,
    )


def model_selection_to_dict(selection: ModelSelectionDecision | dict[str, Any]) -> dict[str, Any]:
    if isinstance(selection, dict):
        return dict(selection)
    if not isinstance(selection, ModelSelectionDecision):
        raise TypeError("model_selection_to_dict expects ModelSelectionDecision or dict")
    return asdict(selection)
