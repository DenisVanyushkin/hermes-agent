# Subagent Runtime Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate Hermes from gateway -> `AIAgent` -> `conversation_loop` role metadata to an orchestrated, registry-constrained subagent pipeline runtime.

**Architecture:** Put orchestration above the current agent execution path, route every top-level request through a pipeline router constrained by `config/pipelines/registry.yaml`, and use a Runtime Factory to turn selected subagent contracts into actual provider/model/client/tool execution. Reuse `conversation_loop` as the single-subagent executor after runtime construction rather than keeping it as the top-level workflow owner.

**Tech Stack:** Python, YAML specs under `config/pipelines/` and `config/subagents/`, prompt artifacts under `prompts/subagents/`, existing `AIAgent`/`conversation_loop`, gateway adapters, review gate utilities, pytest.

---

## Scope And Guardrails

This document is a migration plan only. It does not implement runtime code, restart the gateway, commit, push, or modify gateway/conversation loop/review gate/runtime behavior.

Source-of-truth commit:

```text
cf9ddcbdf docs(architecture): define subagent pipeline runtime contracts
```

Architecture source files used:

- `docs/architecture/subagents.md`
- `docs/architecture/pipelines.md`
- `docs/architecture/orchestrator.md`
- `docs/architecture/engineering-pipeline.md`
- `config/pipelines/registry.yaml`
- `config/pipelines/default_conversation_pipeline.yaml`
- `config/pipelines/engineering_review_pipeline.yaml`
- `config/subagents/general_operator.yaml`
- `config/subagents/hermes_pipeline_router.yaml`
- `config/subagents/hermes_engineer_core.yaml`
- `config/subagents/hermes_code_reviewer.yaml`
- `config/subagents/hermes_security_auditor.yaml`
- `prompts/subagents/general_operator.md`
- `prompts/subagents/hermes_code_reviewer.md`
- `prompts/subagents/hermes_engineer_core.md`
- `prompts/subagents/hermes_security_auditor.md`

Initial live git snapshot before writing this plan:

```text
$ git status --short --untracked-files=all
<empty>

$ git diff --stat
<empty>

$ git log --oneline -10
cf9ddcbdf docs(architecture): define subagent pipeline runtime contracts
06869d8fc feat: slack idea-reaction capture, doctor fast-mode, pilot eval report
e36df6636 fix(review-gate): enforce review for tool file mutations
145f7d292 fix(models): honor package roles and code review tier
237cbb6fe fix(models): sanitize all sdk client kwargs
8ae9f4c96 fix(models): keep runtime metadata out of sdk kwargs
dfb718044 fix(review-gate): honor runtime model selection
ade43f1b1 chore(logging): report effective review gate config
17d9711b0 Improve review gate block diagnostics
80468d780 Use GPT tiers for review gate
```

## Current Architecture Summary

The live runtime path is transport-first and agent-centric:

```text
platform adapter
  -> gateway/run.py
  -> GatewayRunner._handle_message_with_agent()
  -> AIAgent(...)
  -> AIAgent.run_conversation()
  -> agent/conversation_loop.py
  -> model call / tool execution loop
  -> agent/turn_finalizer.py
  -> gateway final response delivery
```

Platform adapters handle inbound and outbound transport. `gateway/platforms/slack.py` receives Slack messages and slash commands, extracts text from Slack blocks, handles socket mode, thread metadata, and sends responses through Slack APIs. Telegram and other adapters follow the same adapter boundary: transport semantics stay outside the agent loop.

`gateway/run.py` owns message intake and session handling. It normalizes platform/session context, loads transcript history, prepares inbound text and media context, wires callbacks for streaming, progress, approvals, clarifications, and finally constructs or reuses an `AIAgent`. The main gateway call site resolves the session model/runtime with `_resolve_session_agent_runtime()`, builds `turn_route` with `_resolve_turn_agent_config()`, creates `AIAgent(model=turn_route["model"], **turn_route["runtime"], ...)`, and calls `agent.run_conversation(...)`.

`run_agent.py` defines `AIAgent` and delegates initialization to `agent.agent_init.init_agent`. The constructor accepts provider, model, API mode, base URL, toolset scope, prompt material, callbacks, session metadata, fallback model, credential pool, and checkpoint settings. Today this constructor is the effective runtime construction boundary.

`agent/conversation_loop.py` is the current top-level executor for a turn. It restores the primary runtime, sets runtime-main metadata for auxiliary clients, composes messages, calls `build_role_context_for_task()`, calls `select_model_policy()`, resolves a provider/model request through `hermes_cli.runtime_provider.resolve_runtime_provider()`, logs selected versus actual model metadata, appends role context to the user message, then runs the model/tool loop.

`hermes_cli/profile_execution.py` and related profile routing code provide deterministic role/context classification. `hermes_cli/model_selection.py` maps role and task context to a model policy. The important current limitation is explicit in `ModelSelectionDecision.debug_metadata`: `live_model_mutation` is `False`. The selected role/model are recorded and logged, but they do not reconstruct the already-created `AIAgent` runtime for the main gateway path.

