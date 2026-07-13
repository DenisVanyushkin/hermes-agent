# Amina assistant — restore from backup (runbook)

Backups: `~/.hermes/private/amina/backups/{assistant,state}-YYYYMMDD.db`
(nightly, rotation 7, via `fam tick maintenance` / `fam-maintenance.timer`).

Note: the rehearsal script only exercises `assistant.db` restores. A
`state.db` restore (see below) is NOT covered by `restore-rehearsal.sh`
and remains unrehearsed -- verify it manually if you rely on it.

## Rehearse (non-destructive, any time)
    ~/.hermes/hermes-agent/custom/fam/scripts/restore-rehearsal.sh
Expect a `PASS:` line. This is the repeatable Phase-6 DoD check.
Pass an explicit backup file as $1 to verify a SPECIFIC (e.g. older)
backup instead of the latest:
    ~/.hermes/hermes-agent/custom/fam/scripts/restore-rehearsal.sh /path/to/assistant-YYYYMMDD.db

## Real recovery (assistant.db corrupt/lost)
1. Stop writers so nothing races the swap:
       systemctl --user stop fam-reminders.timer fam-digest.timer \
                              fam-meds-gen.timer fam-maintenance.timer
2. Pick the CHOSEN backup (newest, or an older one if the newest is
   suspect) and verify exactly that file before trusting it:
       CHOSEN=$(ls -1t ~/.hermes/private/amina/backups/assistant-*.db | head -1)   # or an older one
       custom/fam/scripts/restore-rehearsal.sh "$CHOSEN"    # verifies exactly this file
3. Swap it in (keep the bad one aside for forensics):
       mv ~/.hermes/private/amina/assistant.db ~/.hermes/private/amina/assistant.db.bad
       cp "$CHOSEN" ~/.hermes/private/amina/assistant.db
4. Sanity check through the app:
       custom/fam/bin/fam people list
       custom/fam/bin/fam med list --pending
5. Restart writers:
       systemctl --user start fam-reminders.timer fam-digest.timer \
                              fam-meds-gen.timer fam-maintenance.timer

state.db (hermes dialogue history) restores the same way — stop the gateway,
copy `state-YYYYMMDD.db` over `~/.hermes/state.db`, restart. Losing it drops
recent conversation context, not the source-of-truth family data.
