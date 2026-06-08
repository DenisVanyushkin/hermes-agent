# Job Intel Runtime Reference

Last updated: 2026-06-06

## Purpose

This document isolates the operational runtime facts for `job-intel` inside Hermes.

## Scope And Trust Level

- Facts taken from `CLAUDE.md` are local-repo facts.
- Facts marked here as historical should be re-verified on the live host before using them for changes.

## Runtime Identity

- `job-intel` is the executive job/vacancy intelligence pipeline inside Hermes.
- Primary source path: `/home/hermes/.hermes/hermes-agent/job_intel/`
- Main entry point: `job_intel/cli.py`
- Daily pipeline function: `run_daily()`

## Important Paths

Local and historical paths that matter most:

- Source tree: `/home/hermes/.hermes/hermes-agent/job_intel/`
- Historical local DB path from repo docs: `/home/hermes/.hermes/job_intel/job_intel.sqlite3`
- Canonical deployed DB path on host: `/var/lib/job-intel/state/job_intel.sqlite3` (historical, re-verify live)
- Host env file: `/etc/job-intel/job-intel.env`
- Wrapper script: `/home/hermes/.hermes/hermes-agent/scripts/job_intel_host_wrapper.sh`
- Lock file: `/home/hermes/.hermes/job_intel/job_intel_daily.lock`

Live read-only verification from this session:

- `/var/lib/job-intel/state/job_intel.sqlite3` exists as `161M`, owned by `jobintel:jobintel`, mtime `2026-06-06 14:36`
- `/home/hermes/.hermes/job_intel/job_intel.db` exists but is `0B`
- `/home/hermes/.hermes/job_intel/job_intel.sqlite3` exists as `532K`
- `/home/hermes/.hermes/job_intel/state/job_intel.sqlite3` exists as `280K`

Current best guess: `/var/lib/job-intel/state/job_intel.sqlite3` is the active deployed DB, and the others are stale or alternate-state files until proven otherwise.

## Systemd Timers And Units

Locally documented timers:

- `job-intel-daily.timer`
- `job-intel-weekly-kpi.timer`

Locally documented schedule:

- `job-intel-weekly-kpi.timer` runs Monday at `09:23`

Historical extra unit:

- `job-intel-alert.timer` existed on the host and produced noisy hourly exceptional alert messages before it was disabled

Live read-only verification from this session:

- `job-intel-daily.timer` is enabled
- `job-intel-health.timer` is enabled
- `job-intel-weekly-kpi.timer` is enabled
- `job-intel-alert.timer` exists but is disabled

## Verification Commands

Start with timer and unit state:

```sh
systemctl list-timers --all | grep -E "job-intel"
systemctl status job-intel-daily.timer --no-pager -l
systemctl status job-intel-weekly-kpi.timer --no-pager -l
```

Live read-only verification from this session:

- `job-intel-health.timer`: last trigger `2026-06-06 02:15:00 CEST`, result `success`
- `job-intel-daily.timer`: last trigger `2026-06-06 10:04:33 CEST`, result `success`
- `job-intel-weekly-kpi.timer`: last trigger `2026-06-01 09:23:16 CEST`, result `success`

If alert noise exists:

```sh
systemctl status job-intel-alert.timer --no-pager -l
```

For runtime DB verification, first confirm the real active DB path before reading results.

## Pipeline Shape

Documented pipeline flow:

1. Scrape sources.
2. Deduplicate by URL.
3. Score with `score_v1` and `score_v2`.
4. Produce `Evaluation` with recommendation, score band, and confidence.
5. Persist observability via `record_daily_observability()`.
6. Deliver notifications to Slack.

## Delivery Model

Locally documented behavior:

- Slack reports are part of the pipeline output.
- `job_intel/digest.py` formats Slack reports.

Historical caution:

- Prior rollout notes said the real delivery contract should stay websocket-based, not migrate to Slack webhooks without explicit approval.
- The same rollout also noted that `job_intel/cli.py` still contained `_deliver_to_slack()` behavior and could fail when `JOB_INTEL_SLACK_WEBHOOK_URL` was unset.

