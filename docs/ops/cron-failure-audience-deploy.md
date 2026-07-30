# Deploy Runbook: Cron Failure-Delivery Audience

This is the live half of the `fix/cron-failure-audience` work
(`docs/superpowers/plans/2026-07-29-cron-failure-audience.md`, Task 6). It
requires explicit human authorization before any step is run — none of
this has been executed yet. Read `docs/ops/cron-failure-audience.md`
first for what the feature does and why.

All commands run against the live host through the jump proxy:

```bash
ssh -J proxmox denis@192.168.20.10
```

Host facts this runbook depends on, verified at authoring time
(2026-07-30) by inspecting the host read-only — **re-check anything
timestamped, since state may have moved on by execution time**:

- Live install: `/home/denis/.hermes/hermes-agent`, git worktree on branch
  `local/customizations`, currently at commit `a702e39779`.
- Isolated work worktree (same repo, shared refs): `/home/denis/work/cron-audience`,
  branch `fix/cron-failure-audience`, currently at commit `91970d33f5`.
- systemd user unit: **`hermes-gateway.service`** (confirmed via
  `systemctl --user list-units 'hermes*' --all` — there is exactly one
  hermes unit on this host, no separate cron/ticker unit).
- `~/.hermes/config.yaml` already has a top-level `cron:` key
  (`config.yaml:175-176`, currently just `wrap_response: false`) — the
  config step below is an *addition under the existing key*, not a new
  top-level key.
- `~/.hermes/config.yaml` already has `gateway.error_alerts.channel:
  "telegram:79564752"` (`config.yaml:307-310`) — no alert-channel change
  is needed.
- `~/.hermes/cron/jobs.json` top level is `{"jobs": [...], "updated_at":
  ...}` (a dict, not a bare list).
- Amina's two jobs, confirmed present with `audience` currently unset on
  both:
  - `150d115fe905` — "Вечерний короткий прогноз погоды Алматы — Амина",
    `enabled: true`, `deliver: whatsapp:+77011102626`, schedule `0 20 * *
    *` (Almaty), next run 2026-07-30 20:00 local.
  - `8b751dbfd5d6` — "Утренний короткий прогноз погоды Алматы — Амина",
    `enabled: false`, `deliver: whatsapp:+77011102626`, schedule `0 7 * *
    *`.
- **The `post-commit` hook on `local/customizations` autopushes to
  `origin` on every commit made on that branch** (unlike
  `fix/cron-failure-audience`, where the same hook is inert). Merging the
  fix branch into `local/customizations` in Step 4 below will push to
  GitHub as a side effect of committing the merge. This is expected, not
  a bug, but it means the merge is effectively irreversible on the remote
  once it lands (see Rollback).

---

## Step 1: Back up the live state

```bash
ssh -J proxmox denis@192.168.20.10 'cd ~/.hermes && \
  cp config.yaml config.yaml.bak-cron-audience-20260730 && \
  cp cron/jobs.json cron/jobs.json.bak-cron-audience-20260730'
```

This matches the host's existing backup naming convention (e.g.
`config.yaml.bak-error-alerts-20260716`, `config.yaml.bak-reactions-20260722`
— `config.yaml.bak-<slug>-<YYYYMMDD>`). Adjust the date if you run this on
a different day than 2026-07-30.

Verify both backups exist and are non-empty before proceeding:

```bash
ssh -J proxmox denis@192.168.20.10 'ls -la ~/.hermes/config.yaml.bak-cron-audience-20260730 ~/.hermes/cron/jobs.json.bak-cron-audience-20260730'
```

## Step 2: Add the config safety net

`~/.hermes/config.yaml` already has a `cron:` key. **Do not create a new
one** — add `end_user_targets` under the existing key. As of authoring
time the whole key is just:

```yaml
cron:
  wrap_response: false
```

Edit it to:

```yaml
cron:
  wrap_response: false
  # Delivery targets read by a non-technical person. A technical failure is
  # never delivered to these — it goes to gateway.error_alerts.channel.
  end_user_targets:
    - "whatsapp:+77011102626"
```

Do this with a real editor over the SSH session (`vim`/`nano`), not a
one-shot shell command — `config.yaml` is hand-maintained and a scripted
in-place YAML edit risks reformatting unrelated sections. After editing,
validate the file still parses and the new key round-trips as a list:

```bash
ssh -J proxmox denis@192.168.20.10 'cd /home/denis/.hermes/hermes-agent && \
  venv/bin/python -c "import yaml; c = yaml.safe_load(open(\"/home/denis/.hermes/config.yaml\")); print(c[\"cron\"])"'
```

