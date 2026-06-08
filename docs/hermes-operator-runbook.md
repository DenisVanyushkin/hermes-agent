# Hermes Operator Runbook

Last updated: 2026-06-06

## Purpose

This runbook captures the safest known workflow for inspecting and changing the real Hermes VPS.

## Working Rules

- Start with read-only inspection.
- Explain the intended mutation before changing remote state.
- Do not claim success from edited files alone.
- Verify real runtime state through `systemd`, logs, DB evidence, or container state.
- Prefer `ssh hermes-agent` over `ssh hermes` for Hermes runtime work, because `ssh hermes` can drop into a root-context shell and accidentally use `/root/.hermes`.
- This was re-verified live in this session: `ssh hermes` landed as `root` with `pwd=/root`.

## Host Identity

- Public IP: `75.119.154.183`
- SSH alias: `hermes`
- Service user: `hermes`
- Main repo on host: `/home/hermes/.hermes/hermes-agent/`

## First Checks

Before any change, establish the current context:

```sh
ssh hermes-agent
pwd
whoami
hostname
```

Confirm key paths:

```sh
ls -ld /home/hermes/.hermes
ls -ld /home/hermes/.hermes/hermes-agent
ls -ld /home/hermes/.hermes/logs
```

## Safe Inspection Checklist

For general Hermes runtime inspection:

```sh
systemctl --user status
systemctl status --no-pager
docker ps -a
tail -n 100 /home/hermes/.hermes/logs/gateway.log
tail -n 100 /home/hermes/.hermes/logs/errors.log
```

For monitoring inspection:

```sh
ls -l /home/hermes/.hermes/monitoring
docker ps --format '{{.Names}} {{.Status}}'
```

For job-intel inspection:

```sh
systemctl list-timers --all | grep -E "job-intel"
systemctl status job-intel-daily.timer --no-pager -l
systemctl status job-intel-weekly-kpi.timer --no-pager -l
```

## Known High-Risk Pitfalls

### Wrong Home Context

- Risk: operating through `ssh hermes` can cause commands to run against `/root/.hermes`.
- Effect: split-brain state, wrong credentials, wrong DB, wrong runtime conclusions.
- Safer default: use `ssh hermes-agent` for runtime work.

### File Ownership And Git Trust

- Risk: root-owned files can appear anywhere in the repo due to sandbox containers.
- Common symptom: `fatal: detected dubious ownership in repository`.
- Known mitigation:

```sh
git config --global --add safe.directory /home/hermes/.hermes/hermes-agent
```

- Historical note: this had to be configured for both `root` and the service user in prior rollout work.

### ACL / Traversal Failures

- Risk: services fail even when files exist, because the runtime user cannot traverse repo or runtime directories.
- Common symptoms:
  - `CHDIR` errors
  - permission denied
  - service starts then fails immediately
- Inspect with:

```sh
namei -l /home/hermes/.hermes/hermes-agent
getfacl /home/hermes/.hermes
getfacl /var/lib/job-intel/state
```

## Runtime Verification Standard

Do not close host work until the relevant runtime signal is verified.

Examples:

- For services: `systemctl status ...` and recent journal lines.
- For `job-intel`: verify timer state, recent service run outcome, and DB writes in the canonical deployed DB.
- For Dockerized components: verify container status, not just compose files.
- For monitoring: verify services are actually running, not only that config files exist.

## Job-Intel Specific Notes

- Canonical deployed DB path is historically `/var/lib/job-intel/state/job_intel.sqlite3`.
- Older DB files under `/home/hermes/.hermes/...` should be treated as split-brain risk until re-verified.
- Host env file is `/etc/job-intel/job-intel.env`.
- Browser profile directories historically involved:
  - `/var/lib/browser-desktop/profiles/linkedin`
  - `/var/lib/browser-desktop/profiles/hh`
  - `/var/lib/browser-desktop/profiles/company-career`

## Noisy Timer Handling

If the host is producing recurring noise, first identify the exact source before changing anything.

Historical example:

- `Exceptional executive job alert` was produced by `job-intel-alert.timer`, not by the main daily job.
- That timer was later disabled after it was confirmed redundant.
- Live check in this session confirmed the unit file still exists and remains disabled.

Useful checks:

```sh
systemctl list-timers --all | grep -E "job-intel-alert"
systemctl status job-intel-alert.timer --no-pager -l
```

## Rebase / Upstream Sync Notes

- Local customizations live on branch `local/customizations`.
- Main scripts:
  - `scripts/rebase-local-customizations.sh`
  - `scripts/sync-runtime-scripts.sh`
- Critical implementation detail: re-exec through `sudo` must preserve `HOME`, otherwise paths can resolve under `/root/.hermes/...`.

## Quick Decision Rule

- If the issue is only documentation or repo-local knowledge, edit locally.
- If the issue depends on host truth, inspect live before concluding anything.
- If the issue requires a host mutation, explain the exact intended change and wait for approval first.
