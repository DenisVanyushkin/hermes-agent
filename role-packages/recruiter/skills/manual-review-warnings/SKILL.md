---
name: manual-review-warnings
description: Use when Hermes Recruiter must summarize caveats, missing facts, and source limitations for a decision-support run.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, warnings, review, read-only]
    related_skills: [fit-recommendation, company-assessment, positioning-and-evidence]
---

# Manual Review Warnings

## Overview

Every decision-support output is a draft for Denis's manual review. This skill collects the explicit warnings that must accompany the bundle.

## When to Use

- Automatically with any decision-support run; include whenever warnings are detected.

## Required Flags

Every bundle carries:

- `manual_review_required=true`
- `draft_only=true`
- `no_outbound=true`
- `no_submission=true`

## Warnings to Surface When Relevant

- relocation not confirmed
- role fit is adjacent rather than direct
- company page has an anti-AI policy
- evidence is insufficient for certain claims
- company research is incomplete or stale
- career fact source missing for candidate-specific conclusions

## Required Inputs

- Module results and warnings from the current decision-support run.
- Safety flags of the run (draft_only, no_outbound, no_submission).

## Boundaries

- Warnings must be specific to this run, not boilerplate.
- Never soften or omit a warning to make the recommendation look stronger.

## Expected Outputs

- `manual_review_warnings` module payload: warnings list plus flags manual_review_required, draft_only, no_outbound, no_submission.

## Failure Behavior

- If warnings cannot be derived, still emit the mandatory safety flags; never return an empty payload.
