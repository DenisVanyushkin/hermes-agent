# Amina assistant — restore from backup (runbook)

Backups: `~/.hermes/private/amina/backups/{assistant,state}-YYYYMMDD.db`
(nightly, rotation 7, via `fam tick maintenance` / `fam-maintenance.timer`).

## Rehearse (non-destructive, any time)
    ~/.hermes/hermes-agent/custom/fam/scripts/restore-rehearsal.sh
Expect a `PASS:` line. This is the repeatable Phase-6 DoD check.

## Real recovery (assistant.db corrupt/lost)
1. Stop writers so nothing races the swap:
       systemctl --user stop fam-reminders.timer fam-digest.timer \
                              fam-meds-gen.timer fam-maintenance.timer
2. Pick the newest good backup and verify it first:
       ls -1t ~/.hermes/private/amina/backups/assistant-*.db | head
       custom/fam/scripts/restore-rehearsal.sh   # confirms integrity+schema
3. Swap it in (keep the bad one aside for forensics):
       mv ~/.hermes/private/amina/assistant.db ~/.hermes/private/amina/assistant.db.bad
       cp <chosen-backup> ~/.hermes/private/amina/assistant.db
4. Sanity check through the app:
       custom/fam/bin/fam people list
       custom/fam/bin/fam med list --pending
5. Restart writers:
       systemctl --user start fam-reminders.timer fam-digest.timer \
                              fam-meds-gen.timer fam-maintenance.timer

state.db (hermes dialogue history) restores the same way — stop the gateway,
copy `state-YYYYMMDD.db` over `~/.hermes/state.db`, restart. Losing it drops
recent conversation context, not the source-of-truth family data.
