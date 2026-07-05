---
name: package-reviewer
description: Use when Hermes Recruiter must review a full set of application materials against canonical career facts for hallucinations, unsupported claims, and cross-material inconsistencies.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, review, qa, hallucination-check, consistency, package, read-only]
    related_skills: [application-package-orchestrator, positioning-and-evidence, cv-tailoring, cover-letter, recruiter-message, questionnaire-answers, document-reviewer]
---

# Package Reviewer

## Overview

The QA gate for an application package. Reviews ALL prepared materials together — against `career_facts.json`, `preferences.yaml`, the vacancy text, and each other — to catch hallucinations, unsupported claims and generalizations, tone failures, and cross-material data conflicts. Complements `document-reviewer`, which reviews a single draft ad hoc; this skill owns whole-package review.

## When to Use

- After the material skills have produced drafts and before delivery to the user.
- Invoked by `application-package-orchestrator` in its review loop, or directly by the user on an existing package.

## Boundaries

- Read-only over drafts: report findings, do not rewrite materials yourself.
- Do not convert draft text into candidate facts.
- Never soften findings to make the package look stronger; never claim a package is clean when findings remain.
- Reviewed materials remain draft only.

## Career Facts Contract

- Canonical candidate facts live in `~/.hermes/job_intel/career_facts/` (`career_facts.json`, `preferences.yaml`, gated by `manifest.yaml`).
- Verify the manifest gate (`approved: true` + sha256 match) before reviewing; on failure return `FACTS_UNVERIFIED` — a package cannot be verified against unverified facts.
- The ONLY valid support for a factual claim is: `career_facts.json`, `preferences.yaml`, the vacancy text/form, or explicit user input in this conversation. The thesis packet is derived — if a claim traces only to the thesis packet, verify it against the underlying facts.

## Review Checks

Run every check against every material in the package:

1. **Factual traceability.** Every company, title, date, education item, metric, team size, market, and outcome must be traceable to a valid source. Untraceable → finding `unsupported`, severity high.
2. **Generalization inflation.** Adjacent or partial experience presented as direct/expert experience ("basic knowledge" → "expertise", tool familiarity → hands-on ownership, participation → leadership). → `unsupported`, severity high.
3. **Forbidden claims.** Anything from the thesis packet's `forbidden_claims` list, plus: invented work authorization, visas, citizenship, languages, salary, notice period, relocation commitments, recruiter names, or claimed prior contact. → `unsupported`, severity high / status blocked.
4. **Cross-material consistency.** The same underlying fact must not differ across materials: numbers (FTE, team size, P&L, years), titles, dates, company names, scope. Consistent claims restated in different words are fine; conflicting values are findings. → `inconsistent`, severity high. Also check materials do not contradict the CV's narrative (e.g. CL story conflicts with CV dates).
5. **Fit.** CV top third shows relevance to the vacancy; CL explains fit rather than repeating the CV; recruiter message has a clear hook; questionnaire answers reduce screening friction; role keywords present naturally. → `format` or `tone`, severity medium.
6. **Tone.** Senior and calm; no hype, desperation, clichés, or inflated phrases; channel-appropriate concision. → `tone`, severity low–medium.
7. **Risk.** Weak areas handled consciously; missing answers flagged rather than papered over; package not more aggressive than the evidence supports; nothing accidentally disqualifying. → `risk`, severity per impact.
8. **Formatting.** CV within 2 pages (bullet pyramid respected: full detail only for the most role-relevant 2–3 positions, tail roles one line; a CV over ~25 bullets or ~100 lines is a `format` finding); CV skimmable; CL under one page; message within channel limits; questionnaire copy-paste ready; placeholders clearly marked; file names follow `denis_vanyushkin_<type>_[company]_[role]` snake_case. → `format`, severity low.
9. **Per-material Definition of Done** from each material skill.

## Required Inputs

- All drafts in the package (any subset of CV, cover letter, recruiter message, questionnaire answers).
- `role_thesis_packet_v1` used to generate them.
- Verified career facts and vacancy text.

## Expected Outputs

`package_qa_report_v1`:

- `findings[]`: materials affected, quoted claim/passage, issue_type (`unsupported` | `inconsistent` | `tone` | `format` | `risk`), severity (`high` | `medium` | `low`), suggested_fix (concrete replacement phrasing or "remove"), source_check (what was searched in the facts and what was/wasn't found).
- `missing_confirmations[]`: items requiring user input before submission.
- `final_status`: `pass` (no findings) | `pass_with_notes` | `blocked`. **The finding TYPE decides the status, not its severity:**
  - `unsupported` or `inconsistent` findings of ANY severity → `blocked`. Invented, inflated, or conflicting facts are never deliverable as "notes" — they must be fixed or removed first.
  - `risk` / `tone` / `format` findings, including user-decision items (relocation willingness, salary posture, channel choice, disclosure decisions) → `pass_with_notes`. These are the user's calls to make; do not block on them and do not trigger regeneration for them — surface them clearly in `missing_confirmations` / notes.
- Reminder that all materials are draft only.

**Persist the report:** always write the full report to `package_qa_report.md` in the package output directory (`/home/hermes/.hermes/cache/documents/<company>_<role>/` — same path in sandbox and on host). The orchestrator refuses to deliver a package without this file, and it is attached to the chat together with the materials — so the report must be readable on its own: quote the checked claims and name the source of each verdict.

## Failure Behavior

- No drafts provided → `DRAFTS_REQUIRED`.
- Manifest gate fails → `FACTS_UNVERIFIED`.
- Thesis packet missing → review still runs against raw facts and vacancy text, but report the missing packet as a `risk` finding.
