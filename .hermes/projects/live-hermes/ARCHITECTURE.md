# Architecture

## System overview

`live-hermes` is the Hermes Agent repository. For this workspace, the persistent cognition layer is maintained separately from code in `.hermes/projects/live-hermes/` so that the current state, decisions, task tracking, and work history survive context loss.

## Components

- **Repo-level documentation substrate**
  - `CURRENT_STATE.md` — current objective, phase, blockers, and immediate next actions.
  - `WORKLOG.md` — append-only engineering journal.
  - `DECISIONS.md` — ADR-style record of durable choices and rejected alternatives.
  - `NEXT_STEPS.md` — ordered resume path.
  - `TASKS.md` — hierarchical implementation tracking.
  - `ARCHITECTURE.md` — living technical map.
- **Repo-local operational docs**
  - `AGENTS.md` — local workflow rules for this checkout.
  - `docs/job-intel-operator-guide.md` — operational guide for the job-intelligence system.
  - `docs/plans/2026-05-16-executive-job-intelligence-roadmap.md` — staged implementation plan.
- **Active feature area**
  - `job_intel/` package — storage, scoring, source ingestion, CLI, and supporting logic.
  - `tests/job_intel/` — verification for the job-intelligence package.

## Data flow

1. Source/config inputs are loaded by the job-intelligence package.
2. Vacancy and candidate data are normalized into canonical records.
3. Duplicate records are detected and linked.
4. Scoring/evaluation logic assigns an action recommendation.
5. Results are persisted in SQLite for restart-safe history.
6. Digest or alert output is emitted through the Hermes runtime and cron wrappers.

## Important interfaces

- **Persistent store:** `~/.hermes/job_intel/job_intel.sqlite3`
- **Cron wrappers:** `~/.hermes/scripts/job_intel_*.sh`
- **CLI entry points:** `python -m job_intel bootstrap|daily|alert|enrichment`
- **Repo guidance:** `AGENTS.md` defines local process requirements for substantive Hermes code changes.

## Deployment model

- Development happens in the repo checkout on the `local/customizations` branch.
- Runtime state for job intelligence is kept outside the repo under `~/.hermes/`.
- Cron wrappers must be cwd-independent and able to resolve the repo workdir and Python interpreter explicitly.

## Operational considerations

- Treat these docs as the source of truth for resuming work.
- Update docs during the session, not only at the end.
- Keep operational notes and implementation details separate from transient chat context.
- If the active subproject changes, reflect the new focus here and in `CURRENT_STATE.md`.

## Assumptions and constraints

- The repository may contain unrelated or pre-existing state under `.hermes/`; inspect before making changes.
- Documentation should stay concise, verifiable, and aligned with the live filesystem.
- Any future implementation work should preserve the repo-local workflow requirements in `AGENTS.md`.