`agent/tool_executor.py` dispatches tool calls, applies request/execution middleware, scopes tool search to enabled toolsets, emits post-tool-call hooks, handles cancellation, and records structured tool results back into the message list. This is the right lower-level place for safety gates that must catch dangerous tools regardless of pipeline state.

`agent/turn_finalizer.py` runs after the tool loop. It persists sessions, builds a role execution plan, evaluates `hermes_cli/review_gate.py`, may replace the final response with a review gate block message, and returns a result dict containing `final_response`, messages, model, token usage, and related metadata. This is a late safety net: useful, but too late to be the primary pipeline architecture because the main worker already ran.

`hermes_cli/review_gate.py` can detect material file mutation from assistant tool calls and `git diff HEAD --name-only`, build review packets, invoke a reviewer, and block final response in enforce mode. It is valuable as a reusable review utility and backup gate, but it is not a full pipeline state machine and does not own baseline capture before execution.

Why selected role/model can remain metadata only today:

- Gateway constructs or reuses `AIAgent` before `conversation_loop` selects role/model.
- `conversation_loop` logs `selected_provider` and `selected_model`, then records `actual_provider` and `actual_model`, but does not replace the live `agent.provider`, `agent.model`, API client, tool permissions, or prompt contract for the selected role.
- Role context is appended into the user message via `_compose_turn_user_message_content()`, making the role mostly prompt/context metadata.
- Review gating happens after execution in `turn_finalizer`, so it can block "done" but cannot ensure the right subagent runtime performed the work from the start.

## Target Architecture Summary

The target architecture from the committed specs is:

```text
gateway
  -> orchestrator
  -> LLM Pipeline Router constrained by registry
  -> selected/default pipeline
  -> Runtime Factory
  -> role-specific subagent runtime
  -> Subagent Runner
  -> structured output
  -> pipeline gates/reporting
```

The Orchestrator is a dispatcher and state-machine owner. It must not do specialist work. It receives the platform request, creates a pipeline session, delegates route selection to the Pipeline Router, selects a pipeline, and owns completion reporting.

The Pipeline Router is a routing subagent or routing component backed by an LLM. It reads the request, platform/session context, safety constraints, and `config/pipelines/registry.yaml`, then returns a schema-constrained decision with one of: `selected`, `no_specialized_pipeline`, `needs_clarification`, `blocked_by_policy`, or `routing_failed`.

The Pipeline Registry is the machine-readable list of legal pipeline choices. In the initial source-of-truth bundle it contains `default_conversation_pipeline` and `engineering_review_pipeline`. The router may not invent pipeline IDs outside this registry.

The default fallback pipeline is explicit:

```text
router_status = no_specialized_pipeline
  -> default_conversation_pipeline
  -> primary subagent = general_operator
```

Fallback on `routing_failed` is allowed only when the orchestrator can prove `fallback_safe == true`. Otherwise it must block transparently.

Each pipeline is a state machine. `engineering_review_pipeline.yaml` defines states for baseline capture, engineer invocation, delta analysis, review, peer discussion, rework, model escalation, approval/blocking/failure, waiver, and completion. Loop limits are explicit: 3 review iterations, 1 peer discussion round per iteration, 1 invalid-output retry, 1 tool retry, 1 model escalation, and 0 clarification rounds.

The Runtime Factory turns a selected subagent contract into actual execution state: selected provider/model, actual provider/model/client, system prompt, tool set, permissions, working directory, secret/env access, prompt-cache layout, logging hooks, token/cache accounting, and safety gates. This is the component that turns `role = runtime contract` into reality.

The Subagent Runner invokes the runtime built by the Runtime Factory and returns structured output to the pipeline. It must capture selected and actual provider/model, token/cache usage, tool calls, failures, elapsed time, prompt/output hashes when available, and artifacts created.

The target reporting contract must distinguish selected versus actual provider/model for router and every subagent invocation. It must also report token usage, cache usage, tool-call summaries, changed files, tests, peer messages, reviewer verdicts, user waivers, completion decisions, and blocking reasons.

## Migration Principles

- Preserve current Slack, Telegram, and other platform behavior initially.
- Introduce the new architecture in observe-mode first, with logs/reports but no user-visible execution-path change.
- Avoid a big-bang rewrite. Each slice must be independently testable and revertible.
- `conversation_loop` should become a reusable single-subagent executor, not the top-level orchestrator.
- Role/subagent selection must happen before runtime construction.
- Selected provider/model must create the actual provider/model/client used for the invocation.
- Default fallback must preserve ordinary conversation behavior through an explicit `general_operator` runtime contract.
- Engineering enforcement must be introduced only after telemetry proves routing and runtime correctness.
- Keep platform adapters as transport adapters. Do not move pipeline state into Slack/Telegram adapter code.
- Keep tool middleware as an enforcement layer for dangerous side effects, even after pipelines exist.
- Keep `turn_finalizer` and `review_gate` as utility/legacy safety nets until the pipeline implementation has proven coverage.

## Proposed Implementation Slices

### Slice A - Spec Loaders And Validators

**Goal:** Load and validate pipeline/subagent specs without changing runtime behavior.

**Likely files touched:**

- Create `hermes_cli/pipeline_specs.py`
- Create `tests/test_pipeline_specs.py`
- Optionally add `scripts/validate_pipeline_architecture.py`
- Optionally wire a read-only doctor/CLI helper after tests exist

