# Hermes Controlled Engineering Runtime — SoT-Aligned Slice Plan

## 0. Purpose

This document fixes the agreed direction for moving Hermes controlled/manual execution from orchestration smoke to real SoT-aligned engineering execution.

The goal is not to build a separate patch-applier architecture and not to let the orchestrator perform engineering work.

The target is:

```text
router
→ orchestrator / pipeline state machine
→ runtime factory
→ subagent runner
→ engineer subagent with tools
→ observed workspace changes
→ reviewer packet
→ reviewer subagent
→ gates / report
→ operator decision
```

## 1. Current Production State

Current deployed checkpoint:

```text
commit: cb735e458b960a3d5bdd1fb8a15df9fb7ab18fa2
message: fix(pipelines): disable fake controlled manual production execution
gateway: active/running
production controlled_manual: enabled
fake production execution: disabled
normal AIAgent fallback bypass: blocked
```

Latest live smoke confirmed:

```text
pipeline: engineering_review_pipeline
status: not_executed / blocked
blocked_reason: real_subagent_executor_missing
subagent_runs: 0
models_used: none
files_changed: none
reviewer_invoked: false
host_repo_mutation_status: clean
```

This is the correct honest state until a real executor bridge is implemented.

## 2. What Is Already Working

The following control-plane components are working or sufficiently proven:

```text
- LLM router selects engineering_review_pipeline
- controlled_manual execution mode is accepted
- authorized operator can trigger engineering pipeline without magic trigger
- blocked controlled pipeline does not fall back to normal AIAgent
- fake dry-run helper is no longer used as production success
- gateway survives restart
- reports/workspace shell are created
- explicit fake smoke/dry-run path still exists for tests
```

The system no longer pretends that fake execution is real execution.

## 3. What Is Not Working Yet

The missing piece is the real execution/data plane:

```text
real_subagent_executor_missing
```

Specifically:

```text
- MiMo/OpenRouter engineer is not yet invoked in production controlled_manual
- Codex/gpt-5.5 reviewer is not yet invoked in production controlled_manual
- engineer does not yet receive real role prompt + task + tools
- controlled subagent runtime is not yet connected to AIAgent tool loop
- actual tool calls are not yet recorded through SubagentRunner
- reviewer does not yet receive actual reviewer packet from real engineer changes
```

## 4. SoT Ownership Rule

The orchestrator is not the executor.

The orchestrator / pipeline owns:

```text
- dispatch
- state machine
- policy gates
- workspace/run lifecycle
- baseline/post snapshots
- reviewer packet construction
- completion decision
- report
```

The engineer subagent owns engineering work:

```text
- reads files/context
- decides implementation approach
- invokes allowed tools
- mutates files through tools
- requests/runs tests through allowed tool path
- returns structured status/summary/blockers
```

The reviewer subagent owns review work:

```text
- receives task, engineer output, actual diff, tests, risk context
- approves / requests changes / blocks
- returns structured review decision
```

The git/mutation layer is evidence, not author:

```text
- captures baseline snapshot
- captures post-engineer snapshot
- computes actual material delta
- feeds reviewer/gates/report
```

It must not replace engineer execution.

## 5. Explicit Non-Goals

Do not build these as default architecture:

```text
- orchestrator applies model-proposed patch
- pipeline invents implementation changes itself
- fake dry-run helper used in production controlled_manual
- normal AIAgent fallback for engineering tasks
- broad unmanaged shell access as first real slice
- DB persistence work mixed into runtime bridge work
```

A temporary test double is acceptable only for proving seams in tests. It must not become production behavior.

## 6. Current Code Findings

Known current production divergence, now guarded:

```text
hermes_cli/pipeline_execution_helpers.py
controlled_manual previously routed to run_controlled_engineering_e2e_dry_run(...)
```

Known fake source:

```text
hermes_cli/pipeline_controlled_dry_run.py
_manual_dry_run_provider_factory(...)
_manual_dry_run_engineer_output(...)
```

Known future SoT seam:

```text
execute_bounded_rework_loop(...)
RuntimeFactory
SubagentRunner
AIAgent tool loop
```

Known normal tool layer exists under AIAgent:

```text
run_agent.py
model_tools.py
toolsets.py
```

Known issue:

```text
SubagentRunner requires an injected executor.
ControlledRuntimeRunner is an invocation adapter, not a full agent/tool runtime.
```

## 7. Slice Plan

### Slice 1 — Done: Controlled/manual no-fallback and no fake production success

Status: shipped.

Goal:

```text
controlled_manual production must not silently use fake dry-run helpers.
```

