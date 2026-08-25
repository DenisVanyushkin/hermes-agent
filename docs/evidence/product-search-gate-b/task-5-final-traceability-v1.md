# Gate B final traceability review (Step 5)

**Checkpoint:** `1bc639f7c1` plus the Step 5 working tree  
**Scope:** post-reduction supervised path. This is a review artifact, not a deletion plan.

The review uses the pre-deletion map in
`traceability-current-v2.md`. “Test moved” means the old mechanism's test was
deleted or became inapplicable and a surviving test now covers the replacement.
A test that fails only incidentally is recorded as **uncovered**, even if the
suite happens to go red.

## Validity properties

| Property | Surviving implementation | Surviving test / deployed proof | Test moved with mechanism? | Coverage judgement |
| --- | --- | --- | --- | --- |
| Ordered 48-row corpus is the measured population | `EvidenceManifest.row_count=48`, contiguous ordinals, `_load_corpus_rows_file`, independent `_derive_binding_rows` | `test_gate_b_input_materialization.py::test_rehashed_reordered_corpus_is_not_canonical_order`; `test_gate_b_evaluator.py::test_evaluate_refuses_measurement_cardinality_not_matching_manifest`; composition smoke observes 48 rows | No; corpus tests survived | Covered |
| Raw bytes and per-row input/projection hashes are fixed | Manifest row hashes, corpus loader, projector-derived binding rows | `test_gate_b_input_materialization.py::test_pinned_loader_rejects_manifest_raw_and_selection_identity_drift`; `test_gate_b_runtime_artifact.py::test_manifest_binding_rejects_reorder_policy_and_prompt_drift_distinctly` | No | Covered in library path; smoke exercises the loaded corpus |
| Ordinal identity keeps duplicate inputs distinct | Full `ManifestRef` reservation key; ledger row state | `test_gate_b_collection_runner.py::test_collection_runner_dispatches_duplicate_inputs_as_distinct_rows`; composition dispatch probe saw 48 unique input keys | Yes; duplicate-dispatch coverage moved out of deleted journal tests into collection-runner tests | Covered |
| Executed runtime bytes are the attributed bytes | Content-addressed assembled artifact, whole-tree hash, wrapper `PYTHONHOME`, manifest binding | `test_gate_b_runtime_artifact.py::test_assembled_artifact_hash_covers_runtime_and_manifest_body`; `test_frozen_runtime_materializes_shim_and_matches_gateway_parity`; real artifact smoke | No | Covered for the shipped artifact path |
| Interpreter, stdlib, dependencies and native libraries are reproducible | `gate_b_runtime_v1.build_assembled_artifact`, contained interpreter/stdlib/libs, installer hash | `test_gate_b_runtime_artifact.py::test_frozen_runtime_materializes_shim_and_matches_gateway_parity`; gateway-interpreter smoke build | No | Covered for current build contract |
| Model, prompt, schema, profile and source authorities are pinned | `AuthorityIdentity`, authority files, provider identity checks | `test_gate_b_collection_runner.py::test_provider_authority_drift_fails_before_dispatch`; evidence-v3 authority tests | No | Covered |
| Policy and estimand guardrails are the policy that ran | Explicit manifest-bound Decision policy; benchmark policy module; deny patterns and reviewed allowlist | `test_gate_b_evaluator.py::test_evaluator_uses_loaded_policy_thresholds`; `test_gate_b_evidence_v3.py::test_v3_policy_has_the_exact_company_deny_prefilter`; allowlist characterization suite | No | Covered; deny/allow rules remain validity-critical |
| Prompt and response-schema identity match dispatch | Provider authority checks, structured prompt identity, response schema validation, request binding | `test_gate_b_collection_runner.py::test_provider_authority_drift_fails_before_dispatch`; response/request binding tests in collection/evidence suites | No | Covered |
| Projection is conservative and deterministic | `project_vacancy_evidence_v3`, independent projection hash, validator | `test_gate_b_evidence_v3.py::test_projector_characterization_keeps_role_direct_and_unknown_dimensions` and the v3 characterization suite | No | Covered |
| Reviewed description claims and company-fact denial define the estimand | Reviewed hash allowlist, company deny prefilter, admitted-claim projector | `test_gate_b_evidence_v3.py::test_company_and_marketing_fragments_are_excluded_before_provider_input`; `test_v3_provider_validation_binds_non_unknown_claims_to_reviewed_fragments` | No | Covered |
| Provider responses are schema-valid and citation/claim closed | `validate_provider_payload_v3`, Decision v2 synthesis, response binding | v3 provider-validation tests; `test_terminal_unknown_uses_empty_provider_record_and_conservative_cost` | No | Covered |
| Per-row Decision v2 bytes are immutable and manifest-bound | Create-once DecisionEvidenceStore keyed by full ref; final report stores decision hashes | `test_gate_b_collection_runner.py::test_decision_evidence_store_namespaces_identical_bytes_by_manifest_ref`; missing-ref test; smoke publishes 48 decisions | No | Covered |
| Sealed recordings replay deterministically, including terminal unknown | Create-once RecordingStore, manifest-bound replay, empty response preserved as bytes | `tests/product_search/test_gate_b_recording_replay.py` (four tests); collection terminal-unknown test; smoke verifies five empty recordings | Yes; durable-journal replay tests were deleted, and direct recording/replay tests remain | Covered for supervised evidence; cross-process recovery is intentionally retired |
| Canonical provider record supplies outcome and cost | Provider store record is the canonical anchor; ForegroundDispatchLedger retains terminal record hash in-process | `test_gate_b_collection_runner.py::test_terminal_unknown_uses_empty_provider_record_and_conservative_cost`; smoke records all 48 outcomes | Yes; journal-anchor tests were replaced by collection/provider-record tests | Covered for one foreground process |
| No duplicate dispatch preserves denominator and row evidence | Full-ref reservation identity, ledger claimed-state check, capability reconciliation | Duplicate-input collection test; Step 5 dispatch probe: 48 unique inputs and forced 49th refused | Yes; old journal cap/duplicate test was deleted and replaced by direct ledger + composition tests | Covered |
| Corpus → projection → recording → Decision lineage is intact | Manifest refs carried through rows, recordings, DecisionEvidenceRefs and report hashes | collection runner binding tests; evaluator finalized-hash checks; smoke publishes all artifacts | No | Covered |
| Terminal outcomes include success, terminal failure and terminal unknown with conservative cost | `TerminalOutcome`, provider records, empty-response recording path | `test_terminal_unknown_uses_empty_provider_record_and_conservative_cost`; composition mixed-outcome smoke observes 5 unknown rows | No | Covered |
| Published metrics have correct cardinalities and denominators | `MeasurementReport`, collection metrics, evaluator cardinality checks | `test_gate_b_evaluator.py::test_evaluate_refuses_measurement_cardinality_not_matching_manifest`; smoke observes 48/48 and deliverable 19 | No | Covered |
| Adjudication is bound to exact Decision v2 artifacts | Verdicts carry ManifestRef and decision hash; totals are derived; evaluator rechecks refs | `test_evaluator_rejects_verdict_with_foreign_manifest_ref`, `test_adjudication_decision_hash_must_match_finalized_row`, create-once set test | No | Covered |
| 43 / 5 / 0.80 rules are evaluated as policy rules | GateEvaluator loads policy and emits proceed/revise/refuse with sorted violations; separate evaluate command | `test_evaluator_publishes_proceed_to_shadow_for_complete_passing_measurement`; threshold and incomplete-branch tests; evaluator CLI tests; smoke emits complete/refuse | No | Covered |
| Pricing authority matches actual provider charge | Pricing identity checked before capability issuance and provider authority checked before dispatch | `test_provider_authority_drift_fails_before_dispatch`; runtime pricing identity test | No | Covered with safety/accounting spillover |
| Artifact producer supplies the runtime the wrapper names | `gate_b_runtime_v1` builder and exporter; content-addressed install | `test_gate_b_benchmark_runner.py::test_runtime_export_wrapper_uses_neutral_runtime_builder_and_rejects_dirty_source`; runtime artifact suite; real smoke build | No | Covered |

