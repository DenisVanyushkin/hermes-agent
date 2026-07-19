# Amina data seeding — operator runbook

Bulk edit family data (Люди, Места, События, Серии, Планы, Лекарства,
Покупки) via an xlsx round-trip instead of one-by-one `fam` CLI calls.
Source of truth stays the DB; the xlsx is a working copy for a single
edit session, approved and applied through `data_roundtrip.py`.

Script: `custom/fam/scripts/data_roundtrip.py` (thin wiring over
`fam.seed` / `fam.seed_xlsx` — see those modules for the actual rules).
Run from `custom/fam/`:

    ../../venv/bin/python scripts/data_roundtrip.py <cmd> ...

## Checklist

**1. Stop the writers** so nothing races the import (minute-tick,
digest, meds-gen, maintenance all touch the same DB):

    systemctl --user stop 'fam-*.timer'

**2. Export → edit → diff → отчёт Denis → «да»**

    ../../venv/bin/python scripts/data_roundtrip.py export --out /tmp/amina-data.xlsx

Prints the snapshot name, e.g. `snapshot: export-20260719-1530.json`
(written under `~/.hermes/private/amina/seeding/` by default; pass
`--snapshot-dir DIR` to override). Open the xlsx, edit rows (see the
README sheet inside the workbook for the id/new-row/delete conventions),
save.

    ../../venv/bin/python scripts/data_roundtrip.py diff \
        --file /tmp/amina-data.xlsx \
        --snapshot ~/.hermes/private/amina/seeding/export-20260719-1530.json

Read the printed report (➕ inserts, ✏️ updates, 🗑 deletes, ⚠️ conflicts
per sheet). Exit code 0 = clean or has-changes-but-no-conflicts, exit
code 2 = conflicts present — fix the xlsx and re-diff before proceeding.
**Do not run `apply` until Denis has read this report and said «да».**

**3. Apply**

    ../../venv/bin/python scripts/data_roundtrip.py apply \
        --file /tmp/amina-data.xlsx \
        --snapshot ~/.hermes/private/amina/seeding/export-20260719-1530.json \
        --yes

`apply` re-runs the diff itself first (catches anything that changed in
the DB between step 2 and now); if that re-diff still has conflicts, it
exits 2 and writes nothing. Otherwise it backs up **both** DBs
(`assistant.db` + `state.db`, via `maint.backup_db`, into `backup_dir`
from `fam-config.json`) *before* touching anything, applies every
insert/update/delete inside one `BEGIN IMMEDIATE` transaction, commits,
then re-exports and checks the result matches the file
(`verify_roundtrip`).

- Exit 0: applied and verified clean.
- Exit 2: conflicts, nothing applied (safe to fix and retry).
- Exit 3: **applied and committed, but the post-apply verify didn't
  match.** The data IS already in the DB — do not re-run apply blindly.
  Investigate the mismatch (likely a concurrent write, or a domain-layer
  quirk in how a field round-trips) before deciding whether to restore
  from the backup just taken (see step 6) or fix forward.

Running `apply` **without `--yes`** only prints the report and exits 0 —
nothing is written. Use it as a final dry-run right before the real
`--yes` call if you want to double-check nothing drifted.

**4. Restart the timers**

    systemctl --user start fam-reminders.timer fam-digest.timer \
                            fam-meds-gen.timer fam-maintenance.timer \
                            fam-offsite.timer  # + any other fam-*.timer units

(list every unit stopped in step 1 — `systemctl --user list-units
'fam-*.timer'` before stopping, if unsure which ones exist).

**5. Smoke test** — confirm the app reads the new data cleanly:

    bin/fam people list
    bin/fam places list
    bin/fam cal range 2026-01-01 2099-01-01
    bin/fam plan list
    bin/fam meds list --pending
    bin/fam shop list

**6. If something's wrong** — restore from the backup `apply` just took
(printed as `backup: <path>` for each DB during step 3), following
`custom/fam/RESTORE.md`'s "Real recovery" section (stop writers, verify
the chosen backup with `restore-rehearsal.sh`, swap it in, sanity-check,
restart writers).

## Standalone verify

To check an xlsx still matches the live DB exactly without doing an
export/diff/apply cycle (e.g. right after `apply`, or to sanity-check an
old file):

    ../../venv/bin/python scripts/data_roundtrip.py verify --file /tmp/amina-data.xlsx

Exit 0 = matches, exit 3 = mismatch.

## Notes

- `export` opens the DB **read-only** (`sqlite3` URI `mode=ro`) — it
  cannot write, even by accident.
- DB path resolution for every subcommand: `--db PATH` if given, else
  `FAM_DB` env, else the normal host/sandbox auto-resolve
  (`fam.db.resolve_db_path`).
- `diff`/`apply` accept the xlsx as exported by `seed_xlsx.write_workbook`
  — don't hand-craft the file structure; edit the exported one.