Implemented behavior:

```text
- fake helper disabled for production controlled_manual
- missing real executor returns blocked real_subagent_executor_missing
- no generated tests/test_generated_example.py
- no fake reviewer approval
- no normal AIAgent fallback
- explicit smoke/dry-run path preserved
```

Validation:

```text
live smoke returns real_subagent_executor_missing
host repo remains clean
subagent_runs = 0
models_used = []
```

### Slice 2A — Real execution seam wiring without live provider calls

Goal:

Prove the SoT execution seam:

```text
controlled_manual
→ production controlled manual context
→ execute_bounded_rework_loop(...)
→ SubagentRunner
→ injected executor bridge
→ actual workspace delta observed
→ reviewer packet built from actual delta
```

This slice should not call OpenRouter or Codex yet.

Required behavior:

```text
- production context module separate from pipeline_controlled_dry_run.py
- real_executor_ready=True only when executor bridge is actually supplied
- execute_bounded_rework_loop(...) entered when executor is ready
- SubagentRunner executor actually invoked
- deterministic test executor only in tests
- actual workspace delta observed by git gate
- reviewer packet built from observed delta
- fake dry-run helper not used
- default conversation unaffected
- normal AIAgent fallback still blocked
```

Suggested files:

```text
new: hermes_cli/pipeline_controlled_manual_context.py
new: hermes_cli/pipeline_aiagent_executor.py or seam module
modified: hermes_cli/pipeline_execution_helpers.py
modified: hermes_cli/pipeline_rework_loop.py only if needed
tests: focused controlled/manual real-executor seam tests
```

Explicitly not included:

```text
- live MiMo/OpenRouter calls
- live Codex reviewer calls
- broad terminal tool exposure
- DB persistence fixes
- live Slack/Telegram validation
```

Success criteria:

```text
- tests prove controlled_manual can reach execute_bounded_rework_loop with executor
- tests prove fake helper is not used
- tests prove actual delta is observed
- production without executor still blocks real_subagent_executor_missing
```

### Critical provider/model requirement for autonomous engineering fallback

This requirement must be treated as part of the task before implementation.

For `hermes_engineer_core` autonomous execution:

```text
primary:
  keep the existing configured primary provider/model unchanged.

fallback:
  provider must be openai-codex
  model must be gpt-5.4
  source must be subagent config / runtime_factory role-level fallback
  fallback must be passed into AIAgent as:
    fallback_model = {
      "provider": "openai-codex",
      "model": "gpt-5.4"
    }
```

This is not interchangeable with:

```text
provider=openrouter
model=gpt-5.4

provider=openrouter
model=google/gemma-4-31b-it:free

any global/free/default fallback
```

Required constraints:

```text
- do not change the existing primary engineering provider/model to satisfy fallback wiring
- do not silently normalize openai-codex to openrouter, openai, or any other provider
- do not satisfy this task with a global fallback chain when the requirement is a subagent/runtime_factory role-level fallback
- if the existing fallback resolver cannot accept provider=openai-codex, stop and report a blocking compatibility issue
```

Required RCA questions:

```text
1. Is openai-codex a valid provider identifier for AIAgent fallback_model resolution?
2. Which resolver accepts provider=openai-codex?
3. Does the fallback path preserve provider=openai-codex all the way to provider client selection?
4. Is there any normalization that rewrites openai-codex to openrouter or another provider?
5. If openai-codex is not accepted by the existing fallback resolver, stop and report this as a blocking compatibility issue instead of silently changing provider.
```

Required tests:

```text
fallback_model == {
    "provider": "openai-codex",
    "model": "gpt-5.4",
}
```

The tests must fail if fallback becomes any of:

```text
{"provider": "openrouter", "model": "gpt-5.4"}
{"provider": "openrouter", "model": "google/gemma-4-31b-it:free"}
{"provider": "openai", "model": "gpt-5.4"}
```

Required observability:

```text
fallback_policy_present=true
fallback_policy_source=subagent_config
fallback_model.provider=openai-codex
fallback_model.model=gpt-5.4
global_fallback_used=false
```

If the current resolver cannot use `openai-codex`, the correct outcome is:

```text
CHANGES REQUESTED / blocking incompatibility
```

### Slice 2B — AIAgent executor bridge for engineer, constrained tools only

Goal:

Connect SubagentRunner executor to existing AIAgent tool loop for the engineer role.

Target flow:

```text
SubagentRunner
→ AIAgentSubagentExecutorBridge
→ RuntimeFactory / RuntimeBuildResult.to_aiagent_kwargs()
→ AIAgent with engineer role prompt
→ constrained engineer toolset
→ workspace-rooted tool execution
→ structured engineer output
→ observed git delta
```

