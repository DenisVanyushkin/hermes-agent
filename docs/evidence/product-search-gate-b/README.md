# Product Search Gate B readiness package

## Outcome

Gate B is **blocked before benchmark execution**. No provider call, recording, Decision v2 result, stage-4 result, delivery decision, or human audit was produced.

The blocker is structural and reproducible: Task 10's `RecordedEvidenceSynthesisProvider` accepts only `LLMObservationProvider(mode="replay")`. It rejects the governed Semantic runtime in `mode="record"`. The existing live Semantic record path emits the Semantic `Observation` schema, hashes the Semantic title/text/structured envelope, and cannot produce Task 10's `ProviderEvidencePayloadV1` record format. No governed compatible record seam exists in candidate commit `bb366575a76122f384ff841bcc4d6c3181e03d5f`.

The task instructions require a stop in this condition. No ad hoc client was added, no Task 10 fixture was reclassified as live evidence, and no synthetic or replay-only result is reported as a real benchmark.

## Exact Gate A input

The acceptance test imports the real package at:

`/home/hermes/.hermes/job_intel/experiments/gate-a/65d60daae16093a9a7e34a11a159e2f789dd14dd`

- manifest SHA-256: `6ecc500c291061a34c4482edb5c2a0d6c547993bea0d346ad306041dfa81df3d`
- run: `gate-a-20260816T141344Z`
- raw observations: 2,414
- corrected canonical current vacancies: 1,814
- minimum-evidence records: 1,314

The 1,314 value is a minimum-evidence denominator. It is **not** a qualified, hard-gate-eligible, selected, deliverable, or stage-4 count.

The test opens SQLite using `mode=ro&immutable=1`, verifies all 2,414 content-addressed evidence files against the database hashes, replays the corrected URL canonicalizer to reproduce 1,814/1,314, and checks the manifest/database inode, size, and mtime before and after the import. The immutable Gate A summary, database, evidence, and manifest were not rewritten.

## RED and fail-closed package

The acceptance test was written before this package. Its first run was `3 failed, 2 passed`; the three expected failures were missing `benchmark-summary.json`. The real Gate A import and the governed record-mode rejection already passed in that RED run.

The completed package records every unavailable metric with a machine-readable `status`, nonempty `reason`, and `null` result instead of substituting zeros as observed outcomes:

- corpus selection: `not_selected`; the selected count is `null`, while 1,314 remains the observed minimum-evidence input denominator;
- stage 4: `not_run`; denominator and outcomes are `null`;
- six dimensions: present individually, each `not_run` with evaluated/outcome results `null`;
- verdicts, unresolved questions, daily eligibility, and urgent eligibility: no evaluated results;
- provider: three observed operational call counters are zero because the blocker was reached before any attempt; failure classification, cost, and latency are `not_computable` and `null`;
- offline replay and Decision v2 trace replay: `not_run`;
- human high-risk/random audit: `not_run`, with factual errors, policy errors, and interpretation disagreements kept as separate fields;
- legacy counterfactual: `not_run`, non-authoritative.

The same rule applies recursively: an unrun/not-computable section cannot contain a numeric zero. Numeric zeros remain only in sections explicitly marked `observed`, namely provider call counters and forbidden side-effect counters.

## Corpus boundary

No corpus was materialized because the next required step—real governed recording—could not run. Materializing and manually labeling a corpus that cannot enter the required record/replay path would create a misleading half-benchmark.

The summary pins the reproducible selection algorithm for the revision run. It starts only from the 1,314 minimum-evidence records reconstructed from immutable Gate A hashes, preserves source IDs and content hashes, and stratifies deterministically by source, Search Contract lane/cell, role-pattern class, company, Open Market origin, apparent hard-block signal, and important unknowns. It does not assign Core or Exploration before Decision v2. Gate A contains no active-thesis Strategic Watchlist candidates, so that origin must be reported as unavailable rather than fabricated.

## Candidate hashes, not an accepted hash set

`benchmark-summary.json` records the exact Decision v2 policy/result schema/code, Career Profile v2, Semantic Contract, Task 10 policy, and provider output schema hashes inspected during readiness. They are candidate identities only. With no real benchmark and no owner decision, they are not an accepted shadow/persistence input set.

## Side effects

No production database/store/outbox, Slack, profile, cache, systemd, runtime, protected source, source configuration, or Product Search state was touched. Slack credentials were not loaded into a benchmark process. No Gate B experiment directory was created because no benchmark state existed to persist. Legacy Job Intel remains masked.

## Recommendation

Owner decision remains `pending`. The recommendation is `request_revision`: add a bounded, reviewed Task 10 record-mode adapter through the existing governed Semantic provider/runtime, then rerun Gate B from this exact Gate A package. Task 13 remains unauthorized.
