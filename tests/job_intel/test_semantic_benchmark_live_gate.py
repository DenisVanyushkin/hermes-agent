"""Step 5B Slice 5B-4: spend-gated live mode + budgeted calibration orchestration.

Everything here is offline: the live-mode registry path is tested only up to
its refusal gates (approval flag, pricing requirement); budget/stop logic is
exercised through replay fixtures. No network anywhere.
"""
from __future__ import annotations

import json

import pytest

from job_intel.vacancy_understanding.semantic.benchmark.calibration_live import (
    CalibrationAborted,
    run_llm_calibration,
)
from job_intel.vacancy_understanding.semantic.benchmark.models import LatencyMode
from job_intel.vacancy_understanding.semantic.benchmark.provider_registry import (
    ProviderRegistryError,
    build_benchmark_provider,
)
from job_intel.vacancy_understanding.semantic.benchmark.runner import run_benchmark
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    LLMObservationProvider,
    LLMProviderError,
    RecordingStore,
)

GROWTH_TITLE = "Head of Growth"
GROWTH_TEXT = "Own user acquisition and activation across the region. Full P&L ownership."
PRICING = {"input_usd_per_mtok": 0.25, "output_usd_per_mtok": 2.0,
           "source": "test fixture pricing"}


class FakeMessage:
    def __init__(self, content): self.content = content


class FakeChoice:
    def __init__(self, content): self.message = FakeMessage(content)


class FakeUsage:
    prompt_tokens, completion_tokens, total_tokens = 1000, 500, 1500


class FakeResponse:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]
        self.usage = FakeUsage()
        self.model = "openai/gpt-5-mini"


class FakeTransport:
    def __init__(self, content):
        class _Completions:
            def create(self, **kwargs):
                return FakeResponse(content)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def _obs(excerpt):
    return {
        "observation_id": "obs-1", "excerpt": excerpt, "location": "description",
        "signal_type": "growth_mandate=true", "interpretation": "supported by the excerpt",
        "maps_to": ["mandate.growth_mandate"], "basis": "direct",
    }


def _record(store_dir, title, text):
    content = json.dumps({"observations": [_obs("Own user acquisition and activation")]})
    rec = LLMObservationProvider(store=RecordingStore(store_dir), mode="record",
                                 transport=FakeTransport(content))
    try:
        rec.extract_semantic_observations(title=title, text=text, structured={"title": title})
    except Exception:
        pass


# --- live-mode registry gates ----------------------------------------------

def test_llm_live_requires_approval_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("JOB_INTEL_LLM_LIVE_APPROVED", raising=False)
    with pytest.raises(LLMProviderError, match="live_calls_not_approved"):
        build_benchmark_provider({
            "type": "llm_live", "store_dir": str(tmp_path / "rec"),
            "model_id": "openai/gpt-5-mini", "pricing": PRICING})


