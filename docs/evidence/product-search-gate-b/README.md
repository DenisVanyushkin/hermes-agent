# Product Search Gate B readiness package

## Outcome

The owner-requested revision is prepared but **not live-launchable**. No live
provider call or spend occurred, no benchmark metric is reported, the owner
decision remains `pending` with recommendation `request_revision`, and Task 13
remains unauthorized. A live invocation stays fail-closed until a separate
owner decision installs the exact process-specific root-owned launch witness
described below; this task did not create or install that witness.

The owner-amended first benchmark no longer treats public company-identity
discovery or a `CompanyEvidenceBundleV1` as an admission gate. All 48 rows now
carry a Task 10 v2 company-authority value of `unavailable` with the narrow
reason `unresolved_company_identity`; there is no official-domain claim,
company fact, or company citation. Task 8/9/10/11 v1 contracts are unchanged.

Task 10 now uses one public structured-call capability on the existing governed
Semantic `LLMObservationProvider`; Product Search no longer reads its private
prompt or transport. The single one-shot Gate B runner constructs the live
provider only through that spend-gated factory and accepts neither a caller
provider, caller input loader, nor caller-selected experiment/package root. It
supplies the only record capability after the privileged launch claim, locks
and rehashes the canonical input manifest and every source before use, and
atomically reserves/reconciles calls and spend. Records
pin the exact pricing schedule and 2,000-token output limit, validate provider
usage, recompute cost, use owner-bound metadata HMACs, and persist only closed
failure codes plus sanitized diagnostics. Replay has no transport and rejects
tampered identity, bytes, usage, cost, latency, retry, or decoding metadata.
The ledger distinguishes a safe pre-dispatch `reserved` state from a
post-dispatch `charge_unknown` state. The first benchmark is deliberately
non-auto-resumable: any authority, journal, sidecar, receipt, recording,
partial, or in-flight state beneath the fixed r3 namespace blocks a new record
authorization. A separately owner-approved reconciliation artifact must bind
the complete current namespace inventory before offline-only reconciliation
can close a possibly charged call. Historical checkpoints cannot restart it.

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

The last live materialized hardened input package remains
`input-package-v2-r2/run-manifest.v2.json` beneath that exact corpus root. Its
SHA-256 is `9dd9261c6359d1cd3c899b5df8c85ef0526aea78f83b985d0a1822436f3b5987`;
it orders 48 unique Task 10 v2 input hashes and 48 content-addressed vacancy
artifacts. The ordered allowlist digest is
`32b2d546aa0312ecb42bb65aadbf2a913277cfbb676d21d07f542dbd26b6b89a`.
The next dry preflight materializes the byte-exact inputs under
`input-package-v2-r3`; this task did not mutate the live experiment root. The
r3 record-state identity supersedes r1/r2 package and test-only ledger state;
those artifacts are never migrated into an authorized r3 journal.
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
is accepted or recorded. The r3 authorizer derives one canonical namespace
internally:

`/home/hermes/.hermes/job_intel/experiments/gate-b/b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69/input-package-v2-r3`

It rejects sibling copies and root/package substitution. Before state bind,
provider construction, and every provider call, it descriptor-scans the whole
fixed corpus namespace without following links. The allowed plan is exact:
the corpus manifest, the 48 Task 10 inputs, 48 vacancy artifacts, the r3 run
manifest, the current r3 authority pair/journal, and precisely the recordings
already produced by this one runner. Any legacy r1/r2/r3 authority, ledger,
receipt, SQLite sidecar, unknown file, malformed entry, extra hardlink, or
unplanned recording fails closed.

Authorization itself is read-only. It issues one random, non-serializable
capability bound to the current PID, process start identity, run identity, and
one runner instance. The complete expected witness bytes are immutably bound
inside that capability before authorization returns; `claim()` accepts no
caller-supplied witness or issuer. State cannot bind until the capability reads
and matches the exact privileged launch witness itself. The fixed witness
contract is:

- path:
  `/var/lib/job-intel/gate-b-launch-witness/r3-launch.json`;
- immediate parent: root-owned directory with no group/world write access;
- witness: root-owned regular file, exactly one hardlink, no group/world write
  access, and byte-canonical JSON;
- payload: exact schema/kind, run/corpus/input identities, canonical roots,
  initial full-inventory digest, random runner-instance digest, PID, process
  start identity, and `one_shot_non_resumable` mode.

The witness must be installed separately by an owner-approved privileged
operation. It is process-specific and cannot be reused by a replacement runner
after same-UID deletion or rollback of local state. This task deliberately did
not install it, so live launch remains unavailable. A future installation is a
separate operational change and must not be inferred from this preparation
commit.

