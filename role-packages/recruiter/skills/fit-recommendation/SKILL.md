---
name: fit-recommendation
description: Use when Hermes Recruiter must give a clear apply / consider / do-not-apply decision with confidence and rationale.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, recommendation, decision, read-only]
    related_skills: [vacancy-evaluation, company-assessment, company-risk-register]
---

# Fit Recommendation

## Overview

Synthesize whichever assessments are available into one clear decision: should Denis spend time on this company and role, and what is the next best action?

## When to Use

- When the `recommendation` module is requested.
- After vacancy and/or company assessments have run (degraded mode allowed with explicit missing-input warnings).

## Required Inputs

- Whatever assessment outputs are available: vacancy assessment, company assessment, risk register.
- Approved career fact source when role-fit is part of the decision.

## Boundaries

- Decision must be justifiable from sources; no generic "good fit" claims.
- If inputs are incomplete, either set `manual_review_required` prominently or `confidence: low`, and list what is missing.
- Never imply application intent or relocation readiness unless confirmed by Denis.

## Allowed Decision Labels

`strong_apply`, `apply`, `consider`, `manual_review_required`, `do_not_apply`, `reject`.

## Required Output

- `decision` label and `confidence`
- one-sentence verdict
- top reasons for and against
- role-fit rationale and company-quality rationale
- risk-adjusted upside and critical blockers
- next recommended action
- what would change the recommendation

## Expected Outputs

- `recommendation` module payload: decision, confidence, verdict, reasons for/against, rationales, critical blockers, next action, what would change the recommendation.

## Failure Behavior

- With incomplete inputs, return decision manual_review_required or confidence low with explicit missing-information warnings; never guess.
