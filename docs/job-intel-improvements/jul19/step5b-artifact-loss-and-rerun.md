# Step 5B — Artifact Loss Incident, Re-run, and Two-Run Variance

**Date:** 2026-07-23 · **Owner decision:** option (a) — re-run 5B-4 live, but only after fixing the root cause
**Spend:** $0.6508 (cap $3, previously approved `APPROVE_5B4_CALIBRATION_WITH_CAP_$3`)
**Status of superseded artifacts:** the 2026-07-20 run's artifacts are destroyed and unrecoverable.

## 1. Incident

An upstream-sync/recovery rewrote `local/customizations` (backups:
`backup/pre-recovery-20260720`, `backup/pre-upstream-sync-20260720-113018`).
Commits survived — rebased onto new SHAs, all code and reports intact. The
untracked `artifacts/` tree did not survive. The exact removal mechanism was
NOT established (no `git clean` in the sync scripts); the conclusion below
does not depend on it.

Destroyed: 5A smoke recordings ($0.09), 5B-4 calibration recordings ($0.58),
the deterministic baseline, and the **pinned eligible-corpus snapshot**.

## 2. Root-cause fix (commit `3887f78d8`, before any re-run)

Benchmark output no longer lives in the repo: `paths.artifact_root()` →
`/var/lib/job-intel/benchmark-artifacts`, overridable via
`JOB_INTEL_BENCHMARK_ARTIFACTS` (absolute only — a relative override would
silently reintroduce the failure). `baseline.default_out_root()` resolves at
call time. `.gitignore` gains `artifacts/` as second-line defence for the
Phase I replay defaults that still write repo-relative. A guard test asserts
the default root is outside the repo tree. Regression: 280 passed.

## 3. What was and was not recoverable

| Dataset | Hash before | Hash after | Status |
|---|---|---|---|
| controls-1.0.0 | `185bac3f…` | `185bac3f…` | **identical** |
| golden-fixtures-21 | `d6484c3b…` | `d6484c3b…` | **identical** |
| decision-golden-1.1.1 | `ee3864db…` | `ee3864db…` | **identical** |
| eligible-corpus | `03387fbb…` (3626) | `0b941a29…` (3730) | **diverged** |

The calibration corpus is repo-derived and came back bit-identical, so the
199-case re-run is strictly comparable to the deterministic baseline. The
eligible corpus is DB-derived and grew by 104 vacancies in three days; that
specific snapshot is gone permanently. Deterministic baseline on the new
corpus: 3730 cases, 0 failures, 1729 accepted observations, 2714
zero-observation (72.8%, still consistent with the Step 4B recall ceiling).

## 4. Re-run results and the idempotency fix, validated

199/199 cases, 0 failures, $0.6508. **148 recordings for 199 cases** — the
Slice 5B-4 idempotency fix reused 51 duplicate inputs instead of re-paying
for them. **Live-to-replay: 199/199, 0 mismatches (PASS)** — the 47
mismatches of the first run are gone. The incident thus produced the
end-to-end validation the original run could not perform.

## 5. Two independent live runs of one corpus — the variance finding

| Metric | Run 1 (2026-07-20) | Run 2 (2026-07-23) | Stability |
|---|---|---|---|
| **precision on known gold** | **0.64** | **0.63** | **stable** |
| **controls pass/fail** | **122 / 36** | **122 / 36** | **stable** |
| micro precision | 0.210 | 0.172 | ±20% rel. |
| micro recall | 0.300 | 0.243 | ±20% rel. |
| macro precision | 0.261 | 0.273 | moderate |
| macro recall | 0.381 | 0.322 | ±16% rel. |
| emissions (match / gold-unknown / wrong) | 100 (21/67/12) | 99 (17/72/10) | counts stable, split moves |
| divergent decisions | 22 of 41 | 16 of 41 | ±27% rel. |
| unsupported evidence (golden) | 0.019 | 0.100 | unstable |
| **row-level recurrence of (fact, excerpt) pairs** | — | **30%** | **see below** |

Only **36 of 120** reviewed observation rows recur identically in run 2; 84
disappeared and 86 new ones appeared for the same cases. At temperature 0
the provider is aggregate-stable but **row-unstable**: it reliably reaches
the same overall precision on known gold while quoting substantially
different evidence each time.

This is the single most decision-relevant result of the whole slice, and it
was only obtainable by accident. Consequences:

- Single-run precision/recall point estimates for this provider carry
  material uncertainty; 5B-8 and 5C must publish them as ranges over ≥2
  runs, never as one number.
- Any future prompt iteration (`llm-obs-1.1.0`) cannot be evaluated by
  comparing one run against one run — the run-to-run spread is comparable
  to the effect size a prompt change would plausibly produce.
- The contract's allowance that live repeats need not be byte-identical
  (§Reproducibility) is now quantified rather than assumed.

## 6. Impact on the in-progress manual review

The owner's review was started against run 1's observation rows. With 30%
row-level recurrence, the marks **cannot be mechanically re-applied** to
recompute run-2 precision. They remain valid and useful for the question the
review exists to answer — whether gold-unknown emissions are annotation gaps
or interpretive stretches — because that is a property of the provider and
prompt, not of one sampled run. Recommendation recorded for the owner:
complete the review at the level of recurring failure PATTERNS rather than
exhaustively row by row.

## 7. Verdict status

5B-5 remains `LLM_HISTORICAL_REPLAY_INCONCLUSIVE`, now on firmer ground: the
stable findings across two runs are precision-on-known-gold ≈ 0.63 and a 23%
control failure rate concentrated in ambiguous/conflicting cases. The
recall advantage is real but its magnitude is uncertain.

Added blocker for the paid slices: 5B-6/5B-7 must budget **two runs** for any
figure that will inform 5C, or explicitly accept single-run uncertainty.
Full-corpus projection is unchanged at ~$21 per run on 3730 cases.
