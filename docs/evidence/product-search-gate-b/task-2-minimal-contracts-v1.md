# Gate B minimal evidence contracts (Task 2)

**Status:** final design contract for Tasks 3–10. This document defines data
and interface boundaries only; it does not implement the manifest, journal,
recording store, runner, or evaluator.

## 1. Canonical `EvidenceManifest`

The manifest is the only owner of a Gate B run identity. It is a canonical,
strict JSON document encoded as UTF-8: object keys sorted, compact separators,
no insignificant whitespace, no floating-point values, and decimal money values
encoded as strings. Unknown fields, duplicate keys, non-UTC timestamps, and
relative paths are invalid. `manifest_sha256` is SHA-256 of the canonical
identity body with the `manifest_sha256` and `created_at` fields omitted; it is
not a second identity. `created_at` remains required metadata for audit
chronology, but is deliberately not part of content identity: rebuilding
byte-identical inputs produces the same manifest hash and run identity.

Required top-level fields:

```text
schema_version: "gate-b-evidence-manifest-v1"
run_id: "gate-b-evidence-v1-<16 lowercase hex>"
manifest_sha256: <sha256 of canonical body without this field>
created_at: RFC3339 UTC timestamp
benchmark_kind: "gate_b_description_evidence"
row_count: 48
rows: exactly 48 Row entries, ordinal 0..47 in this order
runtime: RuntimeIdentity
authorities: AuthorityIdentity
limits: {ordered_call_cap: 48, per_call_maximum_usd: "0.01", aggregate_maximum_usd: "0.48"}
```

Each `Row` is immutable and contains:

```text
ordinal: integer 0..47
corpus_key: non-empty canonical selection key
raw_sha256: sha256 of the pinned raw corpus artifact
input_sha256: sha256 of the exact provider input payload
projection_sha256: sha256 of canonical project_vacancy_evidence_v3 output
```

`RuntimeIdentity` records the bytes that actually execute, not merely a Git
ref:

```text
artifact_sha256
interpreter_sha256
stdlib_inventory_sha256
installed_distributions_sha256
installed_files_sha256       # includes every .pth and editable-install file
sys_path_sha256
native_extensions_sha256
shared_libraries_sha256
```

`AuthorityIdentity` records the exact model, prompt, response schema, profile,
policy, Decision v2, pricing schedule, and source-authority hashes. Each value
is a lowercase SHA-256; versions are separate strings where a version is part
of the public contract. The manifest contains no owner signature, launch
receipt, PID, process-start identity, approval window, or launch-attempt ID.

### Manifest invariants

1. `run_id`, `manifest_sha256`, and every row hash are immutable after first
   publication. A changed corpus order, raw byte, projection, policy, prompt,
   schema, authority, runtime byte, or limit creates a new manifest/run.
2. Ordinals are contiguous and unique. `rows[ordinal].input_sha256` is the
   only admissible provider-input identity; no caller-selected alternate input
   may be substituted.
3. The manifest is sufficient to verify that a recording and a decision belong
   to this run, but it does not contain provider responses or human adjudication.
4. The manifest is published before any dispatch. Later components may add
   artifacts referenced by hash, never mutate this document.
5. `created_at` may differ between identical rebuilds without changing
   `manifest_sha256` or `run_id`; a changed identity field listed above creates
   a new manifest/run.

## 2. Shared row reference

Every journal event, recording, replay result, metric, and Decision v2 result
uses this same reference and must not mint an alternative run identity:

```text
ManifestRef = {
  run_id,
  manifest_sha256,
  ordinal,
  input_sha256,
  projection_sha256,
}
```

Consumers reject a reference if it does not match the loaded manifest row.
`recording_sha256` and `decision_sha256` are artifact hashes attached to this
reference; they do not replace it.

## 3. Dispatch journal interface

The journal is durable, append-only state for one manifest. It is the accounting
boundary, not an owner-approval or launch-authorization mechanism.

