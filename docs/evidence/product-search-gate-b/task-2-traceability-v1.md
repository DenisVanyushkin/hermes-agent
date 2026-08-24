# Gate B validity traceability (Task 2)

**Status:** design checkpoint, written before any Gate B mechanism is deleted.

## Contract-name migration

Existing on-disk artifacts use `benchmark_kind: gate_b_at_most_once` because
they were produced by the launch protocol now being retired. The new evidence
contract uses `benchmark_kind: gate_b_description_evidence`: it names the
measurement, not the authorization mechanism. Existing artifacts remain
interpretable under their recorded kind and are historical/non-authoritative;
the mapping is:

```text
gate_b_at_most_once  --historical launch-protocol name-->  gate_b_description_evidence
```

No existing artifact is rewritten or silently reclassified.

This table records what makes the result decision-grade, rather than what makes
the launch privileged or one-shot. “Current mechanism” means the mechanism in
the checkout at this checkpoint; it is not a promise that the mechanism will
survive the later simplification. A partial or absent entry is intentional: the
gap is part of the work, not a reason to quietly retain launch ceremony.

| Validity property | Current mechanism | Assessment at this checkpoint | Surviving contract / later owner |
| --- | --- | --- | --- |
| Exact ordered 48-row corpus and raw hashes | `GateBPackageManifestV3`, `validate_gate_b_package_pure_v3`, corpus/package manifests in `gate_b_benchmark_v3.py` | Present, but coupled to the launch package and its roots | `EvidenceManifest.rows` (Task 4) |
| One fixed ordinal per row, with no reordered or duplicated calls | Package validator and `GateBRunnerSummaryV3` count/ordinal validation | Present and tested | `EvidenceManifest.rows[].ordinal`; journal rejects a second terminal result |
| Identity of the bytes actually executed | `GateBRuntimeManifestV3`, `_load_current_runtime_identity_v3`, `recompute_launch_identity_v3`, immutable runtime export | Partial. The old runtime manifest is a real hash, but the declared gateway and benchmark interpreters have already diverged (stdlib SQLite versus the gateway `pysqlite3` shim) | Manifest runtime inventory and Task 4 pre/post verification |
| Python/dependency lock and reproducibility | Runtime export/tree manifests and the copied immutable runtime | Partial. It attributes the copied tree but does not establish that the declared runtime is the one being executed, nor provide a clean rebuild by itself | Manifest artifact/interpreter/distribution/native-library hashes plus a frozen artifact build (Task 4) |
| Model identity | `_derive_launch_authority_sha256s_v3`; provider governance identity | Present, but embedded in launch identity/receipt machinery | `EvidenceManifest.authorities.model_sha256` |
| Prompt identity and version | `build_task10_prompt_v2`, semantic prompt construction, hashes in `_derive_launch_authority_sha256s_v3` | Present | `EvidenceManifest.authorities.prompt_sha256` and version |
| Response-schema identity | `ProviderEvidencePayloadV2.model_json_schema()` and provider request validation | Present | `EvidenceManifest.authorities.response_schema_sha256` |
| Profile / source-authority identity | `_profile_authority_hashes_v3`, package source-authority hashes, `DecisionAuthorityInputsV2` | Present, but the hashes are currently distributed across package, launch and Decision v2 objects | `EvidenceManifest.authorities.profile_sha256` and `source_authority_sha256s` |
| Benchmark policy identity, including the estimand guardrails | `gate_b_benchmark_policy_v3.py` loader; `company_fact_deny_patterns`; `description_claim_admission: reviewed_hash_allowlist_only` | Present. The deny patterns and reviewed allowlist are validity logic, not launch safety; removing them changes the measured question | `EvidenceManifest.authorities.policy_sha256`; policy remains unchanged |
| Decision v2 identity and immutable references | `decision_v2.py` (`DecisionImmutableReferencesV2`, `DecisionAuthorityInputsV2`, `canonical_decision_bytes`, `run_decision_v2`) | Present for Decision v2 inputs/results, but not yet joined to one Gate B row identity | `EvidenceManifest.authorities.decision_v2_sha256`; row decision reference |
| Conservative evidence projection | `project_vacancy_evidence_v3` and `audit_vacancy_projection_v3` in `gate_b_evidence_v3.py` | Present and characterization-tested | `EvidenceManifest.rows[].projection_sha256`; Task 5 keeps this implementation |
| Reviewed description allowlist and company-claim denial | `load_reviewed_fragment_allowlist_v3`, `_compiled_company_fact_deny_patterns`, `_allowed_claims` | Present and load-bearing. It prevents unavailable company authority from entering the description treatment | Do not touch; hash and reference it from the manifest |
| Provider response validation and citation/claim closure | `_runner_response_request_v3`, `validate_provider_payload_v3`, the Task 10 evidence schema and source references | Present for provider payloads; no independent final replay/evaluator contract yet | Recording contract retains raw bytes, validated payload hash and closure diagnostics |
| Sealed raw recordings sufficient for offline deterministic replay | `LLMObservationProvider`/`RecordingStore`, provider `record` payloads, Gate B recording hashes and marker checks | Partial. Bytes and hashes are persisted, but the recording identity is coupled to the old ledger/recording namespace and replay is not yet a standalone Gate B contract | `RecordingStore` contract and immutable sealed-record format (Task 3/5) |
| Per-row corpus → projection → recording → decision identity | Package input hashes, ledger `input_sha256`/`recording_sha256`, runner rows, Decision v2 references | Partial. The links exist, but there is no canonical manifest reference and no required projection/decision hash in every row | `EvidenceManifest.run_id` + manifest hash + ordinal + input/projection/recording/decision hashes |
| Terminal outcome taxonomy | `GateBCallStateV3`, `GateBTerminalKindV3`, `post_dispatch_outcome_v3`, `GovernedStructuredTerminalUnknown` | Present and conservative: post-dispatch ambiguity is terminal and charged at the maximum | Journal outcome enum; no implicit retry from `DISPATCHED` or `TERMINAL_UNKNOWN` |
| Deliverable/unknown/accuracy thresholds | YAML policy fields `minimum_deliverable_results: 43`, `maximum_terminal_unknown: 5`, `minimum_manual_triage_accuracy: 0.80`; `GateBRunnerSummaryV3` publishes counts | Partial. Counts are collected, but no separate deterministic evaluator currently turns all three rules into a machine-readable gate decision; manual accuracy cannot be known during collection | `GateEvaluator` after evidence finalization; metrics are always published, decision may proceed, revise, or refuse |
| Explicit decision rules | Decision v2's eight-step trace and policy rules; current launch code reports a runner summary rather than a Gate B decision | Partial. Decision v2 is deterministic, but the Gate B promotion rules and adjudication-completeness rule are not a separate contract | `GateEvaluator.evaluate` and versioned `GateDecision` |
| No live job-intel database or production mutation during collection | Current runner's approved-path allowlist, no production DB path in the runner, and historical systemd `InaccessiblePaths`/unit controls | Safety boundary, not the validity estimand. The code path is still coupled to launch/namespace controls; a unit-level proof is separate from an offline code proof | Keep as runner/unit safety tests; do not make it a second run identity |
| Provider call/spend cap and conservative accounting | `StructuredCallCapability`, `GovernedPricingSchedule`, `GateBLedgerV3`, exact 48 / USD 0.48 policy, reservation and terminal-unknown cost rules | Present, but implemented by the at-most-once launch protocol rather than a minimal collection journal | Journal contract owns pre-dispatch append, terminal reconciliation, and cap accounting |

## Deliberately excluded from the validity inventory

Ed25519 launch signing, the recovery keypair, root-owned receipts, approval
windows, launch-attempt IDs, and the immutable copied runtime **as a launch
mechanism** are safety/authorization ceremony. They must be covered by the
retirement review, but they are not evidence that the measured result is true.
The runtime's byte identity remains load-bearing and is retained in the
manifest; only the privileged launch witness is retired.
