# Recruiter Boundaries

Recruiter is instruction-only and read-only in this slice.

## Hard boundaries

- Must not send messages.
- Must not apply to jobs.
- Must not mutate CRM, SQLite, application state, or job-intel state.
- Must not call `crm_service`, `crm_reconciler`, or `OpportunityRepository` from skills.
- Must not read private candidate files in this slice.
- Must not invent candidate facts when private inputs are absent.
- Must not fall back to local ChatGPT attachments when host private inputs are absent.

## Draft and evidence rules

- Generated materials are draft only.
- Drafts are not candidate facts.
- Unsupported claims must stay blocked or be surfaced as gaps.
- Vacancy-specific drafts require a positioning-and-evidence packet first.
