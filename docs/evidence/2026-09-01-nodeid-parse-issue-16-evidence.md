# NodeID parse issue #16 — checkpoint evidence

objective: Complete checkpoint §5 for the three accepted slices, proving the composed checkout candidate and recording the remaining host publication boundary.

authorized_scope: Run the §5 checkpoint on an isolated checkout, remove the dead `_FAILED_LINE` parser, repair the test-only runtime bundle fixtures after the checkpoint found their missing helper dependency, and add this evidence packet. No live runtime publication, service restart, plan edit, or host mutation was authorized.

explicit_deferrals: Publishing the runtime bundle to `/home/hermes/.hermes/scripts/` remains a separate owner-authorized operation. No full-tree pytest was run, by explicit §0.3 constraint. No acceptance claim is made for a post-evidence-commit test run.

base_revision: da1a8b45ce6245d23e0259803e72d20865499824

tested_candidate_revision: 2a121e31254b0b88663a03bf45ce1a73952d9b7b

evidence_commit_revision: SELF

changed_paths: Candidate C removed the unused `_FAILED_LINE` from `scripts/upstream_sync_gate.py`. Candidate C2 added `tests/scripts/runtime_bundle_test_utils.py` and changed `tests/scripts/test_upstream_sync_apply.py` and `tests/scripts/test_upstream_sync_finalize.py`. This evidence commit adds only `docs/evidence/2026-09-01-nodeid-parse-issue-16-evidence.md`.

red_evidence: On C `6670a8c73d6a56962b8dfa4454092f78b26d1e99`, `timeout 300 /home/hermes/.hermes/hermes-agent/venv/bin/python -m pytest tests/scripts/test_upstream_sync_apply.py -q -k test_the_merge_lands_on_the_live_branch --tb=long` exited 1. The failed test was `TestEndToEndWithTheHostFinalizer::test_the_merge_lands_on_the_live_branch`; its finalizer detail contained `ModuleNotFoundError: No module named 'scripts'`, `ModuleNotFoundError: No module named 'pytest_status_lines'`, and `run_gate outcome ... unknown`. Receipt: `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c/candidate-apply-focused.txt`. Root cause was the apply fixture's hand-kept Python-helper tuple omitting `pytest_status_lines.py`; the finalize fixture had a separate glob-plus-parser list. The checkpoint regression was found outside slice 2's owning suites because `test_upstream_sync_apply.py` was not among them.

green_evidence: After the fixture repair, the same named test on C2 passed: `timeout 300 /home/hermes/.hermes/hermes-agent/venv/bin/python -m pytest tests/scripts/test_upstream_sync_apply.py -q -k test_the_merge_lands_on_the_live_branch --tb=short`, exit 0, `1 passed, 30 deselected in 4.99s`. The focused GREEN receipt is `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2/candidate-c2-apply-green-2.txt`; the full-set receipt is `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2-final/candidate2-upstream_sync_apply.txt`. Both stubs now use `tests/scripts/runtime_bundle_test_utils.py`, which derives published Python files from `scripts/acceptance/publish-runtime.sh` and adds the automatically discovered `upstream_sync_*.py` host fallback helpers.

composition_evidence: `composition_verified` for the checkout. All checkpoint commands ran sequentially from `/home/hermes/worktrees/nodeid-parse-issue-16-checkpoint`, at C2 `2a121e31254b0b88663a03bf45ce1a73952d9b7b`, with interpreter `/home/hermes/.hermes/hermes-agent/venv/bin/python`. The two earlier accepted slice packets remain the evidence for their live runner/report/gate composition claims; this checkpoint verifies the complete owning-suite composition at C2.

runtime_evidence: `runtime not published`. No command copied files into `/home/hermes/.hermes/scripts/`, and no service or path unit was touched. `tests/scripts/test_acceptance_publish_runtime.py` passed its dry-run contract test, including the ten-file `RUNTIME_FILES` contract, but that is not publication proof. `deployed_not_verified` is not applicable because nothing was deployed; `complete` is not asserted.

full_gate_evidence: Every row below used an external timeout and ended with a pytest summary plus an explicit exit receipt. The full-tree pytest was not run.

