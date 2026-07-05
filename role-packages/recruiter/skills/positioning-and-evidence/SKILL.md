---
name: positioning-and-evidence
description: Use when Hermes Recruiter must turn vacancy facts and candidate facts into a role thesis packet with an evidence bank for application materials.
version: 2.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, positioning, evidence, role-thesis, candidate-facts, read-only]
    related_skills: [vacancy-evaluation, cv-tailoring, cover-letter, recruiter-message, questionnaire-answers, package-reviewer, application-package-orchestrator, document-writer, document-reviewer]
---

# Positioning And Evidence

## Overview

Build the foundation artifact for all application materials: a role thesis packet (`role_thesis_packet_v1`) containing a vacancy brief, an evidence bank mapped to role success dimensions, a positioning thesis, and an explicit list of forbidden claims. Every downstream material skill consumes this packet so all materials argue the same case with the same facts.

## When to Use

- After vacancy evaluation is available, before drafting any vacancy-specific material.
- When the application-package-orchestrator requests a thesis packet.
- When the user asks for positioning analysis for a specific vacancy.

## Boundaries

- Do not mutate repo files, job-intel state, or candidate records.
- Do not send outbound messages.
- Model inference must never override factual sources.
- Source hierarchy when inputs conflict (highest wins):
  1. Explicit latest instruction from the user.
  2. Vacancy text / application form.
  3. `career_facts.json` (canonical structured resume).
  4. `preferences.yaml` (candidate preferences).
  5. Previous application materials.
  6. General company research.
  7. Model inference (never for facts).

## Career Facts Contract

- Canonical candidate facts live in `~/.hermes/job_intel/career_facts/`:
  - `career_facts.json` — structured resume: roles, companies, dates, team scope, achievements, metrics.
  - `preferences.yaml` — location/remote/relocation stance, target roles and seniority, salary posture, red flags.
  - `manifest.yaml` — source-of-truth gate.
- Before using any candidate fact: read `manifest.yaml`, require `approved: true`, verify the sha256 of both data files against the manifest. On mismatch or `approved: false`, return `FACTS_UNVERIFIED` and stop.
- Every candidate fact in the output must be traceable to `career_facts.json`, `preferences.yaml`, or explicit user input in this conversation. Do not restate candidate facts from documentation, examples, or prior outputs.
- Missing facts are gaps: mark them `EVIDENCE_MISSING`. Never invent, infer, or upgrade facts.
- Other job-intel data must come through `job_intel/recruiter_read_facade.py`. No direct SQLite reads.

## Procedure

1. **Vacancy brief.** Extract from vacancy text: role title and seniority; function; company stage and business model; domain; scope (IC / lead / director / VP / C-level, regional/global, P&L); expected first 6–12 month outcomes; must-have and nice-to-have requirements; screening signals (geography, visa, language, remote, industry, management scale); likely concerns about the candidate's fit. If the vacancy is behind a login wall, use only user-provided text — never invent requirements.
2. **Success dimensions.** Convert the vacancy into 4–6 success dimensions (e.g. product strategy and roadmap ownership; growth/monetization/pricing/retention; cross-functional execution; people leadership and org design; domain expertise; executive stakeholder management).
3. **Evidence bank.** For each dimension, select 1–3 evidence points from `career_facts.json`. Each evidence entry must carry: claim, source role/company/timeframe, scope, action, outcome, metric if present in the source. If no metric exists, use concrete scope-and-outcome language — never invent a number.
4. **Objections and mitigations.** Identify likely objections (industry gap, seniority mismatch, location/visa risk, hybrid product-commercial profile, etc.). For each, decide: address directly, reframe as strength, de-emphasize, or recommend not producing materials.
5. **Positioning thesis.** One paragraph, 3–5 sentences: why the role makes sense for the candidate, why the candidate is credible, what differentiated value they bring, what risk must be managed.
6. **Forbidden claims.** List claims that must NOT appear in any material because the sources do not support them (e.g. work authorization not confirmed in `preferences.yaml`, tools/domains absent from `career_facts.json`, titles never held).

## Required Inputs

- Vacancy text, fetched vacancy content, or a recruiter-approved lookup key.
- Verified career facts files (see Career Facts Contract).
- Optional: vacancy evaluation output, company research, previous materials for the same company.

## Expected Outputs

`role_thesis_packet_v1`:

- `vacancy_brief`: company, role_title, location, work_format, seniority, function, domain, must_haves, nice_to_haves, success_dimensions, application_questions, source_url, source_text.
- `evidence_bank[]`: claim, source_role, source_company, timeframe, evidence_text, metrics, confidence, recommended_use (cv / cl / recruiter_message / questionnaire), risk_notes.
- `role_thesis`: main_positioning, strongest_claims (top 3), company_motivation, likely_objections, objection_handling, application_recommendation.
- `forbidden_claims[]`.
- Legacy compatibility: positioning summary and requirement-to-evidence map with strong/medium/weak/missing markers (as in `recruiter_positioning_packet_v1`).

## Failure Behavior

- Vacancy source text missing → `SOURCE_REQUIRED`.
- Manifest gate fails → `FACTS_UNVERIFIED`; stop, do not build a packet from memory.
- Candidate facts incomplete → continue with explicit `EVIDENCE_MISSING` gaps; never fill gaps by inference.
- If the role appears clearly misaligned, say so in `application_recommendation` — do not soften it.
