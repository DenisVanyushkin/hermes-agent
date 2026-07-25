# Step 5B — Slice 5B-5: Manual Review Outcome & Final Verdict

**Date:** 2026-07-23 · Owner review of run-1 divergent cases, marked file:
`step5b5-manual-review-package-MARKED.md`. $0.
**Final verdict: `LLM_HISTORICAL_REPLAY_NOT_JUSTIFIED_AS_IS` — gate a prompt
iteration (`llm-obs-1.1.0`) before any bounded/full historical spend.**

## What the owner reviewed

120 LLM observations across the 21 divergent decision cases, each marked
✅ (real fact gold missed) / ❌ (interpretive stretch) / ❓ (unclear), plus a
per-case verdict on whose recommendation was closer to correct.

Totals: **48 ✅ · 60 ❌ · 12 ❓**. Case verdicts: **11 det-better · 10
llm-better** (≈50/50).

## The decisive finding: the failure is fact-type-specific

| Fact group | ✅ real | ❌ stretch | ❓ | accept rate |
|---|---|---|---|---|
| **company.*** | 26 | 16 | 0 | **0.62** |
| **mandate.*** | 22 | 36 | 12 | **0.38** |
| organization.* | 0 | 8 | 0 | 0.00 |

The provider is **reliable on company facts** (scale, customer_model, stage,
platform_ecosystem) — most of its "false positives" there are genuine gold
annotation gaps, i.e. the low precision number was partly an artifact of
under-annotated fixtures. But it **over-reaches on mandate facts** —
revenue_proximity, strategy_ownership, scope_breadth, cross_functional
_leadership are rejected far more than accepted.

This is the worst possible split for this system: **mandate facts are the
primary decision discriminator** (§4 of the SoT — mandate/company/feasibility
separation). The provider adds real value exactly where it is cheap
(company context, an enrichment layer) and is unreliable exactly where the
recommendation is decided. That is why the case-level verdicts land at
50/50 despite flawless verbatim evidence: the quotes are real, but the
mandate the LLM infers from a company-level quote often is not.

Most-rejected facts (the stretch signature): product_culture_signal (HR
boilerplate read as culture signal), revenue_proximity and scope_breadth
(company scale/platform language read as the candidate's mandate),
cross_functional_leadership (team-structure descriptions read as the
candidate's leadership remit).

## Why NOT_JUSTIFIED as-is (not INCONCLUSIVE any more)

- Spending ~$21+/run on full historical replay would scale a provider whose
  mandate-fact accuracy is 0.38 and whose decisions are right half the time
  on the cases that actually differ — the calibration exists precisely to
  stop that.
- The recall gain is real but concentrated in company/enrichment facts,
  which the deterministic layer can also be extended to cover far more
  cheaply than an LLM per vacancy.
- The variance finding (two runs, 30% row-level recurrence,
  precision-on-known-gold stable at 0.63–0.64) confirms this is a stable
  property of `llm-obs-1.0.0`, not a sampling artifact — so it will not
  improve by running more.

## Recommended next step (owner decision required)

**Prompt iteration `llm-obs-1.1.0`**, scoped by this review to one change:
suppress mandate-fact emission when the only supporting evidence is
company-level (scale, brand, ecosystem, HR boilerplate) — keep company-fact
extraction as-is. This is a new benchmark identity (new prompt_version →
new provider_config_hash), evaluated against the SAME frozen calibration
corpus, and — per the variance finding — over **≥2 runs**, because the
run-to-run spread is comparable to the effect a prompt change would produce.

Explicitly NOT recommended now: 5B-6 (bounded historical) and 5B-7 (full
historical) on `llm-obs-1.0.0`. They would measure a provider we already
know to be miscalibrated on the decisive fact group.

## Contract compliance

No prompt was changed in this slice (the iteration is a *recommendation* for
owner approval). No SoT/threshold/runtime changes. Gold treated as
authoritative per the fixed rule; the review only reclassifies which
gold-unknown emissions are annotation gaps vs stretches, exactly as the
5B-0 method allows.
