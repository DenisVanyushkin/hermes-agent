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

## NAS offsite (age-encrypted) — weekly, 192.168.1.25:/volume1/hermes-backups

Weekly `fam-offsite.timer` (Sun 23:30 UTC) writes age-encrypted copies of both
DBs to the NFS mount `/mnt/nas-hermes` as `<stem>-YYYYMMDD.db.age`, keeping the
newest 8. The **private age key is NOT on the VM** — it is held off-VM by Denis.
Local daily backups (`~/.hermes/private/amina/backups/`, keep 7) are unaffected.

### Restore from a NAS offsite copy
1. Ensure the mount is up: `mountpoint /mnt/nas-hermes` (unit `mnt-nas\x2dhermes.mount`).
2. Decrypt with the off-VM private key (bring the key in transiently; do not persist it on the VM):
   `age -d -i /path/to/amina-offsite.key -o /tmp/restored.db /mnt/nas-hermes/assistant-YYYYMMDD.db.age`
3. Verify: `cd ~/.hermes/hermes-agent/custom/fam && python3 -c "from fam import maint; print(maint.verify_backup('/tmp/restored.db'))"`
   → expect `(True, {'integrity': 'ok', 'schema_version': '6'})`.
4. Swap in (stop the minute timer first): `systemctl --user stop fam-reminders.timer`,
   copy `/tmp/restored.db` over `~/.hermes/private/amina/assistant.db`, then restart it.

### Rehearsal (non-destructive)
`bash custom/fam/scripts/offsite-restore-rehearsal.sh /path/to/amina-offsite.key`
Pulls newest `assistant-*.db.age` from the NAS, decrypts to a temp copy, runs
`verify_backup`. Live DB never touched. Rehearsed live 2026-07-14 → PASS.