**Behavior changes:** None in gateway or agent runtime.

**Implementation checklist:**

- [ ] Load `config/pipelines/registry.yaml`.
- [ ] Load every `config/pipelines/*.yaml` referenced by the registry.
- [ ] Load every `config/subagents/*.yaml` referenced by pipelines and registry entries.
- [ ] Validate unique IDs, `schema_version`, `config_path`, pipeline `subagents`, prompt paths, router statuses, fallback eligibility, state names, loop limits, completion rules, and disagreement decisive authority.
- [ ] Validate every `system_prompt.path` exists under `prompts/subagents/`.
- [ ] Validate every referenced subagent exists.
- [ ] Validate `default_conversation_pipeline` references `general_operator`.
- [ ] Validate `engineering_review_pipeline` references `hermes_engineer_core` and `hermes_code_reviewer`.
- [ ] Return structured errors with path and field names.

**Tests:**

- Unit: valid committed specs load successfully.
- Unit: missing registry config path fails.
- Unit: missing subagent reference fails.
- Unit: missing prompt path fails.
- Unit: pipeline with peer communication but no decisive authority fails.
- Unit: duplicate pipeline or subagent ID fails.

**Risks:** Low. YAML parsing can accidentally become too strict and reject future draft fields.

**Rollback:** Remove new loader/validator files and tests. No runtime state changed.

### Slice B - Pipeline Router Observe-Mode

**Goal:** Produce structured routing decisions against the registry while leaving execution on the existing path.

**Likely files touched:**

- Create `hermes_cli/pipeline_router.py`
- Create `tests/test_pipeline_router.py`
- Modify `gateway/run.py` only to call router in observe-mode around the existing `_handle_message_with_agent()` path, behind a flag

**Behavior changes:** Logs router decisions; does not change execution path.

**Implementation checklist:**

- [ ] Add config flag such as `pipelines.router_mode: disabled|observe|enforce`, default `disabled` or `observe` only after tests prove stability.
- [ ] Build a deterministic/mockable router interface returning a `RouterDecision`.
- [ ] Start with controlled deterministic heuristics in tests and an LLM-backed implementation behind a separate flag.
- [ ] Constrain router output to registry IDs.
- [ ] Log status, selected pipeline, fallback pipeline, confidence, alternatives, and failure reason.
- [ ] For `routing_failed`, compute and log `fallback_safe` but do not change runtime behavior.

**Tests:**

- Unit: default/conversation request returns `no_specialized_pipeline` or default fallback.
- Unit: code mutation request returns `selected` with `engineering_review_pipeline`.
- Unit: invalid selected pipeline ID is rejected as `routing_failed`.
- Unit: router cannot select a pipeline absent from registry.
- Integration/mocked gateway: observe-mode logs a decision and still calls existing agent path exactly once.

**Risks:** Router logs may expose too much request context if not redacted. LLM router latency can affect gateway if accidentally run synchronously on hot path without timeout.

**Rollback:** Disable router flag. Since execution path is unchanged, rollback is configuration-only if the call site is guarded correctly.

### Slice C - Orchestrator Skeleton And Default Conversation Pipeline

**Goal:** Introduce an orchestrator wrapper that can route the default pipeline to existing conversation behavior with no user-visible behavior change.

**Likely files touched:**

- Create `hermes_cli/orchestrator.py`
- Create `hermes_cli/pipeline_state.py`
- Modify `gateway/run.py` at the shared message handling boundary
- Tests under `tests/test_orchestrator.py` and gateway integration tests

**Behavior changes:** In observe/default mode, gateway enters an orchestrator wrapper, creates a pipeline session, and delegates default execution to existing `AIAgent.run_conversation()`.

**Implementation checklist:**

- [ ] Define `PipelineSession` and `PipelineState`.
- [ ] Add `pipeline_session_id` generation.
- [ ] Route every top-level request through orchestrator when `pipelines.enabled` is true.
- [ ] For default pipeline, build a `general_operator` invocation record but execute through the existing single-turn agent path.
- [ ] Emit a final execution report skeleton internally/log-only.
- [ ] Preserve gateway streaming, approvals, clarifications, progress messages, media extraction, and final response delivery.

**Tests:**

- Unit: default pipeline transitions `task_received -> pipeline_selected -> response_generation -> completion_allowed`.
- Integration/mocked gateway: default orchestration preserves final response text and adapter send behavior.
- Regression: non-engineering requests do not trigger review gates or file mutation paths.

**Risks:** Gateway call site is large and stateful. Any wrapper must preserve session locks, stream callbacks, post-turn goal continuation, and adapter metadata.

**Rollback:** Disable `pipelines.enabled` and return to direct `_handle_message_with_agent()`.

### Slice D - Runtime Factory Foundation

**Goal:** Build role-specific runtime construction that actually uses selected provider/model and tool permissions.

**Likely files touched:**

- Create `hermes_cli/runtime_factory.py`
- Create `tests/test_runtime_factory.py`
- Possibly refactor small reusable pieces from `gateway/run.py` runtime resolution into a helper without changing behavior
- Possibly add `agent/subagent_runtime.py` as a thin data container

