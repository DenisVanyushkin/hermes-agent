# Merge Invariant Gates for upstream-sync

Date: 2026-08-19
Postmortem: `docs/reports/2026-08-19-upstream-sync-mismerge-postmortem.md`
Code location: **the hermes-agent repo on the VPS**, branch `local/customizations`
(`scripts/upstream_sync_apply.py`, `scripts/upstream_sync_llm.py`, `tests/scripts/`).
This plan lives in the knowledge repo; the implementation does not.

## Problem

A resolved merge can be structurally broken while looking perfectly resolved: no
conflict markers, exit code 0, every hunk reported closed. Two distinct ways it
happened on 2026-08-19:

- mechanical `merge-both` concatenated two dict bodies that shared a single closing
  brace outside the conflict block → the module stopped parsing;
- the model was handed a 215-line block that was 211 lines *ours* against 1 line
  *theirs*, resolved the one-line disagreement it could see, and dropped the other
  209 lines → seven definitions vanished while their call sites remained.

Nothing between the resolver and the 20-minute test gate looked at the result.

## Decisions (agreed with the owner, 2026-08-19)

1. **Fail closed.** A tripped invariant means no merge commit at all, plus a precise
   report naming the file and the symbols. The operator fixes or answers.
2. **Checks live inside `commit`.** It is the single point every path goes through,
   including `--amend` after a hand repair. A separate `verify` step could be bypassed
   by calling `handoff` directly and would not cover `--amend` at all.
3. **Asymmetric blocks are not the model's job.** Past a threshold the block goes to
   `needs_manual` with an explanation of what the real disagreement is.

## Design notes

> **Superseded in part, 2026-08-22.** The definition check now takes the merge
> base as a fourth input. A name the base had that exactly one side no longer
> defines — with both sides actually having the file — is treated as an accepted
> deletion and is not reported, so the `submit_pending` case named in Task 2's
> test list is deliberately silent now, and the contract below is
> `(ours ∪ theirs) − result` minus those. The reason is in
> `scripts/upstream_sync_invariants.py`: without the base, every deletion and
> every rename an upstream batch brings became a finding, and the only answer to
> a finding disarmed the gate for the whole merge — so the noise was training the
> operator to bypass it. "A human decides" still holds for everything the base
> cannot settle. The signatures published below have gained `base` / `read_base`
> parameters, and their readers now return `None` for a side that has no such
> file.


**What "definition set" means.** For each changed `.py` file, parse `ours`, `theirs`
and the result, and compare the sets of module-level `def`/`async def`/`class` names
and simple assignment targets. Report `(ours ∪ theirs) − result`.

**Why the check must not auto-restore.** Of three symbols flagged during the incident
audit, two were *supposed* to be gone: one deliberately deleted locally (documented in
a surviving comment) and one retired along with an implementation upstream rewrote.
Auto-restoration would have resurrected both. The check reports and blocks; a human
decides. This is the load-bearing constraint of the whole slice — an implementation
that "helpfully" restores missing symbols is worse than no check.

**Scope.** Python gets both checks (parse + definition set). Everything else gets the
parse-equivalent where a cheap parser exists (`json`, `yaml`) and nothing otherwise;
Markdown and `.ts` are out of scope. The incident's two failures are both Python.

**Threshold for asymmetry.** Start at: block ≥ 100 lines **and** one side ≥ 10× the
other. The incident block was 211:1 at 215 lines. Deliberately conservative — this
gate trades a rare operator interruption against a lost apply cycle. Configurable via
`HERMES_SYNC_MAX_BLOCK_SKEW` / `HERMES_SYNC_MAX_BLOCK_LINES` so a wedged sync can be
unblocked without a code change.

## Tasks

TDD throughout: each task starts with a failing test, and tasks 1–2 build fixtures
from the **real** incident blocks so the regression is the actual event, not a
paraphrase of it.

---

### Task 1 — Extract the two incident conflict blocks as fixtures
**Complexity: S. Depends on: nothing.**

Reproduce the raw merge (`d038719e10` × `2507bc649f`) and save two conflict blocks
verbatim under `tests/fixtures/upstream_sync/`:

- `config_defaults_shared_closer.py.conflict` — the 48-line block whose closing `},`
  sits outside it;
- `gateway_run_asymmetric.py.conflict` — the 215-line block, 211 ours vs 1 theirs.

Also store the *surrounding* file context needed to make the parse check meaningful
(the dict opener above, the shared closer below). Without that context the first
fixture cannot demonstrate the failure at all — the bug only exists relative to lines
outside the block.

**Done when:** both fixtures load and, fed through today's resolvers, reproduce the
exact breakages (unparseable module; seven missing definitions).