Expected: `{'wrap_response': False, 'end_user_targets': ['whatsapp:+77011102626']}`.
No gateway restart is needed for this alone — `resolve_cron_audience()`
loads config fresh (`load_config()`) at failure time, not at startup —
but Step 4 restarts anyway to pick up the code.

## Step 3: Flag both Amina jobs

```bash
ssh -J proxmox denis@192.168.20.10 'python3 - <<PY
import json
p = "/home/denis/.hermes/cron/jobs.json"
data = json.load(open(p))
jobs = data["jobs"]
changed = []
for job in jobs:
    if job["id"] in ("150d115fe905", "8b751dbfd5d6"):
        job["audience"] = "end_user"
        changed.append(job["id"])
json.dump(data, open(p, "w"), ensure_ascii=False, indent=2)
print("flagged:", changed)
PY'
```

Expected output: `flagged: ['150d115fe905', '8b751dbfd5d6']` (order may
vary). This writes the value directly, bypassing `create_job`'s
`normalize_audience()` — the string above is already the exact
normalized form (`"end_user"`, lowercase), so this is safe. **Do not
substitute any other casing or spelling here** — `update_job()`/direct
edits do not normalize, and a typo degrades silently to `"operator"`
rather than erroring (see `docs/ops/cron-failure-audience.md`).

Verify:

```bash
ssh -J proxmox denis@192.168.20.10 'python3 -c "
import json
d = json.load(open(\"/home/denis/.hermes/cron/jobs.json\"))
for j in d[\"jobs\"]:
    if j[\"id\"] in (\"150d115fe905\", \"8b751dbfd5d6\"):
        print(j[\"id\"], j[\"name\"], j.get(\"audience\"))
"'
```

Expected: both lines show `audience` = `end_user`.

**Do not run the gateway or `hermes cron` CLI while `jobs.json` is being
hand-edited** — both take the same advisory file lock (`_jobs_lock()` in
`cron/jobs.py`) but a raw `python3` script bypassing that API does not
acquire it. In practice this window is sub-second; avoid overlapping it
with a job's own run if one happens to be firing.

## Step 4: Deploy the branch and restart the gateway

The code lives in one shared git repository with multiple worktrees. The
live install (`/home/denis/.hermes/hermes-agent`, branch
`local/customizations`) and the isolated work worktree
(`/home/denis/work/cron-audience`, branch `fix/cron-failure-audience`)
share the same `.git` — merging is a plain local `git merge`, no push/pull
of the feature branch needed.

1. Confirm the live worktree is clean before merging (any uncommitted
   local edits there would get swept into the merge commit):

   ```bash
   ssh -J proxmox denis@192.168.20.10 'cd /home/denis/.hermes/hermes-agent && git status --short'
   ```

   At authoring time this showed two modified files unrelated to this
   work (`custom/fam/fam/cal.py`, `custom/fam/fam/road.py`). **Do not
   assume this is still the state at execution time** — re-check. If
   `git status --short` is non-empty, stash before merging
   (`git stash push -u -m "pre-cron-audience-merge"`) and restore
   afterwards (`git stash pop`) rather than merging over a dirty tree.

2. Merge:

   ```bash
   ssh -J proxmox denis@192.168.20.10 'cd /home/denis/.hermes/hermes-agent && \
     git merge fix/cron-failure-audience -m "merge: cron failure-delivery audience (T1-T5,T7)"'
   ```

   This is not a fast-forward (both branches diverged from
   `d54fc22ce5`), so it produces a merge commit. **This commit fires the
   autopush `post-commit` hook** (the hook is only inert on
   `fix/cron-failure-audience`, not on `local/customizations`) and pushes
   to `origin` automatically. Confirm the push succeeded:

   ```bash
   ssh -J proxmox denis@192.168.20.10 'cd /home/denis/.hermes/hermes-agent && git log --oneline -1 && git status --short --branch'
   ```

   Expected: the branch tracking line shows the local branch even/ahead
   of `origin/local/customizations`, not behind (a behind state means the
   autopush failed and needs `git pull --rebase && git push` by hand —
   see the hook's own error message if it printed one).

3. Restart the gateway:

   ```bash
   ssh -J proxmox denis@192.168.20.10 'systemctl --user restart hermes-gateway && sleep 20 && systemctl --user is-active hermes-gateway'
   ```

   Expected: `active`. If it prints anything else, check
   `journalctl --user -u hermes-gateway -n 100` before continuing — do
   not proceed to live verification against a gateway that failed to
   start.

## Step 5: Live-verify with a scratch job — never against Amina's channel

Use a deterministic, non-agent scratch job (a script that always exits
non-zero) rather than an LLM prompt, so the failure is guaranteed and
reproducible rather than depending on what the model decides to do.

**Deviation from the original plan sketch:** the plan's draft command used
`--schedule "manual"`. That is not a valid schedule string —
`cron/jobs.py`'s `parse_schedule()` recognizes durations (`"30m"`,
`"2h"`), `"every ..."` intervals, 5/6-field cron expressions, and ISO
timestamps only; `"manual"` falls through to `parse_duration` and then
raises `ValueError: Invalid schedule 'manual'. Use: ...`. Use a one-shot
schedule far enough out that it cannot fire on its own before you delete
it — `"24h"` — and trigger it explicitly with `cron run` instead, which
executes immediately regardless of `next_run_at`.

