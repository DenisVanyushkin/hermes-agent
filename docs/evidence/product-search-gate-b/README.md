# Product Search Gate B readiness package

## Outcome

The owner-requested revision is ready for a separately authorized record run.
No live provider call or spend occurred, no benchmark metric is reported, the
owner decision remains `pending` with recommendation `request_revision`, and
Task 13 remains unauthorized.

Task 10 now has a bounded record/replay adapter over the existing governed
Semantic `LLMObservationProvider`. The live factory delegates to the existing
Semantic spend-gated factory; it does not introduce a client. A recording is
keyed by the same Task 10 input envelope used by replay and pins the provider,
served model, Semantic prompt, Task 10 prompt, output schema, input and response
hashes, status/failure, usage, computable cost, latency, and retry count.
Replay has no transport and rejects tampered bytes, input, model identity,
prompt identity, schema identity, or status metadata.

## Exact Gate A input and corpus

The dry preflight opened only the immutable Gate A package at
`/home/hermes/.hermes/job_intel/experiments/gate-a/65d60daae16093a9a7e34a11a159e2f789dd14dd`
with SQLite `mode=ro&immutable=1`. It verified manifest SHA-256
`6ecc500c291061a34c4482edb5c2a0d6c547993bea0d346ad306041dfa81df3d`,
run `gate-a-20260816T141344Z`, commit
`65d60daae16093a9a7e34a11a159e2f789dd14dd`, every raw evidence hash,
and the exact 2,414 raw / 1,814 corrected canonical / 1,314 minimum-evidence
counts. The 1,314 denominator is not a qualified count.

The deterministic coverage-first stratified round-robin algorithm selected 48
records and materialized
`/home/hermes/.hermes/job_intel/experiments/gate-b/b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69/corpus-manifest.json`.
Its byte SHA-256 is the directory name. The manifest preserves source IDs and
raw hashes and covers 9 lanes, 6 source families, 7 role patterns, 37 companies,
Open Market origin, hard-block and important-unknown hypotheses, and likely
Core/Exploration sampling hypotheses. Those hypotheses are sampling strata,
not Decision v2 outputs. Gate A contains no Strategic Watchlist evidence.

## Record authorization boundary

The prepared call estimate is 48. The maximum is bounded at USD 0.01 per call
and USD 0.48 total. Authorization fails closed unless the operator supplies the
exact corpus, Decision/Search contracts, profile, Task 10 policy/schema,
Semantic prompt, and model identity hashes, plus the exact explicit approval
token, a call cap of at least 48, and a spend cap of at least USD 0.48. This
revision generated only the hash of that token; it did not authorize a run.

## TDD and side effects

The first Task 10 record test failed during collection because the schema
identity function and record seam did not exist. The first Gate B runner test
failed because the module did not exist. After implementation, the focused
Task 10 and runner suite passed 44 tests. Acceptance was then changed first and
failed 3 of 8 tests against the old blocked documents before the evidence
package was updated.

The fresh dry preflight ran with Slack token variables removed and recorded
zero provider calls, network enablement, Slack credential access/calls,
production writes, runtime mutations, and Gate A mutations. It wrote only the
content-addressed Gate B corpus. No production database/store/outbox/profile/
cache, systemd unit, scraper, source configuration, protected source, or Gate A
artifact was changed. Legacy remains non-authoritative.

## Remaining Gate B work

A future, separately authorized run must capture all 48 Task 10 responses,
replay every response fully offline, execute deterministic Decision v2 for both
paths, compare exact traces, report all stage-4 and six-dimension denominators,
provider failures/cost/latency, delivery and urgency eligibility, and complete
the high-risk plus deterministic-random human audit. Until then every result
field in `benchmark-summary.json` remains explicitly `not_run` or
`not_computable`, never a fabricated zero.
