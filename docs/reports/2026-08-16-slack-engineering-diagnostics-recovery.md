# Slack engineering continuation and diagnostics recovery

Date: 2026-08-16  
Baseline: `49ba5f8ceda4bb4ccd0bc8c4741733583362ebe4`  
Execution branch: `codex/slack-engineering-diagnostics-recovery`  
Live deployment: not performed  
Gateway restart: not performed

## Outcome

The planned recovery is implemented in an isolated, host-linked worktree. The
change closes the failure modes seen in the Slack engineering case:

- a bare `выполняй` is bound only to one same-session approved plan and fails
  closed for missing, ambiguous, or cross-session context;
- long plans retain their exact bytes and SHA binding;
- controlled engineering runs persist a verifiable Git bundle/patch/untracked
  manifest under the durable run root before completion can be claimed;
- `artifact_not_persisted` blocks completion and clears a matching pending
  commit authorization;
- Slack reaction handlers are registered exactly once and Google capture uses
  the active interpreter plus the resolved `HERMES_HOME`;
- Slack `msg_too_long` updates get one bounded shorter fallback, with a
  terminal safe error and no duplicate post;
- the nightly diagnostics collector publishes an atomic lifecycle status, and
  the morning consumer rejects failed, stuck, stale, corrupt, or mismatched
  digests;
- the operator runbook now documents recovery, OAuth prerequisites, and the
  exact gateway restart unit.

## Commits

All commits are on the isolated branch and are reachable from the baseline:

| Commit | Subject |
| --- | --- |
| `e8a3187831` | `fix(router): bind bare execution approvals to reviewed plans` |
| `cb49f11506` | `test(router): cover bare authorization fail-closed cases` |
| `8199daeec6` | `test(pipeline): align observe model expectations with policy` |
| `eaa9924c90` | `fix(pipeline): persist verified engineering change artifacts` |
| `b9a1182e0b` | `fix(slack): deduplicate reactions and pin capture runtime` |
| `5052e697da` | `fix(slack): recover oversized message updates deterministically` |
| `d3af301dda` | `fix(diagnostics): publish atomic collector lifecycle status` |
| `61b60630e8` | `test(pipeline): align observe artifact report contract` |
| `f35f888cd0` | `fix(diagnostics): narrow parsed log entries before comparison` |

The report and operator runbook are included together in the final
documentation commit at the tip of this branch.

## Verification evidence

| Check | Result |
| --- | --- |
| Controlled-pipeline smoke plus artifact/report integration tests | `78 passed in 10.99s` |
| Routing and observe acceptance fixture | `209 passed in 13.67s` |
| Combined focused regression set (routing, pipeline, Slack, reaction capture, diagnostics) | `303 passed in 22.31s` |
| Nightly diagnostics and morning-report suite after final type narrowing | `35 passed in 2.40s` |
| Ruff on all changed Python files | passed |
| `compileall` on all changed Python files | passed |
| `ty` on new artifact and diagnostics modules | passed (`All checks passed!`) |
| Independent review of each implementation/test commit | pass; no findings |

The unrestricted repository suite was also attempted. It collects 38,718
items and is not a usable green gate in this checkout: the first stop was the
unrelated existing test
`tests/agent/test_account_usage.py::test_codex_usage_prefers_explicit_live_agent_credentials`,
which expected legacy labels (`Session`, `Weekly`) while the current provider
contract returns (`5h`, `Week`). The `-x` run stopped after `206 passed`, `1
failed`, `12 skipped`, and `52 deselected` in `51.21s`; no changed-scope test
failed in the focused gates above.

## Changed files

The implementation touches the following tracked files relative to the
baseline:

```text
gateway/run.py
hermes_cli/engineering_task_context.py
hermes_cli/orchestrator.py
hermes_cli/pipeline_autonomous_execution.py
hermes_cli/pipeline_change_artifacts.py
hermes_cli/pipeline_observe.py
hermes_cli/pipeline_report_artifacts.py
hermes_cli/pipeline_rework_loop.py
job_intel/idea_reaction_capture.py
plugins/platforms/slack/adapter.py
scripts/morning_report_context.py
scripts/nightly_diagnostics_collect.py
tests/gateway/test_engineering_task_continuation.py
tests/gateway/test_orchestrator_observe.py
tests/gateway/test_slack_block_kit_adapter.py
tests/gateway/test_slack_plugin_setup.py
tests/hermes_cli/test_run_integration.py
tests/job_intel/test_idea_reaction_capture.py
tests/scripts/test_morning_report_context.py
tests/scripts/test_nightly_diagnostics_collect.py
tests/test_pipeline_controlled_e2e.py
tests/test_pipeline_report.py
docs/hermes-operator-runbook.md
```

## Rollout and rollback

Nothing has been copied into the live checkout, and the gateway has not been
restarted. After an explicit deployment/merge decision, use the normal
protected-branch workflow, then execute this single restart command:

```sh
ssh hermes-agent 'systemctl --user restart hermes-gateway.service'
```

Immediately verify:

```sh
ssh hermes-agent 'systemctl --user status hermes-gateway.service --no-pager -l'
ssh hermes-agent "pgrep -af 'hermes_cli.main gateway run'"
ssh hermes-agent 'journalctl --user -u hermes-gateway.service -n 80 --no-pager'
```

Rollback must target the revision actually deployed to the protected branch,
not the isolated worktree SHAs above. A squash merge, rebase, or cherry-pick
changes those identifiers. First record the deployed revision from the
protected checkout/pipeline, then revert that real revision:

```sh
git log --oneline --decorate -n 10
git revert --no-edit <actual-deployed-commit-or-merge-commit>
```

If several commits were deployed without a merge commit, pass the actual
deployed commit set in reverse dependency order. The source branch range is a
review/audit reference only; it is not a production rollback command.

Do not delete a controlled worktree or durable run root while an
`artifact_not_persisted` report is unresolved.
