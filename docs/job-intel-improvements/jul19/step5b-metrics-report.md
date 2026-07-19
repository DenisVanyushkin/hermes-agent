# Step 5B — Slice 5B-2: Cost and Latency Instrumentation

**Exit gate:** `STEP_5B_METRICS_INFRASTRUCTURE_READY`
**Date:** 2026-07-20
**Scope source:** Roadmap SoT §9.4, `step5b-benchmark-contract.md` §6–§7, owner task (5B-2), known gaps 1/2/4 from `step5b-common-runner-report.md`.

## What was built

1. **`NumericValue`** (`models.py`) — every aggregate number is `{state, value}`;
   state/value consistency is enforced by a validator (`known_zero` pinned to 0,
   `known_value` requires a value, `unknown`/`not_applicable` forbid one).
2. **Per-case cost** (`runner.py::_case_cost`) — `input_tokens*PRICE_IN +
   output_tokens*PRICE_OUT` per MTok. Pricing is a run input
   (`provider_spec["pricing"]`), never a code constant; it is published in the
   manifest (`price_input_usd_per_mtok`, `price_output_usd_per_mtok`,
   `pricing_source`) per contract §6. Deterministic → `known_zero`; missing
   usage or missing pricing → `unknown`, never a silent 0.
3. **Live latency surfaced** (`runner.py`) — recorded live-call `latency_ms`
   from `last_call_metadata` lands in new case fields `live_latency_ms` /
   `live_latency_state`; the wall-clock replay timing stays in `latency_ms`
   with `latency_mode=replay`. Closes known gap 4.
4. **`aggregate.py`** — `aggregate_run(out_dir)` derives
   `provider_benchmark_summary.json` **only from persisted case rows** (resume
   cannot double-count); called automatically at the end of `run_benchmark()`.
   Corrupt case row ⇒ `AggregateError` (explicit block, not silent exclusion).
   Summary is timestamp-free so re-aggregation of unchanged rows is
   byte-identical.

## Normative choices fixed in this slice (part of metric identity)

- **Percentiles: nearest-rank** (`index = ceil(p/100·n)` over the sorted
  series). Changing this later requires a new `benchmark_id` (contract §2).
- **Unknown poisoning:** an `unknown` cost/token/live-latency on any
  contributing case makes the corresponding total `unknown` — partial totals
  are never presented as complete.
- **Latency series are per-mode** (`latency_by_mode: live | replay |
  deterministic`); the live series = live-mode rows' wall-clock + recorded
  live latencies surfaced from replay rows. Series never merge.
- **Pricing is identity:** it participates in `provider_config_hash`, so
  resuming a run under a different price is blocked by the existing 7-axis
  resume gate instead of silently mixing incomparable costs.
- **`cost_per_accepted_observation`** divides by accepted (not emitted)
  observations, per contract §6; 0 accepted ⇒ `not_applicable`.
- `zero_observation_cases` counts succeeded cases with
  `observations_emitted == 0`.

## Evidence

```
venv/bin/python -m pytest tests/job_intel/test_semantic_benchmark_metrics.py -q
# 23 passed

venv/bin/python -m pytest tests/job_intel/ \
  -k "semantic or shadow_evaluator or preference_model or vacancy_understanding" -q
# 250 passed
```

Covered (per owner task's mandatory test list): deterministic `known_zero`;
recorded usage + pricing → `known_value` with exact cost; missing usage →
`unknown`; missing pricing → `unknown` (tokens still recorded); pricing in
manifest; `not_applicable` preserved; exact token/cost aggregation; failed
cases counted; failed token-consuming case included in cost; failed
zero-token transport case contributes zero; nearest-rank p50/p90/p95/p99/max;
replay/live never mixed; 0 accepted ⇒ per-observation `not_applicable`;
resume does not double-count (byte-identical summary); corrupt row blocks
aggregate; aggregate ≡ persisted rows; no network (socket patched); no
provider branching in `aggregate.py` (source-scan test).

## Non-goals honoured

No precision/recall/F1, no compatible-match scoring, no live calls, no
calibration or historical replay, no prompt/contract/SoT/runtime changes,
no provider selection. `RUNNER_VERSION` bumped to `5b2.0.0`.

## Known gaps left for later slices

- Compatible-match artifact still computed-but-unconsumed (5B-5).
- Projected full-corpus cost extrapolation (contract §6, last row) is a
  reporting concern for 5B-5/5B-8, not part of the summary file.