If the one-shot process leaves state, `build_gate_b_owner_reconciliation_request`
produces an exact inventory-bound request. Appending that request to the old
approval is insufficient: a separately owner-installed, root-owned canonical
copy must exist at the fixed path
`/var/lib/job-intel/gate-b-launch-witness/r3-reconciliation.json`, under the
same parent/file ownership, mode, regular-file, and single-hardlink checks as
the launch witness. Only `authorize_gate_b_reconciliation` with the complete
artifact, matching privileged copy, and owner capability may reopen the
existing authority. That authorization has no runner capability, is rejected
by the live runner, cannot issue a generic record capability, cannot reserve or
dispatch another call, and can only perform the governed terminal offline
reconciliation. This revision did not supply an owner capability, install or
approve either privileged artifact, or authorize a run.

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

The final-breaker round began with 13 failing controls for early state binding,
sibling roots, historical restart, late recording discovery, manifest
hardlinks, missing process/witness controls, and unplanned namespace artifacts.
The exact-state reconciliation contract then produced two RED failures, and the
first-bind ordering control produced one more RED failure before the minimal
implementation changes. The final security review added five more RED controls
and then one stale-witness RED, all closed before commit. The complete
record-control file now passes 79 tests;
the complete Product Search plus governed-call and Decision/acquisition
acceptance verification passes 481 tests; and the isolated acceptance/replay
gates pass 12 tests. Ruff lint is clean for all three changed Python files, and
the two dedicated Gate B test files are Ruff-formatted. The Product Search scope
guard, JSON validation, whitespace check, and credential-value review pass.
Bandit is not installed in the project virtual environment, so no Bandit result
is claimed.

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

A future owner decision must first approve and separately install the exact
root-owned launch witness for one freshly authorized process. Only then may a
separately authorized invocation of the single Gate B runner capture all 48 Task 10 responses,
replay every response fully offline, execute deterministic Decision v2 for both
paths, compare exact traces, report all stage-4 and six-dimension denominators,
provider failures/cost/latency, delivery and urgency eligibility, and complete
the high-risk plus deterministic-random human audit. Until then every result
field in `benchmark-summary.json` remains explicitly `not_run` or
`not_computable`, never a fabricated zero.

## V3 benchmark policy replacement scope

The additive v3 at-most-once benchmark policy governs only future Gate B
execution. It neither rewrites nor authorizes historical packages, journals,
or results. This preparation remains non-live: `record_run_authorized=false`.

## Task 8 v3 owner checkpoint request (2026-08-21)

The independently reviewed code candidate is
`21938df34b6a9976fddc27a80d008d4f60e76c6d`.  It is the code pin; this
evidence-only commit follows it and does not replace the runtime candidate.
The historical Task 12B commit
`c7981218a03e616da8e4d60084c0bfb9633ca892` is pre-rebase.  Its
patch-equivalent rebased base is
`b24c5f3f11c56a5705dab537bf63baf359a15b04` (identical stable patch-id
`400e78ad23e34a89a172553c8853ed5b0904a8b2` and identical changed paths),
and the complete 17-file feature delta from that base was reviewed.

No Critical or Important finding remains within the stated trust model.  The
review rechecked the six earlier blockers: only independently reviewed role
responsibility/requirement fragments can be admitted; owner recovery is
mandatory for every stranded `reserved`/`dispatched` row; ambiguous
post-dispatch outcomes are terminal, non-retryable, and conservatively charged
USD 0.01; the namespace is fixed and separate from historical Gate B; every
runtime/package/source/prompt/schema/model/pricing/projection identity is
recomputed from the current immutable export; and the public pure preflight
accepts only plain byte mappings with no path, callback, provider, or I/O
capability.  State transitions, dispatch-marker/ledger/recording disagreement,
torn state, exact recovery inventory/signature binding, one-shot receipt
claiming, and recovery launch identities were mutation-checked.  A crash after
claim but before ledger creation consumes that attempt but cannot dispatch; a
fresh initial attempt is allowed only while no ledger exists.  Once a ledger
exists, a new launch requires the exact owner-signed recovery manifest.

The pure builder computed, without canonical-host materialization:

- prospective package manifest SHA-256:
  `6c3cbd6318e8e03ec58118103fd64ec7829fe5ed763837174546a08729f4953e`;
- prospective package root:
  `/home/hermes/.hermes/job_intel/experiments/gate-b-at-most-once/6c3cbd6318e8e03ec58118103fd64ec7829fe5ed763837174546a08729f4953e`;
- 48 ordered input-hash digest:
  `a952f9188fb47f06f6cdb6925e48123ecb6da6eed4b24cf472f16530dfea736f`;
- 48 ordered projection-hash digest:
  `8c89aa27f3b48b464cb408d34b51bc7739b11d3a1f25cefd4b92178f64a8f243`;
- prospective immutable runtime manifest SHA-256:
  `03278316997f454b722ef5a8a9f52ca752f88a8687eaa63fa3840931beaf4cee`;
- full launch identity SHA-256:
  `7dc08dc11e93fe97ad0f682da51a198f327c20691f2b8774cc0ef1c8351471b3`;
- one-shot unit SHA-256:
  `95ef9ce28e32dc96a469ff539c2dd897d75ccd921ce3837982b8f7599109272c`.

