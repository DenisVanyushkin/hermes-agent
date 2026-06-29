---
name: vacancy-evaluation
description: Use when Hermes Recruiter needs to evaluate vacancy fit using existing job-intel facts and explicit gaps.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, vacancy, evaluation, job-intel, read-only]
    related_skills: [positioning-and-evidence, document-writer, document-reviewer]
---

# Vacancy Evaluation

## Overview

Use this skill to evaluate a vacancy with explicit recruiter reasoning while preserving job-intel as the operational source of truth for machine scoring.

## When to Use

- When a recruiter request includes vacancy text, fetched vacancy content, or a job-intel vacancy identifier.
- When Hermes Recruiter needs a fit recommendation, machine-score framing, evidence summary, and explicit gaps.

## Boundaries

- job-intel data must be accessed through `job_intel/recruiter_read_facade.py` or a future approved runtime adapter.
- Do not read SQLite directly from the skill.
- Do not call CRM service, reconciler, or repository write paths.
- You must not create a parallel vacancy scoring system.
- Machine score context comes from existing job-intel scoring in `job_intel/evaluator.py`, `job_intel/seed/scoring.yaml`, and `score_vacancy_with_version(..., "v3")` or an approved scoring interface.
- Strategic disagreement with the machine score must be explicit.
- You must not send Slack, Gmail, Telegram, LinkedIn, or any other outbound messages.
- You must not apply to jobs or mutate application status.

## Required Inputs

- Vacancy text, normalized vacancy facts, or a recruiter-approved lookup key.
- Candidate/search source-of-truth context when available.
- Job-intel machine score and recommendation when available.

## Expected Outputs

- Normalized vacancy facts.
- Explicit machine score and recommendation context.
- Recruiter interpretation of fit, strengths, risks, and unknowns.
- Evidence and gap list suitable for a downstream positioning packet.

## Failure Behavior

- If vacancy source text is missing, return `SOURCE_REQUIRED`.
- If job-intel data is unavailable, say so explicitly and continue only with visible source text.
- If candidate facts are missing, mark missing facts as gaps.
