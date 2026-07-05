---
name: application-package-orchestrator
description: Use when Hermes Recruiter must prepare a full application package or a subset of materials for a vacancy, including the iterative package review loop.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, package, orchestration, application-materials, draft-only, read-only]
    related_skills: [positioning-and-evidence, cv-tailoring, cover-letter, recruiter-message, questionnaire-answers, package-reviewer, manual-review-warnings]
---

# Application Package Orchestrator

## Overview

Drive end-to-end preparation of application materials for one vacancy: build the role thesis, invoke the requested material skills, run the package-level review loop until clean or the iteration limit is reached, then deliver the package with positioning summary, supporting claims, and risk notes. Supports any subset — a single material still goes through thesis + review.

## When to Use

- When the user asks for an application package, or for any application material(s), for a specific vacancy.
- Single-material requests route here too, so review is never skipped.

## Boundaries

- Draft only. No sending, no applying, no mutations.
- Do not force the full package when a subset is requested; do not skip the internal analysis even when asked for "just the materials".
- Pause for user confirmation (per materials SoT §12) when a form requires: exact salary, work authorization/visa answer, notice period, relocation to a specific place, full-time office commitment, language proficiency level, or disclosure decisions. If the user asked for best-effort output, produce conservative drafts with marked placeholders and risk notes instead of pausing.

## Career Facts Contract

- Verify the manifest gate at `~/.hermes/job_intel/career_facts/manifest.yaml` (`approved: true` + sha256 of `career_facts.json` and `preferences.yaml`) ONCE at the start of the run, then pass the verification result to invoked skills so they do not re-verify.
- On gate failure return `FACTS_UNVERIFIED` and stop the whole run — no materials may be produced from unverified facts.

## Procedure

1. **Determine scope.** Requested subset, or default full package: CV + cover letter + recruiter message; questionnaire answers only when form questions are provided. Note the cover-letter worth-writing test may drop the CL with a recommendation.
2. **Verify career facts** (see contract above).
3. **Ensure thesis.** If no current `role_thesis_packet_v1` exists for this vacancy, invoke `positioning-and-evidence`. If its `application_recommendation` is negative, tell the user before generating materials and confirm they want to proceed.
4. **Generate materials.** Invoke each requested material skill with the thesis packet and the facts-verification result.
5. **Review.** Invoke `package-reviewer` on all drafts.
6. **Fix loop.** While `final_status` is `blocked` or there are findings of severity medium or higher, and fewer than **3 review iterations** have run:
   - regenerate ONLY the affected materials, passing the relevant findings and suggested fixes to the material skill;
   - re-run `package-reviewer` on the full package (fixes can introduce new inconsistencies);
   - count the iteration.
   If findings remain after 3 iterations, stop fixing and mark the package `REVIEW_LIMIT_REACHED`: deliver with unresolved findings listed prominently as risk notes. Never silently drop findings; never claim the package is clean.
7. **Deliver** in this order (full-package format; collapse for subsets):
   1. Recommendation and positioning (angle, strongest 3 claims, likely concern and how materials handle it).
   2. Targeted CV.
   3. Cover letter.
   4. Message to recruiter.
   5. Questionnaire answers.
   6. Supporting claims (claim / evidence source / where used / risk level).
   7. Risks and confirmations needed (including `missing_confirmations` and any unresolved findings).
   8. Usage notes (recommended order of use).
   File naming: `denis_vanyushkin_<cv|cl|recruiter_message|questionnaire|application_package>_[company]_[role]`, lowercase snake_case.

   **Where to save files:** write deliverables ONLY to `/output/<company>_<role>/` (this sandbox directory is the host's `~/.hermes/cache/documents/<company>_<role>/`). NEVER write package files into the repo mount (`/workspace/live-hermes/`) — stray files there dirty the git baseline and block other pipelines.

   **How to deliver files to the chat:** in your FINAL message include one `MEDIA:` line per file with the HOST path, e.g.:

   ```
   MEDIA:/home/hermes/.hermes/cache/documents/acme_head_of_product/denis_vanyushkin_cv_acme_head_of_product.docx
   ```

   The gateway converts each `MEDIA:` tag into a native file attachment on every platform (Slack included). Container path `/output/X` = host path `/home/hermes/.hermes/cache/documents/X` — always translate to the host path in `MEDIA:` tags. A file that is only mentioned as a path on disk is NOT delivered; every deliverable must have a `MEDIA:` line.

   **MS Word:** when the user asks for Word files, produce real `.docx` with python-docx (`pip install python-docx` in the sandbox if missing). Fall back to Markdown only if installation fails, and say so explicitly.
8. **Report** per the delivery standard: what was prepared, positioning angle, review iterations used, key risks/confirmations. No unnecessary explanation when materials are self-contained.

   **Review is a hard gate.** The package-reviewer step (5–6) MUST write its report to `/output/<company>_<role>/package_qa_report.md` (findings, final_status, iteration count). The delivery message MUST (a) state `final_status` and iteration count, and (b) attach `package_qa_report.md` via its own `MEDIA:` line alongside the materials. **A package whose output directory contains no `package_qa_report.md` is not deliverable — go back to step 5.** Skipping review is never acceptable, including for regenerated, subset, or "small" packages, and including runs where materials were drafted in an earlier session.

## Required Inputs

- Vacancy text or URL (login-walled vacancies: user-provided text only).
- Requested output types (or default set).
- Verified career facts (see contract).
- Optional: application form questions, channel for recruiter message, language constraints, company research, previous materials for the same company.

## Expected Outputs

- `application_package_v1`: requested materials + positioning summary + supporting claims + risk notes + usage notes + review-loop summary (iterations, final_status).

## Failure Behavior

- Vacancy text missing or too incomplete to target materials → `SOURCE_REQUIRED`.
- Manifest gate fails → `FACTS_UNVERIFIED`.
- Review loop exhausted with findings → deliver with `REVIEW_LIMIT_REACHED` and explicit unresolved findings.
- Required user decisions outstanding → `NEEDS_USER_INPUT` listing the exact items.