**Behavior changes:** Initially test-only or observe-mode. No engineering enforcement.

**Implementation checklist:**

- [ ] Accept a subagent spec and invocation context.
- [ ] Resolve selected provider/model from `models.default`.
- [ ] Resolve actual provider/model/client using existing runtime provider and credential resolution.
- [ ] Build scoped toolsets from `tools.read`, `tools.write`, `tools.execute`, `tools.gated`, and `tools.forbidden`.
- [ ] Load `system_prompt.path`.
- [ ] Produce a `RuntimeSelectionRecord` with selected and actual provider/model.
- [ ] Fail closed when required model is unavailable and subagent fallback mode is `fail_closed`.
- [ ] Prove the created runtime can initialize `AIAgent` with the configured provider/model, not the session default.

**Tests:**

- Unit: `hermes_engineer_core` selects OpenRouter `xiaomi/mimo-v2.5-pro`.
- Unit: `general_operator` selects `openai-codex` `gpt-5.4-mini`.
- Unit: missing prompt path fails validation before runtime construction.
- Unit: unavailable model with `fail_closed` returns a blocked runtime result.
- Regression: selected model is not metadata-only; constructed agent receives selected model.
- Regression: selected vs actual provider/model is logged/recorded.

**Risks:** Reusing gateway credential resolution may entangle profile/session overrides. Directly importing gateway code into CLI helpers would create cycles; extract small helpers if needed.

**Rollback:** Keep factory unused behind tests/flags until stable.

### Slice E - Subagent Runner

**Goal:** Invoke one role-specific subagent through the runtime produced by the Runtime Factory and capture structured output.

**Likely files touched:**

- Create `hermes_cli/subagent_runner.py`
- Tests under `tests/test_subagent_runner.py`
- Minor `AIAgent.run_conversation()` wrapper support if needed for explicit system prompt/input/output schema

**Behavior changes:** Test-only or default pipeline in observe-mode at first.

**Implementation checklist:**

- [ ] Define `SubagentInvocationRecord`.
- [ ] Invoke `conversation_loop` through an `AIAgent` configured by Runtime Factory.
- [ ] Inject subagent system prompt and stable role contract.
- [ ] Pass required input fields: task, pipeline session id, constraints, baseline context, and conversation context.
- [ ] Parse and validate structured output.
- [ ] Capture final response, status, token usage, cache usage, tool calls, failures, elapsed time, and artifacts.
- [ ] Return invalid output as a pipeline-handled failure, not as user-facing success.

**Tests:**

- Unit: valid structured output parses.
- Unit: invalid structured output triggers retry/block according to pipeline limits.
- Unit: token/cache usage from result dict is copied into invocation record.
- Unit: tool call summary includes tool name, status, duration/error where available.
- Integration: `general_operator` can answer via runner with no file mutation tools.

**Risks:** Existing `conversation_loop` returns prose by default, not guaranteed JSON. The first implementation may need a strong output-schema prompt and parser retries before relying on it.

**Rollback:** Leave runner unused by gateway until structured output tests are reliable.

### Slice F - Engineering Review Pipeline Observe-Mode

**Goal:** Detect engineering tasks, capture baseline and post-run git snapshots, and log what the engineering pipeline would do without blocking completion.

**Likely files touched:**

- Create `hermes_cli/engineering_pipeline.py`
- Extend `hermes_cli/orchestrator.py`
- Tests under `tests/test_engineering_pipeline_observe.py`
- Possibly reuse `hermes_cli/review_gate.py` path classification helpers

**Behavior changes:** Logs engineering pipeline observations. Does not block final response.

**Implementation checklist:**

- [ ] When router selects `engineering_review_pipeline`, capture baseline:
  - `git status --short --untracked-files=all`
  - `git diff --stat`
  - `git log --oneline -10`
- [ ] Run the current execution path or engineer subagent in observe-mode.
- [ ] Capture post-run status/diff.
- [ ] Compute material delta from git, including untracked files.
- [ ] Build reviewer packet from baseline, current snapshot, changed files, tests run, and engineer output.
- [ ] Optionally run reviewer in observe-mode when configured.
- [ ] Log whether completion would be allowed or blocked.

**Tests:**

- Unit: untracked files count as material changes.
- Unit: dirty baseline is recorded and surfaced.
- Unit: no material delta allows completion.
- Unit: material delta builds reviewer packet.
- Unit: reviewer unavailable in observe-mode logs `would_block=true` without blocking.
- Integration/mocked gateway: engineering request still returns through old path while observe report is emitted.

**Risks:** Dirty baselines from user edits can make delta attribution ambiguous. The pipeline must record baseline dirtiness and avoid claiming all changes belong to the subagent.

**Rollback:** Disable engineering pipeline observe flag.

### Slice G - Engineering Review Pipeline Enforce-Mode

**Goal:** Enforce mandatory review for material engineering changes.

**Likely files touched:**

- `hermes_cli/engineering_pipeline.py`
- `hermes_cli/orchestrator.py`
- `hermes_cli/review_gate.py` only if extracting reusable reviewer utilities is necessary
- Tests under `tests/test_engineering_pipeline_enforce.py`

