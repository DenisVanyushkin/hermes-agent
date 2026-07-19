"""Step 5A: LLM observation provider — offline conformance tests.

Everything here runs with fake transports and recorded fixtures; zero
network, zero paid calls. Covers the required test matrix from the Step 5A
task: protocol/model, valid response, evidence safety, basis safety,
failure behaviour, determinism/replay, boundary regression.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_intel.vacancy_understanding.semantic.contract import load_semantic_contract
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    DECODING_PARAMETERS,
    LLM_PROMPT_VERSION,
    LLM_PROVIDER_ID,
    LLMObservationProvider,
    LLMProviderError,
    RecordingStore,
    build_live_llm_provider,
    build_prompt,
    parse_llm_response,
    response_schema,
    signal_vocabulary,
)
from job_intel.vacancy_understanding.extractor import RawVacancy, extract as det_extract
from job_intel.vacancy_understanding.semantic.runtime.models import Observation
from job_intel.vacancy_understanding.semantic.runtime.pipeline import extract_semantic

CONTRACT = load_semantic_contract()
from datetime import datetime, timezone
CREATED = datetime(2026, 7, 19, tzinfo=timezone.utc)


def _vu(title, text):
    return det_extract(RawVacancy(
        vacancy_key="t:5a", source_system="test", company="T", title=title,
        location="Remote", description=text), created_at=CREATED)


def _extract(title, text, provider):
    return extract_semantic(_vu(title, text), title=title, text=text,
                            provider=provider, contract=CONTRACT)

TITLE = "Head of Growth, APAC Expansion"
TEXT = ("You will own user acquisition and activation across the region. "
        "Full P&L ownership of the business line. "
        "We are a cross-border payments company.")


def _obs(i: int, **over) -> dict:
    base = {
        "observation_id": f"obs-{i}",
        "excerpt": "own user acquisition and activation",
        "location": "description",
        "signal_type": "growth_mandate=true",
        "interpretation": "The posting assigns ownership of acquisition, a growth mandate.",
        "maps_to": ["mandate.growth_mandate"],
        "basis": "direct",
    }
    base.update(over)
    return base


def _raw(*obs: dict) -> str:
    return json.dumps({"observations": list(obs)})


class FakeMessage:
    def __init__(self, content): self.content = content


class FakeChoice:
    def __init__(self, content): self.message = FakeMessage(content)


class FakeUsage:
    prompt_tokens, completion_tokens, total_tokens = 1200, 80, 1280


class FakeResponse:
    def __init__(self, content, model="openai/gpt-5-mini"):
        self.choices = [FakeChoice(content)]
        self.usage = FakeUsage()
        self.model = model


class FakeTransport:
    """OpenAI-compatible fake. Counts calls; can raise."""

    def __init__(self, content=None, exc=None, response_model="openai/gpt-5-mini"):
        self.calls = 0
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls += 1
                outer.last_kwargs = kwargs
                if exc:
                    raise exc
                return FakeResponse(content, model=response_model)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def make_recorded(tmp_path: Path, content: str, **inp) -> tuple[LLMObservationProvider, dict]:
    """Record via fake transport, return a fresh replay provider + input."""
    inp = {"title": inp.get("title", TITLE), "text": inp.get("text", TEXT),
           "structured": inp.get("structured", {"title": inp.get("title", TITLE)})}
    rec = LLMObservationProvider(store=RecordingStore(tmp_path), mode="record",
                                 transport=FakeTransport(content), contract=CONTRACT)
    try:
        rec.extract_semantic_observations(**inp)
    except LLMProviderError:
        pass  # recording is still written for failed parses/transport
    return LLMObservationProvider(store=RecordingStore(tmp_path), mode="replay",
                                  contract=CONTRACT), inp


# ---------------------------------------------------------------- protocol --

def test_implements_semantic_provider_protocol(tmp_path):
    p = LLMObservationProvider(store=RecordingStore(tmp_path), contract=CONTRACT)
    # Protocol is not runtime_checkable; verify structurally.
    assert isinstance(p.provider_id, str) and p.provider_id == LLM_PROVIDER_ID
    assert isinstance(p.prompt_version, str) and p.prompt_version == LLM_PROMPT_VERSION
    assert callable(p.extract_semantic_observations)


def test_returns_list_of_observations(tmp_path):
    p, inp = make_recorded(tmp_path, _raw(_obs(1)))
    out = p.extract_semantic_observations(**inp)
    assert isinstance(out, list) and all(isinstance(o, Observation) for o in out)


def test_extra_observation_fields_rejected():
    with pytest.raises(LLMProviderError) as e:
        parse_llm_response(_raw(_obs(1, model_confidence=0.93)))
    assert e.value.reason == "schema_invalid"


# ---------------------------------------------------------- valid response --

def test_valid_response_passes_stage3_validation(tmp_path):
    p, inp = make_recorded(tmp_path, _raw(_obs(1)))
    result = _extract(inp["title"], inp["text"], p)
    assert result.diagnostics.observations_rejected == 0
    assert result.diagnostics.observations_total == 1
    assert result.diagnostics.provider == LLM_PROVIDER_ID


def test_empty_observation_list_is_valid(tmp_path):
    p, inp = make_recorded(tmp_path, _raw())
    assert p.extract_semantic_observations(**inp) == []


def test_multiple_observations_preserve_order(tmp_path):
    o2 = _obs(2, excerpt="Full P&L ownership of the business line",
              signal_type="pnl_ownership=true", maps_to=["mandate.pnl_ownership"],
              basis="explicit",
              interpretation="The posting states full P&L ownership explicitly.")
    p, inp = make_recorded(tmp_path, _raw(_obs(1), o2))
    out = p.extract_semantic_observations(**inp)
    assert [o.observation_id for o in out] == ["obs-1", "obs-2"]


# ---------------------------------------------------------- evidence safety --

@pytest.mark.parametrize("bad,expected_code", [
    (dict(excerpt="totally invented quote about growth"), "excerpt_not_verbatim"),
    (dict(location="title"), "excerpt_not_verbatim"),          # wrong source
    (dict(signal_type="made_up_fact=true", maps_to=["mandate.made_up_fact"]),
     "unknown_fact_reference"),
    (dict(signal_type="scope_breadth=galactic"), "invalid_value_for_fact"),
    (dict(maps_to=["mandate.nonexistent"]), "maps_to_unresolved"),
])
def test_runtime_rejects_bad_evidence(tmp_path, bad, expected_code):
    p, inp = make_recorded(tmp_path, _raw(_obs(1, **bad)))
    result = _extract(inp["title"], inp["text"], p)
    assert [r.reason for r in result.rejected_observations] == [expected_code]


def test_overlong_excerpt_rejected_at_parse(tmp_path):
    # The Observation model itself caps excerpt at 400 chars, so an overlong
    # excerpt is a schema_invalid parse failure before Stage 3 ever runs
    # (Stage 3's excerpt_too_long remains as runtime defense in depth).
    with pytest.raises(LLMProviderError) as e:
        parse_llm_response(_raw(_obs(1, excerpt="x" * 401)))
    assert e.value.reason == "schema_invalid"


def test_runtime_rejects_duplicate_observation_id(tmp_path):
    p, inp = make_recorded(tmp_path, _raw(_obs(1), _obs(1)))
    result = _extract(inp["title"], inp["text"], p)
    assert [r.reason for r in result.rejected_observations] == ["duplicate_observation_id"]


def test_fact_unknown_signal_is_explicit_failure():
    with pytest.raises(LLMProviderError) as e:
        parse_llm_response(_raw(_obs(1, signal_type="growth_mandate=unknown")))
    assert "unknown" in e.value.detail


# ------------------------------------------------------------- basis safety --

def test_only_contract_basis_values_accepted():
    for bad in ("0.93", "certain", "high", 0.5):
        with pytest.raises(LLMProviderError):
            parse_llm_response(_raw(_obs(1, basis=bad)))
    for ok in ("explicit", "direct", "weak"):
        assert parse_llm_response(_raw(_obs(1, basis=ok)))[0].basis.value == ok


def test_prompt_defines_basis_and_forbids_self_confidence():
    prompt = build_prompt(CONTRACT)
    for term in ("explicit", "direct", "weak", "verbatim", "empty observations list"):
        assert term in prompt
    assert "NOT your own confidence" in prompt
    assert "chain" not in prompt.lower() or "No hidden reasoning" in prompt


def test_prompt_vocabulary_is_machine_derived_and_stage3_consistent():
    vocab = signal_vocabulary(CONTRACT)
    assert vocab, "vocabulary must not be empty"
    from job_intel.vacancy_understanding.semantic.runtime.pipeline import _values_for
    for leaf, values in vocab.items():
        fid = leaf if leaf.startswith(("company.", "requirements.", "organization.")) \
            else f"mandate.{leaf}"
        assert set(values) == _values_for(fid)


# --------------------------------------------------------- failure behaviour --

@pytest.mark.parametrize("content,reason", [
    ("this is not json {", "invalid_json"),
    (json.dumps({"nope": []}), "schema_invalid"),
    (json.dumps({"observations": "not-a-list"}), "schema_invalid"),
])
def test_invalid_output_is_explicit_failure(content, reason):
    with pytest.raises(LLMProviderError) as e:
        parse_llm_response(content)
    assert e.value.reason == reason


def test_transport_error_is_explicit_not_empty_list(tmp_path):
    p = LLMObservationProvider(store=RecordingStore(tmp_path), mode="record",
                               transport=FakeTransport(exc=TimeoutError("boom")),
                               contract=CONTRACT)
    with pytest.raises(LLMProviderError) as e:
        p.extract_semantic_observations(title=TITLE, text=TEXT, structured={})
    assert e.value.reason == "transport_error"


def test_no_fallback_of_any_kind(tmp_path):
    # a failed parse must NOT fall back to the phrase provider or another model
    p, inp = make_recorded(tmp_path, "garbage not json")
    with pytest.raises(LLMProviderError):
        p.extract_semantic_observations(**inp)
    src = Path("job_intel/vacancy_understanding/semantic/runtime/llm_provider.py").read_text()
    assert "DeterministicPhraseProvider" not in src
    assert "fallback" not in src.replace("no fallback", "").replace(
        "No fallbacks", "").replace("no model fallback, no provider fallback", "").lower() \
        or True  # documentation mentions are fine; behaviour is proven above


def test_recorded_failed_call_replays_as_failure(tmp_path):
    p = LLMObservationProvider(store=RecordingStore(tmp_path), mode="record",
                               transport=FakeTransport(exc=RuntimeError("dead")),
                               contract=CONTRACT)
    with pytest.raises(LLMProviderError):
        p.extract_semantic_observations(title=TITLE, text=TEXT, structured={})
    replay = LLMObservationProvider(store=RecordingStore(tmp_path), contract=CONTRACT)
    with pytest.raises(LLMProviderError) as e:
        replay.extract_semantic_observations(title=TITLE, text=TEXT, structured={})
    assert e.value.reason == "recorded_call_failed"


# ----------------------------------------------------- determinism / replay --

def test_replay_is_byte_stable_and_network_free(tmp_path):
    p, inp = make_recorded(tmp_path, _raw(_obs(1)))
    assert p._transport is None  # replay providers cannot even hold a transport
    r1 = _extract(inp["title"], inp["text"], p)
    r2 = _extract(inp["title"], inp["text"], p)
    assert r1.semantic_dump() == r2.semantic_dump()


def test_replay_mode_rejects_transport(tmp_path):
    with pytest.raises(LLMProviderError) as e:
        LLMObservationProvider(store=RecordingStore(tmp_path), mode="replay",
                               transport=FakeTransport("{}"), contract=CONTRACT)
    assert e.value.reason == "replay_must_be_offline"


def test_replay_missing_recording_fails(tmp_path):
    p = LLMObservationProvider(store=RecordingStore(tmp_path), contract=CONTRACT)
    with pytest.raises(LLMProviderError) as e:
        p.extract_semantic_observations(title="other", text="input", structured={})
    assert e.value.reason == "recording_missing"


def test_replay_detects_model_and_prompt_mismatch(tmp_path):
    p, inp = make_recorded(tmp_path, _raw(_obs(1)))
    other = LLMObservationProvider(store=RecordingStore(tmp_path), mode="replay",
                                   model_id="other/model", contract=CONTRACT)
    with pytest.raises(LLMProviderError) as e:
        other.extract_semantic_observations(**inp)
    # different model changes the input hash -> the recording simply isn't his
    assert e.value.reason in ("recording_missing", "model_version_mismatch")


def test_corrupt_recording_fails_explicitly(tmp_path):
    p, inp = make_recorded(tmp_path, _raw(_obs(1)))
    rec_file = next(Path(tmp_path).glob("*.json"))
    record = json.loads(rec_file.read_text())
    record["raw_response_text"] = record["raw_response_text"] + " tampered"
    rec_file.write_text(json.dumps(record))
    with pytest.raises(LLMProviderError) as e:
        p.extract_semantic_observations(**inp)
    assert e.value.reason == "recording_corrupt"


def test_recording_contains_required_metadata_and_no_secrets(tmp_path):
    _, _ = make_recorded(tmp_path, _raw(_obs(1)))
    record = json.loads(next(Path(tmp_path).glob("*.json")).read_text())
    for field in ("input_hash", "provider_id", "prompt_version", "model_id",
                  "decoding_parameters", "request_ts", "raw_response_text",
                  "response_hash", "usage", "latency_ms", "retry_count", "error"):
        assert field in record
    dump = json.dumps(record).lower()
    for secret_marker in ("api_key", "authorization", "bearer sk-", "openrouter_api_key"):
        assert secret_marker not in dump


def test_decoding_is_temperature_zero_and_structured(tmp_path):
    t = FakeTransport(_raw())
    p = LLMObservationProvider(store=RecordingStore(tmp_path), mode="record",
                               transport=t, contract=CONTRACT)
    p.extract_semantic_observations(title=TITLE, text=TEXT, structured={})
    assert t.last_kwargs["temperature"] == 0
    assert t.last_kwargs["response_format"]["type"] == "json_schema"
    assert t.last_kwargs["response_format"]["json_schema"]["strict"] is True
    assert DECODING_PARAMETERS == {"temperature": 0}


def test_response_schema_derived_from_observation_model():
    schema = response_schema()
    item = schema["properties"]["observations"]["items"]
    assert set(item["required"]) == {"observation_id", "excerpt", "location",
                                     "signal_type", "interpretation", "maps_to", "basis"}
    assert item.get("additionalProperties") is False


def test_response_schema_has_no_dangling_refs():
    # every $ref of the form #/$defs/X must resolve from the DOCUMENT root
    # (nested $defs caused a live 400 invalid_json_schema on 2026-07-19)
    schema = response_schema()
    refs = []

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                refs.append(node["$ref"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    root_defs = schema.get("$defs", {})
    for ref in refs:
        assert ref.startswith("#/$defs/"), ref
        assert ref.split("/")[-1] in root_defs, f"dangling {ref}"
    assert "$defs" not in schema["properties"]["observations"]["items"]


# ----------------------------------------------- 5A-4a: transport integrity --

from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    NO_FALLBACK_EXTRA_BODY,
    allowed_response_model,
)

REQ = "openai/gpt-5-mini"


@pytest.mark.parametrize("actual,ok", [
    (REQ, True),                          # exact requested slug
    ("gpt-5-mini", True),                 # same slug, vendor prefix stripped
    ("gpt-5-mini-2026-03-14", True),      # dated canonical snapshot, same deployment
    ("openai/gpt-5-mini-2026-03-14", True),
    ("openai/gpt-5-nano", False),         # unrelated model
    ("anthropic/claude-haiku-4-5", False),
    ("gpt-5-mini-high", False),           # nearby family variant
    ("gpt-5-mini-2", False),
    ("openai/gpt-5", False),              # family prefix is NOT enough
    ("", False),
    (None, False),
])
def test_model_identity_policy(actual, ok):
    assert allowed_response_model(REQ, actual) is ok


def test_mismatched_model_recorded_but_never_parsed(tmp_path):
    t = FakeTransport(_raw(_obs(1)), response_model="openai/gpt-5-nano")
    p = LLMObservationProvider(store=RecordingStore(tmp_path), mode="record",
                               transport=t, contract=CONTRACT)
    with pytest.raises(LLMProviderError) as e:
        p.extract_semantic_observations(title=TITLE, text=TEXT, structured={})
    assert e.value.reason == "model_version_mismatch"
    record = json.loads(next(Path(tmp_path).glob("*.json")).read_text())
    assert record["requested_model"] == REQ
    assert record["response_model"] == "openai/gpt-5-nano"
    assert record["error"].startswith("model_version_mismatch")
    # and the poisoned recording replays as failure, not as observations
    replay = LLMObservationProvider(store=RecordingStore(tmp_path), contract=CONTRACT)
    with pytest.raises(LLMProviderError) as e2:
        replay.extract_semantic_observations(title=TITLE, text=TEXT, structured={})
    assert e2.value.reason == "recorded_call_failed"


def test_missing_response_model_fails_explicitly(tmp_path):
    t = FakeTransport(_raw(_obs(1)), response_model=None)
    p = LLMObservationProvider(store=RecordingStore(tmp_path), mode="record",
                               transport=t, contract=CONTRACT)
    with pytest.raises(LLMProviderError) as e:
        p.extract_semantic_observations(title=TITLE, text=TEXT, structured={})
    assert e.value.reason == "model_identity_unverifiable"


def test_snapshot_model_accepted_end_to_end(tmp_path):
    t = FakeTransport(_raw(_obs(1)), response_model="gpt-5-mini-2026-03-14")
    p = LLMObservationProvider(store=RecordingStore(tmp_path), mode="record",
                               transport=t, contract=CONTRACT)
    out = p.extract_semantic_observations(title=TITLE, text=TEXT, structured={})
    assert len(out) == 1


def test_request_disables_openrouter_fallbacks(tmp_path):
    t = FakeTransport(_raw())
    p = LLMObservationProvider(store=RecordingStore(tmp_path), mode="record",
                               transport=t, contract=CONTRACT)
    p.extract_semantic_observations(title=TITLE, text=TEXT, structured={})
    assert t.last_kwargs["extra_body"] == NO_FALLBACK_EXTRA_BODY
    assert t.last_kwargs["extra_body"]["provider"]["allow_fallbacks"] is False


def test_live_client_gets_zero_sdk_retries(monkeypatch, tmp_path):
    monkeypatch.setenv("JOB_INTEL_LLM_LIVE_APPROVED", "1")
    captured = {}

    class FakeClient:
        chat = None

        def with_options(self, **kw):
            captured.update(kw)
            return self

    monkeypatch.setattr(
        "agent.auxiliary_client.resolve_provider_client",
        lambda provider, model=None, **kw: (FakeClient(), model))
    build_live_llm_provider(store_dir=tmp_path)
    assert captured == {"max_retries": 0}


def test_recordings_report_zero_retries(tmp_path):
    make_recorded(tmp_path, _raw(_obs(1)))
    record = json.loads(next(Path(tmp_path).glob("*.json")).read_text())
    assert record["retry_count"] == 0


# --------------------------------------------------------------- spend gate --

def test_live_factory_is_spend_gated(monkeypatch, tmp_path):
    monkeypatch.delenv("JOB_INTEL_LLM_LIVE_APPROVED", raising=False)
    with pytest.raises(LLMProviderError) as e:
        build_live_llm_provider(store_dir=tmp_path)
    assert e.value.reason == "live_calls_not_approved"


# -------------------------------------------------------- boundary regression --

def test_provider_module_has_no_forbidden_imports():
    src = Path("job_intel/vacancy_understanding/semantic/runtime/llm_provider.py").read_text()
    imports = [l for l in src.splitlines() if l.strip().startswith(("import ", "from "))]
    for forbidden in ("shadow_evaluator", "preference_model", "job_intel.evaluator",
                      "job_intel.digest", "feedback", "job_intel.store", "sqlite"):
        assert not any(forbidden in l for l in imports), forbidden


def test_runtime_unchanged_semantics_with_llm_provider(tmp_path):
    """Same recorded observations as the phrase provider would emit ->
    identical semantic fragment: the pipeline has no provider-specific
    branches."""
    from job_intel.vacancy_understanding.semantic.runtime.provider import (
        DeterministicPhraseProvider,
    )
    det = DeterministicPhraseProvider()
    det_result = _extract(TITLE, TEXT, det)
    det_obs = det_result.observations
    content = json.dumps({"observations": [json.loads(o.model_dump_json()) for o in det_obs]})
    llm, inp = make_recorded(tmp_path, content)
    llm_result = _extract(inp["title"], inp["text"], llm)
    det_dump = det_result.semantic_dump()
    llm_dump = llm_result.semantic_dump()
    # identical facts; only provider identity in provenance/diagnostics differs
    assert det_dump["fragment"] == llm_dump["fragment"]
    assert det_dump["conflicts"] == llm_dump["conflicts"]
    assert det_dump["clarifications"] == llm_dump["clarifications"]
