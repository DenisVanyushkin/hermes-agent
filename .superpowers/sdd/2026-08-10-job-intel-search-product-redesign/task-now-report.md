# Task 8 re-checkpoint and Gate B snapshot-policy repair

## Outcome

Task 8 is freshly checkpointed and ready for one initial Task 9 launch. Task 9
was not materialized or run during this repair: provider calls are `0`, provider
spend is `USD 0.00`, and no Gate B unit, receipt, runtime, package, process, or
lock exists in a production path.

The earlier materialization preflight failed before package creation at
`_snapshot_protected_paths_v3 -> _read_path_nofollow_v3` with
`source_file_metadata_invalid`. The first protected path was the live
`/home/hermes/.hermes/state.db`, size `1,714,511,872` bytes. The snapshot treated
every non-credential regular file as immutable content and routed the mutable
database through the `16,000,000`-byte immutable-source guard.

## Fix

- `60be781c95756bb2a0f65f816b9da91bf1121b33` — explicit metadata-only policy
  for mutable `state.db`, Job Intel SQLite, WAL, SHM, and credentials; immutable
  config remains content-hashed; snapshot identity adds device and inode.
- `e2fe977ace144aaa668ffd2c24013671091052a4` — deterministic protected inventory
  derived from ordered mutable, credential, and immutable groups, plus an exact
  production-policy invariant test.
- Before/after drift in device, inode, mode, owner/group, size, or mtime fails
  closed with `protected_paths_changed_during_materialization`.
- Immutable protected content still fails at the 16 MB cap.

Strict TDD evidence: the focused RED run produced `5 failed, 1 passed` for the
old behavior; the repaired focused set passed `7/7`, and the complete changed
module passed `113/113`. Independent initial review found no Critical or
Important findings and one Minor inventory-duplication issue; follow-up review
after `e2fe977ace...` was all-clear with no Critical, Important, or Minor
findings.

## Verification

- Runtime: CPython `3.12.13` from the existing pinned uv installation.
- Full Gate B/Product Search matrix: `639 passed in 429.65s` (`432.07s` wall).
- Scoped Ruff lint: passed; exact six-file format check: passed.
- systemd unit verification, shell syntax, Product Search scope guard,
  `git diff --check`, JSON parsing, credential-literal scan, provider-env
  contract, and clean-worktree checks: passed.
- Repo-wide Ruff remains at the pre-existing unrelated baseline of nine
  `PLW1514` findings outside the changed files.
- Provider env: regular single-link `root:root 0400`, exactly `HERMES_HOME` and
  `OPENROUTER_API_KEY`; live SHA-256
  `bd26b7b07417f068efa14ef223108bf953328ccfa4c278c7e122e8ca994ed450`.
- Legacy daily and weekly unit pairs remain masked/inactive.

## Fresh checkpoint identities

- Candidate: `e2fe977ace144aaa668ffd2c24013671091052a4`.
- Package manifest: `6c3cbd6318e8e03ec58118103fd64ec7829fe5ed763837174546a08729f4953e`.
- Runtime tree: `11a30df195c8228fdd428343d9fc2b9d582ceb27744ba7d7b0ed851295c57f34`.
- Runtime manifest: `7b6d44df5b808e62b64c1be0f12926e7a400b721a379902d511599fa5062781e`.
- Launch identity: `9077d6fb446b294afc2fe5d5e919ef036a31068bd900dc9c41493de5306daecc`.
- Owner checkpoint: `6189797524169f43d036fe76c4125cb986197ea588f39f7a568ab3d62a434d78`.
- Launch-ready bundle: `2e7859f0e4410c4037fc740b4b83cb78ab9a745db0e0bb1f5c26dd156f9b4346`.
- Receipt content: `4eccbdf1c149e7e7aab043fe0008afd0bd20792f21898c24d23cb640bb31d8c5`.
- Attempt: `gate-b-at-most-once-6c3cbd6318e8e03e-5fe9e1bd683d0c5a86b41c49f5dc690ddbb3f1418e38905083956bc9e13c16c4`.
- Window: `2026-08-21T11:12:00Z` through `2026-08-21T11:42:00Z`.
- Recovery public-key SHA-256:
  `9435a761328f4c23783099a3edef822d0a9a870337e995bc3598a5dbf105846f`.
- Source file SHA-256:
  `0b7f5189b6339044bd4e7f30e4561d1b643fdd8403f0eaf25dc90f51262712a6`.
- Test file SHA-256:
  `b1cf1a3be5728342e77f38e349f31e2d2a7003f4a11e1f8d44e06523b66a53ff`.

Prospective runtime components are interpreter
`cd6a26a9b2367f36eda6fa4381373d96c96f155b0ef8fae505f9f5e923b1c162`,
stdlib `5d5643de0414ec4446d488aa5c606485f258bd29ed2b9f20c859be3843b88d4a`,
lock `e262172c0285bdfa9b2be095e3a2481dd620593dd440339170fa484cda8602cd`,
69 installed distributions
`8fddf51dcf13d58533601b6ee0578bce2b885c236b4a10eda4f0e7ebb9fc8267`,
and fixed sys.path
`8e63ea541c14f997b9ca4cb6dc417eef216a4003a17b2ad2214f0ad2f274cba8`.

## Paths and decision

- Public checkpoint:
  `docs/evidence/product-search-gate-b/task8-immediate-checkpoint.json` and `.md`.
- Private uninstalled checkpoint artifacts:
  `/tmp/gate-b-task8-fresh-checkpoint-e2fe-20260821T1104Z/`.
- One-time recovery keypair (unchanged, root-only):
  `/var/lib/job-intel/gate-b-at-most-once/owner-recovery/`.

Decision: Task 8 is ready. Gate B has no run result and therefore no promotion,
revision, or rejection decision yet. Exactly one initial Task 9 launch remains
authorized for 48 calls, USD 0.01 maximum per call, and USD 0.48 aggregate,
with no retry or manual redispatch. Task 13, Gate C, Slack delivery, and
production persistence remain unauthorized.
