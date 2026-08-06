# §7.2 — T5 round 1 results: coverage 6.9% -> 40.7% (holdout confirms)

**Date:** 2026-08-06 · $0

| Slice | Before | After | Lift |
|---|---|---|---|
| DEV (2956) | 6.90% | **40.73%** | 5.9x |
| HOLDOUT (1212) | 6.60% | **41.75%** | 6.3x |

**No overfitting.** Holdout is not merely comparable, it is slightly HIGHER
than DEV. Rules mined from DEV generalise to text they were never written
against — which is the property the split existed to test.

All safety nets green (331 tests): 158 synthetic controls, golden fixtures,
and the 22 owner-marked negative fixtures — no new rule fires on an excerpt
the owner rejected.

## Honest caveat: the lift is concentrated in two broad patterns

| Fact | DEV hits |
|---|---|
| team_build_mandate | 815 (27.6% of corpus) |
| strategy_ownership | 352 (11.9%) |
| scope_breadth | 84 |
| executive_exposure | 74 |
| pnl_ownership | 15 |
| revenue_proximity | 13 |

Two patterns carry most of the gain, and both are broad
("build/grow/scale a team|organisation", "define/drive the strategy").
27.6% of ALL eligible vacancies claiming a team-building mandate is
plausible for leadership roles but suspicious corpus-wide. The 22 negative
fixtures are too small a sample to certify precision at this firing rate.

**Therefore round 1 is NOT declared done.** Before any further rule
authoring, precision on these two facts must be sampled and reviewed
(proposed: 30 random firings each, owner-reviewed like the 5B-5 package).
A high-recall/low-precision extractor would poison Stage 2 explanations
exactly as the LLM did.

## Still at or near zero

board_exposure, org_design_mandate, monetization_core, acquiring_core,
expansion_mandate. These need round-2 constructions.

## Against the acceptance target

Coverage target was why_attractive >= 60% on shown roles; corpus extraction
is a leading indicator, now at ~41% from 6.9%. Meaningful progress, target
not yet met, and the precision question above must be settled first.
