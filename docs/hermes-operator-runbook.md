# Hermes Operator Runbook

Last updated: 2026-08-16

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

## Engineering Continuation And Diagnostics Recovery

These contracts apply to Slack-originated engineering work and to the
nightly diagnostics digest. They are deliberately fail-closed: an operator
must repair or re-run the controlled flow rather than infer missing state from
Slack prose.

### Bare approval binding

- A bare `выполняй` is executable only when the same session/history contains
  exactly one eligible approved engineering plan. No plan, an ambiguous set of
  plans, or a plan from another session fails closed.
- Long plans are retained byte-for-byte and are bound to a SHA-256 digest
  before execution. Do not reconstruct a lost plan from a shortened Slack
  message.
- If the router reports an authorization or plan-resolution failure, ask for a
  fresh explicit plan/approval in the same thread; do not treat a bare command
  as a new authorization.

### Change-artifact recovery

- A controlled run persists its independently verifiable evidence below
  `/home/hermes/.hermes/controlled-runs/<run_id>/`. The metadata file is
  `change-artifact.json` with schema `change-artifact.v1`.
- A successful material-change run must report `status=verified`; the report
  exposes only bounded metadata (type, count, bytes, and a short content-hash
  prefix), never absolute paths or file contents. A no-change run reports
  `status=not_required`.
- `artifact_not_persisted` is a completion blocker. Do not sweep, delete, or
  release the linked worktree/ref until the controlled report and artifact
  root have been inspected. Verify the durable evidence from the canonical
  checkout with:

  ```sh
  python -c 'from hermes_cli.pipeline_change_artifacts import verify_change_artifact; from pathlib import Path; ok, reason = verify_change_artifact(metadata_path=Path("/home/hermes/.hermes/controlled-runs/<run_id>/change-artifact.json"), repo_path=Path("<linked-worktree>"), canonical_repo_path=Path("/home/hermes/.hermes/hermes-agent")); print({"verified": ok, "reason": reason})'
  ```

  Keep the worktree and ref until this returns `verified=True`, or until a
  new controlled run captures a replacement artifact.

### Collector lifecycle and morning report

- The collector publishes `/home/hermes/.hermes/diagnostics/collector-status.json`
  with schema `collector-status.v1`, `state`, `run_id`, timezone-aware
  timestamps, `exit_code`, and `reason_code`. A successful run also records
  `digest_generated_at`, which must match the digest's `generated_at` and
  `run_id`.
- Status writes use a same-directory temporary file, `fsync`, atomic replace,
  and a parent-directory `fsync`; a failed write must not replace a previous
  valid status.
- The morning consumer treats `failed` as `COLLECTOR FAILED`, a stale
  `running` state as `COLLECTOR STUCK`, and missing/corrupt/mismatched state as
  `COLLECTOR STATUS MISSING` or `DIGEST STALE`. Fresh `ok` status is required;
  the default maximum age is two hours and can be overridden with the positive
  `DIAGNOSTICS_MAX_AGE_HOURS` environment variable.

### Google Workspace capture prerequisites

- `HERMES_HOME` must point to the Hermes home containing both
  `google_client_secret.json` and `google_token.json`. Never put either file
  in Git or print token contents in logs.
- Check the credentials before capture:

  ```sh
  python "$HERMES_HOME/skills/productivity/google-workspace/scripts/setup.py" --check
  ```

  The expected result is `AUTHENTICATED` (or an explicit partial-auth warning).
  If setup is missing, provide the client secret with `--client-secret`, then
  run `--auth-url` and exchange the returned code with `--auth-code`. The
  bundled `setup.py` currently has no `--services` flag: it requests its
  built-in Workspace scope set and records the scopes actually granted. Do not
  add unsupported flags; if a least-privilege scope set is required, change
  and review the helper before starting OAuth. Enable the matching Google APIs.
  An Advanced Protection account also needs the OAuth client allowlisted by the
  Workspace administrator.
- The Slack capture helper runs the repository's Google API script with
  `sys.executable` and forwards the resolved `HERMES_HOME`; this avoids using a
  different interpreter or silently falling back to `/home/hermes/.hermes`.

### Controlled gateway restart (only after rollout approval)

The gateway is a user-level systemd service, not `hermes-agent.service`. The
single restart command is:

```sh
ssh hermes-agent 'systemctl --user restart hermes-gateway.service'
```

After a restart, verify `systemctl --user status hermes-gateway.service`, the
main process command, and recent journal lines before declaring the rollout
healthy.

## Quick Decision Rule

- If the issue is only documentation or repo-local knowledge, edit locally.
- If the issue depends on host truth, inspect live before concluding anything.
- If the issue requires a host mutation, explain the exact intended change and wait for approval first.
