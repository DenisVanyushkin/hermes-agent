# Step 5C Decision Record + Phase III Entry

## Part A — Provider Selection Decision (§9.5.5 / §8 change record)

- **Decision:** `deterministic-phrase` is the **canonical semantic provider**
  for decision-critical (mandate/feasibility) facts. No LLM provider is
  adopted on the decision path. (LLM company-fact enrichment remains a
  possible future §8 proposal, not adopted here.)
- **Owner:** approved by Denis in chat, 2026-07-23 ("прими рекомендацию,
  оформляй переход в Phase III").
- **Reason:** §9.4 axes (evidence: `step5c-provider-selection-evidence.md`).
  Deterministic is free, ~3 ms/case, perfectly reproducible, and most precise
  on known gold (0.875) — strongest on the axis that decides recommendations.
  The LLM's recall gain is confined to company/enrichment facts; on the
  mandate axis it is a coin toss (owner review: mandate accept 0.38), prompt
  iteration did not fix it (`step5b-prompt-iteration-outcome.md`), and it
  costs ~$21/run + 12–40 s/case + run-to-run non-determinism.
- **Acceptance-criteria impact:** §9.5 gate 5 satisfied. §7.3 met by the
  selected provider. **§7.2 remains OPEN** (Airwallex GPNI / Wise APAC not
  reliably in top band) — a residual mandate-extraction gap orthogonal to
  provider choice, carried into Phase III as an acknowledged open item, to be
  closed by a separate §8 change (richer source text / mandate signals /
  gold re-annotation).
- **Backward compatibility:** zero runtime behaviour change from the decision
  itself — the selected provider is the one already running.

## Part B — Phase III Entry: Shadow Deployment (§9.2, §5)

Shadow Deployment is now unblocked (conditional on 5C, per §9.2). It is
**observe-only**: the semantic-preference evaluator runs against live
vacancies and records its verdict; it does NOT influence any user-facing
recommendation, notification, or score.

### Rollout stages (§5 order) — where we are

| Stage | §5 item | State |
|---|---|---|
| 0. Shadow observe-only | (pre-rollout) | **LIVE (this deploy)** |
| 1. Feasibility gates only | rollout §1 | not started — needs owner go |
| 2. Mandate/company explanations in cards | rollout §2 | not started |
| 3. Preference reranking (legacy generation kept) | rollout §3 | not started |
| 4. Selection policy: quotas/diversity/exploration | rollout §4 | not started |
| 5. Weekly preference proposal loop | rollout §5 | not started |

Only Stage 0 is activated. Stages 1+ change user-facing behaviour and each
needs an explicit owner go (they are the §5 "controlled rollout" gates).

### What is deployed now (Stage 0)

- `job_intel/vacancy_understanding/semantic/runtime/shadow_deploy.py` —
  `evaluate_semantic_shadow` (deterministic provider → semantic runtime →
  shadow evaluator) and `run_semantic_shadow(store, run_id)`.
- Decoupled post-run job `scripts/job_intel_semantic_shadow.py`, run by
  `job-intel-semantic-shadow.timer` at 09:30 UTC (after the 08:00 daily).
  Chosen over a cli.py hot-loop hook to preserve the production/semantic
  import boundary and to keep a shadow failure from ever touching scoring.
- Shadow verdicts persisted to `semantic_shadow_evaluation` (observe-only
  table). Flag `SEMANTIC_SHADOW_ENABLED=0` disables it.
- Backfill on run 426 (live, 2026-07-23): 3647 vacancies, **0 errors**,
  distribution 2876 unclear / 736 not_recommended / 35 promising — the
  deterministic shadow is appropriately conservative (matches the §7.2
  honest-capping finding).

### Feedback semantics (§5) — recorded, not yet wired

`not_interesting` classification, `save_for_later`, `applied/exceptional`,
and "bad data never trains the preference model" (§5) apply to Stage 3+,
when preference actually influences ranking. Stage 0 only observes; no
feedback loop is closed and no model learns anything.

### Rollback

Stage 0 rollback is a single switch: `SEMANTIC_SHADOW_ENABLED=0` (or disable
`job-intel-semantic-shadow.timer`). It writes only to its own table and
touches no production evaluation — removing it leaves the pipeline exactly as
before. No data migration to undo.

### DoD for Stage 0 (met)

- Observe-only, zero user-facing change ✅
- Every shadow verdict has provenance (semantic_hash, shadow_version) ✅
- Preference SoT not modified; nothing learns automatically ✅
- Verifiable rollback path ✅
- Runs on the live pipeline against real vacancies ✅ (run 426)

### Next owner decisions (NOT taken here)

1. After N days of shadow data: compare shadow verdicts vs production
   recommendations (a weekly drift report — §5 deliverable) to decide whether
   Stage 1 (feasibility gates only) is justified.
2. Close the §7.2 mandate-extraction gap (separate §8 work) before any stage
   that surfaces mandate verdicts to the user.
