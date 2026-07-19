# Step 5B — Slice 5B-1: Provider-Agnostic Benchmark Runner Report

**Дата:** 2026-07-19 · **Verdict:** STEP_5B_COMMON_RUNNER_READY
**Preceding gate:** STEP_5B_BENCHMARK_CONTRACT_READY (`c3b79fde6f`)

## Architecture

```text
build_benchmark_provider(spec)          ← ONLY place that knows provider_id
        │  returns (provider, identity: dict incl. retry_policy,
        │           fallback_policy, cost_known_zero, reports_usage_metadata —
        │           policy decided HERE, read generically downstream)
        ▼
run_benchmark(benchmark_id, provider_spec, dataset_id, cases, out_dir)
        │  1. build_benchmark_provider(spec) at the boundary
        │  2. build BenchmarkManifest, write atomically before any case
        │     (resume: identity mismatch on 7 fields -> ResumeBlocked)
        ▼
run_benchmark_case(provider, case, contract, policy)   ← per case
        │  det_extract() -> extract_semantic() [UNCHANGED runtime]
        │                 -> evaluate() [UNCHANGED Step 3 engine, optional]
        ▼
BenchmarkCaseResult persisted atomically; skipped on resume if valid & unforced
```

`job_intel/vacancy_understanding/semantic/benchmark/` (new package, 6 files):

| File | Role |
|---|---|
| `models.py` | `BenchmarkManifest`, `BenchmarkCaseResult`, `NumericState`, `LatencyMode`, `CaseStatus` (strict pydantic, `extra="forbid"`) |
| `hashing.py` | `sha256_file/text/json` — shared, no provider knowledge |
| `provider_registry.py` | **The only file allowed to branch on provider kind.** `build_benchmark_provider({"type": "deterministic"\|"llm_replay", ...})` |
| `compatible_match.py` | Read-only derivation of recommendation-equivalence classes from the 36-cell Decision SoT matrix |
| `runner.py` | `run_benchmark()` / `run_benchmark_case()` — the one reusable path |

## Changed files (existing runners)

`replay_full.py::run_full_replay()` and `replay_flagships.py::run()` gained one optional kwarg `provider=None` (default preserves exact prior behaviour — `provider or DeterministicPhraseProvider()`). Nothing else in either file changed; `calibration.py` already accepted `provider=None` and needed no edit.

## Manifest schema

24 fields per task spec (`benchmark_id` … `decision_matrix_hash`), all present even when null (e.g. `model_requested=None` for the deterministic provider — never omitted). Written atomically (`tmp` + `os.replace`) **before** the first case executes.

## Case-result schema

22 fields per task spec. Numeric-state discipline (contract §6): deterministic provider → `cost_state=known_zero`, `cost_usd=0.0`, tokens `0`; LLM replay → tokens are real recorded values (`known` facts), but `cost_usd=None`/`cost_state=not_applicable` — **the cost-per-token formula is explicitly Slice 5B-2 scope**, not computed here even though the raw tokens are available. `latency_mode` is `deterministic` / `replay` and is never mixed into one series (contract §7); this slice's LLM latency is **replay latency** (parse + runtime time), not the original live call's latency — that value lives in the recording, referenced via `recording_path`.

## Resume and idempotency

- Manifest identity check on 7 axes: `dataset_hash, provider_id, provider_version, provider_config_hash, prompt_version, metric_contract_hash, decision_matrix_hash` — any mismatch raises `ResumeBlocked`, never silently continues.
- Completed case (valid existing result file) is skipped unless `force=True`.
- Corrupt/partial result file (invalid JSON or schema) is treated as absent and re-run — proven by `test_corrupt_partial_result_is_detected_and_rerun`.
- One case's failure (`recording_missing`, etc.) does not affect sibling cases' results — proven by `test_failed_case_does_not_corrupt_other_cases`.
- Output paths are deterministic: `<out_dir>/cases/<case_id>.result.json`, `.../semantic_dumps/<case_id>.semantic.json`, `.../decisions/<case_id>.decision.json`.

## Compatible-match derivation

`derive_recommendation_equivalences()` groups the matrix's 36 `(mandate_band, company_band)` rows by `recommendation` — the only place the Decision SoT formalizes an equivalence (band criteria themselves are natural-language strings, not a fact-value function; see rationale in the module docstring). Records `source_path`, `source_sha256`, `decision_contract_version`; never mutates the source file (proven by `test_compatible_match_derivation_does_not_mutate_source`); deterministic (`test_compatible_match_derivation_is_deterministic`, byte-equal on repeated calls). This artifact is referenced by the benchmark runner's manifest (`decision_matrix_hash`) but **final precision/recall computation is not implemented in this slice** — Slice 5B-5 scope.

## Offline evidence

- Deterministic case executed through the common runner: `observations_accepted >= 1` on a growth-mandate fixture, `0` on the empty control — same behaviour as direct `extract_semantic()` calls.
- LLM replay case executed through the **same** runner using a self-contained fake-transport recording (no smoke artifacts reused): `input_tokens=1000/output_tokens=500` carried from the recording, `observations_accepted=1`.
- **Network-blocked proof:** `socket.socket` monkeypatched to raise `AssertionError` for the duration of an LLM-replay `run_benchmark()` call — the call still succeeds (`test_network_disabled_llm_replay_succeeds`).
- **Boundary regression:** deterministic-emitted observations replayed verbatim through the LLM provider produce a byte-identical `fragment`/`conflicts`/`clarifications` — the pipeline treats both sources identically (`test_identical_observations_give_identical_semantic_hash`).
- Source-scan proof that `runner.py` and `pipeline.py` contain **zero** occurrences of either provider_id literal or a `provider_id ==`/`!=` comparison; both literals appear only inside `provider_registry.py` (`test_no_provider_branch_in_runner_or_runtime`, `test_provider_specific_branching_confined_to_registry`).

## Tests

`venv/bin/python -m pytest tests/job_intel/test_semantic_benchmark_runner.py -q` → **25 passed** (all 20 required cases covered, plus registry-error and signature-regression tests).
`venv/bin/python -m pytest tests/job_intel/ -k "semantic or shadow_evaluator or preference_model or vacancy_understanding" -q` → **227 passed** (202 pre-existing + 25 new), 0 failed.

Zero network calls anywhere in the suite: deterministic path never imports a transport; LLM path is proven network-blocked as above; `test_runner_module_imports_no_http_client` scans `runner.py` for `requests`/`httpx`/`urllib.request`/direct `OpenAI(` construction.

## Known gaps reserved for Slice 5B-2

1. **Cost formula.** `cost_usd` for LLM cases is deliberately `None`/`not_applicable` — token→price mapping is 5B-2.
2. **Aggregation.** No percentiles, no precision/recall, no `provider_benchmark_summary.json` — this slice validates result *completeness*, not aggregate metrics, per task's explicit non-goal.
3. **Compatible-match consumption.** The equivalence-class artifact exists and is hashed into the manifest, but nothing in this slice *uses* it to compute a matched/mismatched verdict — that lands with precision/recall in 5B-5.
4. **Live latency vs replay latency reconciliation.** The recording's original `latency_ms` (from the live call) is reachable via `recording_path` but not yet surfaced as a separate reportable series — deferred to whichever slice aggregates cost/latency (5B-2).