---

### Task 2 — `scripts/upstream_sync_invariants.py`
**Complexity: M. Depends on: Task 1.**

Pure functions, no git, no I/O beyond reading blobs handed to them:

```python
def parse_failures(files: dict[str, str]) -> list[Finding]
def lost_definitions(ours: str, theirs: str, result: str, path: str) -> list[Finding]
def check_merge(paths, read_ours, read_theirs, read_result) -> Report
```

`Finding` carries path, kind, symbol/line, and a message written for the operator, not
for a log grep.

Tests, red first:

1. the shared-closer fixture → one `unparseable` finding naming the file and line 7;
2. the asymmetric fixture → seven `lost_definition` findings, all `_stale_guard_*`;
3. a clean merge → empty report (guards against a check that always fires);
4. `submit_pending` case → **must** be reported (the checker does not get to decide
   intent), and the report must be shaped so the operator can answer "expected";
5. a symbol that merely moved within the file → **not** reported.

**Done when:** the module reproduces both incident findings from fixtures alone, with
no network, no repo, and no model.

---

### Task 3 — Wire into `commit`, fail closed
**Complexity: M. Depends on: Task 2.**

In `cmd_commit` (both the plain and `--amend` paths), after staging and before writing
the merge commit: run `check_merge` over every path in the merge diff, using
`git show` of both parents for the comparison sides.

On findings: write nothing, emit `{"status": "invariants_failed", "findings": [...]}`,
exit non-zero, leave the scratch clone intact for repair.

An override exists — `HERMES_SYNC_SKIP_INVARIANTS=1` — because a legitimate mass
deletion must not wedge the pipeline forever. It is logged loudly into
`finalize-detail.log` and named in the Slack report.

Tests:

1. `commit` on a scratch whose result carries the shared-closer break → non-zero, no
   commit created, findings in the payload;
2. same for the asymmetric-loss case;
3. clean merge → commits exactly as today (no behaviour change on the happy path);
4. `--amend` path is checked too — the hand-repair route must not be a hole;
5. override set → commits, and the report records that invariants were skipped.

---

### Task 4 — Mechanical `merge-both` validates its own output
**Complexity: S. Depends on: Task 2.**

In `prepare`, where mechanical hunks are closed: after producing the merged text,
parse it. If it does not parse, the hunk is **not** resolved — leave the markers, put
the file in `needs_manual`, and record why. This is the direct fix for case 3: the
mechanical path currently never looks at what it produced.

Tests: the shared-closer fixture goes to `needs_manual` instead of `auto_resolved`; a
mechanically-mergeable hunk still auto-resolves.

---

### Task 5 — Asymmetric blocks bypass the model
**Complexity: S. Depends on: nothing (parallel with 2–4).**

In `upstream_sync_llm.resolve_text`, before calling the model: measure the block. Past
the threshold, do not call — record a failure for that hunk with a message stating the
real shape (`"ours 211 lines vs theirs 1 line; the actual disagreement is the
signature at line N — the remaining 209 lines are unrelated local code dragged into
this block"`). Whole-file semantics already turn one failed hunk into `unresolved`,
so no new control flow is needed.

Tests: the asymmetric fixture is never passed to `call_model` (assert with a spy); a
normal 20-line block still is; the threshold honours its env vars.

---

### Task 6 — Surface findings in the Slack report
**Complexity: S. Depends on: Task 3.**

`invariants_failed` must read as its own outcome, not as a generic apply failure: name
the files, the symbols, and say plainly that nothing was committed and the clone is
preserved. Explicitly distinguish it from a red test gate, so the operator does not
reach for `apply fix` — which cannot help here and, per the postmortem, would be
actively harmful.

Tests: renderer produces the distinct message; it does not offer the triage vocabulary.

---

## Sequencing

```
Task 1 ──┬── Task 2 ── Task 3 ── Task 6
         │              └─ Task 4
         └── Task 5 (independent)
```

Tasks 1–3 are the slice's core: they alone would have caught both incident failures
before a single test ran. Tasks 4–5 remove the two ways the bad input was produced.
Task 6 is what makes the check usable at 3 a.m.

## Verification

Beyond unit tests, one end-to-end rehearsal on the VPS: re-run `prepare` against a
synthetic upstream point that reproduces the shared-closer shape, and confirm the
pipeline stops at `commit` with a readable report and an intact clone.

Run the suite with `--timeout=90` and only the `tests/scripts/` subset — a full pytest
run on this host drives loadavg ~50 and the systemd watchdog kills the live gateway.

## Out of scope

- Sync frequency (the 4320-minute interval) — a separate decision.
- The fork's red baseline (77 failed before any merge).
- Gate triage's default answer.
