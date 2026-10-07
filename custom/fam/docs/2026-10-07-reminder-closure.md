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
