"""Provider construction boundary (Step 5B, Slice 5B-1).

This module is the ONLY place in the benchmark package allowed to know that
"deterministic-phrase" and "llm-observation" are different implementations.
Everything downstream (runner.py, the semantic runtime, the decision
engine) receives a plain SemanticProvider and never branches on
provider_id — that boundary is what Provider Contract 1.0.0 §9 and the
Step 5B task require.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from job_intel.vacancy_understanding.semantic.contract import (
    SemanticFactContract,
    load_semantic_contract,
)
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    LLMObservationProvider,
    RecordingStore,
    build_prompt,
)
from job_intel.vacancy_understanding.semantic.runtime.provider import (
    DeterministicPhraseProvider,
)

from .hashing import sha256_json, sha256_text

_PROVIDER_MODULE = Path(__file__).resolve().parents[1] / "runtime" / "provider.py"


class ProviderRegistryError(ValueError):
    pass


def build_benchmark_provider(
    spec: dict[str, Any], *, contract: SemanticFactContract | None = None,
) -> tuple[Any, dict[str, Any]]:
    """spec -> (provider instance, identity metadata for the manifest).

    spec["type"] in {"deterministic", "llm_replay"}. "llm_replay" is the
    ONLY LLM mode this registry constructs — a live/record mode requires
    the separately spend-gated build_live_llm_provider() and is never
    reached through this boundary (Slice 5B-1 is offline-only per task).
    """
    contract = contract or load_semantic_contract()
    kind = spec.get("type")

    if kind == "deterministic":
        provider = DeterministicPhraseProvider()
        identity = {
            "provider_id": provider.provider_id,
            "prompt_version": provider.prompt_version,
            "provider_version": sha256_text(_PROVIDER_MODULE.read_text())[:16],
            "provider_config_hash": sha256_json({}),
            "model_requested": None,
            "model_actual": None,
            "transport": None,
            "temperature": None,
            # Policy/metadata facts about THIS provider kind — decided once,
            # here, at the construction boundary. The runner reads these
            # fields; it never compares provider_id itself.
            "retry_policy": "n/a (no transport)",
            "fallback_policy": "n/a (no transport)",
            "recording_format_version": None,
            "reports_usage_metadata": False,
            "cost_known_zero": True,
        }
        return provider, identity

    if kind == "llm_replay":
        store_dir = spec.get("store_dir")
        model_id = spec.get("model_id")
        if not store_dir or not model_id:
            raise ProviderRegistryError(
                "llm_replay spec requires 'store_dir' and 'model_id'")
        provider = LLMObservationProvider(
            store=RecordingStore(store_dir), mode="replay",
            model_id=model_id, contract=contract)
        config_for_hash = {"model_id": model_id}  # store_dir is a path, not identity
        identity = {
            "provider_id": provider.provider_id,
            "prompt_version": provider.prompt_version,
            "provider_version": sha256_text(build_prompt(contract))[:16],
            "provider_config_hash": sha256_json(config_for_hash),
            "model_requested": model_id,
            "model_actual": None,  # filled per-case from recording; manifest carries the requested identity
            "transport": "openrouter",
            "temperature": 0.0,
            "retry_policy": "max_retries=0",
            "fallback_policy": "allow_fallbacks=false",
            "recording_format_version": "1.0",
            "reports_usage_metadata": True,
            "cost_known_zero": False,
        }
        return provider, identity

    raise ProviderRegistryError(f"unknown provider spec type: {kind!r}")