**Behavior changes:** If material changes exist, completion is blocked unless the reviewer approves, no material changes exist, or the user gives an explicit valid waiver.

**Implementation checklist:**

- [ ] Add config flag `pipelines.engineering.mode: observe|enforce`.
- [ ] If material delta exists, invoke `hermes_code_reviewer`.
- [ ] Treat reviewer `approved` as completion allowed.
- [ ] Treat reviewer `changes_requested`, `blocked`, invalid output unresolved, or unavailable as completion blocked.
- [ ] Render a user-facing block message with changed files, reviewer verdict, and required next action.
- [ ] Keep existing `turn_finalizer` review gate active as backup until the new pipeline proves stronger coverage.

**Tests:**

- Unit: reviewer `approved` allows completion.
- Unit: reviewer `changes_requested` prevents "done".
- Unit: reviewer `blocked` prevents "done".
- Unit: reviewer unavailable prevents "done" in enforce-mode.
- Unit: explicit valid waiver allows completion and records waiver.
- Regression: material changes from untracked files trigger review.

**Risks:** False positives can block legitimate completion. Enforce-mode should be gated to trusted channels/profiles after observe-mode telemetry.

**Rollback:** Set engineering mode back to observe or disable pipelines.

### Slice H - Rework, Disagreement, And Model Escalation

**Goal:** Add bounded engineer-reviewer rework loops, mediated peer messages, and model escalation policy.

**Likely files touched:**

- `hermes_cli/engineering_pipeline.py`
- `hermes_cli/subagent_runner.py`
- `hermes_cli/runtime_factory.py`
- Tests under `tests/test_engineering_pipeline_loops.py`

**Behavior changes:** The pipeline may run multiple engineer/reviewer iterations within configured limits.

**Implementation checklist:**

- [ ] Track `review_iteration`.
- [ ] Feed reviewer findings back to engineer through append-only dynamic additions.
- [ ] Allow one structured engineer objection per iteration.
- [ ] Let reviewer revise or maintain verdict.
- [ ] Apply decisive reviewer policy when disagreement persists.
- [ ] Escalate engineer or reviewer model according to `model_escalation_policy`.
- [ ] Stop hard at loop limits and ask user.

**Tests:**

- Unit: loop stops after 3 review iterations.
- Unit: one peer discussion round is allowed per iteration.
- Unit: unresolved disagreement blocks completion.
- Unit: model escalation does not reset loop counters.
- Unit: escalation unavailable blocks and asks user.
- Unit: selected and actual provider/model are recorded before and after escalation.

**Risks:** Rework loops can become expensive and slow. Token/cache accounting and max iteration limits must be enforced outside the model.

**Rollback:** Disable rework loops and fall back to single review pass.

### Slice I - Commit, Push, And System-Mutation Gating

**Goal:** Gate commit, push, service restart, and destructive operations through pipeline approval or explicit user waiver.

**Likely files touched:**

- `agent/tool_executor.py`
- `hermes_cli/middleware.py` or existing tool middleware modules
- `hermes_cli/orchestrator.py`
- Tests for tool middleware and gateway approvals

**Behavior changes:** Risky tools require pipeline state approval in addition to existing approval prompts.

**Implementation checklist:**

- [ ] Store active pipeline approval state in a place tool middleware can read.
- [ ] Gate `git commit`, `git push`, service restart, and destructive shell commands.
- [ ] Allow commit only after review approval or valid explicit waiver.
- [ ] Allow push only when user requested push and pipeline approval/waiver exists.
- [ ] Keep git hooks as backup safety net, not primary architecture.
- [ ] Log denial as a tool result so the model sees the blocked action.

**Tests:**

- Unit: commit without pipeline approval is denied.
- Unit: commit after reviewer approval is allowed.
- Unit: push without explicit user request is denied.
- Unit: service restart requires explicit user approval.
- Unit: denied tool calls return structured tool results.

**Risks:** Middleware needs robust session/pipeline state lookup. A stale approval state could be dangerous; include pipeline session id and expiry checks.

**Rollback:** Disable pipeline-aware middleware and rely on existing approval/review gate.

## Integration-Point Options

### Option 1 - Above `agent.run_conversation()` In `gateway/run.py`

**What it means:** Route requests in the gateway shared runtime path before constructing or reusing the main `AIAgent`.

**Pros:**

- Correctly happens before runtime construction.
- Preserves platform adapters as transport-only code.
- Can keep existing `_handle_message_with_agent()` as the default execution adapter during observe-mode.
- Gives orchestrator access to platform/session context, transcript, adapter metadata, and user-facing delivery policy.

**Cons/Risks:**

- `gateway/run.py` is large and stateful.
- Must preserve session locking, streaming, approvals, clarifications, and post-turn goal continuation.
- Careless wrapping can duplicate sends or lose callbacks.

**Use when:** Primary migration path. This is the best place to introduce orchestration without rewriting adapters or the agent loop.

### Option 2 - Inside `AIAgent.run_conversation()`

**What it means:** Put orchestrator entry inside the `AIAgent` method or the forwarding path to `conversation_loop`.

**Pros:**

- Centralizes CLI/gateway/background agent behavior.
- Has direct access to the agent instance, current tools, model, and session state.

