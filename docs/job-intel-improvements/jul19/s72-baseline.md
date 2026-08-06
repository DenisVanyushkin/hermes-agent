# §7.2 — Baseline extraction coverage (T2 gate, before any new rule)

**Date:** 2026-08-06 · Full eligible corpus, deterministic provider, $0.

| Slice | Roles | With ≥1 mandate fact | Rate |
|---|---|---|---|
| DEV (70%) | 2956 | 204 | **6.90%** |
| HOLDOUT (30%) | 1212 | 80 | **6.60%** |

Split is unbiased (6.90 vs 6.60), so holdout acceptance will be meaningful.

## Per-fact reality: 2 of 13 targeted facts ever fire

| Fact | DEV hits / 2956 |
|---|---|
| mandate.growth_mandate | 103 |
| mandate.scope_breadth | 73 |
| mandate.pricing_core | 12 |
| mandate.pnl_ownership | 9 |
| mandate.expansion_mandate | 6 |
| mandate.team_build_mandate | 3 |
| revenue_proximity, strategy_ownership, org_design_mandate, executive_exposure, board_exposure, monetization_core, acquiring_core | **~0** |

## Reading this correctly

Two different numbers are in play and must not be conflated:

- **6.9%** — share of ALL eligible vacancies yielding any mandate fact
  (this metric, corpus-wide).
- **18%** — share of SHOWN roles with a non-empty `why_attractive`
  (measured earlier on 50 already-filtered roles).

Shown roles score higher because production already filtered for
seniority/function. Both confirm the same defect at different points.

## Consequence for the acceptance target

The agreed target (why_attractive ≥60% on shown roles) starts from a
corpus-wide extraction floor of 6.9%, with 7 of 13 mandate facts at
effectively zero. This is a bigger lift than the earlier 18% figure
suggested, and likely needs more than one authoring round. The target is
kept as-is; if round 1 lands materially short, that is reported as a fact,
not quietly renegotiated.
