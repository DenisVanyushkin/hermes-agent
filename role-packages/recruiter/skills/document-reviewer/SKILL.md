---
name: document-reviewer
description: Use when Hermes Recruiter must review application-material drafts for unsupported claims, weak evidence, and generic positioning.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, review, drafts, hallucination-check, evidence]
    related_skills: [vacancy-evaluation, positioning-and-evidence, document-writer]
---

# Document Reviewer

## Overview

Use this skill to review draft only recruiter materials for hallucinations, unsupported claims, generic phrasing, and weak positioning.

## When to Use

- After draft creation.
- Before a user decides whether to edit, approve, or manually send a message outside this slice.

## Boundaries

- Reviewed materials remain draft only.
- Do not convert draft text into candidate facts.
- Highlight unsupported claims, missing evidence, and generic positioning.
- You must not send messages or apply to jobs.

## Required Inputs

- Draft text.
- Positioning-and-evidence packet.
- Candidate facts and vacancy facts that can support or refute claims.

## Expected Outputs

- Review findings with explicit unsupported claims.
- Suggestions for tighter evidence-backed phrasing.
- Final reminder that the output is draft only.

## Failure Behavior

- If draft text is missing, return `DRAFT_REQUIRED`.
- If evidence is missing, mark uncertainty rather than approving the claim.
- If the request implies outbound action, refuse and restate the boundary.
