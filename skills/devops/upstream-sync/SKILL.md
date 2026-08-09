---
name: upstream-sync
description: Safely update local/customizations from upstream NousResearch/hermes-agent - merge conflict-free updates automatically, or negotiate per-feature conflict resolution with the operator over Slack.
version: 0.4.0
metadata:
  hermes:
    tags: [devops, git, maintenance]
---

# Upstream Sync

You maintain the fork of hermes-agent (branch `local/customizations`, personal
remote `origin` = DenisVanyushkin/hermes-agent, upstream =
NousResearch/hermes-agent). Your job: bring in upstream updates without losing
local features, keeping the operator informed in plain language.

## Where things are (your terminal runs inside the sandbox)

- Live repo (host bind mount, read-write): `/workspace/live-hermes` — run all
  git commands here. Do NOT use `/home/hermes/.hermes/hermes-agent` or
  `/root/.hermes/hermes-agent`; inside the sandbox those are stale copies.
  If git reports "dubious ownership", run
  `git config --global --add safe.directory /workspace/live-hermes` once.
- Shared state dir: `/root/.hermes/state/upstream-sync/` (persisted on the
  host; the host scripts read the same files):
  - `last-synced.json` — written by the host smoketest on success; never write it yourself
  - `pending.json` — your saved conflict-decision state (schema `upstream-sync-pending/v1`)
  - `decision-memory.json` — remembered operator decisions (schema
    `upstream-sync-decisions/v1`), consulted to auto-apply exact-repeat
    conflicts. Managed only via `scripts/upstream_sync_decisions.py`; never
    hand-edit.
  - `finalize-request.json` / `finalize-result.json` — handshake with the host finalizer (below)
- The preflight report arrives as DATA in the cron prompt (markdown + a fenced
  `upstream-sync-preflight/v1` JSON block).

## The host finalizer

You cannot restart the gateway or run host smoke tests from the sandbox. A
systemd watcher on the host executes those steps when you write
`/root/.hermes/state/upstream-sync/finalize-request.json`:

```json
{"action": "sync" | "finalize" | "rollback",
 "upstream_sha": "<upstream head sha>",
 "backup_ref": "<backup branch name or empty>"}
```

- `sync` — host creates a backup ref, runs the full sync script
  (merge + push + gateway restart), then the smoketest; rolls back on failure.
  `rebase` is accepted as a legacy alias for this action.
- `finalize` — you already merged the repo yourself; host syncs runtime
  scripts, pushes, restarts the gateway, smoketests; rolls back to
  `backup_ref` on failure.
- `rollback` — explicit rollback to `backup_ref`.

After writing the request, poll `finalize-result.json` (check its
`finished_at` is newer than your request) every 15 seconds for up to 10
minutes. The result has `status` (`ok`/`failed`) and a `detail` log tail.
Always relay the status and key detail lines in your report. If no result
appears within 10 minutes, report that the finalizer did not respond and stop.

## Invariants (never violate)

1. Any repo mutation needs a backup ref first. For the `sync` action the host
   creates it; before your own manual merge (Mode B) create it yourself:
   `git -C /workspace/live-hermes branch backup/pre-upstream-sync-$(date +%Y%m%d-%H%M%S) HEAD`.
   Mention the backup ref in every report.
2. If `pending_decision_present` is true in the preflight JSON and you were
   started by cron: do not begin a new sync. If `pending.json` is younger than
   7 days, post a reminder that a decision is still awaited; if older, post the
   conflict report again. The reminder MUST be self-contained — the operator
   should never have to dig up the original report to decide. Read
   `pending.json` and include, per awaiting feature: its id, `files`, the
   `operator_prompt` (or a one-line summary of `local_subjects` if absent), and
   the exact reply format, e.g.:

   | # | Files | Local vs upstream |
   |---|-------|-------------------|
   | F1 | agent/conversation_loop.py | approval preflight vs api_content sidecar |

   Reply per feature in this thread: `F1: merge-both` / `keep-local` /
   `take-upstream`. Features already decided from memory are listed separately
   as auto-applied, not asked about.
