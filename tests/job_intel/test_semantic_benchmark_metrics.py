"""Step 5B Slice 5B-2: cost and latency instrumentation + aggregate metrics.

No network anywhere: deterministic runs, recorded LLM replay, and
hand-crafted persisted case rows for aggregate-only edge cases (failed
live-mode cases cannot be produced offline, but the aggregate operates on
persisted rows, so those rows are written directly).
"""
from __future__ import annotations

import json
import socket

import pytest

from job_intel.vacancy_understanding.semantic.benchmark.aggregate import (
    AggregateError,
    aggregate_run,
)
from job_intel.vacancy_understanding.semantic.benchmark.models import (
    BenchmarkCaseResult,
    CaseStatus,
    LatencyMode,
    NumericState,
    NumericValue,
)
from job_intel.vacancy_understanding.semantic.benchmark.runner import run_benchmark
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    LLMObservationProvider,
    RecordingStore,
)

GROWTH_TITLE = "Head of Growth"
GROWTH_TEXT = "Own user acquisition and activation across the region. Full P&L ownership."
EMPTY_TITLE = "Team Member"
EMPTY_TEXT = "Great snacks and a modern office."

PRICING = {"input_usd_per_mtok": 0.25, "output_usd_per_mtok": 2.0,
           "source": "test fixture pricing 2026-07-20"}


class FakeMessage:
    def __init__(self, content): self.content = content


class FakeChoice:
    def __init__(self, content): self.message = FakeMessage(content)


class FakeUsage:
    prompt_tokens, completion_tokens, total_tokens = 1000, 500, 1500


class FakeResponse:
    def __init__(self, content, with_usage=True):
        self.choices = [FakeChoice(content)]
        if with_usage:
            self.usage = FakeUsage()
        self.model = "openai/gpt-5-mini"


class FakeTransport:
    def __init__(self, content, with_usage=True):
        class _Completions:
            def create(self, **kwargs):
                return FakeResponse(content, with_usage=with_usage)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def _obs(excerpt, signal="growth_mandate=true", maps=None):
    return {
        "observation_id": "obs-1", "excerpt": excerpt, "location": "description",
        "signal_type": signal, "interpretation": "supported by the excerpt",
        "maps_to": maps or ["mandate.growth_mandate"], "basis": "direct",
    }


def _record_llm_fixture(store_dir, title, text, observations, with_usage=True):
    content = json.dumps({"observations": observations})
    rec = LLMObservationProvider(store=RecordingStore(store_dir), mode="record",
                                 transport=FakeTransport(content, with_usage=with_usage))
    try:
        rec.extract_semantic_observations(title=title, text=text, structured={"title": title})
    except Exception:
        pass


def _det_cases():
    return [
        {"case_id": "c1", "vacancy_key": "v1", "title": GROWTH_TITLE, "text": GROWTH_TEXT},
        {"case_id": "c2", "vacancy_key": "v2", "title": EMPTY_TITLE, "text": EMPTY_TEXT},
    ]


def _llm_spec(store_dir, pricing=PRICING):
    spec = {"type": "llm_replay", "store_dir": str(store_dir),
            "model_id": "openai/gpt-5-mini"}
    if pricing is not None:
        spec["pricing"] = pricing
    return spec


def _run_det(tmp_path, out="run"):
    return run_benchmark(
        benchmark_id="m-det", run_id="r1", provider_spec={"type": "deterministic"},
        dataset_id="ds", cases=_det_cases(), out_dir=tmp_path / out)


def _write_case_row(out_dir, **overrides):
    """Persist a hand-crafted case row (for aggregate-only edge cases that a
    fully offline runner cannot produce, e.g. failed live calls)."""
    base = dict(
        benchmark_id="m-hand", run_id="r1", case_id="cx", vacancy_key="vx",
        provider_id="llm-observation", status=CaseStatus.ok,
        observations_emitted=1, observations_accepted=1, observations_rejected=0,
        latency_ms=100.0, latency_mode=LatencyMode.live,
        input_tokens=1000, output_tokens=500,
        cost_usd=0.00125, cost_state=NumericState.known_value,
        started_at="t", completed_at="t",
    )
    base.update(overrides)
    row = BenchmarkCaseResult(**base)
    path = out_dir / "cases" / f"{row.case_id}.result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row.model_dump(mode="json")))
    return row


