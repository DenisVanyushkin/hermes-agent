"""Step 5B — prompt iteration llm-obs-1.1.0 (offline).

1.1.0 adds one review-scoped change: mandate signals may not be inferred
from company-level evidence (scale, brand, ecosystem, valuation, HR
boilerplate). 1.0.0 stays FROZEN and byte-identical — old recordings must
replay forever.
"""
from __future__ import annotations

import json

import pytest

from job_intel.vacancy_understanding.semantic.contract import load_semantic_contract
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    PROMPT_BUILDERS,
    FROZEN_PROMPT_V1_0_0_SHA256,
    LLMObservationProvider,
    LLMProviderError,
    RecordingStore,
    build_prompt,
    build_prompt_for_version,
)

CONTRACT = load_semantic_contract()


def test_prompt_registry_has_both_versions():
    assert set(PROMPT_BUILDERS) == {"llm-obs-1.0.0", "llm-obs-1.1.0"}


def test_v1_0_0_is_frozen_byte_identical():
    import hashlib
    prompt = build_prompt_for_version("llm-obs-1.0.0", CONTRACT)
    assert prompt == build_prompt(CONTRACT)  # default builder is still 1.0.0
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    assert digest == FROZEN_PROMPT_V1_0_0_SHA256, (
        "the 1.0.0 prompt changed — every recording keyed on it would be "
        "orphaned; freeze is inviolable")


def test_v1_1_0_differs_and_adds_mandate_gating():
    v10 = build_prompt_for_version("llm-obs-1.0.0", CONTRACT)
    v11 = build_prompt_for_version("llm-obs-1.1.0", CONTRACT)
    assert v11 != v10
    low = v11.lower()
    assert "mandate" in low
    # names the failure mode the owner review identified
    assert "company" in low
    for cue in ("scale", "valuation", "brand"):
        assert cue in low, f"1.1.0 should name company-level cue '{cue}'"


def test_v1_1_0_keeps_vocabulary_and_hard_rules_intact():
    # the change is additive: the allowed-signal vocabulary and the verbatim
    # rules must survive unchanged (only mandate-gating is new)
    v11 = build_prompt_for_version("llm-obs-1.1.0", CONTRACT)
    assert "Allowed signals and values" in v11
    assert "EXACT verbatim substring" in v11


def test_unknown_prompt_version_rejected():
    with pytest.raises(LLMProviderError, match="unknown_prompt_version"):
        build_prompt_for_version("llm-obs-9.9.9", CONTRACT)


def test_provider_defaults_to_frozen_version(tmp_path):
    p = LLMObservationProvider(store=RecordingStore(tmp_path / "r"), mode="replay")
    assert p.prompt_version == "llm-obs-1.0.0"


def test_provider_uses_selected_prompt_version(tmp_path):
    p = LLMObservationProvider(store=RecordingStore(tmp_path / "r"), mode="replay",
                              prompt_version="llm-obs-1.1.0")
    assert p.prompt_version == "llm-obs-1.1.0"
    assert p._prompt == build_prompt_for_version("llm-obs-1.1.0", CONTRACT)


def test_input_hash_changes_with_prompt_version(tmp_path):
    a = LLMObservationProvider(store=RecordingStore(tmp_path / "a"), mode="replay",
                              prompt_version="llm-obs-1.0.0")
    b = LLMObservationProvider(store=RecordingStore(tmp_path / "b"), mode="replay",
                              prompt_version="llm-obs-1.1.0")
    args = dict(title="Head of Growth", text="Own the roadmap.", structured={})
    assert a.input_hash(**args) != b.input_hash(**args)


def test_replaying_wrong_prompt_version_is_blocked(tmp_path):
    """A 1.0.0 recording replayed by a 1.1.0 provider must never be served.
    Because prompt_version is part of input_hash, the 1.1.0 provider looks up
    a DIFFERENT key entirely and gets recording_missing — stronger isolation
    than an in-record version check (the two can never collide)."""
    store = RecordingStore(tmp_path / "r")

    class _Msg:
        content = json.dumps({"observations": []})

    class _Choice:
        message = _Msg()

    class _Usage:
        prompt_tokens = completion_tokens = total_tokens = 1

    class _Resp:
        choices = [_Choice()]
        usage = _Usage()
        model = "openai/gpt-5-mini"

    class _T:
        class chat:
            class completions:
                @staticmethod
                def create(**k):
                    return _Resp()

    rec = LLMObservationProvider(store=store, mode="record", transport=_T(),
                                prompt_version="llm-obs-1.0.0")
    rec.extract_semantic_observations(title="T", text="x", structured={})
    replay11 = LLMObservationProvider(store=store, mode="replay",
                                     prompt_version="llm-obs-1.1.0")
    with pytest.raises(LLMProviderError, match="recording_missing"):
        replay11.extract_semantic_observations(title="T", text="x", structured={})
    # the frozen 1.0.0 provider still replays the same recording fine
    replay10 = LLMObservationProvider(store=store, mode="replay",
                                     prompt_version="llm-obs-1.0.0")
    assert replay10.extract_semantic_observations(title="T", text="x", structured={}) == []


def test_registry_passes_prompt_version(tmp_path):
    from job_intel.vacancy_understanding.semantic.benchmark.provider_registry import (
        build_benchmark_provider,
    )
    _, identity = build_benchmark_provider({
        "type": "llm_replay", "store_dir": str(tmp_path / "r"),
        "model_id": "openai/gpt-5-mini", "prompt_version": "llm-obs-1.1.0"})
    assert identity["prompt_version"] == "llm-obs-1.1.0"


def test_registry_defaults_prompt_version_to_frozen(tmp_path):
    from job_intel.vacancy_understanding.semantic.benchmark.provider_registry import (
        build_benchmark_provider,
    )
    _, identity = build_benchmark_provider({
        "type": "llm_replay", "store_dir": str(tmp_path / "r"),
        "model_id": "openai/gpt-5-mini"})
    assert identity["prompt_version"] == "llm-obs-1.0.0"