1. Create the scratch script:

   ```bash
   ssh -J proxmox denis@192.168.20.10 'mkdir -p ~/.hermes/scripts && cat > ~/.hermes/scripts/scratch-audience-check.sh <<'"'"'SH'"'"'
   #!/bin/bash
   echo "scratch-audience-check: deliberately failing for live verification" >&2
   exit 1
   SH'
   ```

   (`.sh` scripts run via `bash <path>`, invoked directly rather than
   executed — no `chmod +x` needed, per `_run_job_script_with_claim_heartbeat`
   in `cron/scheduler.py`.)

2. Create the job, `--no-agent` so the script's exit code is the failure
   with no LLM involved, delivering to **your own Telegram**
   (`telegram:79564752` — the same channel as `gateway.error_alerts.channel`,
   so a policy bug surfaces where you're already watching):

   ```bash
   ssh -J proxmox denis@192.168.20.10 'cd /home/denis/.hermes/hermes-agent && venv/bin/python -m hermes_cli.main cron create \
     --name "scratch-audience-check" \
     --schedule "24h" \
     --deliver "telegram:79564752" \
     --script "scratch-audience-check.sh" \
     --no-agent'
   ```

   Note the job id printed (`Created job: <id>`) — you need it for the
   next steps.

3. Flag it `end_user` the same way as Step 3 (substitute the real job id):

   ```bash
   ssh -J proxmox denis@192.168.20.10 'python3 - <<PY
   import json
   p = "/home/denis/.hermes/cron/jobs.json"
   data = json.load(open(p))
   for job in data["jobs"]:
       if job["name"] == "scratch-audience-check":
           job["audience"] = "end_user"
           print("flagged:", job["id"])
   json.dump(data, open(p, "w"), ensure_ascii=False, indent=2)
   PY'
   ```

4. Run it:

   ```bash
   ssh -J proxmox denis@192.168.20.10 'cd /home/denis/.hermes/hermes-agent && venv/bin/python -m hermes_cli.main cron run scratch-audience-check'
   ```

5. Verify: check the Telegram chat (`telegram:79564752`) directly — that
   is the authoritative check. Expected message text (composed by
   `plan_cron_failure_delivery` in `cron/scheduler.py`):

   ```
   ⚠️ Cron 'scratch-audience-check' failed and the failure was WITHHELD from the
   end-user target (telegram:79564752) — nothing was delivered there.
   Reason: Script exited with code 1 stderr: scratch-audience-check: deliberately failing for live verification
   Job id: <job id>. Full details saved in cron output.
   ```

   It must **not** contain any filesystem path (e.g. no
   `/home/denis/...`). It is delivered plain, without the usual
   "Cronjob Response: ..." header/footer (`wrap=False` for operator
   alerts).

   As a secondary check, `~/.hermes/logs/gateway.log` (or
   `journalctl --user -u hermes-gateway`) will have log lines around the
   run, but note: **no code path in `cron/scheduler.py` writes the literal
   string `"WITHHELD"` to the log** — that word only appears inside the
   message text delivered to Telegram (composed in
   `plan_cron_failure_delivery`, never separately logged verbatim). Don't
   rely on `grep WITHHELD gateway.log` as your check; the Telegram message
   itself is the verification. If you want a log-side signal, grep for the
   job id instead:

   ```bash
   ssh -J proxmox denis@192.168.20.10 'journalctl --user -u hermes-gateway --since "10 min ago" | grep -i "scratch-audience-check\|cron operator alert"'
   ```

6. Confirm no delivery reached the job's own `deliver` target — trivially
   true here since it and the operator alert are the same chat
   (`telegram:79564752`), so there is only the one message. (This is
   exactly why Amina's own channel must never be used for this check: if
   `deliver` and the alert channel were different, a policy bug would
   show up as a *second*, wrongly-delivered message on the job's own
   target — you'd want to see that happen in your own chat, not hers.)

7. Clean up — remove the scratch job and script:

   ```bash
   ssh -J proxmox denis@192.168.20.10 'cd /home/denis/.hermes/hermes-agent && venv/bin/python -m hermes_cli.main cron remove scratch-audience-check'
   ssh -J proxmox denis@192.168.20.10 'rm -f ~/.hermes/scripts/scratch-audience-check.sh'
   ```

   Verify removal:

   ```bash
   ssh -J proxmox denis@192.168.20.10 'cd /home/denis/.hermes/hermes-agent && venv/bin/python -m hermes_cli.main cron list --all | grep -i scratch || echo "gone"'
   ```

## Rollback

If anything in Steps 2-5 needs to be undone:

**Config** — restore the pre-change backup:

```bash
ssh -J proxmox denis@192.168.20.10 'cp ~/.hermes/config.yaml.bak-cron-audience-20260730 ~/.hermes/config.yaml'
```

**Job flags** — restore the pre-change backup (this also reverts any
partial scratch-job artifacts left in `jobs.json` if cleanup in Step 5.7
was skipped):

```bash
ssh -J proxmox denis@192.168.20.10 'cp ~/.hermes/cron/jobs.json.bak-cron-audience-20260730 ~/.hermes/cron/jobs.json'
```

Or, narrower — just drop the `audience` key from the two Amina jobs
without touching anything else that may have changed in `jobs.json`
since (e.g. `last_run_at` from real runs):

```bash
ssh -J proxmox denis@192.168.20.10 'python3 - <<PY
import json
p = "/home/denis/.hermes/cron/jobs.json"
data = json.load(open(p))
for job in data["jobs"]:
    if job["id"] in ("150d115fe905", "8b751dbfd5d6"):
        job.pop("audience", None)
json.dump(data, open(p, "w"), ensure_ascii=False, indent=2)
PY'
```

**Code** — the merge commit from Step 4 already pushed to `origin` via
autopush, so a local revert does not un-publish it; treat this as a
forward-only revert, not an erasure:

```bash
ssh -J proxmox denis@192.168.20.10 'cd /home/denis/.hermes/hermes-agent && git revert --no-edit -m 1 <merge-commit-sha>'
```

(`<merge-commit-sha>` is the commit printed by `git log --oneline -1`
right after the Step 4 merge — record it at merge time.) This commit will
itself autopush. After reverting, restart the gateway again:

```bash
ssh -J proxmox denis@192.168.20.10 'systemctl --user restart hermes-gateway && sleep 20 && systemctl --user is-active hermes-gateway'
```

## Step 6: Confirm the real job recovers

The evening forecast (`150d115fe905`) next runs 2026-07-30 20:00 Almaty
time. After that run:

```bash
ssh -J proxmox denis@192.168.20.10 'python3 -c "
import json
d = json.load(open(\"/home/denis/.hermes/cron/jobs.json\"))
j = [x for x in d[\"jobs\"] if x[\"id\"] == \"150d115fe905\"][0]
print(j[\"last_status\"], j[\"last_run_at\"], j[\"last_error\"])
"'
```

Expected: `ok`, a timestamp on 2026-07-30, and `None` for `last_error` —
and a forecast actually arrived in Amina's WhatsApp (check with her or
via the chat directly), not a warning or silence. If `last_status` is
anything other than `ok`, or `last_error` is not `None`, stop and
investigate — do not treat "gateway restarted successfully" (Step 4) as
sufficient evidence the feature works end-to-end; this is the real,
unfaked confirmation.

If the job runs and fails for an unrelated reason (e.g. a weather API
outage) with `audience: end_user` now set, the correct observed behavior
is: **nothing** delivered to Amina's WhatsApp, and a withheld-failure
alert at `telegram:79564752` instead. That is success for this feature
even though the underlying job failed — the two are different questions.

---

## What was intentionally left out of this runbook

- `docs/ops/cron-failure-audience.md` should already be committed on
  `fix/cron-failure-audience` before you start this runbook (Task 6a).
  This runbook does not create or commit that file.
- This runbook does not touch `hermes_cli/subcommands/cron.py` to add an
  `--audience` CLI flag — that's out of scope for this plan (see the
  operator doc's "no CLI flag" section). If that ever gets added, this
  runbook's Step 3/Step 5.3 hand-edits should be replaced with the real
  flag.