def _write_hand_manifest(out_dir):
    """Aggregate needs a manifest for identity; copy one from a real det run."""
    src = json.loads((out_dir / "manifest.json").read_text())
    return src


# ---------------------------------------------------------------------------
# NumericValue discipline
# ---------------------------------------------------------------------------

def test_numeric_value_states_validate():
    assert NumericValue(state=NumericState.known_zero, value=0.0).value == 0.0
    assert NumericValue(state=NumericState.known_value, value=3.5).value == 3.5
    assert NumericValue(state=NumericState.unknown).value is None
    assert NumericValue(state=NumericState.not_applicable).value is None
    with pytest.raises(Exception):
        NumericValue(state=NumericState.known_value)  # value required
    with pytest.raises(Exception):
        NumericValue(state=NumericState.unknown, value=1.0)  # value forbidden
    with pytest.raises(Exception):
        NumericValue(state=NumericState.known_zero, value=2.0)  # must be zero


# ---------------------------------------------------------------------------
# Case-level cost states
# ---------------------------------------------------------------------------

def test_deterministic_case_cost_is_known_zero(tmp_path):
    _, results = _run_det(tmp_path)
    for r in results:
        assert r.cost_state == NumericState.known_zero
        assert r.cost_usd == 0.0


def test_recorded_llm_usage_with_pricing_gives_known_value_cost(tmp_path):
    store_dir = tmp_path / "recordings"
    _record_llm_fixture(store_dir, GROWTH_TITLE, GROWTH_TEXT,
                        [_obs("Own user acquisition and activation")])
    _, results = run_benchmark(
        benchmark_id="m2", run_id="r1", provider_spec=_llm_spec(store_dir),
        dataset_id="ds",
        cases=[{"case_id": "c1", "vacancy_key": "v1", "title": GROWTH_TITLE, "text": GROWTH_TEXT}],
        out_dir=tmp_path / "run")
    r = results[0]
    assert r.cost_state == NumericState.known_value
    # 1000/1e6 * 0.25 + 500/1e6 * 2.0
    assert r.cost_usd == pytest.approx(0.00025 + 0.001)


def test_missing_expected_usage_gives_unknown_cost(tmp_path):
    store_dir = tmp_path / "recordings"
    _record_llm_fixture(store_dir, GROWTH_TITLE, GROWTH_TEXT,
                        [_obs("Own user acquisition and activation")],
                        with_usage=False)
    _, results = run_benchmark(
        benchmark_id="m3", run_id="r1", provider_spec=_llm_spec(store_dir),
        dataset_id="ds",
        cases=[{"case_id": "c1", "vacancy_key": "v1", "title": GROWTH_TITLE, "text": GROWTH_TEXT}],
        out_dir=tmp_path / "run")
    r = results[0]
    assert r.cost_state == NumericState.unknown
    assert r.cost_usd is None
    assert r.input_tokens is None and r.output_tokens is None


def test_missing_pricing_gives_unknown_cost_even_with_usage(tmp_path):
    store_dir = tmp_path / "recordings"
    _record_llm_fixture(store_dir, GROWTH_TITLE, GROWTH_TEXT,
                        [_obs("Own user acquisition and activation")])
    _, results = run_benchmark(
        benchmark_id="m4", run_id="r1", provider_spec=_llm_spec(store_dir, pricing=None),
        dataset_id="ds",
        cases=[{"case_id": "c1", "vacancy_key": "v1", "title": GROWTH_TITLE, "text": GROWTH_TEXT}],
        out_dir=tmp_path / "run")
    r = results[0]
    assert r.cost_state == NumericState.unknown
    assert r.cost_usd is None
    assert r.input_tokens == 1000  # tokens are still recorded facts


