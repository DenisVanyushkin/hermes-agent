# Step 5C — Provider Selection Evidence Package

**Purpose:** the evidence base for the owner's Provider Selection Review
(Roadmap SoT §9.5.5). This document does NOT select a provider — it presents
the measured axes (§9.4: no aggregate score) and a recommendation. The
selection is the owner's decision.

**Date:** 2026-07-23 · **Status:** awaiting owner decision.
**Providers evaluated:** `deterministic-phrase`, `llm-observation`
(`llm-obs-1.0.0` frozen baseline; `llm-obs-1.1.0` iteration).

---

## 1. Acceptance-gate status (§9.5)

| Gate | deterministic-phrase | llm-observation |
|---|---|---|
| 1. contract compliant | ✅ | ✅ (schema-valid, verbatim-checked) |
| 2. replay reproducible | ✅ identical hashes across runs | ✅ replay 100% deterministic; **live-repeat NOT byte-stable** (see §3) |
| 3. calibration complete | ✅ 158/0 controls | ✅ run + owner review complete |
| 4. benchmark completed | ✅ all §9.4 axes measured | ✅ all §9.4 axes measured (2 live runs each prompt) |
| 5. recommendation approved | — this document — | — this document — |

Both providers clear gates 1–4. Gate 5 is this review.

---

## 2. The §9.4 axes, side by side

Calibration corpus (controls 158 + golden 21 + decision 20 = 199), unless
noted. Numbers are per-run averages; LLM figures span 2 runs.

| Axis | deterministic-phrase | llm-obs-1.0.0 | llm-obs-1.1.0 |
|---|---|---|---|
| observations accepted | 158 | 449 | 345 |
| zero-observation cases | 95 (48%) | 7 (3.5%) | 42 (21%) |
| **precision on known gold** | **0.875** micro / 0.80 macro | 0.63 | 0.63–0.68 |
| **recall vs gold** | 0.20 micro | 0.30 | 0.24 |
| evidence verbatim rate | n/a (rule-based, always exact) | **1.000** | 1.000 |
| unsupported-evidence rate | n/a | 0.019 | ~0.02 |
| **cost / case** | **$0** | ~$0.0033 (full-size ~$0.0056) | ~$0.0033 |
| **latency p50** | **~3 ms** | 12–40 s | 16–33 s |
| full-corpus cost (3730/run) | **$0** | **~$21** | ~$21 |
| reproducibility (live-repeat) | exact | **30% row recurrence** | 30% row recurrence |

Full eligible corpus, deterministic only: 3730 cases, 0 failures, 1729
observations, 2714 zero-observation (72.8%) — matches the Step 4B recall
ceiling.

---

## 3. The two findings that decide it

### 3a. The LLM's added value is fact-type-specific (owner review, 5B-5)

The owner manually reviewed 120 LLM observations on the divergent cases:

| Fact group | accept rate | meaning |
|---|---|---|
| company.* | **0.62** | mostly real facts the gold under-annotated — genuine recall gain |
| mandate.* | **0.38** | over-reach: company-level quotes read as the candidate's remit |
| organization.* | 0.00 | over-reach |

Mandate facts are the **primary decision discriminator** (SoT §4). The LLM
adds value where it is cheap (company/enrichment context) and is unreliable
exactly where the recommendation is decided. Case-level verdicts on the
divergent set landed 11 deterministic-better / 10 LLM-better — a coin toss.

### 3b. Prompt iteration does not fix it (1.1.0, this session)

The one review-scoped rule (forbid mandate inference from company evidence)
cut company emissions harder (−29%) than mandate (−16%), collapsing
owner-approved company facts (stage 5→1, ecosystem 6→3) while leaving the
worst mandate stretch (scope_breadth) intact. Known-gold precision stayed
flat within run-to-run variance. **The over-reach is a stable property of an
LLM semantic provider on this contract, not a tunable prompt bug.**

### 3c. Run-to-run variance bounds what any LLM figure can claim

Two independent live runs of one frozen corpus recur at only **30% row
level** while staying aggregate-stable (precision-on-known-gold 0.63–0.64).
Any LLM point estimate carries ±~20% relative uncertainty; a canonical LLM
provider would need ≥2 runs per reported figure in production.

---

