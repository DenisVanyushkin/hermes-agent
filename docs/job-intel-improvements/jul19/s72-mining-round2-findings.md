# §7.2 — T3 round 2: quality fixed, but frequency ranking is the wrong tool

**Date:** 2026-08-06 · DEV slice · $0

## Quality: fixed

Mined examples are now genuine duty statements from real vacancies:

- `pnl_ownership`: "You own a P&L and a single brutal number." /
  "Own the Roadmap and P&L: take full ownership of the consumer lending
  product roadmap" / "owning the full P&L"
- `pricing_core`: "You will own the pricing and incentive structure for the
  segment." / "You own acquisition, product strategy, onboarding, pricing…"
- `org_design_mandate`: "Lead and Scale the Organization: Build, mentor, and
  develop a multi-layer organization of Product Managers"
- `executive_exposure`: "bridging the gap between our C-level vision,
  commercial goals, and product execution"

Salary lines, About-blurbs and customer-marketing prose are gone.

## The real constraint: the target population is 52, not 2956

```
DEV 2956 -> target-population 52
```

This is not a filter bug. It matches production reality: of ~3650 vacancies
per run, production shows ~50. The executive-product population simply IS
that small.

**Consequence:** every mined candidate has n=1. Frequency ranking — the
selection mechanism assumed in the plan — cannot work on 52 documents. Noise
was the round-1 problem; sparsity is the round-2 problem, and no amount of
regex tuning fixes it.

## Revised approach for T5 (rule authoring)

Do NOT rank by frequency. Instead treat the mined duty sentences from the 52
DEV target roles as a **hand-readable exemplar corpus** (small enough to read
in full) and author rules against recurring *constructions* rather than
recurring exact strings — e.g. "own the <thing> P&L", "own(s) the pricing",
"build/scale the organisation/team", "present to the board / senior
leadership".

This keeps the anti-circularity property that matters: rules are still
written from REAL vacancy language, and acceptance is still measured on the
untouched HOLDOUT. Only the candidate-selection step changes, from frequency
ranking to reading a small exemplar set.

Plan §4 T5 is amended accordingly; T1/T2/T4 and the acceptance criteria are
unchanged.
