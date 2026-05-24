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