### 3d. §7 flagship acceptance criteria — an open gap NEITHER provider closes

The SoT §7 names specific flagship outcomes as success criteria. Measured on
the golden/decision fixtures:

| §7 criterion | deterministic | llm-obs-1.0.0 |
|---|---|---|
| §7.3 Wise Financial Crime / Airwallex Fraud NOT strong | ✅ reject / reject | ✅ reject / reject |
| §7.2 Airwallex GPNI in top band | ❌ unclear | ⚠️ promising **this run**, but `not_recommended` in the other run (unstable) |
| §7.2 Wise APAC in top band | ❌ unclear (title-only, honestly capped) | ❌ unclear |

**§7.2 is met by neither provider reliably.** Deterministic is honestly
"unclear" — it cannot extract the mandate signal from the title-only Wise
APAC snapshot or the GPNI text. The LLM hits GPNI in one run and rejects it
in another — its success is not reproducible. This is a **residual
mandate-extraction / gold-completeness gap that provider selection does not
resolve**; it needs separate work (richer source text, mandate-signal
extraction, or gold re-annotation) regardless of which provider is canonical.
It must NOT be read as a reason to prefer the LLM: an unreproducible pass on
one flagship is not "стабильно входят в верхний band."

## 4. Recommendation

**Adopt `deterministic-phrase` as the canonical provider for
mandate/decision-critical facts.** It is free, ~3 ms/case, perfectly
reproducible, and the most precise provider on known gold (0.875) — and it
is strongest exactly on the axis that decides recommendations.

**Do not adopt any LLM provider as the decision-critical semantic provider.**
Its recall advantage is concentrated in company/enrichment facts; on the
mandate axis it is a coin toss, prompt-tuning does not fix that, and it costs
~$21/run + 12–40 s/case + run-to-run non-determinism.

**Optional, separate track (not part of this selection):** the LLM
(`llm-obs-1.0.0`) could serve as an **enrichment layer for company facts
only** — where recall gain is real (0.62 accept) and precision acceptable —
via a non-decision-critical integration, if the owner later wants the extra
company context. This is a new proposal under §8, not a Phase III blocker.

**Do not run 5B-6 (bounded historical) or 5B-7 (full historical).** They
would spend ~$21–42 to re-confirm a conclusion the calibration corpus
already establishes. The calibration gate did its job: it stopped the spend.

---

## 5. If the owner approves the recommendation

Per §9.2, Shadow Deployment (Phase III) is conditional on this review. With
deterministic-phrase selected, Phase III proceeds using the provider already
running — no new provider integration, no runtime change. The existing
Step 4B deterministic pipeline IS the shadow candidate.

**But Phase III does not close §7.2.** The flagship gap (§3d) is orthogonal
to provider choice and remains open: entering Phase III with deterministic
means accepting that Wise APAC / Airwallex GPNI sit at "unclear/investigate"
until a separate §8 change improves mandate extraction or source-text
completeness. That is a defensible shadow-phase state (honest "investigate"
beats an unreproducible "save"), but it should be an explicit owner
acknowledgement, not a silent carry-over.

Standing owner gates that remain (outside this document): the current
prohibitions on touching production config, restarting the gateway, and
running historical LLM spend all stay in force until separately lifted.

---

## 6. Evidence index (all committed, branch local/customizations)

| Slice | Report | Commit |
|---|---|---|
| 5B-2 metrics infra | step5b-metrics-report.md | 428c66a9e |
| 5B-3 deterministic baseline | step5b-deterministic-baseline-report.md | ba4695d45 |
| 5B-4 LLM calibration | step5b-llm-calibration-report.md | d9871b198 |
| 5B-5 comparison + owner review | step5b-calibration-comparison-report.md, step5b5-review-outcome.md, step5b5-manual-review-package-MARKED.md | 109b02658, 591d6b64a |
| artifact-loss incident + re-run | step5b-artifact-loss-and-rerun.md | bd37b5c0c |
| 1.1.0 iteration | step5b-prompt-iteration-outcome.md | 274b71c93 |
| artifacts relocation fix | (paths.py) | 3887f78d8 |

Live artifacts: `/var/lib/job-intel/benchmark-artifacts/` (outside the repo).
Total live spend across Step 5B: ~$2.6.