def test_pricing_is_published_in_manifest(tmp_path):
    store_dir = tmp_path / "recordings"
    _record_llm_fixture(store_dir, GROWTH_TITLE, GROWTH_TEXT,
                        [_obs("Own user acquisition and activation")])
    manifest, _ = run_benchmark(
        benchmark_id="m5", run_id="r1", provider_spec=_llm_spec(store_dir),
        dataset_id="ds",
        cases=[{"case_id": "c1", "vacancy_key": "v1", "title": GROWTH_TITLE, "text": GROWTH_TEXT}],
        out_dir=tmp_path / "run")
    assert manifest.price_input_usd_per_mtok == 0.25
    assert manifest.price_output_usd_per_mtok == 2.0
    assert manifest.pricing_source == PRICING["source"]


# ---------------------------------------------------------------------------
# Case-level latency modes
# ---------------------------------------------------------------------------

def test_llm_replay_case_surfaces_recorded_live_latency(tmp_path):
    store_dir = tmp_path / "recordings"
    _record_llm_fixture(store_dir, GROWTH_TITLE, GROWTH_TEXT,
                        [_obs("Own user acquisition and activation")])
    _, results = run_benchmark(
        benchmark_id="m6", run_id="r1", provider_spec=_llm_spec(store_dir),
        dataset_id="ds",
        cases=[{"case_id": "c1", "vacancy_key": "v1", "title": GROWTH_TITLE, "text": GROWTH_TEXT}],
        out_dir=tmp_path / "run")
    r = results[0]
    assert r.latency_mode == LatencyMode.replay      # wall-clock series stays replay
    assert r.live_latency_state == NumericState.known_value
    assert r.live_latency_ms is not None and r.live_latency_ms >= 0


def test_deterministic_case_live_latency_not_applicable(tmp_path):
    _, results = _run_det(tmp_path)
    for r in results:
        assert r.latency_mode == LatencyMode.deterministic
        assert r.live_latency_state == NumericState.not_applicable
        assert r.live_latency_ms is None


# ---------------------------------------------------------------------------
# Aggregate: provider_benchmark_summary.json
# ---------------------------------------------------------------------------

def test_aggregate_written_and_matches_persisted_rows(tmp_path):
    out = tmp_path / "run"
    _run_det(tmp_path)
    summary_path = out / "provider_benchmark_summary.json"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text())
    assert summary["cases_total"] == 2
    assert summary["cases_succeeded"] == 2
    assert summary["cases_failed"] == 0
    # recompute from persisted rows -> identical
    recomputed = aggregate_run(out)
    assert recomputed.model_dump(mode="json") == summary


def test_deterministic_aggregate_cost_and_tokens_known_zero(tmp_path):
    out = tmp_path / "run"
    _run_det(tmp_path)
    s = aggregate_run(out)
    assert s.cost_usd_total.state == NumericState.known_zero
    assert s.input_tokens_total.state == NumericState.known_zero
    assert s.output_tokens_total.state == NumericState.known_zero
    assert s.cost_per_case.state == NumericState.known_zero


def test_exact_token_and_cost_aggregation(tmp_path):
    store_dir = tmp_path / "recordings"
    _record_llm_fixture(store_dir, GROWTH_TITLE, GROWTH_TEXT,
                        [_obs("Own user acquisition and activation")])
    other_text = "Own user acquisition and activation across new markets."
    _record_llm_fixture(store_dir, "Other Role", other_text,
                        [_obs("Own user acquisition and activation")])
    cases = [
        {"case_id": "c1", "vacancy_key": "v1", "title": GROWTH_TITLE, "text": GROWTH_TEXT},
        {"case_id": "c2", "vacancy_key": "v2", "title": "Other Role", "text": other_text},
    ]
    out = tmp_path / "run"
    run_benchmark(benchmark_id="m8", run_id="r1", provider_spec=_llm_spec(store_dir),
                  dataset_id="ds", cases=cases, out_dir=out)
    s = aggregate_run(out)
    assert s.input_tokens_total.state == NumericState.known_value
    assert s.input_tokens_total.value == 2000
    assert s.output_tokens_total.value == 1000
    assert s.cost_usd_total.state == NumericState.known_value
    assert s.cost_usd_total.value == pytest.approx(2 * 0.00125)
    assert s.cost_per_case.value == pytest.approx(0.00125)
    accepted = s.observations_accepted
    assert s.cost_per_accepted_observation.value == pytest.approx(2 * 0.00125 / accepted)


