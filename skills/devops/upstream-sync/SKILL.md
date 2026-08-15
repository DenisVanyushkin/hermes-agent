---
name: upstream-sync
description: Safely update local/customizations from upstream NousResearch/hermes-agent - merge conflict-free updates automatically, or negotiate per-feature conflict resolution with the operator over Slack.
version: 0.5.0
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

- Live repo (host bind mount, **read-only**): `/workspace/live-hermes` — read
  it freely (`git log`/`diff`/`merge-tree` without `--write-tree`), never write
  to it: no branches, no commits, no `--write-tree`. Do NOT use
  `/home/hermes/.hermes/hermes-agent` or `/root/.hermes/hermes-agent`; inside
  the sandbox those are stale copies. If git reports "dubious ownership", run
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
{"action": "sync" | "apply-merge" | "finalize" | "rollback",
 "upstream_sha": "<upstream head sha>",
 "merge_sha": "<apply-merge only: the merge you built in the clone>",
 "scratch_repo": "<apply-merge only: clone dir name under the state dir>",
 "backup_ref": "<backup branch name or empty>"}
```

- `sync` — host creates a backup ref, runs the full sync script
  (merge + push + gateway restart), then the smoketest; rolls back on failure.
  `rebase` is accepted as a legacy alias for this action.
- `apply-merge` — the conflict path (Mode B). You built a merge in your own
  clone; the host adopts the clone, re-derives trust from the commit's parents
  rather than your word, runs the fork's tests on it, backs up, fast-forwards,
  publishes and smoke-tests, and records the decisions into memory.
  `upstream_sync_apply.py handoff` writes this request for you — do not
  hand-write it.
- `finalize` — you already merged the repo yourself; host syncs runtime
  scripts, pushes, restarts the gateway, smoketests; rolls back to
  `backup_ref` on failure. Superseded by `apply-merge`: it needs a repo you
  could write to, and the live checkout is read-only.
- `rollback` — explicit rollback to `backup_ref`.

After writing the request, poll `finalize-result.json` (check its
`finished_at` is newer than your request). `upstream_sync_apply.py wait` does
this for you, for up to 30 minutes — the apply now runs the fork's test suite
twice, so it takes considerably longer than the old ten-minute ceiling. The
result has `status` (`ok`/`failed`), `failed_stage`, and a `detail` log tail.
Always relay the status and key detail lines in your report. If no result
appears before the wait times out, report that the finalizer did not respond
and stop.

## Invariants (never violate)

1. Any repo mutation needs a backup ref first — and **the host always makes
   it**, for every action including `apply-merge`. You cannot: the live
   checkout is mounted read-only, so `git -C /workspace/live-hermes branch ...`
   fails. Never write to `/workspace/live-hermes` at all; read it freely.
   The backup ref comes back in the finalize result — mention it in every
   report.
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
   a. Write `pending.json` with every feature pre-decided from `remembered`
      (copy each `decision`) and `status: "auto_apply"`. The host will make the
      backup ref when it applies the merge; you cannot (invariant 1).
   b. Run **Mode B steps 2–4 only** (`prepare` → resolve whatever it left →
      `handoff` → `wait`) — SKIP Mode B step 1: there is no operator reply to
      match in a full-auto cron run, the decisions are already known.
   c. On `ok`: the host has already recorded the applied decisions into
      memory — it archives `pending.json` as `pending.json.applied-<timestamp>`
      and runs `upstream_sync_decisions.py record` itself, because your session
      does not survive the gateway restart. Do not record again. Post a
      **post-facto Slack notice**: list each auto-resolved feature, its
      decision, and that it was applied automatically from a prior operator
      decision. On `failed`: the host rolled back or never touched the repo;
      lead with the failure and backup ref (invariant 5).

3. **If `new` is non-empty (partial):** do NOT auto-apply. Group the `new`
   entries into features for the human report as before. Write `pending.json`
   with `remembered` features already carrying their `decision` plus
   `"source": "memory"`, and `new` features as `awaiting_decision`. In the
   Slack report, ask the operator ONLY about the `new` features, and state that
   the remembered ones will be applied automatically once the new ones are
   decided. End the run; do not poll.

## Mode B — applying operator decisions

`/workspace/live-hermes` is mounted **read-only**. You cannot create a backup
ref there, cannot commit, and must not try. You build the merge in your own
writable clone and hand the host a SHA — the host owns every write to the live
repository. The mechanics live in a script; your job is the part that needs
judgement: resolving the `merge-both` hunks it could not close by itself.

1. Read `pending.json`. Match the operator's reply to feature ids and write each
   decision into its feature (`"decision": "merge-both"` / `"keep-local"` /
   `"take-upstream"`, plus `"status": "decided"`, `"source": "operator"`). If any
   feature's decision is missing or ambiguous, ask one clarifying question in
   the thread and stop.

2. Prepare the merge:

   ```
   python3 /workspace/live-hermes/scripts/upstream_sync_apply.py prepare
   ```

   It re-clones the live checkout into
   `/root/.hermes/state/upstream-sync/scratch` (`--shared`, fresh config, no
   rerere), checks that the decisions still cover the conflicts of the **live
   HEAD** against the **gated** upstream point (`upstream_head` from
   `pending.json` — never "whatever upstream is now": upstream always moves
   while a gate waits, and the host only accepts a merge into the gated point),
   merges with zdiff3, applies `keep-local`/`take-upstream` mechanically, closes
   the `merge-both` hunks that are mechanical, and prints JSON:

   - `status: ready` — go on. `needs_manual` lists the files still holding
     conflict markers with a `remaining_hunks` count; `auto_resolved` is done.
   - `status: new_conflicts` — the live branch moved since the gate and a file
     nobody decided about now conflicts. Do NOT resolve it yourself: add it to
     `pending.json` as a new feature (`awaiting_decision`, with its `files` and
     the local commit subjects from `git -C /workspace/live-hermes log
     --format=%s <merge_base>..HEAD -- <file>`), ask the operator about ONLY the
     new files in the thread, and stop.
   - `status: missing_decisions` — go back to step 1.
   - `status: error` — report the reason and stop; nothing was touched.

3. Resolve every file in `needs_manual` inside the clone
   (`/root/.hermes/state/upstream-sync/scratch`), then `git add` it there:
   - `merge-both`: combine both changes; keep local behaviour and adopt upstream
     structure. `zdiff3` shows the merge base between the two sides — "both
     added something here" is a union, while "upstream moved code we had
     modified" needs the local change ported into the new structure, not pasted
     back.
   - Never touch files outside `needs_manual`, and never re-run `prepare` unless
     you mean to start over: it wipes the clone and your resolutions with it.

   If a file is unmanageable, report honestly and stop: nothing has touched the
   live repository, so there is nothing to undo.

4. Hand off and wait:

   ```
   python3 /workspace/live-hermes/scripts/upstream_sync_apply.py handoff
   python3 /workspace/live-hermes/scripts/upstream_sync_apply.py wait
   ```

   `handoff` refuses leftover markers or unmerged paths (`unresolved` — finish
   step 3), refuses if the live branch moved since `prepare` (`live_moved` — run
   `prepare` again and redo step 3), commits the merge, verifies its parents are
   exactly (live HEAD, gated upstream) and writes `finalize-request.json` with
   `action: "apply-merge"`. The host then adopts the clone, runs the fork's own
   tests on the merge and refuses new failures, creates the backup ref itself,
   fast-forwards, syncs the runtime scripts, pushes, restarts, smoke-tests —
   rolling back on failure — and records the decisions into memory. `wait` polls
   for the result and exits 0 on `ok`, 8 on `failed`, 9 on timeout.

5. Report. On `ok`: summarize per feature what was done (closed by the script vs
   resolved by you), the backup ref from the result, the commit counts, and that
   the tests and smoketest passed. Do NOT run `upstream_sync_decisions.py
   record` — the host already did. On `failed`: the host either left the
   repository untouched or rolled it back and kept the decision armed; lead with
   `failed_stage` and the detail tail. On timeout: say the finalizer did not
   answer and where to look (`finalize-detail.log` on the host).

## Reporting style

Human first: no walls of SHAs, no raw diffs. Every report states: backup ref,
commit counts, per-area breakdown, conflicts and how each was (or will be)
resolved, smoketest result. Write for an operator reading on a phone.