3. If `worktree_dirty` lists files that are not routine runtime artifacts,
   note them in the report; the host sync script auto-stashes, so this is
   informational, not blocking.
4. Never `git push` from the sandbox. There is no force-push anywhere in this
   workflow any more: a merge does not rewrite history, so a rejected push
   means another host added commits — the host script folds them in with a
   merge and pushes again. A force-push here would mean something is wrong.
5. If a finalize result comes back `failed`, the host has already rolled back;
   lead your report with the failure, the rollback, and the backup ref.

## Mode detection

- **Mode A (cron run):** the prompt DATA contains a preflight report. The
  host script decides for itself whether the update is conflict-free — it runs
  `git merge-tree` before touching anything — so the `risk` field is advisory
  context for your report, not the decision.
- **Mode B (operator reply):** you are in a Slack thread whose earlier message
  is an upstream-sync conflict report, and the operator has replied with
  decisions. Read `/root/.hermes/state/upstream-sync/pending.json` and execute
  the decisions.

## What the host script does on its own

Before any mutation the script runs `git merge-tree` against upstream. Three
outcomes, and only the first one changes anything:

- **conflict-free** — it merges in a throwaway worktree, runs the fork's own
  tests there before and after the merge, and lands the result on the live
  branch only if no new test failures appeared. Then push, gateway restart,
  smoketest.
- **conflicts** — nothing is touched at all. It prints the conflicting paths
  with hunk counts and exits successfully, leaving the decision to you and the
  operator through the conflict path below.
- **new test failures** — nothing is landed. It prints which tests the merge
  broke.

So a clean update needs no decision from you; your job starts where the script
stops.

## Mode A — risk: clean

1. Write `finalize-request.json` with `action: "sync"` and the
   `upstream_head` SHA from the preflight JSON.
2. Poll for the result. On `ok`, respond with a human summary: how many
   commits came in, what areas they touch (use `upstream_commit_count_by_area`
   and notable subjects from `upstream_commits_sample`), the backup ref, and
   "smoketest passed". On `failed`, report the failure and rollback per
   invariant 5.

## Mode A — risk: overlap_only

For each entry in `overlap_files`, compare both sides in the live repo:
`git -C /workspace/live-hermes diff <merge_base>..HEAD -- <file>` vs
`git -C /workspace/live-hermes diff <merge_base>..<upstream_head> -- <file>`.
If every overlap is clearly independent (different functions/sections),
proceed as the clean path but list the overlapping files and your reasoning in
the summary. If any overlap looks like upstream rewrote logic that local
commits also change, treat the whole run as the conflict path.

## Mode A — risk: conflicts

Do NOT modify the repo yet. First consult decision memory, then branch.

1. Partition the conflicts against remembered decisions:
   `python3 /workspace/live-hermes/scripts/upstream_sync_decisions.py partition \
      --preflight <(printf '%s' "$PREFLIGHT_JSON") \
      --memory /root/.hermes/state/upstream-sync/decision-memory.json`
   (If process substitution is awkward, write the preflight JSON to a scratch
   file UNDER `/tmp` — e.g. `/tmp/upstream-sync-preflight.json` — and likewise
   keep ANY helper script or intermediate output you create under `/tmp`,
   never inside the repo tree `/workspace/live-hermes`: a stray file in the
   repo dirties the git baseline and blocks later pipelines.) The output has `remembered` (auto-decided from memory) and `new`
   (must ask the operator; includes anything on a security/auth path).

   > **pending.json feature schema (required for memory to work).** Every feature
   > you write to `pending.json` MUST include `files` and the local-commit
   > subjects, in one of two shapes: `"local_commits": [{"subject": "..."}]`
   > (copied verbatim from the matching preflight `conflicts[]` entries) OR
   > `"local_subjects": ["..."]` (copied straight from `partition`'s output for a
   > `remembered` feature). `record` derives the memory fingerprint from
   > `files` + these subjects; if BOTH are absent the fingerprint will not match
   > and the remembered decision is silently orphaned. Note: the preflight caps
   > `conflicts` at 60 files; a merge touching more than 60 conflicted files may
   > leave some features partially invisible to the memory system — call this
   > out in the report if the conflict count is near the cap.

