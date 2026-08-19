# Upstream-Sync Mismerge Postmortem

Date: 2026-08-19
Landed: `869749e5fc` (merge of upstream `2507bc649f`), published as `10008c7f5c`
Backup ref: `backup/pre-upstream-sync-20260819-122013`

## Symptom

The scheduled sync cron reported a bare permission error and stopped:

```text
:warning: Cron 'hermes-rebase-local-customizations' failed: Script exited with code 1
stderr: [Errno 13] Permission denied: '…'
```

Behind that one message sat four independent defects, each of which hid the next.
The sync had not run since 2026-08-15; upstream was 862 commits ahead.

## Timeline (server time, CEST)

| Time | Event |
|---|---|
| 08-18 06:26 | Gateway restart re-provisions the sandbox mirror; a `chmod` there voids the ACL the host user needs |
| 08-18 18:35 | Cron dies at its first `mkdir`, 0.6 s in, before any work |
| 08-19 07:40 | ACL restored; state moved out of the sandbox mirror; six defaults repointed |
| 08-19 09:04 | Sync runs, posts the conflict report, F7/F9 go to the operator |
| 08-19 09:30 | Operator answers `merge-both`; decisions recorded — and nothing runs |
| 08-19 10:58 | Path unit repointed; the finalizer picks up the 88-minute-old request |
| 08-19 11:10 | Test gate refuses: post-merge does not even import (69 collection errors) |
| 08-19 11:54 | Second attempt: gate compares cleanly, refuses on 3 real regressions |
| 08-19 12:22 | Third attempt lands; smoketest passes; gateway restarted |

## Root causes

### 1. ACL mask silently voided the traverse grant

Live state sat in `$HERMES_HOME/sandboxes/docker/default/home/.hermes/state/upstream-sync`
— a leftover from when the sync ran inside a sandbox. That tree is provisioned as
root. A `chmod` on the mirrored `.hermes` recalculated the POSIX ACL mask to `---`,
which nullifies the named entry `user:hermes:--x` while still displaying it:

```text
user:hermes:--x   #effective:---
mask::---
```

`ls -l` shows only `drwx------` and a `+`. The grant looks present in `getfacl` and
is dead. The host-owned cron could not `stat` its own state dir.

### 2. The finalizer's trigger was never repointed

Finalization is driven by a **systemd path unit**, not by cron:

```ini
PathExists=…/sandboxes/docker/default/home/.hermes/state/upstream-sync/finalize-request.json
```

After the state moved, the operator's decisions were recorded and
`finalize-request.json` was written — and no apply started, because the watcher was
staring at the retired directory. The unit template lives in `deploy/systemd/`,
outside `scripts/`, so a path sweep restricted to scripts missed it.

### 3. Mechanical `merge-both` broke the syntax of `config_defaults.py`

Git **did** report this file as conflicted. The break was introduced by the
mechanical resolver in `prepare`, which closes "merge-both hunks that are
mechanical" by concatenating the two sides.

The conflict block ended like this:

```python
<<<<<<< HEAD
    "pipelines": {
        ...
        "execution": { ... },      # ours ends here
=======
    "session": {
        "terminal_continue": True, # theirs ends here
>>>>>>> 2507bc649f
    },                             # ONE shared closing brace, outside the block
```

That final `},` is shared context. In *ours* it closed `pipelines`; in *theirs* it
closed `session`. Concatenating both bodies leaves a single closer for two dicts:
the brace was never lost, it never existed twice. Result — `DEFAULT_CONFIG` unclosed,
the whole module unparseable, 69 collection errors, and a gate that could not compare
the two test runs at all.

### 4. The model dropped 209 lines from an asymmetric conflict block

`gateway/run.py` produced a conflict block of **215 lines: 211 ours against 1 theirs**.

The genuine disagreement was a single line — the signature of
`_start_gateway_housekeeping`, where the fork added `runner`/`stale_guard_cfg` and
upstream added `cron_provider`. But the local stale-guard block (a constant and seven
functions) sat immediately above that function with no anchor of its own, so git
attached all 209 lines to the *ours* side.

