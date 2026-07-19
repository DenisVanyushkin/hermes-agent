# Step 5B — Slice 5B-4: LLM Calibration Live Run

**Exit gate:** `STEP_5B_LLM_CALIBRATION_RUN_COMPLETE`
**Date:** 2026-07-20 · **Approval:** `APPROVE_5B4_CALIBRATION_WITH_CAP_$3` (owner, in chat)
**Spend:** **$0.5794 known** (cap $3, estimate $0.7–0.9) · 199/199 cases, 0 failed

## Setup

- Provider `llm-observation`, prompt `llm-obs-1.0.0` (frozen), `openai/gpt-5-mini`
  via OpenRouter, temperature 0, retries 0, fallbacks off.
- Pricing verified pre-run at openrouter.ai: $0.25/M input, $2.00/M output —
  exactly the spend-gate assumption; published in each manifest and hashed
  into `provider_config_hash`.
- New infrastructure this slice (TDD, offline-tested): `llm_live` registry
  mode (constructs only through the spend-gated `build_live_llm_provider`,
  refuses without `JOB_INTEL_LLM_LIVE_APPROVED=1`, pricing mandatory);
  `latency_mode` moved into registry identity; `run_benchmark(max_new_cases=…)`
  budget hook; `calibration_live.run_llm_calibration()` — chunked execution
  (25/chunk) with hard stops on known-cost>cap and ≥3 consecutive failures.

## Results

| Dataset | Cases | Obs accepted / rejected | Zero-obs | Tokens in/out | Cost | Cost/case | Live p50 / p95 / max |
|---|---|---|---|---|---|---|---|
| controls | 158 | 248 / 4 | 21 | 148k / 153k | $0.343 | $0.0022 | 11.9s / 25.2s / 63.9s |
| golden | 21 | 95 / 5 | 0 | 27k / 56k | $0.119 | $0.0056 | 23.6s / 35.1s / 37.1s |
| decision | 20 | 89 / 3 | 0 | 25k / 56k | $0.117 | $0.0059 | 23.5s / 42.1s / 44.5s |

First contrast with the deterministic baseline (formal scoring is 5B-5):
on golden the LLM accepted 95 observations vs 16 deterministic, zero-obs
cases 0 vs 10; on controls 248 vs 126, zero-obs 21 vs 76.

## Finding: input-hash collisions in record mode (fixed)

The post-run live-to-replay check failed 47/199. RCA: recordings are keyed
by `input_hash(title,text,structured)`, and the corpus legitimately contains
**199 cases over 148 unique inputs** (repeated neutral control phrases;
decision cases reusing golden fixtures). Record mode re-called live on every
case and **overwrote** the recording; repeated live calls at temperature 0
are not byte-identical (contract §Reproducibility explicitly allows this),
so 47 of the 51 duplicate-slot cases ended up with rows derived from a
response that no longer exists in the store.

- **Not a benchmark blocker:** the contract's blocker is `replay_mismatch`
  (replay infidelity). Replay-vs-replay determinism over all 199 cases:
  **0 mismatches (PASS)**. The 47 are live-repeat variance, confined to
  duplicate inputs; decision (unique replay of surviving recordings): 0.
- **Fix (TDD):** record mode is now idempotent per input — a successful
  recording for the same input/model/prompt is reused
  (`last_call_metadata.mode="record_cached"`), only failed recordings are
  retried live. Future paid runs cannot double-pay or orphan rows.
- **Cost impact here:** 51 avoidable calls ≈ $0.11 of the $0.579.

## Canonical artifact designation (for 5B-5)

`5b4-llm-calibration-replay/` (replay of the surviving recordings, 199/199,
deterministic) is the **canonical semantic result set** for comparison
slices. `5b4-llm-calibration/` (live) remains the cost/latency/usage
evidence. Both are under `artifacts/semantic-benchmark/` (untracked).

## Updated full-corpus cost projection (resolves handover discrepancy)

Full-size vacancies cost ~$0.0056–0.0059/case (golden/decision, complete
usage data). Projection for 3626 eligible: **~$21** (± text-length spread) —
the earlier session-handoff figure of ~$5.5 was an underestimate; the $22
estimate from the other handover is confirmed. Bounded historical (200):
**~$1.2**. Spend gates 5B-6/5B-7 must budget accordingly.

## Evidence

```
tests/job_intel/test_semantic_benchmark_live_gate.py   # 9 passed
full suite -k "semantic or shadow_evaluator or preference_model or vacancy_understanding"
# 267 passed
```

Stop conditions never triggered (0 failures, cost 19% of cap). Model
identity verified per call (exact slug), retry_count=0 everywhere.

## Non-goals honoured

No prompt changes, no contract/SoT/runtime-semantics changes (provider
record-mode idempotency is a transport-layer fix inside the provider
implementation), no provider selection, no bounded/full historical calls.
