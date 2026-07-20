# Step 5B — Slice 5B-5: Calibration Comparison Report

**Exit gate:** `STEP_5B_CALIBRATION_COMPARISON_COMPLETE`
**Verdict (recommendation to owner): `LLM_HISTORICAL_REPLAY_INCONCLUSIVE`** — blocked on the mandatory manual review, see §Verdict.
**Date:** 2026-07-20 · Inputs: 5B-3 deterministic baseline, 5B-4 canonical replay artifacts. $0 spent (all offline).

## New instrumentation (`benchmark/comparison.py`, 8 tests)

Micro+macro aggregation over the EXISTING calibration framework's per-fact
rows; mechanical evidence metrics from semantic dumps; decision divergence
categorised through the derived Decision-SoT equivalence classes
(compatible_match artifact consumed for the first time — never a hardcoded
table). No aggregate score anywhere (§9.4). Analysis artifact:
`artifacts/semantic-benchmark/5b5-comparison.json`.

## Fact-level precision/recall vs gold (21 fixtures, gold authoritative)

| | micro P | micro R | macro P | macro R | facts counted |
|---|---|---|---|---|---|
| deterministic | **0.875** | 0.200 | 0.800 | 0.244 | 21 |
| llm (llm-obs-1.0.0) | 0.210 | **0.300** | 0.261 | 0.381 | 28 |

Decomposition of the LLM's 100 emissions: **21 match · 67 on gold-unknown
facts · 12 wrong value on known gold.** Precision restricted to known-gold
facts = 21/33 = 0.64 (vs det 0.875). The headline «0.21» is dominated by
emissions where the annotator left `unknown` — under the fixed owner rule
these count against precision, but whether they are annotation gaps or
fabricated inference is exactly what manual review decides. Worst
over-emitters: customer_model, product_culture_signal, team_build_mandate,
cross_functional_leadership, strategy_ownership.

## Synthetic controls (158)

deterministic **158/0** · llm **122 pass / 36 fail**. Failure kinds:
ambiguous 14, conflicting 13, positive 6, negative 2, unknown 1 — the LLM
resolves ambiguity and picks sides in conflicts instead of returning
unknown (consistent with the 5A signal-prefix finding).

## Evidence quality (LLM, golden)

verbatim 1.000 · accepted-verbatim 1.000 · unsupported 0.019 · missing 0.000.
Evidence discipline is NOT the problem — the prompt quotes faithfully; the
issue is interpretive over-reach on top of real quotes.

## Decision divergence (mandatory-review population)

golden: 10 exact / 11 divergent of 21 · decision: 9 exact / 11 divergent of
20. Directions: `unclear→not_recommended` 12, `unclear→promising` 8,
`not_recommended→promising` 2. The LLM's extra facts push cases out of
"unclear" in BOTH directions — including the known Airwallex-GPNI
false-reject. Review package (every divergent case with its LLM excerpts):
`step5b5-manual-review-package.md`.

## Verdict rationale

- **Not RECOMMENDED (yet):** full historical replay (~$21) would score 3626
  cases with a provider whose known-gold precision is 0.64 and whose
  control failure rate is 23% — the calibration purpose is precisely to
  stop that spend until the dominant failure mode is understood.
- **Not NOT_JUSTIFIED either:** recall gain is real (+10пп micro, +14пп
  macro; zero-obs 0/21 vs 10/21 on golden), evidence is flawless, and 67 of
  79 «misses» may be annotation gaps rather than errors — if review
  confirms even half as true facts, LLM precision rewrites upward
  substantially.
- **⇒ INCONCLUSIVE**, resolution = the owner review fixed in 5B-0:
  calibration corpus 100% accepted-observation review + all 22 divergent
  decisions (package prepared). After review: either prompt iteration
  (`llm-obs-1.1.0` = new benchmark identity, owner decision) or bounded
  historical run (5B-6) with review-informed expectations.

## Non-goals honoured

No prompt changes, no threshold tuning, no SoT/runtime changes, no
provider selection (that is 5C), no live calls.
