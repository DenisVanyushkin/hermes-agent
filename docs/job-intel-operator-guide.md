# Job Intelligence Operator Guide

## What it does

This system continuously searches for executive product/business leadership roles for Denis Vanyushkin, scores them, deduplicates repeated postings, stores history in SQLite, and emits Slack-ready digests.

## Where state lives

- SQLite database: `/var/lib/job-intel/state/job_intel.sqlite3`
- Host runtime state: `/var/lib/job-intel/state`
- Host wrapper scripts: `<repo-root>/scripts/job_intel_host_wrapper.sh` and `/root/.hermes/scripts/job_intel_*.sh`
- Seed configs: `job_intel/seed/*.yaml`
- Deployment/runbook: [`docs/job-intel-host-runtime.md`](job-intel-host-runtime.md)

## CLI

From the repo root:

```bash
./venv/bin/python -m job_intel bootstrap
./venv/bin/python -m job_intel daily
./venv/bin/python -m job_intel alert
./venv/bin/python -m job_intel enrichment
./venv/bin/python -m job_intel health
```

## Host timers

- Daily digest / source acquisition: `job-intel-daily` (runs in the twice-daily 09:00/17:00 windows)
- Exceptional alerts: `job-intel-alert` (reads persisted inventory; does not re-scan sources)
- Candidate enrichment: `job-intel-enrichment`
- Nightly health report: `job-intel-health` (summarizes acquisition quality, signal quality, and session health)

All four are configured to deliver to the Slack thread plus `C0B42K4H4KV`.

The host-side wrappers are cwd-independent:
- `JOB_INTEL_WORKDIR` defaults to the repository checkout root discovered from the wrapper location / git top-level
- `JOB_INTEL_PYTHON` defaults to `$JOB_INTEL_WORKDIR/venv/bin/python`
- `JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN` points at the persistent LinkedIn browser profile; HeadHunter uses `JOB_INTEL_HH_TOKEN_CACHE` and the official API
- `JOB_INTEL_HEADHUNTER_QUERY_LIMIT` limits configured queries; `JOB_INTEL_HEADHUNTER_PER_PAGE` controls page size only; `JOB_INTEL_HEADHUNTER_MAX_ITEMS` bounds acquisition per query (default `100`, hard cap `2000`)
- `JOB_INTEL_HH_DELAY_SECONDS` paces HeadHunter search and detail requests; `JOB_INTEL_TEXT_BACKFILL_DELAY_SECONDS` applies only to non-HH ATS detail requests
- each wrapper `cd`s into the workdir before invoking `python -m job_intel ...`

The shell wrappers are cwd-independent:
- `JOB_INTEL_WORKDIR` defaults to `/home/hermes/.hermes/hermes-agent`
- `JOB_INTEL_PYTHON` defaults to `$JOB_INTEL_WORKDIR/venv/bin/python`
- each wrapper `cd`s into the workdir before invoking `python -m job_intel ...`

## Maintenance

- Update `job_intel/seed/*.yaml` when candidate preferences or scoring rules change.
- Use the SQLite store as the source of history and deduplication state.
- Add new job sources by extending `job_intel/sources.py` and the ingestion loop in `job_intel/cli.py`.
- If alert volume is too high, raise the exceptional threshold or narrow search queries.
