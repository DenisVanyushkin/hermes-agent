# Job Intelligence Operator Guide

## What it does

This system continuously searches for executive product/business leadership roles for Denis Vanyushkin, scores them, deduplicates repeated postings, stores history in SQLite, and emits Slack-ready digests.

## Where state lives

- SQLite database: `~/.hermes/job_intel/job_intel.sqlite3`
- Cron scripts: `~/.hermes/scripts/job_intel_*.sh`
- Seed configs: `job_intel/seed/*.yaml`

## CLI

From the repo root:

```bash
./venv/bin/python -m job_intel bootstrap
./venv/bin/python -m job_intel daily
./venv/bin/python -m job_intel alert
./venv/bin/python -m job_intel enrichment
```

## Cron jobs

- Daily digest / source acquisition: `job-intel-daily` (runs in the twice-daily 09:00/17:00 windows)
- Exceptional alerts: `job-intel-alert` (reads persisted inventory; does not re-scan sources)
- Candidate enrichment: `job-intel-enrichment`

All three are configured to deliver to the Slack thread plus `C0B42K4H4KV`.

The shell wrappers are cwd-independent:
- `JOB_INTEL_WORKDIR` defaults to `/home/hermes/.hermes/hermes-agent`
- `JOB_INTEL_PYTHON` defaults to `$JOB_INTEL_WORKDIR/venv/bin/python`
- each wrapper `cd`s into the workdir before invoking `python -m job_intel ...`

## Maintenance

- Update `job_intel/seed/*.yaml` when candidate preferences or scoring rules change.
- Use the SQLite store as the source of history and deduplication state.
- Add new job sources by extending `job_intel/sources.py` and the ingestion loop in `job_intel/cli.py`.
- If alert volume is too high, raise the exceptional threshold or narrow search queries.
