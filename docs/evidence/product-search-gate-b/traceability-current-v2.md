# Gate B current traceability map (pre-deletion)

**Checkpoint:** `45601c8528`  
**Status:** review artifact only. No production or test mechanism is deleted by
this document, and this document does not propose a deletion order.

This is the current map after the round-3 review. It deliberately classifies
properties into three buckets:

- **validity** — the mechanism makes the measured result true or preserves the
  identity of evidence used to answer the hypothesis;
- **safety-to-keep** — not part of the estimand, but must survive the
  simplification: live-database isolation and the hard 48-call / no-retry cap;
- **unattended-only** — exists so a human does not have to watch the run,
  including resume, recovery, crash-window handling, durable journal replay, and
  systemd lifecycle.

A mechanism is classified by what it does now, not by why it was originally
introduced. If a safety mechanism has become load-bearing for validity, it is
listed as validity and the reason is stated. “No mechanism” and “not called” are
intentional findings, not implied approvals.

| Claimed property | Current mechanism and liveness | Classification and judgement |
| --- | --- | --- |
| Exactly the intended ordered 48-row corpus is measured | `EvidenceManifest` requires `row_count=48`; `_load_corpus_rows_file` validates contiguous ordinals and raw hashes; `run_collection` repeats the cardinality/order checks. The deployed wrapper supplies neither manifest nor config, so this mechanism is not reachable from the shipped unit. | **validity — partial/unreachable in deployment.** The contract is explicit, but the production composition root exits before it can use it (`bugs+impl-2`, `docs+tests-1`). |
| Raw bytes for every corpus row are fixed and attributable | `EvidenceManifestRow.raw_sha256` and independent corpus loading; `_derive_binding_rows` compares observed raw/input/projection identities rather than passing `manifest.rows`. | **validity — present in the library path.** It has no deployed-path proof because the unit stops before loading the corpus. |
| Row identity is ordinal and duplicates are not silently merged | Contiguous manifest ordinals; `_reservation_input_hash` includes the full `ManifestRef`, including ordinal; duplicate-row composition test exercises two transports. Inter-process pre-dispatch serialization is absent (`adversarial-17`). | **validity — partial.** Dropping ordinal would change the denominator and adjudication identity; therefore this is validity, not merely a cap convenience. |
| The bytes actually executed are the bytes attributed by the manifest | `build_assembled_artifact`, `RuntimeIdentity`, whole-tree artifact hash, wrapper path, `_artifact_binding_context`, and `verify_manifest_binding`. Runtime identity checks default several absent manifest fields back to the manifest itself, omit `shared_library_provenance`, and the builder can leave `artifact_tree_sha256` as a zero placeholder (`bugs+impl-3`, `bugs+impl-4`, `bugs+impl-9`). | **validity — partial/broken.** Runtime byte identity is load-bearing even though the old immutable runtime was also launch machinery. |
| The frozen Python, stdlib, dependencies and native libraries are reproducible | `gate_b_runtime_v1.build_assembled_artifact` copies the interpreter/stdlib/libraries; installer verifies a content-addressed tree; wrapper sets `PYTHONHOME` and `LD_LIBRARY_PATH`. Shared-library closure is derived only from the Python executable (`adversarial-7`), and the runtime identity is not independently complete. | **validity — partial.** Reproducibility is evidence provenance, not unattended lifecycle. |
| Model, prompt, schema, profile and source authorities are pinned | `AuthorityIdentity`, `authority_paths`, `_authority_identity`, provider authority checks, and manifest authority hashes. Post-dispatch authority checks hash the semantic prompt rather than the dispatched prompt (`adversarial-12`); authority wiring is therefore not fully equivalent to the bytes sent. | **validity — partial.** |
| The benchmark policy and estimand guardrails are the policy that ran | `gate_b_benchmark_policy_v3.py`, explicit `decision_policy` in `run_collection`, `company_fact_deny_patterns`, and `description_claim_admission=reviewed_hash_allowlist_only`. The executed policy is not cryptographically tied to the manifest-bound policy bytes (`adversarial-11`). | **validity — partial.** Deny patterns and reviewed allowlist define the estimand and must not be treated as ceremony. |
| Prompt and response-schema identity match the actual dispatch | Manifest authorities and provider identity checks name prompt/schema hashes; the post-dispatch prompt check uses the wrong semantic source (`adversarial-12`), and requests are accepted without binding them to the current response (`adversarial-13`). | **validity — partial/broken.** |
| Projection is conservative and deterministic | `project_vacancy_evidence_v3`, `audit_vacancy_projection_v3`, independent projection hash derivation, and characterization tests. | **validity — present in the direct and collection paths; deployed reachability remains blocked by missing inputs.** |
| Reviewed description claims and company-fact denial define what description treatment means | `load_reviewed_fragment_allowlist_v3`, `_compiled_company_fact_deny_patterns`, and `_allowed_claims`. | **validity — present and load-bearing.** Removing this changes the hypothesis/estimand rather than simplifying launch safety. |
| Provider responses are schema-valid and citation/claim closed | `_runner_response_request_v3`, `validate_provider_payload_v3`, response schema authority, and Decision v2 synthesis. Decision requests are not bound to the current response (`adversarial-13`). | **validity — partial.** |
| Per-row Decision v2 output is immutable, manifest-bound evidence | `DecisionEvidenceStore.save_exclusive`, full `ManifestRef` keying, `bytes_for`, `verify`, and collection row refs. | **validity — present for stored bytes.** The promotion half has no production caller (`arch+quality-5`), so evidence exists without a shipped run-level consumer. |
| Sealed recordings can be replayed deterministically offline | `RecordingStore.save_exclusive`, `bytes_for`, `verify(ref, manifest, terminal_entry)`, and `replay` with no provider/database/socket. The terminal-unknown recording canonicalizes an empty response as `b"{}"` and then fails its own verify (`bugs+impl-1`); the normal terminal commit can precede recording creation (`arch+quality-2`). | **validity — broken for required failure states.** Replay is not only unattended convenience when it is the evidence used to adjudicate the result. |
| Canonical provider record supplies the actual outcome and cost | `GovernedProvider`, provider store records, `_issue_collection_capability`, and journal `recording_sha256` anchor. Recovery writes a record the real provider store cannot load (`adversarial-4`), and commit ordering violates the sealed-before-terminal contract. | **validity — partial/broken.** This began as accounting/safety, but it now determines the measured outcome and cost, so it cannot be classified as unattended-only. |
| No duplicate dispatch changes the measured denominator or row evidence | Full-ref reservation ids, journal state checks, terminal-row reconstruction, and capability reconciliation. There is no inter-process serialization (`adversarial-17`), and recovery is not usable for all terminal states. | **validity — partial.** Duplicate dispatch is a measurement identity error, not merely a restart concern. |
| Corpus → projection → recording → Decision v2 lineage is unbroken | Manifest refs, row input/projection hashes, recording refs, DecisionEvidenceRefs, and final report hashes. Response binding is incomplete (`adversarial-13`), and the report artifact is neither manifest-bound nor create-once (`arch+quality-4`). | **validity — partial/broken.** |
| Terminal outcomes include success, terminal failure and terminal unknown with conservative cost | `TerminalOutcome`, provider post-dispatch records, `_recover_dispatched_row`, empty-response rules and conservative-cost metadata. Terminal unknown is currently self-invalidating; recovery therefore wedges rather than producing a valid terminal result. | **validity — broken for terminal unknown.** |
| Published metrics have correct cardinalities and denominators | `_write_measurement_report`, `CollectionMetrics`, `MeasurementReport`, and `GateEvaluator`. The evaluator does not bind measurement cardinalities to the 48-row manifest (`adversarial-15`); `deliverable_count` counts transport outcomes rather than validator-rejected rows (`docs+tests-6`). | **validity — partial/broken.** A wrong denominator changes the answer even if the run is supervised. |
| Adjudication is bound to the exact Decision v2 artifacts reviewed | `AdjudicationVerdict` carries `ManifestRef` and `decision_sha256`; `AdjudicationSet` derives totals from verdicts; evaluator checks verdict references. Completeness/cardinality is not tied back to the 48-row manifest (`adversarial-15`). | **validity — partial.** |
| The 43 / 5 / 0.80 rules are the policy rules actually evaluated | Policy fields and `GateEvaluator` produce `proceed_to_shadow`, `revise`, or `refuse`. `evaluate_report` finalizes two adjudication fields but not the three counts required for promotion (`bugs+impl-5`), and the promotion path has no caller outside tests (`arch+quality-5`). | **validity — broken/unconsumed.** A threshold rule that cannot reach a production decision is “not called,” not “closed.” |
| Exact spend and ordered-call cap are enforced, with no implicit retry | `Limits`, `StructuredCallCapability`, pricing identity, reservation/dispatch/reconcile, and journal states. No test covers cap enforcement (`docs+tests-9`); the manifest accepts arbitrary spend ceilings (`adversarial-6`), and recovery/terminal commit defects undermine cap continuity. | **safety-to-keep — partial.** The cap is not the estimand, but it is the minimum accounting boundary that must survive; duplicate dispatch and canonical cost records are separately validity properties. |
| Pricing authority is the pricing used for the actual charge | `capability.pricing.identity_sha256`, provider authority identity, manifest `pricing_sha256`, and provider records. Two incompatible hashes share the installed-distributions identity field (`adversarial-9`), so the authority inventory is not a clean independent source. | **safety-to-keep with validity spillover — partial.** Pricing is a safety/accounting boundary, but wrong pricing changes cost and therefore the interpretation of a capped run. |
| The live job-intel database and production state are never touched | Runner import/code-path boundary, wrapper unsets DB/outbox/browser env, and systemd `InaccessiblePaths` protects the live DB/WAL/SHM/outbox/state/cache paths. | **safety-to-keep — present as two independent mechanisms.** This is not part of the description estimand, but the July live-DB incident makes it non-negotiable. The shipped unit currently fails earlier on missing inputs; that does not remove the isolation requirement. |
| The run can resume after interruption and recover crash windows | `AppendOnlyJournal`, `Journal.create/open`, `DISPATCHED` recovery, terminal reconstruction, provider-record anchor, and systemd one-shot lifecycle. Recovery is broken for terminal unknown, journal truncation can accept a shorter history and reset the cap (`bugs+impl-6`), and the provider commit/record ordering is unsafe. | **unattended-only — partial/broken.** The journal's provider-record anchor and duplicate-dispatch consequences are tracked above as validity; the restart/resume machinery itself exists only to avoid human supervision. |
| Durable journal replay is available across process death | `AppendOnlyJournal` append/fsync, `snapshot/entries`, torn-tail handling, and recovery branch. `Journal.snapshot()` is a dead forwarder, scans are duplicated, and the recovery branch cannot produce a valid terminal-unknown recording. | **unattended-only — partial/broken.** Its lifecycle value is unattended operation; its canonical provider anchor is validity and is not being discarded under this label. |
| The systemd unit starts the exact artifact and lifecycle | Content-addressed artifact installer, `StateDirectory`, wrapper, `ExecStartPre`, and template unit. The shipped unit supplies none of the manifest/config/StateDirectory inputs required by `run-collection`; every start exits 2 before work (`bugs+impl-2`, `docs+tests-1`). | **unattended-only — currently absent as a usable path.** Systemd lifecycle is not evidence of truth; it exists to avoid a human start, and the current path does not reach collection. |
| Artifact publication and producer supply the runtime the unit names | `gate_b_runtime_v1.build_assembled_artifact`, exporter wrapper, content-addressed install script, and artifact whole-tree hash. The producer exists and is exercised by the 180-line composition harness; runtime identity fields/closure still have the validity gaps above. | **validity — partial.** The artifact is the source-of-bytes attribution, not merely an unattended launcher. |
| Launch receipts, owner approval, signing and recovery keys authenticate an unattended start | No current production implementation after the fortress retirement; old mechanisms live only in historical artifacts/docs. | **unattended-only — absent/retired.** They are not validity properties and are not mapped to a surviving mechanism. |

## Classification summary

The map yields three different conclusions, not one deletion recommendation:

- **Validity survives the simplification.** Projection, allowlist/deny rules,
  authority identity, runtime byte identity, provider outcome/cost lineage,
  recordings, Decision v2 evidence, adjudication and evaluator correctness are
  all still required. Several are currently partial or broken and cannot be
  waved away as launch ceremony.
- **Safety-to-keep is deliberately narrow.** Live-database isolation and the
  hard cap/no-retry boundary must remain even in a supervised run. Canonical
  pricing and duplicate-dispatch handling have validity spillover and are
  therefore listed above rather than discarded as pure safety.
- **Unattended-only is the simplification target.** Resume, recovery, crash
  windows, journal replay as lifecycle machinery, and systemd lifecycle exist
  to avoid human supervision. They are not being deleted here; their
  classification is recorded for the next decision.

This document is the pre-deletion map. It makes no claim that an unattended-only
mechanism is safe to remove before every validity and safety owner is identified,
and it intentionally contains no deletion order.