2. **If `new` is empty (full auto-apply):**
   a. Create the backup ref (invariant 1).
   b. Write `pending.json` with every feature pre-decided from `remembered`
      (copy each `decision`) and `status: "auto_apply"`.
   c. Run **Mode B steps 2–5 only** (create backup, merge applying the
      remembered decisions, finalize, record) — SKIP Mode B step 1: there is no
      operator reply to match in a full-auto cron run, decisions are already
      known from memory.
   d. On `ok`: record the applied decisions —
      `python3 .../upstream_sync_decisions.py record --pending <pending.json> \
        --memory .../decision-memory.json --now "$(date -u +%Y-%m-%dT%H:%M:%SZ)"`
      — then delete `pending.json` and post a **post-facto Slack notice**:
      list each auto-resolved feature, its decision, and that it was applied
      automatically from a prior operator decision (cite the memory entry's
      `apply_count`/`last_applied_at` if useful). On `failed`: host rolled back;
      lead with the failure and backup ref (invariant 5); do not touch memory.

3. **If `new` is non-empty (partial):** do NOT auto-apply. Group the `new`
   entries into features for the human report as before. Write `pending.json`
   with `remembered` features already carrying their `decision` plus
   `"source": "memory"`, and `new` features as `awaiting_decision`. In the
   Slack report, ask the operator ONLY about the `new` features, and state that
   the remembered ones will be applied automatically once the new ones are
   decided. End the run; do not poll.

## Mode B — applying operator decisions

1. Read `pending.json`. Match the operator's reply to feature ids. If any
   feature's decision is missing or ambiguous, ask one clarifying question in
   the thread and stop.
2. Create the backup ref (invariant 1).
3. In `/workspace/live-hermes`, run
   `git -c merge.conflictStyle=zdiff3 merge --no-edit origin/main`. One merge
   raises every conflict at once — there is no per-commit replay. Resolve each
   per the decided option for the feature owning that file:
   - `keep-local`: keep the local side. In a merge `--ours` IS the local side
     and `--theirs` is upstream — the opposite of a rebase, where the sides are
     inverted. Verify by inspecting the conflicted file before staging.
   - `take-upstream`: keep the upstream side, same verification.
   - `merge-both`: edit the file to combine both changes; keep local behavior
     and adopt upstream structure; then `git add`.
   `zdiff3` puts the merge base between the two sides, which is what tells
   "both sides added something here" apart from "upstream moved code we had
   modified" — the second case needs the local change ported into the new
   structure, not pasted back.
   Then `git commit --no-edit`. If the merge becomes unmanageable,
   `git merge --abort` and report honestly.

   Do NOT enable rerere for this merge. The repository carries recorded
   resolutions from the rebase era, where "ours" and "theirs" are inverted
   relative to a merge; applying them here resolves conflicts backwards and
   silently.
4. Write `finalize-request.json` with `action: "finalize"`, the upstream head
   SHA, and your backup ref. Poll for the result.
5. On `ok`: first write the operator's decision into each feature of
   `pending.json` (set `"decision"` to the chosen option; leave `remembered`
   features' decisions as-is), then record them:
   `python3 /workspace/live-hermes/scripts/upstream_sync_decisions.py record \
      --pending /root/.hermes/state/upstream-sync/pending.json \
      --memory /root/.hermes/state/upstream-sync/decision-memory.json \
      --now "$(date -u +%Y-%m-%dT%H:%M:%SZ)"`.
   Then delete `pending.json` and summarize per-feature what was done, marking
   which features were auto-applied from memory vs freshly decided. On
   `failed`: the host rolled back; keep both `pending.json` and memory
   unchanged and report.

## Reporting style

Human first: no walls of SHAs, no raw diffs. Every report states: backup ref,
commit counts, per-area breakdown, conflicts and how each was (or will be)
resolved, smoketest result. Write for an operator reading on a phone.
