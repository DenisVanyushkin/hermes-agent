# Task 8 final checkpoint and Task 9 one-shot launch report

## Outcome

Task 8 was rebuilt from the independently reviewed code candidate
`8b46e07a2bf3c445eafae5e6684bbdeb52c73bf8`, verified, materialized, and bound
to a fresh 30-minute owner-authorized receipt. Task 9 then issued exactly one
new `systemctl start` at `2026-08-21T13:38:18Z`.

The start failed closed before the `ExecStartPre` command executed and before
the receipt was consumed. systemd returned `226/NAMESPACE` while resolving
`ReadWritePaths=/run/job-intel/gate-b-at-most-once`; that path did not exist.
There was no `ExecStart`, provider transport, ledger, recording, summary,
process, run lock, Slack call, or production write. Provider calls are `0/48`
and spend is `USD 0.00`.

The at-most-once rule forbids retry or manual redispatch. No deployment patch
or second start was attempted. The installed unit and unconsumed receipt were
removed, the newly created empty `runs` parent was removed with `rmdir`, and
the unit now reports `not-found/inactive/dead`.

## Exact failure boundary and cause

- Start: `2026-08-21T13:38:18Z`, exactly one start for this checkpoint.
- Control process: PID `981334`, `status=226/NAMESPACE`.
- Journal evidence:
  `Failed to set up mount namespacing: /run/job-intel/gate-b-at-most-once: No such file or directory`.
- Unit ordering: systemd constructs the mount namespace for all
  `ReadWritePaths` before executing the root `ExecStartPre` receipt consumer.
- Installer coverage: the root installer prepared only the fixed
  `/home/hermes/.hermes/job_intel/experiments/gate-b-at-most-once/runs`
  namespace parent. It did not prepare the separate fixed `/run/...` parent.
- Consequence: receipt consumption and transport were unreachable. The receipt
  remained `launch.pending.json` until the exact teardown removed it.

This is a deployment-contract gap, not a provider failure and not an ambiguous
dispatch. It requires a separately reviewed fix and a new checkpoint/receipt;
the failed attempt cannot be reused.

## Strict TDD repairs included in the candidate

The final candidate contains the bounded materialization/runtime repairs made
before this launch:

- `60be781c95756bb2a0f65f816b9da91bf1121b33`: mutable production databases,
  WAL/SHM, and credentials use fail-closed metadata snapshots; immutable
  source/config remains fully content-hashed under the 16 MB cap.
- `e2fe977ace144aaa668ffd2c24013671091052a4`: deterministic protected-path
  inventory and exact production-policy invariant.
- `56fdd5ae3192ba4efa962d7ac38719967281e732`: trusted immutable CPython
  executable receives only the explicit 64 MB content-read limit.
- `deaa1264eaa5db0a3aaeb8289f4efd6a3755d0cc` and
  `00598e26c739cd920dfba89e4630a2aa16e2b489`: fixed `runs` namespace-parent
  preparation and root-only immutable unit installation.
- `112046fa030b98e63cbe2cf97a8bae6c12e81b10`,
  `725a464d098e86a71866d109720bc20b90332e77`,
  `3536dc19925252fc49d22044824faa3c38d21cda`, and
  `8b46e07a2bf3c445eafae5e6684bbdeb52c73bf8`: validate uv-created interpreter
  aliases, perform `uv sync` and freeze while those aliases are intact, then
  atomically regularize `python`, `python3`, and `python3.12` before final
  manifest/self-checks.

The production-shaped export proves all three venv aliases are regular,
single-link, mode `0555`, and byte-identical to pinned CPython SHA-256
`cd6a26a9b2367f36eda6fa4381373d96c96f155b0ef8fae505f9f5e923b1c162`.

## Verification and independent review

- Existing pinned runtime: CPython `3.12.13`.
- Exact Task 8 matrix: `647 passed in 406.86s`.
- Related exporter/deployment matrix: `127 passed`.
- Ruff check and exact-file format check: passed.
- Shell syntax, `systemd-analyze verify`, Product Search scope guard,
  `git diff --check`, JSON parsing, and credential-literal scan: passed.
- Independent review of candidate `8b46e07...`: all-clear; no Critical,
  Important, or Minor findings.
- Feature and main worktrees: clean after the evidence commit.
- Legacy daily/weekly services and timers: masked and inactive.

## Fresh Task 8 identities

- Candidate: `8b46e07a2bf3c445eafae5e6684bbdeb52c73bf8`.
- Public checkpoint commit: `e7181561e56abb5bb906bb03cd925f81b4eada3c`.
- Package manifest:
  `6c3cbd6318e8e03ec58118103fd64ec7829fe5ed763837174546a08729f4953e`.
- Runtime tree:
  `65be976e51e71c8e981ae34801622df98842ed2687f994d4b92f1fda98c74a3f`.