This is a likely design tension and should be rechecked against the live code and host configuration before changing delivery behavior.

Live journal evidence from this session suggests the current runtime is successfully producing daily and health report outputs, including:

- `Daily Executive Review` on `2026-06-06`
- `System Health Warning` on `2026-06-06`

That confirms the timer-driven reporting path is alive, even though the transport design still deserves explicit code/config verification before refactoring.

## Scoring And Alert Semantics

Score band order:

- `strong_fit`
- `potential_fit`
- `near_miss`
- `needs_review`
- `reject`

Alert semantics:

- Only `strong_fit` and high `potential_fit` should trigger exceptional alerts.
- `near_miss` does not trigger exceptional alerts.

## Feature Flags

Locally documented defaults:

- `JOB_INTEL_SEND_SOURCE_SEARCH_UPDATES=0`
- `JOB_INTEL_SEND_HEALTH_OK_REPORTS=0`

## Observability Tables

Documented tables:

- `vacancy_observability`
- `vacancy_rejection_summary`
- `vacancy_rejection_events`
- `source_kpi_run`

Important data semantics:

- `canonical_url` stores the real URL without a duplicate suffix.
- `url` may include `#dup:{vacancy_id}` as a uniqueness discriminator.

Rejection classifier groups:

- blockers:
  - `non_product_role`
  - `low_seniority`
  - `blocked_geography`
  - `onsite_requirement_mismatch`
  - `duplicate`
  - `sales_role`
  - `marketing_role`
  - `business_development_role`
  - `analyst_role`
- unknowns:
  - `salary_unknown`
  - `pnl_unknown`
  - `company_score_unknown`
  - `hiring_likelihood_unknown`
  - `location_unknown`
- warnings:
  - `weak_company_signal`
  - `low_confidence`
  - `unclear_scope`
  - `missing_product_ownership_evidence`

## Host Deployment Notes

Historical rollout findings:

- Deployment blockers were primarily host-side ACL and `safe.directory` issues.
- Service-user traversal into repo and runtime directories mattered.
- Access to `/var/lib/job-intel/state` had to be correct.
- Browser profile directories also needed appropriate permissions.

Useful inspection commands:

```sh
namei -l /home/hermes/.hermes/hermes-agent
getfacl /home/hermes/.hermes
getfacl /var/lib/job-intel/state
```

## Python Resolution In Host Wrapper

Historical resolution order in the host wrapper:

1. `JOB_INTEL_BROWSER_PYTHON`
2. `JOB_INTEL_PYTHON`
3. `/var/lib/browser-desktop/playwright-venv/bin/python`
4. `$workdir/venv/bin/python`
5. `$workdir/.venv/bin/python`
6. `python3`
7. `python`

## Browser Runtime Dependencies

Historical profile directories:

- `/var/lib/browser-desktop/profiles/linkedin`
- `/var/lib/browser-desktop/profiles/hh`
- `/var/lib/browser-desktop/profiles/company-career`

If scraping fails after deploy, re-verify these before blaming pipeline logic.

## Exporter And Grafana Notes

- Exporter Dockerfile: `deploy/docker/job-intel-exporter.Dockerfile`
- Base image: `python:3.11-slim`
- DB volume mapping: `/home/hermes/.hermes/job_intel` -> `/root/.hermes/job_intel`
- The mapping is intentionally not read-only because WAL mode needs `-shm` writes.
- Historical host ACL requirement:

```sh
setfacl -m g:jobintel:rwx /var/lib/job-intel/state/
```

- Grafana SQLite datasource plugin: `frser-sqlite-datasource` `4.0.6`
- Datasource UID: `job_intel_sqlite`
- Use `queryText`, not `rawQueryText`

## Known Open Questions

- Is `/var/lib/job-intel/state/job_intel.sqlite3` still the canonical live DB?
- Is `job-intel-alert.timer` still absent/disabled?
- Is delivery still expected to remain websocket-based in production?
- Do current browser profiles still live under `/var/lib/browser-desktop/profiles/`?
