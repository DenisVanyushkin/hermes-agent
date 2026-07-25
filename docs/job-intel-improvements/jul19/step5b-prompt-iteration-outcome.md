# Step 5B — Prompt Iteration llm-obs-1.1.0: Outcome

**Result: NEGATIVE. Do not canonicalize 1.1.0.**
**Recommendation: stop prompt-tuning; take the strategic decision to Step 5C.**
**Date:** 2026-07-23 · Spend: $1.26 (2 runs, cap $3; approved
`APPROVE_5B_PROMPT_ITER_WITH_CAP_$3`). 1 transient transport failure (runB,
1/199, not systematic).

## Hypothesis tested

The 5B-5 review showed the provider over-reaches on mandate facts (accept
0.38) but is reliable on company facts (0.62). 1.1.0 added one rule:
forbid inferring a mandate signal from company-level evidence. Prediction:
mandate emissions down, company emissions stable, mandate precision up.

## What actually happened (2 runs, replay-verified 0 mismatches each)

**Emitted observations, golden+decision, avg of 2 runs:**

| Group | 1.0.0 | 1.1.0 | change |
|---|---|---|---|
| mandate | 112 | 94 | −16% |
| company | 68 | 48 | **−29%** |
| organization | 14 | 10.5 | −25% |

The rule cut **company** emissions harder than mandate — the exact opposite
of the intent. Per-fact:

| Fact (owner verdict) | 1.0.0 | 1.1.0 |
|---|---|---|
| mandate.scope_breadth (stretch) | 16 | **15.5** (unchanged) |
| mandate.revenue_proximity (stretch) | 8 | 7 |
| mandate.strategy_ownership (stretch) | 18 | 11 |
| company.stage (**approved**) | 5 | **1** (collapsed) |
| company.platform_ecosystem (**approved**) | 6 | **3** (halved) |
| company.scale (**approved**) | 17 | 13 |

**Precision on known gold:** 1.0.0 ≈ 0.63–0.64; 1.1.0 = 0.68 / 0.63 across
the two runs — flat within the measured run-to-run variance. micro precision
0.21 → 0.24 / 0.20: unchanged.

## Interpretation

The gating rule was too blunt. Told "don't infer mandate from company
evidence", the model became generally more cautious about emitting from
company-context sentences — suppressing the legitimate company observations
the owner had approved (stage, ecosystem, scale) while leaving the worst
mandate stretch (scope_breadth) essentially intact. It damaged the working
axis and barely touched the broken one. Net precision did not move.

## Why stop iterating

1. **Prompt-only fixes are fragile here.** One targeted rule produced
   collateral damage larger than its intended effect. Further tuning risks
   the same.
2. **Run-to-run variance (~±20% rel., established in 5B-4/5B-5) exceeds the
   effect size.** Chasing sub-variance prompt gains needs ≥2 runs each to
   even detect — expensive and low-yield.
3. **The strategic answer is already in the data.** Across 1.0.0 (2 runs) and
   1.1.0 (2 runs): the LLM adds real recall on **company/enrichment** facts
   (annotation gaps it fills) and does not reliably beat deterministic on the
   **decision-critical mandate** axis. That is a provider-role conclusion,
   not a prompt bug.

## Recommendation for Step 5C (owner decision)

**Deterministic phrase provider stays canonical for mandate/decision facts.**
The LLM's demonstrated value is as an **enrichment layer for company facts
only** (where recall gain is real and precision acceptable) — if adopted at
all, and only via a separate, non-decision-critical integration. Do NOT run
5B-6/5B-7 (bounded/full historical) on either prompt: we already have the
answer they would buy, at 1/20th the cost.

`llm-obs-1.0.0` remains the frozen benchmark baseline. `llm-obs-1.1.0` is
retained in the registry as evaluated-and-rejected evidence (commit
`c5b0da03c`), not deleted — it documents that the prompt-tuning path was
tried and measured.

## Contract compliance

No SoT/threshold/runtime changes. Both prompts frozen and reproducible.
Gold authoritative. This slice measures; the provider-selection decision is
Step 5C's to make.