- Runtime manifest:
  `eeeaabcaa342429c8ea70ee9b62e39d0bf4a7c732a2b17ab17ea1ff4917c30c8`.
- Launch identity:
  `184cfc0516daf53e62af8a8418d6573d2fad8cbff23935ddb69ebebdb0585fd9`.
- Owner checkpoint:
  `16effec614ef2bf33eb734eda95a3a21cf2b08b9b9eb137957660b44af4d03f2`.
- Launch-ready bundle:
  `90be50e4ce5d560d54ccf72cb3cf94e4f5b37dca8a595882f96a3597fc5615dc`.
- Receipt content:
  `1942dbafe8b59e2db605373582738d911d2ba3e03c395aeb1c95e6dfdd138e5e`.
- Attempt:
  `gate-b-at-most-once-6c3cbd6318e8e03e-352f1f797ebea910629550fdbc0dc59287321af5ada856db6c2663bbf0b57e20`.
- Window: `2026-08-21T13:36:00Z` through `2026-08-21T14:06:00Z`.
- Provider env live SHA-256:
  `bd26b7b07417f068efa14ef223108bf953328ccfa4c278c7e122e8ca994ed450`.
- Recovery public-key SHA-256:
  `9435a761328f4c23783099a3edef822d0a9a870337e995bc3598a5dbf105846f`.
- Unit SHA-256:
  `95ef9ce28e32dc96a469ff539c2dd897d75ccd921ce3837982b8f7599109272c`.
- Installer SHA-256:
  `071a624ab3df6300371ba31384ad1123a586e2ecca177c105a598dcb142f98a7`.
- Runner SHA-256:
  `9d3c7f29d8317ef8cce6d7f0067e13f01f95b82aa27046ce95c04edd6ab23d7c`.
- Exporter SHA-256:
  `cdffa41d7595fbc68e8a2a9cbe033c7ad8ff1aabef453b9fc81b818637b0d51e`.

Runtime components remain interpreter `cd6a26a9...`, stdlib
`5d5643de0414ec4446d488aa5c606485f258bd29ed2b9f20c859be3843b88d4a`,
lock `e262172c0285bdfa9b2be095e3a2481dd620593dd440339170fa484cda8602cd`,
distributions
`8fddf51dcf13d58533601b6ee0578bce2b885c236b4a10eda4f0e7ebb9fc8267`,
and sys.path
`8e63ea541c14f997b9ca4cb6dc417eef216a4003a17b2ad2214f0ad2f274cba8`.

## Preserved and removed paths

Preserved read-only evidence:

- Package:
  `/home/hermes/.hermes/job_intel/experiments/gate-b-at-most-once/6c3cbd6318e8e03ec58118103fd64ec7829fe5ed763837174546a08729f4953e`.
- Runtime:
  `/home/hermes/.hermes/job_intel/experiments/gate-b-at-most-once/immutable-runtime`.
- Private checkpoint:
  `/tmp/gate-b-task8-fresh-checkpoint-8b46-20260821T1326Z/`.
- Root-only recovery keypair:
  `/var/lib/job-intel/gate-b-at-most-once/owner-recovery/`.
- Provider env: `/etc/job-intel/gate-b-provider.env`, root-owned `0400`, exact
  two-key contract; secret value is not present in repository evidence.

Removed after the pre-transport failure:

- `/etc/systemd/system/job-intel-gate-b-benchmark.service`;
- `/etc/job-intel/gate-b-at-most-once/` and its unconsumed pending receipt;
- `/home/hermes/.hermes/job_intel/experiments/gate-b-at-most-once/runs` after
  proving it empty.

`/run/job-intel/gate-b-at-most-once` never existed. There is no consumed
receipt, attempt run root, ledger, provider-recording directory, summary,
benchmark process, or attempt lock.

## Metrics and Gate B decision

- Provider calls attempted/succeeded/failed: `0/0/0`.
- Provider spend: `USD 0.00` of `USD 0.48` authorized.
- Terminal benchmark records: `0`; unstarted inputs: `48`.
- Deliverable/success threshold `>=43`: not met because execution never began.
- Terminal-unknown threshold `<=5`: not evaluable as a quality gate without a
  run.
- Manual-triage accuracy threshold `>=0.80`: not computable.
- Offline replay, Decision v2 comparison, and human audit: not run because no
  provider recordings exist.
- Forbidden side effects: `0` production DB/store/outbox/profile/cache writes,
  `0` Slack calls, and `0` Task 13/Gate C actions.

Gate B recommendation is `request_revision`. There is no promotion result. The
deployment contract must be repaired and re-reviewed before the owner can
authorize a wholly new checkpoint and attempt. This report does not authorize
a retry, Task 13, Gate C, Slack delivery, production persistence, legacy-unit
restoration, or any reuse of the failed receipt/window.
