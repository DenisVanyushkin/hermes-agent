---
name: positioning-and-evidence
description: Use when Hermes Recruiter must turn vacancy facts and candidate facts into an explicit positioning packet.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, positioning, evidence, candidate-facts, read-only]
    related_skills: [vacancy-evaluation, document-writer, document-reviewer]
---

# Positioning And Evidence

## Overview

Use this skill to convert vacancy facts and candidate facts into a recruiter positioning packet that shows what to emphasize, what to avoid, and where evidence is missing.

## When to Use

- After vacancy evaluation is available.
- Before any vacancy-specific document drafting.

## Boundaries

- Private candidate/search inputs live outside the repo under `~/.hermes/private/career/`.
- Files may be absent.
- Their absence must not trigger invented facts.
- Their absence must not trigger fallback to local ChatGPT attachments.
- Missing facts must be surfaced as gaps.
- Report missing facts as gaps.
- Do not mutate repo files, job-intel state, or candidate records.
- Do not send outbound messages.

## Required Inputs

- Vacancy facts or recruiter vacancy packet.
- Candidate facts and search preferences when available.
- Optional company context from the approved read-only facade path.

## Expected Outputs

- Positioning summary.
- Requirement-to-evidence map.
- Strong, medium, weak, and missing evidence markers.
- RecruiterPositioningPacket for downstream draft creation.

## Failure Behavior

- If vacancy facts are missing, return `SOURCE_REQUIRED`.
- If candidate facts are incomplete, continue with explicit gaps and uncertainty.
- If company context is missing, do not invent it; label the gap.
