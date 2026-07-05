---
name: questionnaire-answers
description: Use when Hermes Recruiter must draft truthful, form-compatible answers to application form or screening questions for a specific vacancy.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, questionnaire, screening, application-form, draft-only, read-only]
    related_skills: [positioning-and-evidence, package-reviewer, application-package-orchestrator]
---

# Questionnaire Answers

## Overview

Draft screening-form answers optimized for truthfulness, clarity, brevity, direct-answer-first structure, role-relevant evidence, and avoiding accidental disqualification. Answers are not persuasive essays unless the question asks for one.

## When to Use

- When application form questions are provided for a vacancy.
- Only after a `role_thesis_packet_v1` exists for this vacancy.

## Boundaries

- Draft only. No submissions, no outbound messages.
- Never invent: salary expectations, work authorization, visa status, notice period, relocation willingness, years of experience, education, languages, citizenship.
- Never turn "no" into "yes". Never upgrade "basic knowledge" into "expertise".
- Sensitive/optional demographic questions: answer only if the user provided the exact answer; otherwise "Prefer not to say" or leave blank.
- Respect `forbidden_claims` from the thesis packet.

## Career Facts Contract

- Canonical candidate facts live in `~/.hermes/job_intel/career_facts/` (`career_facts.json`, `preferences.yaml`, gated by `manifest.yaml`).
- Verify the manifest gate (`approved: true` + sha256 match) before use; on failure return `FACTS_UNVERIFIED` and stop. Skip re-verification if the orchestrator passed a fresh result in the same run.
- Location, remote/relocation stance, and salary posture come from `preferences.yaml` — never from memory or documentation examples. Experience answers come from `career_facts.json` via the evidence bank.
- Anything not present in those files is unknown: use a conservative pattern, a marked placeholder, or flag for user input.

## Procedure

1. **Classify each question** into one of: factual eligibility; work authorization/visa; location/relocation/remote; salary; notice period/availability; experience yes/no; experience narrative; motivation; behavioral; portfolio/links; demographic (optional); legal/compliance/background; free-form "anything else".
2. **General pattern** for open-ended questions: direct answer in the first sentence → 1–2 evidence points → tie back to the role → respect the form's character limit.
3. **Yes/no questions:** "Yes — [one sentence evidence]" when the form allows. Partial truth: "Partially. [truthful scope] + [adjacent relevant experience] + [bridge]". True no: "No. [optional adjacent experience]".
4. **Salary:** if no target is in `preferences.yaml` or user input, use a conservative deferral pattern ("Open to discussing a package aligned with role scope, location, and total rewards"). If the form requires a number → flag `NEEDS_USER_INPUT`.
5. **Work authorization/visa:** state only what `preferences.yaml` or the user confirms; otherwise conservative phrasing that does not claim authorization, or `NEEDS_USER_INPUT` if the form forces a definitive claim.
6. **Location/remote/relocation:** reflect `preferences.yaml` exactly. If the role requires full-time office relocation not covered by stated preferences → flag as fit risk before answering confidently.
7. **Notice period:** only if known; otherwise "To be confirmed depending on timing and current obligations"; exact-date fields → `NEEDS_USER_INPUT`.
8. **Motivation:** connect role dimensions and verified company context to evidence domains; no generic admiration.
9. **Behavioral:** STAR with executive concision (situation, ownership, action, result, relevance), sourced from achievements in `career_facts.json`.
10. **"Anything else":** only if it adds value; never paste a cover letter.
11. **Output format:** per question — Question / Recommended answer / Confidence (High/Medium/Low) / Notes. For long forms, group: (a) ready to submit, (b) needs user confirmation, (c) potentially risky / may affect eligibility.
12. **Definition of Done.** Every question answered directly; every factual claim supported or conservative; salary/visa/relocation/authorization/notice never invented; likely character limits respected; risky answers flagged; strongest role evidence present in open-ended answers; copy-paste ready.

## Required Inputs

- Application form questions (verbatim, with character limits if known).
- `role_thesis_packet_v1` for this vacancy.
- Verified career facts (see Career Facts Contract).

## Expected Outputs

- Answer sheet per the output format above, named `denis_vanyushkin_questionnaire_[company]_[role]` (lowercase snake_case).
- List of `NEEDS_USER_INPUT` items (red flags per the materials SoT §12: salary number, work authorization, visa sponsorship, notice period, relocation to a specific place, full-time office willingness, language proficiency level, undisclosed domain experience years, current-employer disclosure, prior application to the same company).

## Failure Behavior

- No questions provided → `QUESTIONS_REQUIRED`.
- No thesis packet → `THESIS_REQUIRED`.
- Manifest gate fails → `FACTS_UNVERIFIED`.
- Form forces an answer that cannot be safely generated → mark that item `NEEDS_USER_INPUT`; never guess.
