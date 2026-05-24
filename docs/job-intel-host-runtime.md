# Job-intel host-managed runtime

This deployment moves job-intel acquisition out of the Hermes cron sandbox and into a single host-managed runtime namespace.

## Deployment bundle

The repo includes deployable host artifacts under `deploy/`:

- `deploy/install_job_intel_host_runtime.sh`
- `deploy/verify_job_intel_host_runtime.sh`
- `deploy/env/job-intel.env.example`
- `deploy/systemd/*.service`
- `deploy/systemd/*.timer`

## Canonical locations

- Repo / workdir: repository checkout root (resolved from the wrapper location / git top-level)
- DB: `/var/lib/job-intel/state/job_intel.sqlite3`
- State dir: `/var/lib/job-intel/state`
- Browser profiles:
  - LinkedIn: `/var/lib/browser-desktop/profiles/linkedin`
  - HeadHunter: `/var/lib/browser-desktop/profiles/hh`
  - Company career: `/var/lib/browser-desktop/profiles/company-career` (optional; created by the installer and required only when browser-native company crawling is enabled)
  - Base: `/var/lib/browser-desktop/profiles`
- Browser runtime: `/var/lib/browser-desktop`
- Env file: `/etc/job-intel/job-intel.env`
- Host wrapper: `<repo-root>/scripts/job_intel_host_wrapper.sh`

## Host contract

Every job-intel command must fail loudly unless:

- `JOB_INTEL_SERVICE_USER` is set and matches the current service account
- `JOB_INTEL_WORKDIR` exists and points at the canonical checkout
- `JOB_INTEL_DB_PATH` is writable
- `JOB_INTEL_STATE_DIR` is writable
- the LinkedIn / HH browser profile directories exist
- job-intel imports resolve from `JOB_INTEL_WORKDIR`
- the runtime git HEAD matches `JOB_INTEL_EXPECTED_GIT_COMMIT`

The company-career browser profile is tracked in runtime provenance for observability. It is not a hard deployment prerequisite, but if browser-native company-page crawling is enabled then the profile must be populated; otherwise the runtime will fall back to plain HTTP.

Each timer or wrapper must run under the configured service user (default: `hermes`) and export that value explicitly so the runtime provenance can detect user mismatches. If `JOB_INTEL_SERVICE_USER` is set, the installer / wrapper must verify that both the user and its primary group exist before proceeding.

## Scheduler

Hermes cron jobs are deprecated for job-intel acquisition.
Use host-side systemd timers instead:

- `job-intel-daily`
- `job-intel-alert`
- `job-intel-health`
- `job-intel-enrichment`
- `job-intel-market`
- `job-intel-strategic`

Each timer loads `/etc/job-intel/job-intel.env` and executes the host wrapper under the configured service user.

## State migration

The old Hermes-side database was archived before bootstrap of the new canonical DB.
The new DB is intentionally fresh to eliminate stale replay such as repeated `vacancies_found=88`.

## Slack delivery

The runtime supports Slack delivery via `JOB_INTEL_SLACK_WEBHOOK_URL`.
If that secret is not available, the host worker will still execute and record the delivery failure in the DB, but it cannot post automatically to Slack.
