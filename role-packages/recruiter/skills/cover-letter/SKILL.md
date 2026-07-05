---
name: cover-letter
description: Use when Hermes Recruiter must draft a narrative cover letter for a specific vacancy from the role thesis packet.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, cover-letter, narrative, draft-only, read-only]
    related_skills: [positioning-and-evidence, package-reviewer, application-package-orchestrator]
---

# Cover Letter

## Overview

Draft a senior, thoughtful cover letter that explains why this role is a coherent next step and why the candidate should be considered. It must not repeat the CV — it makes a narrative argument from the same thesis and evidence bank.

## When to Use

- When the application requires a cover letter, the role is strategically attractive, or the fit is compelling but not obvious from the CV alone.
- Only after a `role_thesis_packet_v1` exists for this vacancy.
- **Worth-writing test:** if the role is weak or generic, say a cover letter may not be worth the effort instead of writing a hollow one.

## Boundaries

- Draft only. No outbound messages, no applications, no mutations.
- Do not: summarize the full CV; use generic company flattery; exceed one page unless explicitly requested; apologize for gaps; over-explain relocation/visa unless necessary; include salary unless asked; claim passion without evidence; mention weaknesses unless strategically reframed; create a story that conflicts with CV facts.
- Avoid inflated phrases: "visionary leader", "game-changing", unearned "uniquely qualified"/"passionate about", repeated "proven track record", generic "I am excited to apply".
- Respect `forbidden_claims` from the thesis packet.

## Career Facts Contract

- Canonical candidate facts live in `~/.hermes/job_intel/career_facts/` (`career_facts.json`, `preferences.yaml`, gated by `manifest.yaml`).
- Verify the manifest gate (`approved: true` + sha256 match) before use; on failure return `FACTS_UNVERIFIED` and stop. Skip re-verification if the orchestrator passed a fresh result in the same run.
- Every factual claim must be traceable to the evidence bank (itself sourced from `career_facts.json`), the vacancy text, or explicit user input. Missing facts → placeholder or gap, never invention.

## Procedure

1. **Length.** Default 350–550 words; short version 180–250; up to 650 only for very senior, complex roles. Always under one page.
2. **Opening.** Name the role and company; give the thesis in one or two sentences. No generic excitement — immediate relevance.
3. **Fit paragraph 1.** Strongest role-relevant experience: claim → evidence (where it happened, with scope) → relevance to this role.
4. **Fit paragraph 2.** Second-strongest angle or domain bridge, same claim-evidence-relevance pattern.
5. **Motivation paragraph.** Based on real company signals from the thesis packet (business model, product line, growth stage, market move, regulatory/monetization complexity) — never generic praise. Only verified signals.
6. **Closing.** Short, calm, confident. No desperation.
7. **Language.** English by default for international roles; Russian when the vacancy/recruiter/channel is Russian-speaking.
8. **Definition of Done.** First paragraph names role + specific thesis; 2–3 strong evidence points; points connected to company needs; a real motivation signal; under one page; not a bullet-by-bullet CV duplicate; no unsupported claims; sounds like a senior executive, not a template.

## Required Inputs

- `role_thesis_packet_v1` for this vacancy.
- Verified career facts (see Career Facts Contract).
- Optional: length/language constraints.

## Expected Outputs

- Cover letter draft, named `denis_vanyushkin_cl_[company]_[role]` (lowercase snake_case).
- Short note on the angle used and any risk (e.g. motivation signal weak because company research is shallow).
- Or an explicit recommendation to skip the cover letter, with reason.

## Failure Behavior

- No thesis packet → `THESIS_REQUIRED`.
- Manifest gate fails → `FACTS_UNVERIFIED`.
- No verifiable company motivation signal → write the motivation from role dimensions only and flag the gap; do not invent company facts.