The per-hunk resolver asked the model to merge "211 lines against 1 line". It returned
a combined signature and dropped the payload — a defensible reading of what it was
shown. The call sites survived, so the merge kept calling `_stale_guard_arm()` and
`_stale_guard_tick()` that no longer existed: a `NameError` on every gateway start,
not merely a red test. The same exception is why a third test — about
`preload_turn_path_modules`, a function that was never touched — also failed.

**Common denominator for 3 and 4:** the conflict block does not coincide with a
semantic unit. In case 3 the unit (a dict literal) is larger than the block and the
decisive token lies outside it. In case 4 the unit (a signature) is smaller than the
block and unrelated code got dragged in. Three-way merge works on lines and knows
nothing about brace balance or definition boundaries, so `merge-both` there means
"concatenate two pieces of text", not "keep both meanings".

## What worked

- **Nothing reached the live checkout.** Both failed attempts ended `repo untouched`,
  the backup ref was created, no rollback was needed. Build-in-scratch → test →
  fast-forward behaved exactly as designed.
- The test gate refused to land a merge it could not evaluate, rather than
  interpreting 69 collection errors as ordinary regressions.

## What did not

- **The test gate was the only check, and it stands last.** Both breakages are caught
  deterministically in seconds — `ast.parse` for case 3, a top-level definition diff
  for case 4 — but nothing ran either. The price was two ~20-minute gate cycles.
- **Its diagnosis was thin.** First run: "could not compare the two test runs" plus a
  log tail. Second run: one symbol named out of seven actually lost.
- **Gate triage proposes patching tests.** Here that would have been destructive: an
  `apply fix` would have adjusted the tests to a merge missing 209 lines, deleting a
  local feature from the fork silently and permanently. `apply fix` must never be the
  default answer to a red gate.

## Audit performed

Because the resolver dropped 209 lines without saying so, all six merged files were
compared by AST — the set of top-level definitions on each side against the result.
Three symbols showed as missing; two were legitimate and only reading the code could
tell them apart:

| Symbol | Verdict |
|---|---|
| `_stale_guard_*` (7 names, `gateway/run.py`) | **real loss** — restored |
| `submit_pending` (`tools/approval.py`) | deliberate local deletion, documented in a surviving comment |
| `_ALLOWLIST_SHELL_OPERATOR_RE` (`tools/approval.py`) | retired with the local implementation upstream rewrote as quote-aware |

This is why automatic restoration of anything "missing" would be wrong: it would have
resurrected a deliberately deleted function and a superseded regex.

## Fixes landed

| Commit | Fix |
|---|---|
| `da71bd3c48` | State moved out of the sandbox mirror; 6 defaults repointed; gate test forbids referencing the mirror |
| `2cd196d1c4` | Finalize path unit repointed; gate test extended to `deploy/systemd/` |
| `3edda85ed1` | `scripts/lib/git-retry.sh` — the upstream fetch survives GitHub's per-IP 429s |
| `d038719e10` | `sync-runtime-scripts.sh` carries `scripts/lib/` into the runtime copy |

The merge itself was repaired by hand: one brace in `config_defaults.py`, and the
209-line stale-guard block restored between anchors that match verbatim on both sides.

## Follow-ups

Tracked in `docs/plans/2026-08-19-merge-invariant-gates.md`:

1. Invariant checks inside `commit` — fail closed, with a precise report.
2. Mechanical `merge-both` must validate its own output before claiming success.
3. Asymmetric conflict blocks go to the operator instead of the model.

Not in that plan, worth deciding separately:

- **Sync more often.** The interval is 4320 minutes; 862 upstream commits accumulated.
  Wider windows mean larger blocks and a higher chance of unrelated code being dragged
  into one. Right after this merge upstream was 33 commits ahead with **zero**
  conflicts.
- **Gate triage's default.** It can only edit tests, so it proposes editing tests
  whatever the cause, and it does so with the same confidence when the cause is lost
  production code.
- **A red baseline.** The fork's own suite is 77 failed / 4959 passed before any
  merge. The gate compares against that, so a merge touching any of those 77 can
  confuse the comparison.
