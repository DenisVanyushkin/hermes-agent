# Reminder closure survives regeneration

Incident: on 2026-10-07 Amina's "already driving" reply closed event 130's
pending reminders. A later road estimate changed from 10 to 3 minutes and
`rem.regenerate()` created new leave reminders for the same occurrence.

Schema 17 stores `events.reminder_closed_scope` (`none`, `prepare`, `all`).
`ack_chain(scope="prepare")` persists `prepare` only when it acknowledges
pending prepare rows; an empty prepare acknowledgement retains the existing
resolution protocol's `prepare_phase_over` behavior. `ack_chain(scope="all")`
and explicit `cancel_chain()` persist `all`, including when no rows are pending.
An all closure is never downgraded by a later prepare acknowledgement.
Closure updates and reminder transitions share the caller's transaction.

`regenerate()` respects this state for every trigger (road, calendar edits,
rule changes). The calendar event remains active. Closing one occurrence
does not close its series or subsequent occurrences. Calendar edits of the
same occurrence retain its closure; a new occurrence starts with `none`.

The migration reconstructs closure from retained `rem.ack` and
`rem.cancel_chain` audit records and closes pending rows that the old code
already recreated. No-op legacy prepare acknowledgements are ignored.
Backfill runs only when the column is added; malformed records are skipped.
Evidence predating the current row's creation is skipped. A subsequent
`cal.add` audit record resets earlier closure for the same numeric ID, so
SQLite ID reuse within one second cannot close an unrelated new occurrence.
Without a retained creation record, same-second closure is ambiguous and is
left unset; later closure remains recoverable after creation-audit retention.
Acknowledgements older than audit retention cannot be reconstructed from
missing evidence. New closure state survives audit pruning and process restarts.

## Verification

Run the FAM suite with the canonical hermetic runner from the repository root:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh custom/fam/tests/ \
  -o pythonpath=custom/fam -q -j 2
```

The incident regression exercises the real resolver, SQLite, road tick,
regeneration and reminder tick on a temporary database. The classifier's
response, route estimate and outbound transport are local test substitutes;
tests make no provider calls or user deliveries.

## Deployment and recovery

1. Verify a SQLite backup and rehearse schema migration on a private copy.
2. Pause the active FAM timers, allow existing FAM jobs to finish, and stop
   the gateway before migrating data or changing the live checkout.
3. Fast-forward `local/customizations` to the reviewed commit and run
   `custom/fam/bin/fam init` to install schema 17 and restore retained closure.
4. Rebuild the pending-acks projection, start the gateway, and restore the
   timers that were active before deployment.
5. Check the gateway, timer state, database integrity and a normal reminder
   tick. Confirm event 130 has scope `all` and no pending reminders; future
   occurrences retain their independent reminders.

For recovery, stop the same writers, restore the verified pre-deployment
database and the recorded pre-deployment commit together, then restart and
verify services. Keep the original backup unchanged during rehearsal.

## Deployment acceptance — 2026-10-07

- Installed code: `6ad215710e63678878a49ba347cf560b5755f176` on
  `hermes-home`, branch `local/customizations`; base
  `d5087ad7e23afc1566990db089160bbd5f845cac`.
- Independent review of final patch SHA256
  `08dc43cc22b5152bf50a52e657ff242a94b0cb42b237bb30bf5acbf150f7543a`
  found no remaining blockers. Its earlier ID-reuse finding was reproduced
  RED, fixed, and independently rechecked.
- Full hermetic FAM run (`-j 4`, retries disabled): 140 files,
  2,135 passed, 3 failed, 1 skipped, exit 1. All three failures reproduced
  on the unmodified base and came from expired August-2026 test fixtures.
  After correcting only those fixture clocks/dates, their complete files
  passed separately: `test_plans_attach_detour.py` 10 passed, exit 0;
  `test_seed_apply.py` 22 passed, exit 0. Final coverage across the 140
  files is 2,138 passed, 1 skipped; production code stayed unchanged
  during these fixture corrections. The 19 new regression cases passed.
- Isolated Ruff checks for the new tests and error checks for changed
  legacy modules, formatting of the new test file, and `git diff --check`
  passed.
- Backup immediately before deployment:
  `/home/denis/.hermes/backups/deploy-reminder-closure-20261007T051551Z/assistant.db`,
  42,770,432 bytes, SHA256
  `c4d2049db5795f3fb4947c4b9a01afb7d963e8e459380b9d578c93d0017ee36d`.
  SQLite integrity was verified; schema migration and idempotence had
  already been rehearsed on a separate private snapshot.
- Live schema 17: integrity and foreign-key checks passed. Event 130
  remains active with scope `all` and zero pending reminders; 125 pending
  future reminders on independently open occurrences remain.
- Deployment completed at 10:16 Asia/Almaty. Gateway PID changed from
  `243924` to `483824`; all 9 previously active FAM timers were restored.
  Fresh startup recorded WhatsApp and Telegram connected at 10:16:23.
- Normal reminder tick at 10:17 completed successfully with
  `due=0 sent=0 error=0`. This verifies deployment and the installed
  database state; the deterministic resolver/road regression is a
  composition test, not a new live human WhatsApp conversation.
- `AGENTS.md` now specifies the test-first, verification, independent
  review and authorized deployment workflow directly; the missing skill
  is no longer a required dependency.