def test_llm_live_requires_pricing(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_INTEL_LLM_LIVE_APPROVED", "1")
    with pytest.raises(ProviderRegistryError, match="pricing"):
        build_benchmark_provider({
            "type": "llm_live", "store_dir": str(tmp_path / "rec"),
            "model_id": "openai/gpt-5-mini"})


def test_latency_mode_comes_from_registry_identity(tmp_path):
    _, det_identity = build_benchmark_provider({"type": "deterministic"})
    assert det_identity["latency_mode"] == "deterministic"
    _record(tmp_path / "rec", GROWTH_TITLE, GROWTH_TEXT)
    _, replay_identity = build_benchmark_provider({
        "type": "llm_replay", "store_dir": str(tmp_path / "rec"),
        "model_id": "openai/gpt-5-mini"})
    assert replay_identity["latency_mode"] == "replay"


# --- runner budget hook: max_new_cases --------------------------------------

def _cases():
    return [
        {"case_id": "c1", "vacancy_key": "v1", "title": GROWTH_TITLE, "text": GROWTH_TEXT},
        {"case_id": "c2", "vacancy_key": "v2", "title": "Team Member",
         "text": "Great snacks and a modern office."},
    ]


def test_max_new_cases_limits_execution_and_resumes(tmp_path):
    out = tmp_path / "run"
    _, first = run_benchmark(
        benchmark_id="g1", run_id="r1", provider_spec={"type": "deterministic"},
        dataset_id="ds", cases=_cases(), out_dir=out, max_new_cases=1)
    assert len(first) == 1
    assert not (out / "cases" / "c2.result.json").exists()
    _, second = run_benchmark(
        benchmark_id="g1", run_id="r1", provider_spec={"type": "deterministic"},
        dataset_id="ds", cases=_cases(), out_dir=out, max_new_cases=1)
    assert len(second) == 2  # c1 cached + c2 newly executed
    assert (out / "cases" / "c2.result.json").exists()


# --- calibration orchestration: cap and failure-streak stops ----------------

def _replay_spec(store_dir):
    return {"type": "llm_replay", "store_dir": str(store_dir),
            "model_id": "openai/gpt-5-mini", "pricing": PRICING}


def test_calibration_stops_when_cap_exceeded(tmp_path):
    store = tmp_path / "rec"
    cases = []
    for i in range(4):
        title, text = f"Head of Growth {i}", GROWTH_TEXT + f" Extra {i}."
        _record(store, title, text)
        cases.append({"case_id": f"c{i}", "vacancy_key": f"v{i}",
                      "title": title, "text": text})
    # each replay case costs 0.00125; cap below the 4-case total aborts early
    with pytest.raises(CalibrationAborted, match="cap"):
        run_llm_calibration(
            out_root=tmp_path / "run", provider_spec=_replay_spec(store),
            dataset_specs=[("ds", cases)], cap_usd=0.002, chunk_size=1)


def test_calibration_stops_on_consecutive_failures(tmp_path):
    store = tmp_path / "rec"  # empty: every replay lookup fails
    cases = [{"case_id": f"c{i}", "vacancy_key": f"v{i}",
              "title": f"T{i}", "text": f"unrecorded {i}"} for i in range(5)]
    with pytest.raises(CalibrationAborted, match="consecutive"):
        run_llm_calibration(
            out_root=tmp_path / "run", provider_spec=_replay_spec(store),
            dataset_specs=[("ds", cases)], cap_usd=3.0, chunk_size=1,
            consecutive_failure_limit=3)


# --- record-mode idempotency (5B-4 finding: input-hash collisions) ----------

class CountingTransport:
    def __init__(self, content):
        outer = self
        outer.calls = 0

        class _Completions:
            def create(self, **kwargs):
                outer.calls += 1
                return FakeResponse(content)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def test_record_mode_reuses_existing_recording_for_same_input(tmp_path):
    store_dir = tmp_path / "rec"
    content_a = json.dumps({"observations": [_obs("Own user acquisition and activation")]})
    t1 = CountingTransport(content_a)
    p1 = LLMObservationProvider(store=RecordingStore(store_dir), mode="record", transport=t1)
    obs1 = p1.extract_semantic_observations(
        title=GROWTH_TITLE, text=GROWTH_TEXT, structured={"title": GROWTH_TITLE})
    assert t1.calls == 1

    content_b = json.dumps({"observations": []})  # would differ if re-called
    t2 = CountingTransport(content_b)
    p2 = LLMObservationProvider(store=RecordingStore(store_dir), mode="record", transport=t2)
    obs2 = p2.extract_semantic_observations(
        title=GROWTH_TITLE, text=GROWTH_TEXT, structured={"title": GROWTH_TITLE})
    assert t2.calls == 0  # no second paid call, no overwrite
    assert [o.excerpt for o in obs2] == [o.excerpt for o in obs1]
    assert p2.last_call_metadata["mode"] == "record_cached"
    assert p2.last_call_metadata["usage"]["prompt_tokens"] == 1000


def test_record_mode_does_not_reuse_failed_recording(tmp_path):
    store_dir = tmp_path / "rec"
    t_fail = CountingTransport(json.dumps({"observations": []}))
    t_fail.chat.completions.create = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    p1 = LLMObservationProvider(store=RecordingStore(store_dir), mode="record", transport=t_fail)
    with pytest.raises(LLMProviderError):
        p1.extract_semantic_observations(
            title=GROWTH_TITLE, text=GROWTH_TEXT, structured={"title": GROWTH_TITLE})

    t2 = CountingTransport(json.dumps(
        {"observations": [_obs("Own user acquisition and activation")]}))
    p2 = LLMObservationProvider(store=RecordingStore(store_dir), mode="record", transport=t2)
    obs = p2.extract_semantic_observations(
        title=GROWTH_TITLE, text=GROWTH_TEXT, structured={"title": GROWTH_TITLE})
    assert t2.calls == 1  # failed recording is retried live, not reused
    assert len(obs) == 1


def test_calibration_completes_within_cap(tmp_path):
    store = tmp_path / "rec"
    _record(store, GROWTH_TITLE, GROWTH_TEXT)
    cases = [{"case_id": "c1", "vacancy_key": "v1",
              "title": GROWTH_TITLE, "text": GROWTH_TEXT}]
    outcome = run_llm_calibration(
        out_root=tmp_path / "run", provider_spec=_replay_spec(store),
        dataset_specs=[("ds", cases)], cap_usd=3.0, chunk_size=10)
    assert outcome["datasets"]["ds"]["cases_total"] == 1
    assert outcome["known_cost_usd"] == pytest.approx(0.00125)
    summary = json.loads(
        (tmp_path / "run" / "ds" / "provider_benchmark_summary.json").read_text())
    assert summary["cases_failed"] == 0
