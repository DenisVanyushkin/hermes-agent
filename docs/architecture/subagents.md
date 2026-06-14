# Subagents Architecture

Status: Draft v0.3
Canonical source: `docs/hermes-subagent-architecture-source-of-truth-v2.md` from the architecture track

## Purpose

Hermes subagents are specialist executors with runtime-bound contracts. A subagent is not a prompt flavor applied after a generic runtime already exists. The contract must determine the actual provider, selected model, actual model, tools, permissions, IO schema, and reporting behavior used for the invocation.

Core rule:

```text
role != prompt
role = runtime contract
```

## Runtime contract

A subagent spec must define:

- purpose and selection rules;
- allowed, gated, and forbidden tools;
- actual model policy, fallback mode, and escalation rules;
- prompt-cache policy or explicit inheritance from the pipeline;
- system prompt path or an explicit statement that prompt material is deferred;
- input and output schemas;
- write and execution permissions;
- communication policy with peer subagents;
- observability and failure behavior.

Subagents do not select themselves, do not declare the overall user task complete, and do not bypass pipeline gates. They return structured output to the pipeline.

## Required runtime-backed subagents

The current draft architecture requires explicit specs for these runtime contracts:

- `general_operator` as the safe fallback worker for `default_conversation_pipeline`;
- `hermes_pipeline_router` as the LLM-backed pipeline selector;
- `hermes_engineer_core` as the mutable engineering worker;
- `hermes_code_reviewer` as the decisive review specialist in the reference engineering pipeline;
- `hermes_security_auditor` as the read-first optional specialist for security-sensitive engineering work.

The fallback path must not silently inherit the session default runtime. `general_operator` must be defined as a real subagent contract in `config/subagents/general_operator.yaml`.

Draft prompt artifacts for these contracts are included under `prompts/subagents/` so the runtime contracts are not left dangling.

## Conceptual schema

```yaml
id: hermes_engineer_core
display_name: Hermes Engineer
version: 1
purpose: Implements code, config, script, and test changes for Hermes engineering tasks.
selection:
  choose_when:
    - user asks for repo mutation, debugging, refactor, or repair
  do_not_choose_when:
    - task is documentation-only
    - task is career-only
models:
  default:
    provider: openrouter
    model: xiaomi/mimo-v2.5-pro
    class: base_coding
  allowed:
    - provider: openrouter
      model: xiaomi/mimo-v2.5-pro
      class: base_coding
    - provider: openai-codex
      model: gpt-5.5
      class: senior_coding
  escalation:
    allowed: true
    rules:
      - condition: blockers_persist_after_2_review_cycles
        escalate_to:
          provider: openai-codex
          model: gpt-5.5
          class: senior_coding
  fallback:
    mode: fail_closed
tools:
  read: [file_read, grep, git_status, git_diff, test_result_read]
  write: [patch, write_file]
  execute: [terminal, pytest]
  gated: [git_commit, git_push, service_restart, destructive_shell_commands]
  forbidden: [credential_exfiltration, unrelated_repo_mutation]
permissions:
  can_mutate_files: true
  can_restart_services: requires_explicit_user_approval
  can_commit: requires_pipeline_approval
  can_push: requires_pipeline_approval_and_user_request
prompt_cache_policy:
  stable_prompt_prefix_required: true
  preserve_original_prompt_position: true
  dynamic_additions_append_only: true
  dynamic_additions:
    - reviewer_findings
    - peer_messages
    - escalation_notes
system_prompt:
  path: prompts/subagents/hermes_engineer_core.md
input_schema:
  required: [task, repo, pipeline_session_id, constraints, baseline_context]
output_schema:
  status:
    enum: [done, needs_user_input, blocked, failed, disagree_with_reviewer]
  required_fields: [summary, changed_files, tests_run, risks, confidence]
communication:
  can_send_to: [hermes_code_reviewer, hermes_security_auditor]
  can_receive_from: [hermes_code_reviewer, hermes_security_auditor]
  channel_policy:
    mode: mediated_by_pipeline
    direct_uncontrolled_chat: false
observability:
  log_selected_provider_model: true
  log_actual_provider_model: true
  log_token_usage: true
  log_cache_usage: true
  log_tool_calls: true
  log_mutation_events: true
failure_policy:
  invalid_output: pipeline_blocks_or_retries
  model_unavailable: fail_closed
  tool_failure: return_failed_with_details
```

## Communication model

Subagents may exchange structured peer messages only when the current pipeline allows it. The pipeline owns:

- which subagents may talk;
- why communication is allowed;
- how many rounds are allowed;
- what schema is required;
- which side is decisive if disagreement remains.

No uncontrolled agent chat is allowed. All peer messages must be logged and included in the execution report.

## Disagreement rules

Every pipeline that permits disagreement must define a decisive authority. Supported patterns:

1. decisive specialist, for example reviewer owns the review verdict;
2. dedicated arbitrator subagent;
3. rerun with a higher-class model under escalation policy;
4. block and escalate to the user.

If the decisive rule is not explicit, the pipeline is invalid.

## Model policy and escalation

Model policy is part of the subagent contract, not advisory metadata. The selected provider and selected model must be recorded before invocation, and the actual provider and actual model used by the runtime must be reported after invocation.

Escalation rules must be explicit, policy-bound, and logged. Valid triggers include:

- persistent blockers across allowed iterations;
- repeated invalid structured output;
- low confidence;
- unresolved peer disagreement.

Subagent or pipeline specs must also declare the bound on model escalations. Model escalation does not reset loop counters unless the pipeline explicitly says so.

If the escalation target is unavailable, Hermes must fail closed and ask the user.

## Prompt-cache discipline

Subagents that operate inside cyclic pipelines must keep a stable prompt prefix. Recommended order:

1. system prompt;
2. stable role contract;
3. stable tool and permission policy;
4. stable output schema;
5. original user task;
6. stable pipeline context;
7. dynamic iteration additions such as reviewer findings, peer messages, and escalation notes.

Rule: `dynamic_additions_append_only = true`. The original task should not be rewritten between iterations.

If the prompt-cache policy is defined at the pipeline layer, cyclic subagent specs must explicitly inherit it rather than relying on implicit behavior.

## Observability contract

Each invocation should emit:

- selected provider and model;
- actual provider and model;
- prompt and output hashes where available;
- token and cache usage;
- tool calls;
- elapsed time;
- status;
- artifacts created;
- peer messages if any.

This is required so routing, review, escalation, fallback behavior, and cost behavior can be audited later.