```text
Journal.open(manifest: EvidenceManifest, store: JournalStore) -> Journal
Journal.append_pre_dispatch(ref: ManifestRef) -> DispatchReceipt
Journal.commit_terminal(
    receipt: DispatchReceipt,
    outcome: TerminalOutcome,
    recording_sha256: sha256,
    measured_cost_usd: Decimal | None,
    conservative_cost_usd: Decimal,
) -> None
Journal.snapshot() -> tuple[JournalEntry, ...]
Journal.verify() -> None
```

`append_pre_dispatch` must fsync the `DISPATCHED` intent before entering the
provider transport. It is the durable point after which a crash is ambiguous
and no implicit retry is allowed. `commit_terminal` is idempotent only for the
same reference, outcome, recording hash, and cost; a conflict is fatal. A
terminal commit is permitted only after the recording is sealed and verified.

The only row states are `PENDING`, `DISPATCHED`, `SUCCESS`,
`TERMINAL_FAILURE`, and `TERMINAL_UNKNOWN`. `DISPATCHED` and
`TERMINAL_UNKNOWN` never transition back to `PENDING`. Recovery is an explicit,
offline operation outside this interface; opening a journal cannot reinitialize
or reset it. The journal enforces the manifest's exact call/spend limits and
records conservative maximum cost for ambiguous post-dispatch outcomes.

`JournalEntry` always embeds `ManifestRef`, state, an event sequence, recording
hash when present, measured cost when known, and conservative cost. A journal
event without the manifest run/hash/ordinal/input/projection tuple is invalid.

## 4. Sealed recording and replay interface

```text
RecordingStore.save_exclusive(record: SealedRecording) -> RecordingRef
RecordingStore.load(ref: RecordingRef) -> SealedRecording
RecordingStore.verify(ref: RecordingRef, manifest: EvidenceManifest) -> None
RecordingStore.replay(ref: RecordingRef) -> ReplayObservation
```

`SealedRecording` contains the shared `ManifestRef`, `recording_sha256`, the
canonical request bytes, response bytes (or an explicit empty response for a
terminal unknown), response/schema/model/prompt hashes, normalized usage and
cost, latency, terminal outcome, and sanitized failure diagnostics. Its hash is
over the canonical sealed bytes. `save_exclusive` is create-once: an existing
hash with different bytes is an error, never an overwrite or delete/recreate.

`verify` checks all manifest and row hashes, schema/model/prompt identity,
usage/cost consistency, and that the recording is a regular immutable artifact
inside the run store. `replay` reads bytes only: it opens no provider, socket,
database, subprocess, clock, or credential source and returns a byte-identical
observation. It must work for successful, terminal-failure, and
terminal-unknown records.

## 5. Deterministic gate evaluator interface

Collection always emits immutable measurements. Promotion is a separate pure
step after all evidence and, when required, human adjudication are complete.

```text
GateEvaluator.evaluate(
    manifest: EvidenceManifest,
    measurements: MeasurementReport,
    adjudication: AdjudicationSet,
) -> GateDecision
```

`MeasurementReport` contains counts and denominators (deliverable results,
terminal unknowns, terminal failures, provider failures, replay checks,
adjudicated rows, and coverage) plus hashes of the finalized evidence. It is
published even when a gate decision cannot be granted. `AdjudicationSet` is
optional only when the policy does not require accuracy; for this Gate B it is
required, pinned to the same `ManifestRef`, and must cover the complete audited
denominator before accuracy is evaluated.

`GateDecision` contains the manifest/run reference, evaluator contract hash,
`measurement_status` (`complete` or `incomplete`), `decision`
(`proceed_to_shadow`, `revise`, or `refuse`), and a sorted machine-readable
`violated_rules` tuple. The evaluator applies exactly:

```text
deliverable_count >= 43
terminal_unknown_count <= 5
manual_triage_accuracy >= 0.80, only when the audited set and denominator are complete
```

Missing adjudication sets `measurement_status: incomplete`, emits
`adjudication_incomplete`, and produces `decision: revise`; it is not an
assumed zero or a caveat attached to a promotable number. A complete
measurement that violates a threshold produces `measurement_status: complete`
and `decision: refuse`, while a complete measurement satisfying every rule
produces `proceed_to_shadow`. The evaluator is deterministic, side-effect free,
and cannot dispatch, mutate production state, or alter the manifest, recordings,
journal, or Decision v2 result.
