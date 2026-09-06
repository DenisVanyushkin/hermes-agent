# External Calendar and Taya Operations

This is the short operator reference for the external-calendar sync. The
source of truth for schema details is `custom/fam/fam/db.py`.

## Storage and configuration

- `ext_exports` is the local journal for the Hermes write calendar.
- `ext_exports_taya` is the local journal for the Taya write-only calendar.
- `extcal_export_issues` holds active export errors and conflicts. Each row is
  scoped by `target` (`hermes` or `taya`) and `event_id`.
- `meta.extcal_last_run` records that the export tick ran. `meta.extcal_last_ok`
  records the last fully clean tick; it is not the liveness marker.
- `extcal_write_calendar` configures the Hermes write URL.
  `extcal_taya_calendar` configures the Taya write URL.

The journals store the managed event href, ETag, semantic body hash, and sync
time. A journal row is local bookkeeping; it is not by itself proof that the
remote resource still exists.

## Health probes

The probes are included in the nightly diagnostics digest under
`~/.hermes/diagnostics/fam-digest-latest.json`, in the `probes` section. To
read them directly:

```bash
cd ~/.hermes/hermes-agent/custom/fam
python3 - <<'PY'
from fam import db, gate, health

conn = db.connect()
cfg = gate.load_config()
print(health.extcal_staleness(conn, cfg))
print(health.extcal_failures(conn, cfg))
PY
```

`extcal_staleness` answers whether a tick ran recently, using
`meta.extcal_last_run`. `extcal_failures` answers whether an export is stuck,
using `extcal_export_issues` and the split terminal streaks. They are separate
signals: a healthy liveness marker does not clear an export issue, and an
export issue does not mean that the timer stopped.

## Conflicts

List conflicts with:

```bash
custom/fam/bin/fam cal-ext conflicts
custom/fam/bin/fam cal-ext conflicts --json
```

Resolve one conflict with an explicit decision and target:

```bash
custom/fam/bin/fam cal-ext resolve EVENT_ID --target hermes --keep-remote
custom/fam/bin/fam cal-ext resolve EVENT_ID --target taya --force-push
```

`--keep-remote` accepts the verified iCloud fields. `--force-push` explicitly
overwrites iCloud with the local Hermes fields. Omit `--target` only when that
event has exactly one conflict row; when more than one target is present,
pass `--target hermes` or `--target taya` explicitly.

## Taya routing

An event whose subject has the person slug `taya` is routed to
`extcal_taya_calendar`; events with Taya only as a participant remain in the
Hermes calendar. One shared planner computes the desired route before either
journal is changed.

When an already-exported event changes route, the reconciler performs one
CalDAV `MOVE` from the source href to the destination href. It first checks
that the destination is free, then GETs the moved resource to verify its UID
and obtain its new ETag. The destination journal is committed before the
source journal is removed locally. A 405 or 501 response does not fall back to
PUT plus DELETE. A journal and remote destination can be reconciled on a later
tick if the MOVE succeeded before local journal commit.

## Operational traps

- iCloud rejects two resources with the same UID, so destination-first PUT is
  incompatible with route transitions.
- iCloud ignores `Overwrite: F` and can silently overwrite an occupied
  destination. The destination must be checked explicitly.
- MOVE does not return an ETag. The follow-up GET is mandatory before writing
  the destination journal.
- A `SKILL.md` change in git can be overwritten by the `skill-sync` timer,
  which copies the deployed file back into the repository. After an authorized
  commit, copy the repository file to `~/.hermes/skills/amina-fam/SKILL.md`.
- A skill change does not affect an agent session already in progress. Use an
  explicit `skill_view` in the dialogue or reset the session.
