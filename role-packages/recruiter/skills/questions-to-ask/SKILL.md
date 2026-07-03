---
name: questions-to-ask
description: Use when Hermes Recruiter must convert risks and unknowns into a concrete diligence plan and interview questions.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, diligence, questions, read-only]
    related_skills: [company-risk-register, vacancy-evaluation, fit-recommendation]
---

# Questions to Ask / Diligence Plan

## Overview

Turn open risks and unknowns from the vacancy and company analysis into specific questions Denis can ask at each stage of the process.

## When to Use

- Before a recruiter screen, hiring manager conversation, or offer discussion.
- When the `questions_to_ask` module is requested (may run in degraded mode from just a vacancy/company source, marking gaps).

## Required Inputs

- Vacancy or company source; ideally vacancy assessment, company assessment, and risk register outputs.
- Gaps must be marked when running from a bare vacancy/company source.

## Boundaries

- Questions must trace back to identified risks, unknowns, or missing evidence — no generic question dumps.
- Mark which inputs were unavailable when running in degraded mode.

## Required Structure

Group questions by audience:

- recruiter screen
- hiring manager
- product leadership
- team interviews
- compensation / relocation discussion

Cover categories where relevant: role scope, decision rights, success metrics, team structure, company strategy, financial health, culture, relocation, compensation.

## Examples

- How much of the role is direct ownership versus broader coordination?
- What are the first 6–12 month success metrics?
- Is relocation mandatory, and what support is provided?

## Expected Outputs

- `questions_to_ask` module payload grouped by audience: recruiter_screen, hiring_manager, product_leadership, team_interviews, compensation_relocation.

## Failure Behavior

- In degraded mode (no assessments available), still produce source-traceable questions but mark the gaps and lower confidence.
