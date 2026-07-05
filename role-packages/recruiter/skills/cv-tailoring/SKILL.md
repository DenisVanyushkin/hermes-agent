---
name: cv-tailoring
description: Use when Hermes Recruiter must produce a targeted CV for a specific vacancy from the role thesis packet and canonical career facts.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, cv, resume, tailoring, draft-only, read-only]
    related_skills: [positioning-and-evidence, package-reviewer, application-package-orchestrator]
---

# CV Tailoring

## Overview

Produce a targeted, screening-oriented CV for one specific vacancy: structured, concise, evidence-driven, keyword-aligned. The CV is the screening document — it must not read like a cover letter. Output includes a change summary against the canonical facts so the user sees exactly what was tailored and why.

## When to Use

- When the user or the application-package-orchestrator requests a targeted CV for a vacancy.
- Only after a `role_thesis_packet_v1` exists for this vacancy.

## Boundaries

- Draft only. No outbound messages, no applications, no mutations.
- Facts, dates, titles, and metrics come exclusively from `career_facts.json` — never alter or invent them.
- Do not: rewrite every role aggressively; turn the CV into a narrative essay; add photo/age/marital status/nationality/full address unless required; add false ATS keywords; overfit until the CV looks artificial; delete major senior experience just to hit length; manipulate titles (e.g. rename a commercial role into a product role); claim direct product-type experience the evidence does not support; create unexplained career gaps by editing.
- Respect `forbidden_claims` from the thesis packet.

## Career Facts Contract

- Canonical candidate facts live in `~/.hermes/job_intel/career_facts/` (`career_facts.json`, `preferences.yaml`, gated by `manifest.yaml`).
- Verify the manifest gate (`approved: true` + sha256 match) before use; on failure return `FACTS_UNVERIFIED` and stop. The orchestrator may pass a fresh verification result — do not re-verify within the same run if it does.
- Every line of the CV must be traceable to `career_facts.json` or explicit user input. Contact details come from `career_facts.json` only.
- Missing facts → `EVIDENCE_MISSING` gap or clearly marked placeholder, never invention.

## Procedure

1. **Headline.** Match the role without misrepresenting the candidate; compose from `career_facts.json` headline/positioning tags filtered by the vacancy function and domain. Never use a title the candidate has not held as an implied current identity.
2. **Professional summary.** 4–6 lines connecting the candidate to this role: years/level of experience, target domains, strongest matching capabilities, leadership scale, one differentiating executive angle. All values from the evidence bank.
3. **Competencies.** 10–14 maximum, prioritized by exact match to the vacancy; drawn from `core_competencies` in `career_facts.json`. Drop generic ones that do not help this role.
4. **Experience bullets — pyramid with a hard budget.** Relevance to THIS vacancy decides depth, not recency alone:
   - Top tier (the 2–3 most role-relevant positions, usually the most recent): 4–6 bullets each, rewritten around the thesis packet's success dimensions, action + context + outcome pattern ("Led [scope] to achieve [outcome] by [action], in [business context]").
   - Middle tier (next 2–3 positions): 2–3 bullets each — only the proof points that serve this vacancy.
   - Tail (older/less relevant roles, typically pre-2016): ONE line each — title, company, dates, plus at most one standout metric; or title/company/dates only.
   - Total bullet budget for the whole CV: **max 25 bullets.** Count them before formatting; if over, cut from the tail first, then thin the middle tier.
   - NEVER delete a role entirely (no unexplained gaps) — compress it to one line instead. Preserve facts, dates, scope, and strong metrics from the source; remove bullets that distract from the target role.
5. **Keyword coverage.** Compare against the vacancy: role function, domain, leadership, methods, seniority keywords. Integrate naturally; no keyword stuffing.
6. **Length — enforced, not aspirational.** Hard limit 2 pages (3 only for C-level breadth AND only if the user asks). Before delivery, estimate pages: with standard 11pt formatting a page fits roughly 45–50 lines; summary + 12 competencies + 25 bullets + one-line tail roles + education must land within ~2 pages. If the estimate exceeds the limit, cut per the pyramid budget above and re-check. State the resulting page estimate in the change summary.
7. **Final checks (Definition of Done).** Targeted headline; summary tailored to the vacancy; strongest matching evidence on the first page / top third; ≥80% of critical vacancy requirements addressed; no unsupported claims; no unexplained gaps; dates and titles preserved; pasteable into DOCX without structural cleanup; change summary included; risk notes included where a key requirement is weak.

## Required Inputs

- `role_thesis_packet_v1` for this vacancy.
- Verified career facts (see Career Facts Contract).
- Output format request (markdown default; DOCX-ready or PDF-ready text on request).

## Expected Outputs

- Targeted CV in Markdown (DOCX/PDF-ready on request), named `denis_vanyushkin_cv_[company]_[role]` (lowercase snake_case).
- Change summary / diff brief: what was changed vs canonical facts presentation and why.
- Risk notes for weakly covered requirements.

## Failure Behavior

- No thesis packet → `THESIS_REQUIRED` (do not build one implicitly; ask the orchestrator or user).
- Manifest gate fails → `FACTS_UNVERIFIED`.
- Required evidence absent for a must-have requirement → keep the CV truthful, note the gap in risk notes; if the gap makes targeting meaningless, report `EVIDENCE_MISSING`.