**Cons/Risks:**

- Too late for selected role/model to drive actual runtime construction, because `AIAgent` already exists.
- Reinforces `conversation_loop` as top-level orchestrator.
- Harder to route to multiple subagent runtimes cleanly.

**Use when:** Only for reusable single-subagent execution hooks after the Runtime Factory has created the right `AIAgent`.

### Option 3 - Inside `conversation_loop.py`

**What it means:** Expand current role context/model selection into the pipeline orchestrator.

**Pros:**

- Close to existing role metadata logging.
- Can reuse existing model/tool loop variables.

**Cons/Risks:**

- Violates target invariant that routing and role selection happen before runtime construction.
- Keeps selected model as metadata unless the loop mutates the live runtime mid-turn.
- Makes an already-large executor own workflow state, routing, review, and final reporting.

**Use when:** Do not use as primary integration point. Refactor it into a reusable subagent executor instead.

### Option 4 - Tool Executor / Tool Middleware

**What it means:** Enforce safety policy at tool-call boundaries.

**Pros:**

- Strong place for side-effect controls that must not rely on prompt compliance.
- Catches dangerous actions from any path, including legacy paths.
- Existing middleware hooks already exist in `agent/tool_executor.py`.

**Cons/Risks:**

- Cannot choose pipeline or construct runtimes.
- Too late to decide which subagent should handle a task.
- Needs reliable access to active pipeline state.

**Use when:** Commit/push/destructive/system mutation gating and defense in depth.

### Option 5 - `turn_finalizer` / `review_gate`

**What it means:** Keep final response blocking and review packet logic at post-loop finalization.

**Pros:**

- Existing code already detects material changes and can block completion.
- Good backup safety net during migration.
- Useful utility code for reviewer packet construction.

**Cons/Risks:**

- Runs after the main work is complete.
- Cannot ensure the selected model/client/toolset was used.
- Cannot own pipeline state, baseline capture, or bounded rework loops.

**Use when:** Legacy safety net and reusable helper, not primary workflow.

### Option 6 - `hermes_cli/profile_execution.py`

**What it means:** Extend deterministic profile routing into pipeline routing.

**Pros:**

- Existing role/operation-category classification can seed router context.
- Pure/import-light code is easy to test.

**Cons/Risks:**

- Current profile execution is deterministic role metadata, not a registry-constrained pipeline router.
- It does not own gateway session state, runtime construction, or tool gates.

**Use when:** As an input signal or compatibility bridge, not as the orchestrator.

### Recommended Integration Strategy

Primary orchestration should sit above `agent.run_conversation()` in the gateway/shared runtime path. The orchestrator should create pipeline state before any specialist execution, call the Pipeline Router, select/default the pipeline, then ask the Runtime Factory to create role-specific subagent runtimes.

`conversation_loop` should be reused as the single-subagent invocation engine after runtime construction. It should not decide the top-level workflow.

Tool middleware should provide safety gates for commit, push, restart, destructive shell commands, and other privileged side effects.

`turn_finalizer` and `review_gate` should be demoted to utility/legacy safety net. They can continue blocking unsafe completions during the migration, but the target architecture should make pipeline gates decisive before the final response is produced.

## Minimal Data Model And Structured Events

### `pipeline_session_id`

Format:

```text
pipe_<utc-yyyymmddThhmmssZ>_<platform>_<short-session-hash>_<random8>
```

Example:

```text
pipe_20260614T083015Z_slack_a1b2c3d4_f09e7a12
```

Requirements:

- unique per top-level request;
- stable across subagent invocations and rework loops;
- included in logs, structured events, subagent input, tool middleware context, and final report.

### Router Decision Object

Fields:

- `pipeline_session_id`
- `router_subagent_id`
- `status`
- `selected_pipeline_id`
- `fallback_pipeline_id`
- `confidence`
- `reasoning_summary`
- `requires_clarification`
- `clarification_question`
- `policy_block_reason`
- `routing_failure_reason`
- `alternatives`
- `fallback_safe`
- `selected_provider`
- `selected_model`
- `actual_provider`
- `actual_model`
- `token_usage`
- `cache_usage`

### Pipeline State Object

Fields:

- `pipeline_session_id`
- `pipeline_id`
- `state`
- `mode`
- `platform`
- `session_key`
- `user_message_hash`
- `baseline_git_snapshot`
- `current_git_snapshot`
- `material_changes_detected`
- `changed_files`
- `review_iteration`
- `peer_discussion_round`
- `model_escalation_count`
- `completion_allowed`
- `completion_blocked_reason`
- `final_verdict`

### Subagent Invocation Record

Fields:

- `pipeline_session_id`
- `invocation_id`
- `subagent_id`
- `pipeline_state_before`
- `pipeline_state_after`
- `input_schema_version`
- `output_status`
- `output_summary`
- `structured_output_valid`
- `failure_reason`
- `started_at`
- `elapsed_ms`

### Runtime Selection Record

Fields:

- `pipeline_session_id`
- `invocation_id`
- `subagent_id`
- `selected_provider`
- `selected_model`
- `selected_model_class`
- `actual_provider`
- `actual_model`
- `base_url_host`
- `api_mode`
- `fallback_used`
- `fallback_reason`
- `escalation_reason`

