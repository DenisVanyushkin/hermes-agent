# Hermes Recruiter

Recruiter is a role-scoped skill package for read-only vacancy evaluation, positioning, and draft-only application materials, including full application packages with a package-level review loop.

## Scope

- Evaluate vacancy fit using recruiter skills and lightweight bundles.
- Build a role thesis packet (vacancy brief, evidence bank, positioning thesis, forbidden claims) from vacancy facts and canonical career facts.
- Produce draft-only application materials, one skill per material: targeted CV, cover letter, recruiter message, questionnaire answers.
- Orchestrate full or subset application packages with an iterative package review loop (max 3 iterations).
- Review whole packages against canonical career facts for unsupported claims, generalization inflation, and cross-material inconsistencies; review single drafts ad hoc.

## Career Facts Contract

- Canonical candidate facts: `~/.hermes/job_intel/career_facts/` (`career_facts.json`, `preferences.yaml`), gated by `manifest.yaml` (`approved: true` + sha256 per file).
- Skills verify the gate before using facts; gate failure returns `FACTS_UNVERIFIED` and stops material generation.
- No candidate facts are hardcoded in skills; editing the facts files (then running `scripts/update_career_facts_manifest.sh`) changes outputs without skill edits.
- Missing facts become explicit gaps or placeholders, never inventions.

## Boundaries

- Must not send messages.
- Must not apply to jobs or submit forms.
- Must not mutate job-intel state.
- Must not read SQLite directly from skills.
- Must use `job_intel/recruiter_read_facade.py` or a future approved runtime adapter for job-intel data.
- Must treat generated text as draft only.

## Package Layout

- `role-package.yaml` defines the package and role contract.
- `role.yaml` mirrors the role identity for future runtime wiring.
- `skills/` contains recruiter primitives as instruction-only skills.
- `bundles/` groups skills into lightweight recruiter workflows.
- `docs/` records recruiter role intent and recruiter boundaries.

## Skills

- `vacancy-evaluation` — fit evaluation with job-intel machine-score framing.
- `positioning-and-evidence` (v2) — role thesis packet + evidence bank (foundation for all materials).
- `application-package-orchestrator` — full/subset package driver with review loop.
- `cv-tailoring` — targeted CV + change summary.
- `cover-letter` — narrative fit letter.
- `recruiter-message` — short channel-fitted opener.
- `questionnaire-answers` — form-compatible screening answers.
- `package-reviewer` — whole-package QA gate (traceability, inflation, cross-material consistency).
- `document-writer` / `document-reviewer` — generic single-document drafting and ad-hoc review.
- `company-research`, `company-assessment`, `company-risk-register`, `fit-recommendation`, `questions-to-ask`, `manual-review-warnings` — decision-support modules.
