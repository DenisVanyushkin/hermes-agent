# Product Search Gate B readiness package

## Outcome

The owner-requested revision is ready for a separately authorized record run.
No live provider call or spend occurred, no benchmark metric is reported, the
owner decision remains `pending` with recommendation `request_revision`, and
Task 13 remains unauthorized.

The owner-amended first benchmark no longer treats public company-identity
discovery or a `CompanyEvidenceBundleV1` as an admission gate. All 48 rows now
carry a Task 10 v2 company-authority value of `unavailable` with the narrow
reason `unresolved_company_identity`; there is no official-domain claim,
company fact, or company citation. Task 8/9/10/11 v1 contracts are unchanged.

Task 10 now uses one public structured-call capability on the existing governed
Semantic `LLMObservationProvider`; Product Search no longer reads its private
prompt or transport. The single resumable Gate B runner constructs the live
provider only through that spend-gated factory and accepts neither a caller
provider nor a caller input loader. It supplies the only record capability
after authorization, locks and rehashes the canonical input manifest and every
source before use, and atomically reserves/reconciles calls and spend. Records
pin the exact pricing schedule and 2,000-token output limit, validate provider
usage, recompute cost, use owner-bound metadata HMACs, and persist only closed
failure codes plus sanitized diagnostics. Replay has no transport and rejects
tampered identity, bytes, usage, cost, latency, retry, or decoding metadata.
The ledger distinguishes a safe pre-dispatch `reserved` state from a
post-dispatch `charge_unknown` state. A crash in the latter state cannot
automatically retry a possibly charged call: only owner-governed reconciliation
or replay of an already sealed record can close it.

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

The additive hardened input package is
`input-package-v2-r2/run-manifest.v2.json` beneath that exact corpus root. Its
SHA-256 is `9dd9261c6359d1cd3c899b5df8c85ef0526aea78f83b985d0a1822436f3b5987`;
it orders 48 unique Task 10 v2 input hashes and 48 content-addressed vacancy
artifacts. The ordered allowlist digest is
`32b2d546aa0312ecb42bb65aadbf2a913277cfbb676d21d07f542dbd26b6b89a`.
Every admitted vacancy fragment is an exact bounded substring of the pinned
raw artifact. Exact fragments containing the unavailable company label are
retained in the vacancy artifact for provenance but excluded from the provider
allowlist; their text hashes are sealed in each input and rejected as claims in
every dimension. The exclusion set is a required v2 field, so omitting it
cannot silently restore permissive behavior. Company labels and URLs are not
provider authority.

## Record authorization boundary

The prepared and exact call cap is 48; the exact spend cap is USD 0.48, with a
conservative USD 0.010000 reservation per call. Authorization fails closed
unless the owner supplies an opaque capability whose hash is bound to the exact
corpus/run identity, pinned pricing (`975559f7...eb3e`), exact caps, prompts,
schemas, model, profile, and contracts. No deterministic/self-generated token
is accepted or recorded. This revision did not supply a capability or authorize
a run.

## TDD and side effects

The Task 12B behavioral and mutation test was run before production changes and
failed exactly 12 tests: the v2 authority union and Assessment contract were
absent, no 48-input package/validator existed, the allowlist was not bound, and
the public runner still accepted a provider and loader. After the minimal v2
path was added, the same file passed all 12 tests.
Two follow-up path mutations brought that file to 14 passing tests. Independent
review then produced four Important and two Minor findings. The fix-round REDs
were: one failure for an ambiguous post-dispatch crash; three failures for
cross-dimension company claims, package materialization, and v2 schema identity;
eight failures for actual I/O denial and ledger/recording/package symlink or
path-swap safety; one failure showing the stale approval-env test entered
provider construction; and one self-review failure for a child-thread boundary
bypass. No provider API key was present and no provider call completed.

The final focused fix suite passed 99 tests, the complete Product Search plus
governed-call and Decision/acquisition acceptance verification passed 428
tests, and the isolated acceptance/replay gates passed 12 tests. Ruff lint was
clean for all eight changed Python files; the two dedicated Gate B test files
are Ruff-formatted. The Product Search scope guard, whitespace check, and
redaction scan passed.

The fresh dry preflight ran with Slack token variables removed. A process audit
hook enforced the boundary at actual file, SQLite, socket, subprocess, exec,
link, rename, and mutation operations; tests prove direct socket, subprocess,
and outside-root writes are denied without relying on voluntary counters. It
measured 2,416 Gate A file reads, zero provider/network/Slack-credential
attempts, and zero production/protected/runtime writes; immutable before/after
snapshots matched. Descriptor-relative `O_NOFOLLOW` traversal and atomic
publication anchor the corpus, package, recording store, and ledger against
symlink and path-swap races. The first Task 12B run created only the 97
approved package files beneath the canonical corpus root; its repeat creates
zero files. No production database/store/outbox/profile/
cache, systemd unit, scraper, source configuration, protected source, or Gate A
artifact was changed. Legacy remains non-authoritative.

## Remaining Gate B work

A future, separately authorized invocation of the single Gate B runner must capture all 48 Task 10 responses,
replay every response fully offline, execute deterministic Decision v2 for both
paths, compare exact traces, report all stage-4 and six-dimension denominators,
provider failures/cost/latency, delivery and urgency eligibility, and complete
the high-risk plus deterministic-random human audit. Until then every result
field in `benchmark-summary.json` remains explicitly `not_run` or
`not_computable`, never a fabricated zero.
