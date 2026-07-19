# Step 5B — Slice 5B-3: Offline Deterministic Baseline

**Exit gate:** `STEP_5B_DETERMINISTIC_BASELINE_READY`
**Date:** 2026-07-20
**Cost:** $0 (deterministic provider, `cost_state=known_zero` throughout; DB access read-only)

## What was built

1. **`calibration.py::iter_control_cases()`** — pure extraction of the
   control-case enumeration out of `run_synthetic_controls()` (verbatim
   logic move, no semantic change; calibration regression stays green:
   158 pass / 0 fail / 7 exempt, unchanged). Single source of truth so the
   benchmark dataset can never drift from the calibration runner.
2. **`benchmark/datasets.py`** — four deterministic dataset builders with a
   common case shape (`case_id, vacancy_key, title, text, company, location,
   source_system`), each mirroring the construction environment of its
   source (ControlCo/synthetic_control, golden `replay_input`,
   `replay_full.classify_corpus` for eligible). Plus JSONL snapshot
   export/load with hash-preserving roundtrip.
3. **`benchmark/baseline.py`** — `run_deterministic_baseline()`: one common-
   runner run per dataset under
   `artifacts/semantic-benchmark/5b3-deterministic-baseline/` (untracked),
   snapshotting the eligible corpus to `datasets/eligible.jsonl` first.

## Dataset sizes — actuals vs handoff plan

| Dataset | Plan said | Actual | Why |
|---|---|---|---|
| controls | 175 | **158** | 158 = executable controls (calibration `pass+fail`); the handoff's 175 counted a different roll-up. 7 controls are structurally exempt (no quotable phrase / paired-observation scenarios), 2 facts exempt by design. |
| golden | 21 | **21** | ✓ |
| decision | 25 | **20** | 27 golden decision cases minus 7 `policy_only` (built from synthetic canonical records — no source text to extract from). |
| eligible | 3626 | **3626** | ✓ matches Step 4B full-replay classification. |

## Baseline results (deterministic-phrase, prompt n/a, $0)

| Dataset | dataset_hash (sha256) | cases | failed | obs accepted | zero-obs cases |
|---|---|---|---|---|---|
| controls-1.0.0 | `185bac3f4e7899b8…` | 158 | 0 | 126 | 76 |
| golden-fixtures-21 | `d6484c3b7797671e…` | 21 | 0 | 16 | 10 |
| decision-golden-1.1.1 | `ee3864db6ef2d23b…` | 20 | 0 | 16 | 9 |
| eligible-corpus | `03387fbb76b4a571…` | 3626 | 0 | 1711 | 2626 |

Eligible-run detail: 1711 emitted = 1711 accepted, 0 rejected. Wall-clock
(deterministic latency series): total 46.1 s, p50 11.6 ms, p95 19.8 ms,
p99 38.5 ms, max 135 ms per vacancy. All cost/token aggregates
`known_zero`.

**Cross-validation vs Step 4B:** 2626/3626 = 72.4% eligible cases with zero
observations — consistent with the Step 4B closure figure («потолок recall
phrase-провайдера 72.6% eligible без semantic-фактов»). The deterministic
baseline reproduces the Phase I evidence through the new benchmark
infrastructure.

## Reproducibility (contract §Reproducibility)

Repeated deterministic run over the **snapshotted** eligible corpus
(`datasets/eligible.jsonl`, separate out_dir `eligible-repeat/`, run_id r2):
**3626/3626 semantic hashes identical, 0 mismatches.** Repeated-run
equality holds at full-corpus scale.

## Normative notes for later slices

- 5B-6/5B-7 MUST consume `datasets/eligible.jsonl` (hash
  `03387fbb76b4a571683ebb7e86c7711eb86276469fa969801c1c4d3505b1fe5f`), not a
  fresh DB scan — the DB grows daily and a rescan changes the dataset
  identity, making providers incomparable.
- The zero-observation population (2626 cases) is exactly where the LLM
  provider must demonstrate added recall; the 1711-observation population is
  where it must not lose precision.

## Evidence

```
tests/job_intel/test_semantic_benchmark_datasets.py  # 8 passed
full suite -k "semantic or shadow_evaluator or preference_model or vacancy_understanding"
# 258 passed
```

## Non-goals honoured

No precision/recall computation (5B-5), no LLM calls, no calibration
changes, no contract/SoT/runtime semantic changes (calibration refactor is
enumeration-extraction only, verified by unchanged control outcomes).
