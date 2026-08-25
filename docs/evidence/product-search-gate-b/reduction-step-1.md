# Gate B reduction — Step 1 supervised spine

Step 1 keeps the old system inert and adds the supervised composition root.
The canonical entry point is the root-owned-at-install
`scripts/job_intel_gate_b_supervised.sh` shipped inside the content-addressed
artifact. It invokes `sudo -n systemd-run --wait --pipe --uid=hermes` and
passes one `InaccessiblePaths` property per protected path. Missing optional
paths use systemd's `-` prefix. The wrapper is the only place that composes
this invocation; operators and the harness do not hand-assemble it.

The protected paths are:

- `/home/hermes/.hermes/state.db`
- `/home/hermes/.hermes/job_intel/job_intel.sqlite3`
- `/home/hermes/.hermes/job_intel/job_intel.sqlite3-wal`
- `/home/hermes/.hermes/job_intel/job_intel.sqlite3-shm`
- `/home/hermes/.cache`
- `/var/lib/browser-desktop/profiles`

The wrapper has a six-path completeness guard. The artifact installer makes
the published tree `root:hermes`, so the constrained service user cannot edit
the deny-list.

`init-run` provisions the append-only journal explicitly. `run-supervised`
then loads the pinned manifest and 48-row corpus, derives projections, loads
the reviewed allowlist and policy, resolves provider and decision factories
inside the artifact, opens the pre-provisioned journal and evidence stores, and
runs the existing collection machinery. The canonical provider record remains
the source of cost and outcome; recording and Decision v2 evidence remain
content-addressed and manifest-bound.

The call/spend capability is process-local in this reduction. Its counter and
reservations live for one foreground process; a restart is not promised to
preserve the cap and is not an implicit retry path. This is an explicit scope
reduction from the retired unattended protocol.

The supervised target removes the Step 0 spine guard only when this wrapper,
provisioning, collection wiring and harness are present. Known validity defects
such as terminal-unknown recovery remain intentionally out of scope for Step 1
and are measured by the later reduction step.