## Safety-to-keep properties

| Property | Surviving implementation | Independent negative / composition proof | Test moved with mechanism? | Coverage judgement |
| --- | --- | --- | --- | --- |
| Live job-intel DB and protected production state are unreachable | Root-owned supervised wrapper uses five hard-fail `InaccessiblePaths` entries through transient systemd | Inside supervised provider probe: all five paths returned errno 13 and `reachable=false`; deploy wrapper tests cover missing-path and composition failures | Yes; systemd unit tests were removed with the unit; wrapper tests and smoke probe replaced them | Covered |
| Hard 48-call / spend cap and no implicit retry | ForegroundDispatchLedger checks call cap before duplicate state and aggregate spend cap; one-shot foreground process; no recovery/retry branch | Direct 49th and spend-ceiling tests; mutation of each raise made its own test fail; composition probe observed 48 unique dispatches and a forced 49th refused with `call_cap_exhausted` | Yes; old journal cap test was deleted and replaced by direct ledger + deployed-command probe | Covered for one foreground process; restart durability is not claimed |

## Explicitly retired / not validity

Resume, crash-window recovery, durable journal replay lifecycle, systemd unit lifecycle,
installer lifecycle, launch receipts, owner approval, signing and recovery-key
plumbing are unattended-only and were removed. No surviving validity property depends
on them. The in-memory dispatch ledger is not a durable journal: it exists only to
carry the canonical provider record anchor, prevent duplicate dispatch, and enforce the
foreground cap.

## Review findings

- The cap test was initially orphaned when the journal tests were deleted. It is now
  directly covered at both ledger level and through the deployed wrapper; mutation
  checks show the signals are not incidental.
- The first Step 5 smoke attempt exposed missing probe environment propagation through
  the canonical wrapper. The wrapper now passes the dispatch probe variables through
  `systemd-run); the rerun observed the expected cap refusal and all five isolation
  denials.
- One evaluator test still asserted the pre-derived-totals behavior. It was updated to
  assert the accepted contract: `evaluate_report` derives all three adjudication
  totals from the verdict set.
- The public `GateEvaluator.evaluate` totals guard has its own direct test,
  `test_evaluator_rejects_direct_call_with_mismatched_adjudication_totals`. A
  mutation replacing that guard with `False` made this test fail (alongside
  other branch-specific tests), so the guard is not covered only incidentally.
- No validity property was found whose only surviving coverage is an incidental
  evaluator failure. Where tests moved with deleted mechanisms, the replacement test
  is named above.