Engineer role:

```text
subagent: hermes_engineer_core
provider/model: openrouter / xiaomi/mimo-v2.5-pro
```

Tool mapping:

```text
file_read  -> read_file
grep       -> search_files
patch      -> patch
write_file -> write_file
git_status -> wrapper or constrained terminal git status
git_diff   -> wrapper or constrained terminal git diff
pytest     -> constrained pytest wrapper or pipeline_test_runner
terminal   -> only if constrained; do not expose broad terminal first
```

Workspace rules:

```text
- all writes inside controlled workspace
- reject absolute paths outside workspace
- reject ..
- reject symlink/path escapes
- terminal cwd must be workspace
- no host repo mutation
```

Success criteria:

```text
- engineer receives real task and role prompt
- AIAgent invoked through SubagentRunner executor, not normal fallback
- allowed tools available, disallowed tools denied
- tool calls recorded
- actual workspace diff observed
- no fake generated_example.py
```

### Slice 2C — Reviewer bridge with actual reviewer packet

Goal:

Invoke reviewer through the same role-runtime/subagent path.

Reviewer role:

```text
subagent: hermes_code_reviewer
provider/model: openai-codex / gpt-5.5
```

Reviewer input must include:

```text
- original task
- engineer structured output
- actual changed files
- actual diff or redacted diff artifact
- actual test results
- reviewer packet
```

Reviewer output:

```text
approved
changes_requested
blocked
risk_flags
summary
```

Success criteria:

```text
- reviewer invoked only when material changes require review
- reviewer receives actual packet, not synthetic summary only
- invalid reviewer output blocks
- unconditional fake approval impossible in production
```

### Slice 2D — Rework loop with reviewer feedback

Goal:

Allow bounded engineer/reviewer loop.

Flow:

```text
engineer
→ observed diff/tests
→ reviewer
→ if changes_requested:
     engineer receives reviewer blockers
     engineer performs another bounded iteration
→ reviewer
→ completion_allowed only after approval or accepted no-change case
```

Constraints:

```text
- bounded iteration count
- fail closed on repeated invalid output
- no commit/push/restart
- workspace-only mutations
```

Success criteria:

```text
- reviewer changes_requested causes a second engineer pass
- loop limit enforced
- final report shows iterations and decision path
```

### Slice 2E — Live controlled/manual provider smoke

Goal:

Run one low-risk real provider smoke in production controlled_manual.

Expected:

```text
pipeline: engineering_review_pipeline
engineer: actually invoked
reviewer: actually invoked if material changes
workspace delta: task-dependent
tests: actual
fake helper: not used
normal AIAgent fallback: not used
host repo: clean
commit/push/restart: not attempted
```

Test task should be intentionally small and reversible.

### Slice 3 — DB persistence returns to Hermes as real task

Precondition:

```text
Slice 2B/2C live smoke passes.
```

Then DB persistence blockers can be given to Hermes controlled pipeline as a real engineering task.

Current DB stash:

```text
stash: codex-preserve-db-persistence-before-controlled-manual-sot-slice
hash: 6e75da1beaf3ff7700003466ecfcdab670aebbfc
```

Known DB blockers to fix later:

```text
- new files unreadable by hermes
- db_persisted false success
```

Do not mix DB persistence with runtime bridge work.

## 8. Review Gates Per Slice

Every implementation slice must include:

```text
- focused tests
- ruff
- git diff --check
- no push
- no live config change
- no gateway restart until explicit ship step
- no DB stash touch unless slice explicitly says so
```

Every production ship requires:

```text
- review
- commit
- gateway restart
- health check
- live smoke with expected behavior
```

## 9. Drift Guards

If a future plan proposes any of the following, stop and re-check SoT:

```text
- orchestrator writes code directly
- fake helper returns completion_allowed in production
- controlled_manual falls back to normal AIAgent
- model-declared changes accepted without observed git delta
- reviewer approves without receiving actual diff/tests
- DB persistence mixed with runtime bridge work
- broad terminal enabled before workspace/tool constraints
```

## 10. Current Next Task

The next task is Slice 2A:

```text
Implement controlled/manual real executor seam wiring with test executor,
without live provider calls.
```

Not DB persistence.

Not full live MiMo/Codex execution.

Not orchestrator patch application.

The immediate success condition is:

```text
controlled_manual can enter execute_bounded_rework_loop through SubagentRunner
with an injected executor, produce observed task-dependent workspace delta in tests,
and still fail closed in production when no real executor is supplied.
```