### Token And Cache Usage Record

Fields:

- `pipeline_session_id`
- `invocation_id`
- `input_tokens`
- `output_tokens`
- `total_tokens`
- `cache_read_tokens`
- `cache_write_tokens`
- `reasoning_tokens`
- `usage_source`
- `estimated_cost_usd`

### Tool Call Summary

Fields:

- `pipeline_session_id`
- `invocation_id`
- `tool_call_id`
- `tool_name`
- `tool_risk_class`
- `allowed_by_pipeline`
- `gated`
- `approval_required`
- `approval_status`
- `status`
- `duration_ms`
- `error_type`
- `mutation_targets`

### Peer Message Record

Fields:

- `pipeline_session_id`
- `from_subagent_id`
- `to_subagent_id`
- `round`
- `purpose`
- `message_hash`
- `summary`
- `pipeline_state`

### Reviewer Verdict Record

Fields:

- `pipeline_session_id`
- `review_iteration`
- `reviewer_subagent_id`
- `verdict`
- `summary`
- `findings`
- `required_changes`
- `tests_required`
- `confidence`
- `decisive`
- `invalid_output_retry_count`

### User Waiver Record

Fields:

- `pipeline_session_id`
- `waiver_id`
- `requested_by`
- `scope`
- `reason`
- `accepted_risks`
- `expires_at`
- `applies_to_changed_files`
- `recorded_at`

### Completion Decision Record

Fields:

- `pipeline_session_id`
- `pipeline_id`
- `completion_allowed`
- `reason`
- `blocked_reason`
- `reviewer_verdict`
- `user_waiver_id`
- `material_changes_detected`
- `changed_files`
- `final_report_hash`

## Testing Strategy

### Slice A Tests

- Unit: committed YAML/spec validation passes.
- Unit: missing `config_path` fails.
- Unit: missing subagent reference fails.
- Unit: missing prompt path fails.
- Unit: invalid router status fails.
- Unit: peer communication without decisive authority fails.

### Slice B Tests

- Unit: router can select default versus engineering.
- Unit: invalid LLM router output is rejected.
- Unit: no selected pipeline is required for `no_specialized_pipeline`.
- Unit: `routing_failed` does not silently choose default unless `fallback_safe == true`.
- Integration/mocked gateway: observe-mode logs route without changing execution.

### Slice C Tests

- Unit: default state machine transitions are valid.
- Integration/mocked gateway: default path returns the same final response shape.
- Regression: non-engineering requests preserve behavior.
- Regression: platform delivery semantics are unchanged.

### Slice D Tests

- Unit: runtime factory creates actual configured provider/model.
- Unit: no selected model remains metadata-only.
- Unit: selected versus actual provider/model is logged.
- Unit: unavailable model fails closed for subagent specs with `fallback.mode: fail_closed`.
- Unit: tool permissions are derived from subagent spec.

### Slice E Tests

- Unit: subagent structured output parses and validates.
- Unit: invalid output triggers retry/block.
- Unit: prompt artifacts load from `prompts/subagents/*.md`.
- Unit: token/cache/tool accounting is captured.
- Integration: general operator can run with read-only/no-write tools.

### Slice F Tests

- Unit: material changes trigger review path in observe-mode.
- Unit: untracked files count as material changes.
- Unit: dirty baseline is recorded.
- Unit: no material changes allow completion.
- Integration/mocked gateway: observe-mode does not block final response.

### Slice G Tests

- Unit: reviewer approved allows completion.
- Unit: reviewer `changes_requested` prevents "done" in enforce-mode.
- Unit: reviewer `blocked` prevents "done" in enforce-mode.
- Unit: reviewer unavailable prevents "done" in enforce-mode.
- Unit: explicit valid waiver permits completion and is reported.

### Slice H Tests

- Unit: loop limits stop cycles.
- Unit: one peer discussion round is allowed.
- Unit: unresolved disagreement blocks.
- Unit: model escalation records before/after selected and actual provider/model.
- Unit: escalation does not reset loop counters.

### Slice I Tests

- Unit: commit/push/system mutation tool calls require pipeline approval or waiver.
- Unit: push also requires explicit user request.
- Unit: denied tool calls return structured tool results.
- Integration: existing gateway approval prompts still work.

### Live Smoke Tests

Run only after explicit restart permission:

- Slack low-risk conversational request: routes to default, behavior unchanged.
- Slack engineering request in observe-mode: logs engineering pipeline decision and baseline, does not block.
- Slack engineering request in enforce-mode test channel: material change triggers reviewer gate.
- Telegram low-risk request: default path behavior unchanged.
- Gateway logs contain pipeline session id, router decision, selected vs actual provider/model, and completion decision.

## Rollout Strategy

### Flags

Recommended flags:

- `pipelines.enabled: false|true`
- `pipelines.router.mode: disabled|observe|enforce`
- `pipelines.default.mode: observe|enforce`
- `pipelines.engineering.mode: observe|enforce`
- `pipelines.runtime_factory.enabled: false|true`
- `pipelines.reviewer.auto_run_in_observe: false|true`
- `pipelines.tool_gates.enabled: false|true`
- `pipelines.report.user_visible: false|true`

