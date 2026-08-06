# Phase III Stage 2 — Plan: Mandate/Company Explanations in Cards

**Status:** PLAN ONLY. Not started. **Hard-gated on closing §7.2 (gate A)** —
Stage 2 is the first stage that surfaces a *mandate verdict* to the user, and
we established the mandate axis is unreliable until the §7.2 gap is closed.

## Goal (§5 rollout item 2)

Each shown role carries a short, evidence-backed explanation of *why* — the
mandate read (what the role owns), the company read, and the feasibility
caveats (already delivered in Stage 1). Still observe-adjacent: explanations
annotate, they do not change which roles appear or their order.

## Preconditions (all must hold before task 1)

1. **§7.2 closed** (separate §8 track): mandate extraction reliable enough
   that a mandate explanation is not misleading on flagship roles. This is
   the gate — Stage 2 does not begin until the owner signs off the §7.2 fix.
2. Stage 1 advisory live and stable (feasibility explanations already shipping).
3. Drift/reaction data shows the mandate read is not systematically wrong on
   shown roles (re-check via an extended drift report with a mandate-accuracy
   cut).

## Clarifying questions for the owner (answer before task 1)

- **Q1. Delivery:** same separate-message mechanism as Stage 1 (one combined
  "why" block per run), or per-role (one explanation appended under each
  card)? Per-card needs bot-token chat.update (webhook can't edit) — a
  transport change. Recommendation: extend the Stage 1 advisory message into
  a fuller "why" block first (no transport change), evaluate, then decide on
  per-card later.
- **Q2. Depth:** one-line verdict summary per role, or the full
  why_attractive / why_may_not_work / unknowns breakdown (the shadow decision
  already produces all three)? Recommendation: start one-line, expand on ask.
- **Q3. Language:** RU (to match the operator digests) or EN? The decision
  engine statements are currently EN.

## Tasks (numbered, TDD, with dependencies + complexity)

> Format per the owner's planning convention. Each task: TDD (test first),
> dependency, complexity S/M/L.

**T0 — §7.2 mandate-extraction fix (BLOCKER, separate §8 track).**
Not part of Stage 2 code; Stage 2 cannot start until it lands. Tracked
separately. Complexity: L. Dependency: none (parallelizable now if desired).

**T1 — Capture explanation payload in the shadow.**
The shadow decision already yields `explanations` (verdict_summary,
why_attractive, why_may_not_work, unknowns). Extend `evaluate_semantic_shadow`
to carry a compact explanation dict, mirroring how Stage 1 captured
feasibility. Store as `explanation_json` (idempotent `_ensure_column`).
TDD: shadow result contains a non-empty verdict_summary for a role with a
clear mandate; empty-safe for a title-only role.
Dependency: T0 (else the mandate line is untrustworthy). Complexity: S.

**T2 — Explanation builder + formatter.**
`shadow_explanations.py` (pure production, store + stdlib): build one entry
per shown role {company, title, mandate_line, company_line, feasibility_line};
format as a labelled observe-only "why" block. Reuse the Stage 1 concern
filter for feasibility.
TDD: builder drops rejected roles; formatter is labelled observe-only and
lists mandate + company + feasibility; empty -> None.
Dependency: T1. Complexity: M.

**T3 — Entrypoint + flag, dry-run first.**
Extend the Stage 1 advisory entrypoint (or a sibling) behind
`SEMANTIC_SHADOW_EXPLANATIONS_ENABLED` (default OFF), dry-run default. Reuse
the webhook poster. Owner previews the rendered block before enabling.
TDD: flag default OFF; dry-run renders, does not post; posting needs flag AND
--post. Dependency: T2. Complexity: S.

**T4 — Accuracy guardrail (the §7.2 tie-in).**
Before enabling, an extended drift report cut: on shown roles, how often does
the shadow's mandate line agree with the owner's reactions / gold where
available? Gate enablement on this being clean (mirrors how C2 was gated on
the feasibility drift being clean).
TDD: report computes a mandate-line agreement metric on shown+reacted roles.
Dependency: T1, live data. Complexity: M.

**T5 — Enable + observe.**
Flip the flag after owner preview + T4 clean. Observe for a window; no further
stage until reviewed. Dependency: T3, T4, owner go. Complexity: S.

## Non-goals (explicitly out of Stage 2)

- No reranking or filtering (that is Stage 3).
- No change to which roles are shown.
- No preference-model learning (Stage 5).
- No transport change to per-card editing unless Q1 chooses it (then a
  separate task to introduce bot-token chat.update).

## Rollback

Single flag `SEMANTIC_SHADOW_EXPLANATIONS_ENABLED=0`; the explanation is a
separate message, so disabling it leaves cards and decisions untouched.

## DoD

- Every shown role can carry a mandate+company+feasibility explanation,
  observe-only, evidence-backed (provenance from the decision items).
- Mandate line demonstrably not systematically wrong (T4 clean).
- Owner previewed the rendered block before it went live.
- Reversible via one flag; no production decision changed.
- §7.2 closed and referenced in the enablement decision record.