def test_failed_case_counted_and_unknown_cost_poisons_total(tmp_path):
    store_dir = tmp_path / "recordings"
    _record_llm_fixture(store_dir, GROWTH_TITLE, GROWTH_TEXT,
                        [_obs("Own user acquisition and activation")])
    cases = [
        {"case_id": "ok1", "vacancy_key": "v1", "title": GROWTH_TITLE, "text": GROWTH_TEXT},
        {"case_id": "missing", "vacancy_key": "v2", "title": "Other", "text": "unrecorded"},
    ]
    out = tmp_path / "run"
    run_benchmark(benchmark_id="m9", run_id="r1", provider_spec=_llm_spec(store_dir),
                  dataset_id="ds", cases=cases, out_dir=out)
    s = aggregate_run(out)
    assert s.cases_total == 2
    assert s.cases_failed == 1
    assert s.cases_succeeded == 1
    # failed replay case has no usage data -> unknown, so the total is unknown
    assert s.cost_usd_total.state == NumericState.unknown
    assert s.cost_usd_total.value is None


def test_failed_token_consuming_case_included_in_cost(tmp_path):
    out = tmp_path / "run"
    _run_det(tmp_path)  # provides a valid manifest
    for f in (out / "cases").glob("*.result.json"):
        f.unlink()
    _write_case_row(out, case_id="ok", status=CaseStatus.ok,
                    input_tokens=1000, output_tokens=500,
                    cost_usd=0.00125, cost_state=NumericState.known_value)
    _write_case_row(out, case_id="fail-tokens", status=CaseStatus.failed,
                    observations_emitted=0, observations_accepted=0,
                    observations_rejected=0, error_code="schema_failure",
                    input_tokens=2000, output_tokens=0,
                    cost_usd=0.0005, cost_state=NumericState.known_value)
    s = aggregate_run(out)
    assert s.cost_usd_total.value == pytest.approx(0.00125 + 0.0005)
    assert s.input_tokens_total.value == 3000


def test_failed_zero_token_transport_case_contributes_zero(tmp_path):
    out = tmp_path / "run"
    _run_det(tmp_path)
    for f in (out / "cases").glob("*.result.json"):
        f.unlink()
    _write_case_row(out, case_id="ok", status=CaseStatus.ok)
    _write_case_row(out, case_id="fail-zero", status=CaseStatus.failed,
                    observations_emitted=0, observations_accepted=0,
                    observations_rejected=0, error_code="transport_failure",
                    input_tokens=0, output_tokens=0,
                    cost_usd=0.0, cost_state=NumericState.known_zero)
    s = aggregate_run(out)
    assert s.cost_usd_total.state == NumericState.known_value
    assert s.cost_usd_total.value == pytest.approx(0.00125)
    assert s.input_tokens_total.value == 1000


def test_percentiles_nearest_rank(tmp_path):
    out = tmp_path / "run"
    _run_det(tmp_path)
    for f in (out / "cases").glob("*.result.json"):
        f.unlink()
    for i, ms in enumerate([100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]):
        _write_case_row(out, case_id=f"c{i}", latency_ms=float(ms),
                        latency_mode=LatencyMode.live)
    s = aggregate_run(out)
    live = s.latency_by_mode["live"]
    assert live.case_count == 10
    assert live.latency_total_ms.value == pytest.approx(5500)
    assert live.latency_p50_ms.value == pytest.approx(500)
    assert live.latency_p90_ms.value == pytest.approx(900)
    assert live.latency_p95_ms.value == pytest.approx(1000)
    assert live.latency_p99_ms.value == pytest.approx(1000)
    assert live.latency_max_ms.value == pytest.approx(1000)