The prospective runtime manifest binds Python `3.12.13`, runtime tree
`f0c5aa64693bd746fee8e4628cb27437147358531a9ec5dd6fa26e679d8bbdde`,
interpreter
`cd6a26a9b2367f36eda6fa4381373d96c96f155b0ef8fae505f9f5e923b1c162`,
stdlib tree
`9575d6493bc5817e49175e251b9825125e771b21ecb3576feb180601872ad078`,
lock file
`e262172c0285bdfa9b2be095e3a2481dd620593dd440339170fa484cda8602cd`,
69 non-editable installed distributions
`8fddf51dcf13d58533601b6ee0578bce2b885c236b4a10eda4f0e7ebb9fc8267`,
and fixed `sys.path`
`8e63ea541c14f997b9ca4cb6dc417eef216a4003a17b2ad2214f0ad2f274cba8`.
Task 9 must reproduce the canonical runtime manifest byte-for-byte at
`/home/hermes/.hermes/job_intel/experiments/gate-b-at-most-once/immutable-runtime`;
any mismatch voids approval.

The launch binds `openai/gpt-5-mini`, pricing identity
`openrouter-openai-gpt5-mini-2026-08-17` (USD 0.25/M input tokens, USD 2.00/M
output tokens), maximum 24,000 input and 2,000 output tokens, exactly 48 calls,
USD 0.01 maximum per call, and USD 0.48 aggregate.  The owner-checkpoint
manifest offered for approval is
`80590c6457b74316107aa4f694816cef327f0e9478eb8175b0a1bd3f58de6e5d`
and becomes effective at `2026-08-21T07:50:00Z` only if explicitly approved
before that instant.  Its initial root-owned receipt is:

- pending path:
  `/etc/job-intel/gate-b-at-most-once/gate-b-at-most-once-6c3cbd6318e8e03e-3ff636e021976407c5bf78ba1b890d9d190d7e32d0946cc7c05f69a9cd0cc1da/launch.pending.json`;
- canonical content SHA-256:
  `f0080a3cdda9b35d36639a2d37883ee4ed44f890e6e8f3822a25c28f3081a35c`;
- validity: `2026-08-21T07:50:00Z` through `2026-08-21T08:20:00Z`
  (12:50–13:20 Asia/Almaty), exactly 30 minutes;
- pending ownership/mode `root:root 0400`; consumed ownership/mode
  `root:hermes 0440` under the matching attempt directory in
  `/run/job-intel/gate-b-at-most-once`.

Approval received at or after `2026-08-21T07:50:00Z`, launch outside the stated
window, or any candidate/runtime/package/checkpoint/receipt/unit hash drift
voids this request and requires a newly reviewed checkpoint.  No v3 package,
runtime, receipt, unit, provider call, network call, or spend exists yet.

The only state/output root is
`/home/hermes/.hermes/job_intel/experiments/gate-b-at-most-once/runs/6c3cbd6318e8e03ec58118103fd64ec7829fe5ed763837174546a08729f4953e/gate-b-at-most-once-6c3cbd6318e8e03e`.
It contains `ledger/ledger.jsonl`, `provider-recordings/`, the per-attempt launch
claim, and `summary.json`.  Historical
`/home/hermes/.hermes/job_intel/experiments/gate-b` remains read-only and
non-authoritative.  The legacy daily and weekly Job Intel service/timer pairs
were freshly verified `masked` and `inactive`; no new Gate B unit is installed.

If Task 9 fails after privileged installation, the bounded rollback is:

```bash
sudo systemctl stop job-intel-gate-b-benchmark.service
sudo systemctl disable job-intel-gate-b-benchmark.service
sudo rm -f /etc/systemd/system/job-intel-gate-b-benchmark.service
sudo systemctl daemon-reload
sudo rm -rf /etc/job-intel/gate-b-at-most-once/gate-b-at-most-once-6c3cbd6318e8e03e-3ff636e021976407c5bf78ba1b890d9d190d7e32d0946cc7c05f69a9cd0cc1da
sudo rm -rf /run/job-intel/gate-b-at-most-once/gate-b-at-most-once-6c3cbd6318e8e03e-3ff636e021976407c5bf78ba1b890d9d190d7e32d0946cc7c05f69a9cd0cc1da
sudo systemctl mask job-intel-daily.timer job-intel-daily.service job-intel-weekly-kpi.timer job-intel-weekly-kpi.service
```

The immutable package, runtime, ledger, recordings, and summary are retained as
evidence; deleting them requires a separate retention decision.  Rollback does
not restart any legacy collector.

Fresh pre-checkpoint verification used the existing Gate A Python `3.12.13`
without creating or mutating a venv: 632 tests passed in 404.62 seconds; Ruff
lint passed; all six named files were already Ruff-formatted; the scope guard,
systemd unit verification, shell syntax, `git diff --check`, JSON parsing,
credential/private-marker review, and clean-worktree check passed.  Credential
literal scanning found no credential value; the only email literal was
`test@example.invalid`, and fixed private source paths contain no private
payload.  The legacy exporter process is read-only observability and is not a
collector.