### Metrics To Watch

- router decision count by status;
- selected pipeline count by pipeline id;
- fallback count and fallback reasons;
- router invalid output count;
- selected vs actual provider/model mismatch count;
- subagent invocation count and failure count;
- token/cache usage by subagent;
- material delta count;
- reviewer verdict count;
- completion blocked count and reason;
- tool gate denial count;
- latency per pipeline and per subagent.

### Logs To Inspect

- gateway inbound message and session logs;
- router decision logs;
- runtime selection logs;
- subagent invocation logs;
- review gate and pipeline completion logs;
- tool middleware denial logs;
- final execution report logs.

### Slack Live Smoke Strategy

1. Enable Slice B observe-mode only.
2. Send a normal low-risk Slack message; verify response text and thread behavior are unchanged.
3. Send an engineering-looking request that should not actually mutate files; verify route logs select engineering but execution still follows existing path.
4. Enable default orchestrator observe/default mode.
5. Repeat low-risk Slack and Telegram requests.
6. Enable engineering observe-mode in a controlled channel.
7. Ask for a tiny docs-only mutation only after explicit approval; verify baseline/current snapshots and reviewer packet logging.
8. Do not enable enforce-mode on broad channels until observe-mode telemetry has no selected/actual model surprises.

### Rollback Plan

- Prefer configuration rollback first: disable `pipelines.enabled`.
- If router causes latency or bad decisions, set `pipelines.router.mode: disabled`.
- If Runtime Factory causes provider/model failures, disable factory and return to session runtime.
- If engineering pipeline blocks too aggressively, set `pipelines.engineering.mode: observe`.
- If tool gates block legitimate work, disable pipeline-aware tool gates while keeping existing approval prompts.
- Do not restart gateway until explicit user permission is granted.
- Do not push until the user requests it.

## Failure Modes

- Router unavailable: fail closed unless default fallback is proven safe; log `routing_failed`.
- Invalid router output: reject output, optionally retry once, then block or safe-fallback only if `fallback_safe == true`.
- No pipeline selected: use default only for `no_specialized_pipeline`; otherwise block.
- Default fallback unsafe: block transparently and ask user for clarification or approval.
- Subagent model unavailable: fail closed according to subagent fallback policy.
- Selected model does not match actual model: record mismatch; in enforce-mode block if mismatch violates subagent policy.
- Prompt path missing: spec validation fails before runtime.
- Tool permission violation: tool middleware denies and returns structured tool result.
- Git repo unavailable: engineering pipeline blocks because baseline/delta cannot be trusted.
- Dirty baseline: record baseline dirtiness; review packet must distinguish pre-existing changes from subagent changes.
- Reviewer unavailable: observe-mode logs would-block; enforce-mode blocks completion.
- Reviewer invalid output: retry within limit; block when unresolved.
- Loop limit exceeded: block and ask user.
- Disagreement unresolved: decisive reviewer policy applies, then escalation or block.
- Token usage unavailable: record `usage_source=unavailable`; do not fail default conversation solely for missing usage, but keep report explicit.
- Cache usage unavailable: record unavailable rather than guessing.
- Gateway adapter send failure: preserve existing adapter error handling; pipeline state should record final delivery failure separately from execution completion.
- Runtime state lost across gateway restart: pipeline session should be resumable or mark interrupted; do not assume approval state survived unless persisted.
- User waiver ambiguous: reject waiver and ask for explicit scope.

## Open Questions For Denis

- Should initial `pipelines.router.mode` default to `disabled` or `observe` after Slice B lands?
- Should pipeline execution reports be user-visible in Slack/Telegram immediately, or log-only until enforce-mode?
- Where should durable pipeline events live: existing session DB, a new SQLite table, structured logs only, or both?
- Should `general_operator` truly forbid all write tools from day one, or should default fallback initially mirror current tool access in observe-mode for behavior preservation?
- Which channels/profiles should be allowed to test engineering enforce-mode first?
- What is the required shape for explicit user waivers in chat: free text, `/waive-review`, approval button, or all of these?
- Should the LLM Pipeline Router use `hermes_pipeline_router` as a normal subagent through Runtime Factory from the start, or use a deterministic/router-client bridge until Runtime Factory is ready?
- Should commit/push gating apply to all sessions or only sessions where `engineering_review_pipeline` is active?

## Recommended First Implementation Task

Start with Slice A - Spec Loaders And Validators.

Exact first coding task:

```text
Create hermes_cli/pipeline_specs.py and tests/test_pipeline_specs.py.
Implement read-only loaders for config/pipelines/registry.yaml, config/pipelines/*.yaml, and config/subagents/*.yaml.
Validate IDs, config_path references, subagent references, prompt paths, fallback/default pipeline invariants, loop limits, and decisive disagreement policy.
Expose a test helper or optional CLI/doctor validation entry point.
Do not call the loader from gateway runtime yet.
```

Why this is first:

- It has no runtime behavior change.
- It makes the committed architecture bundle executable as a contract.
- It catches broken refs before any router/orchestrator code depends on them.
- It gives later slices a typed, tested source of truth instead of ad hoc YAML reads.

