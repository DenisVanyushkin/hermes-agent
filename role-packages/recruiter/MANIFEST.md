# Hermes Recruiter

Recruiter is a role-scoped skill package skeleton for read-only vacancy evaluation, positioning, and draft-only application materials.

## Scope

- Evaluate vacancy fit using recruiter skills and lightweight bundles.
- Build a positioning-and-evidence packet from vacancy facts and candidate facts.
- Produce draft only materials for user review.
- Review drafts for unsupported claims, generic positioning, and missing evidence.

## Boundaries

- Must not send messages.
- Must not apply to jobs.
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