def test_replay_and_live_latency_never_mixed(tmp_path):
    store_dir = tmp_path / "recordings"
    _record_llm_fixture(store_dir, GROWTH_TITLE, GROWTH_TEXT,
                        [_obs("Own user acquisition and activation")])
    out = tmp_path / "run"
    run_benchmark(
        benchmark_id="m12", run_id="r1", provider_spec=_llm_spec(store_dir),
        dataset_id="ds",
        cases=[{"case_id": "c1", "vacancy_key": "v1", "title": GROWTH_TITLE, "text": GROWTH_TEXT}],
        out_dir=out)
    s = aggregate_run(out)
    # wall-clock replay timings live under "replay"; recorded live latency
    # from recordings lives under "live"; neither leaks into the other
    assert s.latency_by_mode["replay"].case_count == 1
    assert s.latency_by_mode["live"].case_count == 1
    assert s.latency_by_mode["deterministic"].case_count == 0
    assert s.latency_by_mode["live"].latency_total_ms.state in (
        NumericState.known_value, NumericState.known_zero)


def test_zero_accepted_observations_per_observation_metrics_not_applicable(tmp_path):
    out = tmp_path / "run"
    run_benchmark(
        benchmark_id="m13", run_id="r1", provider_spec={"type": "deterministic"},
        dataset_id="ds",
        cases=[{"case_id": "c2", "vacancy_key": "v2", "title": EMPTY_TITLE, "text": EMPTY_TEXT}],
        out_dir=out)
    s = aggregate_run(out)
    assert s.observations_accepted == 0
    assert s.zero_observation_cases == 1
    assert s.cost_per_accepted_observation.state == NumericState.not_applicable
    det = s.latency_by_mode["deterministic"]
    assert det.latency_per_accepted_observation_ms.state == NumericState.not_applicable


def test_resumed_run_does_not_double_count(tmp_path):
    out = tmp_path / "run"
    _run_det(tmp_path)
    first = aggregate_run(out)
    # resume: identical invocation skips completed cases, then re-aggregates
    _run_det(tmp_path)
    second = aggregate_run(out)
    assert first.cases_total == second.cases_total == 2
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_corrupt_case_row_blocks_aggregate(tmp_path):
    out = tmp_path / "run"
    _run_det(tmp_path)
    (out / "cases" / "c1.result.json").write_text("{broken json")
    with pytest.raises(AggregateError, match="c1"):
        aggregate_run(out)


def test_aggregate_requires_manifest(tmp_path):
    with pytest.raises(AggregateError, match="manifest"):
        aggregate_run(tmp_path / "nonexistent")


def test_aggregate_performs_no_network_calls(tmp_path):
    out = tmp_path / "run"
    _run_det(tmp_path)

    def _no_net(*a, **k):
        raise AssertionError("NETWORK CALL DURING AGGREGATE")

    real_socket = socket.socket
    socket.socket = _no_net
    try:
        aggregate_run(out)
    finally:
        socket.socket = real_socket


def test_no_provider_branch_in_aggregate():
    import inspect
    from job_intel.vacancy_understanding.semantic.benchmark import aggregate as agg_mod
    src = inspect.getsource(agg_mod)
    assert "llm-observation" not in src
    assert "deterministic-phrase" not in src
    assert "provider_id ==" not in src
    assert "provider_id !=" not in src


def test_summary_numeric_fields_all_carry_state(tmp_path):
    out = tmp_path / "run"
    _run_det(tmp_path)
    summary = json.loads((out / "provider_benchmark_summary.json").read_text())
    for field in ("input_tokens_total", "output_tokens_total", "cost_usd_total",
                  "cost_per_case", "cost_per_accepted_observation"):
        assert set(summary[field]) == {"state", "value"}, field
    for mode_block in summary["latency_by_mode"].values():
        for k, v in mode_block.items():
            if k == "case_count":
                continue
            assert set(v) == {"state", "value"}, k