| Set | Command target | Final pytest line | Exit | Receipt |
|---|---|---|---:|---|
| gate | `tests/scripts/test_upstream_sync_gate.py` | `117 passed in 32.47s` | 0 | `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2-final/candidate2-upstream_sync_gate.txt` |
| runner | `tests/test_run_tests_parallel.py` | `20 passed, 1 skipped in 10.99s` | 0 | `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2-final/candidate2-run_tests_parallel.txt` |
| sync-local | `tests/scripts/test_sync_local_customizations.py` | `16 passed in 20.16s` | 0 | `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2-final/candidate2-sync_local_customizations.txt` |
| selection | `tests/scripts/test_run_fork_tests_selection.py` with exact venv `/home/hermes/.hermes/hermes-agent/venv/bin/python` | `40 passed in 17.15s` | 0 | `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2-final/candidate2-run_fork_tests_selection.txt` |
| finalize | `tests/scripts/test_upstream_sync_finalize.py` | `1 failed, 75 passed in 151.40s`; failure by name: `TestRepoLock::test_rebase_script_refuses_to_run_while_repo_lock_is_held` | 1 | `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2-final/candidate2-upstream_sync_finalize.txt` |
| apply | `tests/scripts/test_upstream_sync_apply.py` | `31 passed in 15.16s` | 0 | `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2-final/candidate2-upstream_sync_apply.txt` |
| triage | `tests/scripts/test_upstream_sync_triage.py` | `24 passed in 3.07s` | 0 | `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2-final/candidate2-upstream_sync_triage.txt` |
| slack | `tests/scripts/test_upstream_sync_slack.py` | `28 passed in 2.57s` | 0 | `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2-final/candidate2-upstream_sync_slack.txt` |
| status-lines | `tests/scripts/test_pytest_status_lines.py` | `21 passed in 2.03s` | 0 | `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2-final/candidate2-pytest_status_lines.txt` |
| measure | `tests/scripts/test_measure_order_leakage.py` | `8 passed in 1.68s` | 0 | `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2-final/candidate2-measure_order_leakage.txt` |
| acceptance/runtime | `tests/scripts/test_acceptance_publish_runtime.py` | `2 passed in 1.67s` | 0 | `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2-final/candidate2-acceptance_publish_runtime.txt` |
| fail-closed | `tests/scripts/test_nodeid_fail_closed.py` | `2 passed in 2.19s` | 0 | `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2-final/candidate2-nodeid_fail_closed.txt` |

The only C2 failure is the known baseline failure. The same eight historical sets were run on separate worktree `/home/hermes/worktrees/nodeid-parse-issue-16-baseline-8dc` at `8dc8a8c48d4ffbf6d0fbc56a434549ccf9210e8a`; this was a real sequential baseline run, not an inference from an older report. It produced the same named `TestRepoLock::test_rebase_script_refuses_to_run_while_repo_lock_is_held` failure in finalize and no failure in apply (`31 passed`). Baseline receipts: `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-baseline-8dc/`.

known_limitations: The parser's supported class is balanced brackets in the nodeid payload. Two named, intentionally retained limitations from spec §3 remain: (1) an unmatched `[` can absorb diagnostic text into the nodeid (`test_x[a[b] - boom`); (2) an identifier containing `value] - inner` is fundamentally ambiguous and neither strategy can resolve it. These are not claimed fixed by this checkpoint.

working_tree_state: Immediately before creating this packet, `git status --porcelain` in `/home/hermes/worktrees/nodeid-parse-issue-16-checkpoint` was empty at tested candidate C2. The live checkout `/home/hermes/.hermes/hermes-agent` remained on branch `local/customizations`, HEAD `da1a8b45ce6245d23e0259803e72d20865499824`, with only the pre-existing untracked `docs/plans/2026-08-22-upstream-sync-gate-review-fixes.md`. The evidence commit is intended to be the direct child of C2 and to add only this file.

pending_authority_or_decisions: Owner/Claude acceptance of this packet and a separate decision to publish the tested runtime bundle into `/home/hermes/.hermes/scripts/` remain pending. The live host therefore remains `runtime not published`; no post-E gate is run or claimed, and no plan checkbox is changed in this commit.

## Issue §7 cross-check

| Issue criterion | Closing slice | Concrete evidence |
|---|---|---|
| A live run with `alpha - old` / `alpha - new` yields two nodeids in report and log parsing | Slice 1 (report), slice 2 (log) | Accepted slice packets in `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-slice-1/evidence-packet.md` and `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-slice-2/evidence-packet.md`; checkpoint owning suites are green on C2 except the named baseline failure. |
| Both doors return equal node lists | Slice 2 | Slice 2 live parity receipt and `tests/scripts/test_upstream_sync_gate.py` result in `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2-final/candidate2-upstream_sync_gate.txt`. |
| Parsing is shared: one rule, not two similar rules | Slices 1, 2, and 3 | Slice packets' grep evidence, slice 3 final parser receipt, and C2's shared fixture source `tests/scripts/runtime_bundle_test_utils.py`; the checkpoint gate and runner suites pass on C2. |
| Existing suites remain green | Checkpoint §5 | The eleven C2 receipts above; the sole failure is the named baseline `TestRepoLock...`, confirmed by the eight-set baseline run. |
| Known parse refusal remains blocking rather than silent | Slice 2, confirmed at checkpoint | Slice 2 fail-closed regression evidence plus the C2 run of `tests/scripts/test_nodeid_fail_closed.py`: `2 passed`, exit 0, receipt `/home/hermes/.hermes/evidence/nodeid-parse-issue-16-checkpoint-c2-final/candidate2-nodeid_fail_closed.txt`. |

status: `composition_verified` for the checkout; `runtime not published` for the host; `deployed_not_verified` not applicable; `complete` not set.
