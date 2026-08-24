# Gate B Task 10 retirement review

Status: implementation review, after Task 9b and before the authorized 48-call
run. The legacy v3 launch fortress is removed; the older `gate_b.py` corpus and
evidence loader remains because `gate_b_evidence_runner_v1.load_gate_b_corpus_rows`
still reuses its source/projection validation helpers.

## Retired mechanisms and surviving owners

| Validity property | Surviving implementation | Surviving test | Status |
| --- | --- | --- | --- |
| Ordered 48-row corpus and raw hashes | `EvidenceManifest.rows`, `load_gate_b_corpus_rows` | `tests/product_search/test_gate_b_evidence_skeleton.py`, `test_gate_b_collection_runner.py` | closed |
| Unique contiguous ordinal | `EvidenceManifest` validation and journal row refs | `test_gate_b_evidence_skeleton.py`, `test_gate_b_collection_runner.py` | closed |
| Bytes actually executed | `RuntimeIdentity`, assembled artifact whole-tree hash, manifest binding | `test_gate_b_runtime_artifact.py`, deploy unit tests | closed by Tasks 4/9b |
| Reproducible Python/dependency runtime | `gate_b_runtime_v1.build_assembled_artifact` and frozen venv | `test_gate_b_runtime_artifact.py` | closed by Tasks 4/9b |
| Model, prompt, schema, profile and source authority identity | `EvidenceManifest.authorities` | `test_gate_b_evidence_skeleton.py`, `test_gate_b_evidence_v3.py` | closed |
| Policy identity and estimand guardrails | `gate_b_benchmark_policy_v3.py`, deny patterns, reviewed allowlist | `test_gate_b_evidence_v3.py` | intentionally retained |
| Decision v2 identity and immutable output | explicit policy in runner, `DecisionEvidenceStore` | `test_gate_b_evidence_skeleton.py`, `test_gate_b_recording_replay.py` | closed by Task 7 |
| Conservative projection and provider validation | `gate_b_evidence_v3.py` projector/validator | `test_gate_b_evidence_v3.py` | closed |
| Sealed recordings and offline replay | `RecordingStore`, manifest-bound replay | `test_gate_b_recording_replay.py` | closed by Task 6 |
| Corpus → projection → recording → decision identity | `ManifestRef`, row refs and artifact hashes | `test_gate_b_collection_runner.py`, `test_gate_b_recording_replay.py` | closed by Tasks 3/6/7 |
| Terminal outcome taxonomy | `TerminalOutcome`, journal and collection report | `test_gate_b_evidence_skeleton.py`, `test_gate_b_collection_runner.py` | closed |
| 43 / 5 / 0.80 thresholds | pure `GateEvaluator` with complete/incomplete states | `test_gate_b_evidence_skeleton.py` | closed by Task 8 |
| Explicit proceed/revise/refuse rules | `GateEvaluator` and `GateDecision` | `test_gate_b_evidence_skeleton.py` | closed by Task 8 |
| No live DB / production mutation during collection | runner import/code-path boundary plus systemd `InaccessiblePaths` | `test_gate_b_evidence_skeleton.py`, `test_gate_b_description_evidence_unit.py` | closed as two independent proofs |
| Call and spend cap | durable journal and governed dispatch capability | `test_gate_b_evidence_skeleton.py`, `test_gate_b_collection_runner.py` | closed by Task 5 |

## Retired ceremony

`gate_b_benchmark_v3.py` and its v3 tests are deleted. This removes package and
launch identity manifests, root-owned pending/consumed receipts, recovery keys,
owner approval windows, Ed25519 launch signing, the old ledger/receipt protocol,
and the legacy immutable-runtime launch path. The export shell script survives
only as a wrapper around `gate_b_runtime_v1 build-artifact`; it is a build
mechanism, not authorization.

The old acceptance artifact `tests/acceptance/test_decision_v2_gate.py` was also
retired: it asserts the historical handover summary, which now records the
observed pre-`ExecStartPre` namespace failure rather than the obsolete
launch-ready status. Its failures were handover drift, not a surviving validity
property.

`gate_b.py` is deliberately not deleted. It is the older corpus/evidence module
and remains the loader dependency for the real collection runner; copying those
helpers into a new module would duplicate validity logic rather than retire
ceremony.

## Unclassified findings

No additional unclassified production behavior was found after the Task 9b
producer audit. The only unexpected dependency was the surviving corpus loader
reuse of `gate_b.py`; it was checked before deletion and retained deliberately.

## Stale runtime cleanup

A fresh pre-delete `ls` identified exactly six removable trees (the live
`immutable-runtime` directory was listed separately and was not a target):

```text
immutable-runtime.failed-00598e-uv-path-20260821T1220Z       304M
immutable-runtime.failed-725a-alias-20260821T1255Z           333M
immutable-runtime.stale-00598e26c739cd920-db747c68           308M
immutable-runtime.stale-3536dc19925252fc-a9130d36-symlink    308M
immutable-runtime.stale-56fdd5ae3192ba4e-c9d91571            308M
immutable-runtime.stale-e2fe977ace144aaa-7b6d44df            308M
```

The two failed trees were removed by the initial exact cleanup; the four
read-only stale trees required `sudo -n rm -rf` after their paths were
re-checked. A post-delete `ls` shows only the live `immutable-runtime` (524M),
which is retained until the execution source migration is independently
confirmed.
