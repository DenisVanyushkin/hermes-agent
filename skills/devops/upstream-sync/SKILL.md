---
name: upstream-sync
description: Safely update local/customizations from upstream NousResearch/hermes-agent - triage the preflight report, auto-rebase clean updates, or negotiate per-feature conflict resolution with the operator over Slack.
version: 0.1.0
metadata:
  hermes:
    tags: [devops, git, maintenance]
---

# Upstream Sync

You maintain the fork of hermes-agent at `/home/hermes/.hermes/hermes-agent`
(branch `local/customizations`, personal remote `origin` =
DenisVanyushkin/hermes-agent, upstream = NousResearch/hermes-agent). Your job:
bring in upstream updates without losing local features, keeping the operator
informed in plain language.

## Paths and helper scripts

- Repo: `/home/hermes/.hermes/hermes-agent`
- Preflight report: arrives as DATA in the cron prompt (markdown + a fenced
  `upstream-sync-preflight/v1` JSON block)
- State dir: `/home/hermes/.hermes/state/upstream-sync/`
  - `last-synced.json` — written by the smoketest on success; do not write it yourself
  - `pending.json` — your saved conflict-decision state (schema `upstream-sync-pending/v1`)
- Scripts (run via terminal, always with `bash`):
  - `/home/hermes/.hermes/scripts/rebase-local-customizations.sh` — full rebase + push + gateway restart
  - `/home/hermes/.hermes/scripts/upstream-sync-smoketest.sh <upstream_sha>` — import check, gateway restart, health check; records `last-synced.json` on pass
  - `/home/hermes/.hermes/scripts/upstream-sync-rollback.sh <backup-ref>` — hard rollback + gateway restart

## Invariants (never violate)

1. Before ANY repo mutation create a backup: `git branch backup/pre-upstream-sync-$(date +%Y%m%d-%H%M%S) HEAD` and a tag with the same name. Mention the backup ref in every report.
2. If `pending_decision_present` is true in the preflight JSON and you were started by cron: do not begin a new sync. If `pending.json` is younger than 7 days, post a short reminder that a decision is still awaited; if older, post the conflict report again.
3. If `worktree_dirty` lists files that are not routine runtime artifacts, note them in the report; the rebase script auto-stashes, so this is informational, not blocking.
4. Never force-push; the rebase script's `--force-with-lease` push is the only push.
5. If the smoketest fails after a merge: immediately run the rollback script with the backup ref, verify it reports `ROLLBACK DONE`, and lead your report with the failure and the rollback.

## Mode detection

- **Mode A (cron run):** the prompt DATA contains a preflight report. Act per its `risk` field.
- **Mode B (operator reply):** you are in a Slack thread whose earlier message is an upstream-sync conflict report, and the operator has replied with decisions. Read `/home/hermes/.hermes/state/upstream-sync/pending.json` and execute the decisions.

## Mode A — risk: clean

1. Create the backup branch + tag.
2. Run `bash /home/hermes/.hermes/scripts/rebase-local-customizations.sh`.
3. Run `bash /home/hermes/.hermes/scripts/upstream-sync-smoketest.sh <upstream_head from JSON>`.
4. On pass, respond with a human summary: how many commits came in, what areas they touch (use `upstream_commit_count_by_area` and notable subjects from `upstream_commits_sample`), the backup ref, and "smoketest passed". On smoketest failure: rollback per invariant 5.

## Mode A — risk: overlap_only

For each entry in `overlap_files`, compare both sides:
`git diff <merge_base>..HEAD -- <file>` vs `git diff <merge_base>..<upstream_head> -- <file>`.
If every overlap is clearly independent (different functions/sections), proceed as the clean path but list the overlapping files and your reasoning in the summary. If any overlap looks like upstream rewrote logic that local commits also change, treat the whole run as the conflict path.

## Mode A — risk: conflicts

Do NOT modify the repo. Instead:

1. Group the `conflicts` entries into FEATURES: cluster files that share the same local commits (the `local_commits` lists). Name each feature from the commit subjects, in plain language (e.g. "controlled execution reports", not SHAs).
2. Write `/home/hermes/.hermes/state/upstream-sync/pending.json`:
   ```json
   {
     "schema": "upstream-sync-pending/v1",
     "created_at": "<ISO8601>",
     "upstream_head": "<sha>",
     "merge_base": "<sha>",
     "features": [
       {"id": 1, "name": "<feature name>", "files": ["..."],
        "local_commits": [{"sha": "...", "subject": "..."}],
        "upstream_summary": "<what upstream did to these files>",
        "options": ["keep-local", "take-upstream", "merge-both"]}
     ],
     "status": "awaiting_decision"
   }
   ```
3. Respond with the conflict report (this is what lands in Slack):
   - What upstream changed overall, in human terms, grouped by area.
   - Per-feature sections, numbered: feature name, which local functionality is at stake, what upstream did to the same files, your recommended option and why.
   - Ask the operator to reply in this thread with one decision per feature, e.g. `1: keep local, 2: take upstream, 3: merge both`.
   - End with this exact footer line so the follow-up session knows what to do:
     `_When you reply here, I will load the upstream-sync skill, read pending.json and apply your decisions._`
4. End the run. Do not wait or poll.

## Mode B — applying operator decisions

1. Read `pending.json`. Match the operator's reply to feature ids. If any feature's decision is missing or ambiguous, ask one clarifying question in the thread and stop.
2. Create the backup branch + tag.
3. Run `git -C <repo> rebase origin/main` (upstream tracking ref). At each conflict stop, resolve per the decided option for the feature owning that file:
   - `keep-local`: `git checkout --ours -- <file>` (during rebase, `--ours`/`--theirs` are inverted relative to intuition — verify with `git status` and file content which side is the local feature before staging).
   - `take-upstream`: take the upstream side the same way, verifying content.
   - `merge-both`: edit the file to combine both changes; keep local behavior and adopt upstream structure; then `git add`.
   Then `git rebase --continue`. Repeat until done. If the rebase becomes unmanageable, `git rebase --abort` and report honestly.
4. After the rebase completes, run `bash /home/hermes/.hermes/scripts/rebase-local-customizations.sh` — with the branch already rebased it is a no-op rebase, but it syncs runtime scripts, pushes to the fork and restarts the gateway.
5. Run the smoketest with the upstream head SHA. On pass: delete `pending.json`, summarize per-feature what was done. On fail: rollback per invariant 5 and keep `pending.json`.

## Reporting style

Human first: no walls of SHAs, no raw diffs. Every report states: backup ref, commit counts, per-area breakdown, conflicts and how each was (or will be) resolved, smoketest result. Write for an operator reading on a phone.
