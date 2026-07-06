---
name: company-assessment
description: Use when Hermes Recruiter must decide whether a company is worth engaging with, based on sourced public research.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, company, assessment, read-only]
    related_skills: [company-research, company-risk-register, fit-recommendation]
---

# Company Assessment

## Overview

Assess whether the company behind a vacancy is worth Denis's time and attention, using only claims that passed the company research quality gate.

## When to Use

- After company research claims are available for the target company.
- When the `company_assessment` module is requested in a decision-support run.

## Required Inputs

- Company research claims that passed the quality gate (source, date, confidence, fact-vs-inference).
- Optional: approved career fact source when the personal-fit dimension is requested.

## Boundaries

- Use only sourced research claims; never invent funding, compensation, reporting lines, or hiring urgency.
- Respond in the language of the incoming request: a Russian request gets a fully Russian answer, an English request gets English. Do not mix languages in one reply.
- Mark unknowns as unknown instead of guessing.
- No internal pipeline/process/provenance language in the user-facing output.

## Required Assessment Dimensions

1. Business quality — model clarity, market size/growth, revenue model, customer segments, product maturity, competitive position, global expansion potential.
2. Financial and funding health (when data is available) — stage, valuation signals, profitability path, layoffs/restructuring, burn-rate concerns, exit plausibility, investor quality.
3. Strategic momentum — launches, expansion, partnerships, regulatory milestones, customer growth, leadership hires. Is the company moving forward, stagnating, or firefighting?
4. Reputation and culture signals — review patterns, leadership reputation, workload/burnout signals, talent density. Separate repeated pattern / single anecdote / unverified claim.
5. Product and technical credibility — product-market fit, platform quality, infrastructure maturity, operational reliability, regulatory complexity. For product leadership roles: strategic vs execution-only vs cleanup vs politically exposed vs high-leverage.
6. Role attractiveness within company context — why the role exists, real authority, scope vs title, success metrics, empowered vs accountable-without-control, core vs peripheral function.
7. Compensation / upside signals — only when available; otherwise mark unknown.
8. Personal fit for Denis — remote/relocation compatibility, timezone/travel, visa concerns, family/lifestyle friction, stress level, domain interest, career narrative fit. Include a practical note: would this be worth Denis's time?

## Required Output

A `company_assessment` module payload with: recommendation (e.g. worth_engaging / needs_diligence / avoid), confidence, per-dimension findings, a source list, and explicit fact-vs-inference separation.

## Expected Outputs

- `company_assessment` module payload: recommendation, confidence, summary, per-dimension findings, source list, fact-vs-inference separation.

## Failure Behavior

- If research is missing or too weak, report the module as BLOCKED with the research gate reason instead of producing a generic assessment.
