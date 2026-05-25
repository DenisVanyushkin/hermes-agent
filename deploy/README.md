# Job-intel host deployment bundle

This directory contains the deployable host artifacts for the job-intel runtime.

## Contents

- `install_job_intel_host_runtime.sh` — installs the env file, systemd units, and browser profile directories
- `verify_job_intel_host_runtime.sh` — verifies the host contract and can fail closed by disabling timers on failure
- `env/job-intel.env.example` — env template used by the installer
- `systemd/*.service` / `systemd/*.timer` — host unit files for the job-intel timers

## Default layout

The deployed runtime expects:

- repo checkout: `~/.hermes/hermes-agent` for the service user
- env file: `/etc/job-intel/job-intel.env`
- state dir: `/var/lib/job-intel/state`
- browser profiles: `/var/lib/browser-desktop/profiles/{linkedin,hh,company-career}`

## Typical install

```bash
sudo ./deploy/install_job_intel_host_runtime.sh --enable-now
```

The installer renders the service files under `/etc/systemd/system`, runs verification, and disables any newly-enabled timers if verification fails.

## Runtime smoke checklist

Before enabling timers on a fresh host, run the smoke flow in this order:

1. **Install the host runtime**
   - `sudo ./deploy/install_job_intel_host_runtime.sh`
   - use `--enable-now` only after the smoke flow passes

2. **Edit the env file if needed**
   - `/etc/job-intel/job-intel.env`
   - keep it `0600`
   - confirm `JOB_INTEL_WORKDIR`, `JOB_INTEL_DB_PATH`, `JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN`, `JOB_INTEL_BROWSER_PROFILE_DIR_HH`, and `JOB_INTEL_EXPECTED_GIT_COMMIT`

3. **Run runtime verification**
   - `sudo ./deploy/verify_job_intel_host_runtime.sh`
   - this starts `job-intel-health.service`, waits for completion, inspects `journalctl`, checks the canonical DB, verifies provenance health, validates Python/venv imports, and checks browser profile access

4. **Run a one-shot daily job**
   - `sudo systemctl start --wait job-intel-daily.service`
   - confirm the run row appears in `/var/lib/job-intel/state/job_intel.sqlite3`

5. **Inspect the journal**
   - `journalctl -u job-intel-health.service --no-pager -o cat -n 200`
   - `journalctl -u job-intel-daily.service --no-pager -o cat -n 200`

6. **Test Slack delivery**
   - set `JOB_INTEL_SLACK_WEBHOOK_URL` in the env file
   - run `sudo systemctl start --wait job-intel-alert.service`
   - check the notification row and the Slack workspace for the test delivery

7. **Enable timers only after the smoke passes**
   - `sudo systemctl enable --now job-intel-daily.timer job-intel-alert.timer job-intel-health.timer job-intel-enrichment.timer job-intel-market.timer job-intel-strategic.timer`

If any step fails, fix the host contract first and re-run the verifier before enabling timers.
