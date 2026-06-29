---
name: document-writer
description: Use when Hermes Recruiter must create draft-only application materials from an approved positioning packet.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, drafts, cv, cover-letter, review-only]
    related_skills: [vacancy-evaluation, positioning-and-evidence, document-reviewer]
---

# Document Writer

## Overview

Use this skill to create draft only recruiter materials that a human reviews before any outbound action.

## When to Use

- Only after a positioning-and-evidence packet exists.
- For CV, cover letter, recruiter message, or follow-up drafts that must remain user-reviewable.

## Boundaries

- Generated CV, cover letter, recruiter message, and follow-up text is draft only.
- Drafts are not candidate facts.
- Drafts cannot promote claims into source of truth.
- claims require candidate facts or explicit Denis confirmation.
- If no positioning-and-evidence packet exists, return `POSITIONING_REQUIRED`.
- The skill must not generate vacancy-specific drafts without that positioning packet.
- The skill must not send messages or apply to jobs.

## Required Inputs

- RecruiterPositioningPacket or equivalent positioning-and-evidence packet.
- Requested document type.
- Vacancy facts and candidate facts that support each claim.

## Expected Outputs

- User-reviewable draft only text.
- Unsupported or weakly-supported claims called out inline or in notes.
- Explicit gaps when evidence is missing.

## Failure Behavior

- If positioning is missing, return `POSITIONING_REQUIRED`.
- If supporting facts are missing, keep placeholders or note the gap instead of inventing facts.
- If the request implies sending or applying, refuse and restate the draft-only boundary.
