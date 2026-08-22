# Upstream-sync invariant replay methodology (2026-08-22)

The replay corpus is stored in `tests/fixtures/upstream_sync/replay_9f3feebcd3.json`.
For a merge OID and path, `scripts/upstream_sync_replay.py` resolves the two
parents, their merge base, and each file blob from Git trees. `ours` is the
first parent (the local fork) and `theirs` is the second parent (upstream).
`result` is read from the merge commit tree; the worktree is not consulted.

The `both_sides` set is the intersection of paths changed from the merge base
on the first and second parents. The raw invariant measurement has no policy
filter. It distinguishes an absent tree entry (`None`) from an empty blob and
records unparseable files as hard findings rather than treating them as an
empty definition set. Repeated module-level names are currently represented
as one name in the raw set; this limitation is covered by T15.

Recorded OIDs for the anchor case:

- merge: `9f3feebcd3`
- base: `9ef9b2d2d01eb4bcf9420973c5ef6c98f2176455`
- ours/first parent: `50b47b36e7ceba9cbc05a33740705ba3a77d1e03`
- theirs/second parent: `f7a5c9e520b4da422b2c088a52a6a0165d8ed788`
- path: `tools/approval.py`

That replay currently produces no raw findings. The five manually classified
historical cases are recorded in the fixture: the real `_stale_guard_*` loss,
the intentional `submit_pending` local deletion, the upstream retirement of
`_ALLOWLIST_SHELL_OPERATOR_RE`, and the two body-contribution losses in
`check_all_command_guards` and `check_execute_code_guard`. These classifications
are evidence about the old corpus, not a policy filter applied to the raw
measurement.

## Fork-test selection measurement

For T4, the selector was measured read-only on the 15 most recent merge
commits using the same `upstream/main` boundary. It took 0.581 seconds to
compute all selections. The total selected files per merge were:

`528, 106, 106, 98, 122, 446, 529, 88, 91, 82, 82, 83, 81, 81, 81`.

The largest set was 529 files, below the explicit default hard limit of 800.
The selector reports the count and refuses rather than truncating if a future
merge exceeds `HERMES_FORK_TEST_MAX_FILES`; changing that limit is an operator
decision, not an implicit fallback.
