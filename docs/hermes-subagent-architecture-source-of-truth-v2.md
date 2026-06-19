# Hermes Subagent Architecture — Source of Truth

Status: Draft v0.2  
Owner: Denis / Hermes architecture track  
Purpose: define the target architecture for subagents, pipelines, orchestration, runtime selection, pipeline routing, inter-agent communication, model escalation, loop limits, prompt-cache discipline, disagreement resolution, and execution reporting.

---

## 1. Core Principle

Hermes must not treat a role as a prompt decoration inside one already-created generic agent.

A role must be a runtime contract.

```text
role != prompt
role = runtime contract
```

A pipeline must not be one model call.

```text
pipeline != one LLM call
pipeline = controlled workflow of subagent calls, checks, decisions, gates, and user interactions
```

The orchestrator must not do specialist work itself.

```text
orchestrator != engineer/reviewer/researcher
orchestrator = dispatcher + state machine owner + policy gatekeeper
```

Pipeline routing must not be a brittle chain of hardcoded `if` statements.

```text
pipeline_selection != hardcoded if/else tree
pipeline_selection = LLM-assisted routing decision constrained by a registry and schema
```

Target architecture:

```text
User / Slack / Telegram message
  -> Hermes Orchestrator
      -> Pipeline Router subagent evaluates request against Pipeline Registry
      -> selected pipeline OR default fallback pipeline
      -> create pipeline session
      -> pipeline state machine
          -> select subagent(s)
          -> Runtime Factory creates actual role-specific runtimes
          -> Subagent Runner invokes subagents
          -> pipeline evaluates structured outputs
          -> subagents may exchange messages through controlled channel
          -> pipeline applies gates, loop limits, model escalation, completion rules
      -> final response + execution report
```

---

## 2. Main Architectural Components

### 2.1. Subagent Spec

A Subagent Spec describes a specialist executor.

It defines:

- what the subagent is for;
- when the orchestrator/pipeline may choose it;
- what tools it may use;
- what tools are forbidden or gated;
- what system prompt it receives;
- what models it may use;
- how model selection and escalation work;
- what input schema it expects;
- what output schema it must return;
- what permissions it has;
- how it may communicate with other subagents;
- what observability data it must emit;
- what failure behavior applies.

Subagents do not select themselves.

Subagents do not decide whether the whole user request is complete.

Subagents do not bypass pipeline gates.

Subagents return structured results to the pipeline.

### 2.2. Pipeline Spec

A Pipeline Spec describes the algorithm for resolving a class of user requests.

It defines:

- entry conditions;
- involved subagents;
- state machine;
- decision rules;
- loop rules;
- model escalation rules;
- disagreement resolution rules;
- decisive subagent rules;
- user interaction points;
- completion rules;
- failure modes;
- artifacts and reports;
- policy gates.

The pipeline owns the execution flow.

A subagent may say “I am done with my part,” but only the pipeline decides whether the overall task is complete.

### 2.3. Orchestrator

The Orchestrator is the top-level dispatcher.

It receives a user/platform message and decides:

- what the user is asking for;
- which pipeline should handle the request;
- whether the request needs clarification;
- whether no specialized pipeline applies;
- whether the default pipeline should be used;
- whether the request is unsafe or impossible;
- whether user approval is required;
- how to present the final result.

The orchestrator should not perform specialist work directly.

It should delegate routing to the Pipeline Router and delegate work to a selected pipeline.

### 2.4. Pipeline Router

The Pipeline Router is a specialized routing subagent or routing component backed by an LLM.

It is responsible for selecting the best pipeline from the Pipeline Registry.

It must not be implemented as only a hardcoded `if/else` tree.

Hard rules may exist as guardrails, but the final semantic routing decision should be made by an LLM using a structured routing schema.

The Pipeline Router receives:

- user message;
- platform context;
- current conversation/session context;
- available pipeline registry;
- pipeline descriptions;
- entry criteria;
- safety constraints;
- recent state if relevant.

It returns a structured routing decision:

```json
{
  "status": "selected",
  "pipeline_id": "engineering_review_pipeline",
  "confidence": 0.93,
  "reasoning_summary": "The user asks to modify Hermes engineering architecture and likely repository files.",
  "alternatives": [
    {
      "pipeline_id": "architecture_discussion_pipeline",
      "confidence": 0.42
    }
  ],
  "requires_clarification": false
}
```

Allowed router statuses:

```text
selected
no_specialized_pipeline
needs_clarification
blocked_by_policy
routing_failed
```

The Pipeline Router should itself have a Subagent Spec:

```yaml
id: hermes_pipeline_router
purpose: Selects the most appropriate pipeline for a user request.
models:
  default:
    provider: openai-codex
    model: gpt-5.4-mini
  allowed:
    - provider: openai-codex
      model: gpt-5.4-mini
    - provider: openai-codex
      model: gpt-5.5
  escalation:
    allowed: true
    rules:
      - condition: low_confidence_or_ambiguous_pipeline_selection
        escalate_to:
          provider: openai-codex
          model: gpt-5.5
tools:
  read:
    - pipeline_registry_read
    - session_context_read
  write: []
  execute: []
permissions:
  can_mutate_files: false
  can_call_external_tools: false
output_schema:
  status:
    enum:
      - selected
      - no_specialized_pipeline
      - needs_clarification
      - blocked_by_policy
      - routing_failed
```

### 2.5. Default Fallback Pipeline

If no specialized pipeline is selected, Hermes must not fail silently or guess a random specialist workflow.

There must be a default path.

The default pipeline handles ordinary conversational, explanatory, planning, and low-risk tasks.

Default behavior:

```text
Pipeline Router returns no_specialized_pipeline
  -> Orchestrator selects default_conversation_pipeline
  -> general operator/general assistant subagent responds
```

Default fallback pipeline:

```yaml
id: default_conversation_pipeline
purpose: Handles requests that do not match any specialized pipeline.
entry_conditions:
  - router_status == no_specialized_pipeline
  - no policy block
subagents:
  primary: general_operator
completion_rules:
  can_say_done:
    - response_generated
  cannot_mutate_files_without_reclassification: true
```

If the default pipeline detects that the task actually requires a specialized workflow, it should return:

```text
status = reclassification_required
```

and hand control back to the orchestrator.

### 2.6. Runtime Factory

The Runtime Factory creates actual role-specific subagent runtimes.

It must ensure that selected role/model/tool policy becomes real execution state, not metadata.

For example:

```text
selected_role = engineer
selected_provider = openrouter
selected_model = xiaomi/mimo-v2.5-pro
```

must result in an actual LLM call using:

```text
provider = openrouter
model = xiaomi/mimo-v2.5-pro
```

not the session/default model.

The Runtime Factory is responsible for constructing:

- provider;
- model;
- API client/runtime;
- system prompt;
- tool set;
- tool permissions;
- working directory;
- secrets/env access;
- context window policy;
- prompt-cache policy;
- logging hooks;
- token accounting hooks;
- safety gates.

### 2.7. Subagent Runner

The Subagent Runner invokes a subagent runtime and returns structured output to the pipeline.

It should capture:

- actual provider/model;
- prompt/input hash;
- response/output hash;
- token usage;
- cache hit/cache write information if available;
- tool calls;
- elapsed time;
- failure status;
- artifacts created.

---

## 3. Subagent Spec Schema

Canonical YAML-like shape:

```yaml
id: hermes_engineer_core
display_name: Hermes Engineer
version: 1

purpose: >
  Implements code, config, scripts, tests, and repository changes for Hermes-related engineering tasks.

selection:
  choose_when:
    - user asks to modify code, configs, tests, scripts, deployment logic, or repo behavior
    - task touches agent/**, gateway/**, hermes_cli/**, job_intel/**, scripts/**, tests/**, config/**, cron/**
    - task requires debugging, refactoring, implementation, or test repair
  do_not_choose_when:
    - task is only a career/job evaluation
    - task is only writing or documentation with no repo mutation
    - task is only security review without implementation request

models:
  default:
    provider: openrouter
    model: xiaomi/mimo-v2.5-pro
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
      - after_review_cycles_with_blockers: 2
        escalate_to:
          provider: openai-codex
          model: gpt-5.5
          class: senior_coding
      - disagreement_unresolved_after_peer_discussion: true
        escalate_to:
          provider: openai-codex
          model: gpt-5.5
          class: senior_coding
  fallback:
    mode: fail_closed
    reason: no approved coding model available

tools:
  read:
    - file_read
    - grep
    - git_status
    - git_diff
    - test_result_read
  write:
    - patch
    - write_file
  execute:
    - terminal
    - pytest
  gated:
    - systemctl_restart
    - service_restart
    - git_commit
    - git_push
    - destructive_shell_commands
  forbidden:
    - credential_exfiltration
    - unrelated_repo_mutation

permissions:
  can_mutate_files: true
  can_restart_services: requires_explicit_user_approval
  can_commit: requires_pipeline_approval
  can_push: requires_pipeline_approval_and_user_request

system_prompt:
  path: prompts/subagents/hermes_engineer_core.md

prompt_cache_policy:
  stable_prefix_required: true
  dynamic_additions_after_base_prompt: true
  ordering:
    - system_prompt
    - stable_role_contract
    - stable_tool_policy
    - stable_output_schema
    - original_user_task
    - stable_pipeline_context
    - dynamic_iteration_additions
    - reviewer_findings
    - peer_messages
    - retry_or_escalation_notes

input_schema:
  required:
    - task
    - repo
    - pipeline_session_id
    - constraints
    - baseline_context
  optional:
    - reviewer_findings
    - peer_messages
    - previous_attempts
    - escalation_reason

output_schema:
  status:
    enum:
      - done
      - needs_user_input
      - blocked
      - failed
      - disagree_with_reviewer
  required_fields:
    - summary
    - changed_files
    - tests_run
    - risks
    - confidence
  optional_fields:
    - reviewer_objections
    - questions_for_user
    - questions_for_peer_subagent

communication:
  can_send_to:
    - hermes_code_reviewer
    - hermes_security_auditor
  can_receive_from:
    - hermes_code_reviewer
    - hermes_security_auditor
  channel_policy:
    mode: mediated_by_pipeline
    direct_uncontrolled_chat: false

observability:
  log_actual_provider_model: true
  log_token_usage: true
  log_cache_usage: true
  log_tool_calls: true
  log_mutation_events: true
  log_peer_messages: true

failure_policy:
  invalid_output: pipeline_blocks_or_retries
  model_unavailable: fail_closed
  tool_failure: return_failed_with_details
```

---

## 4. Pipeline Spec Schema

Canonical YAML-like shape:

```yaml
id: engineering_review_pipeline
display_name: Engineering Review Pipeline
version: 1

purpose: >
  Execute code-changing engineering tasks with mandatory review, bounded rework loop,
  model escalation, disagreement resolution, and final execution report.

entry_conditions:
  any:
    - selected_role in [engineer, hermes_engineer_core]
    - task_classification.domain == engineering
    - task_intent includes code_mutation
    - target_paths match engineering_path_patterns

subagents:
  engineer: hermes_engineer_core
  reviewer: hermes_code_reviewer
  optional:
    - hermes_security_auditor

state_machine:
  states:
    - task_received
    - pipeline_selected
    - baseline_captured
    - engineer_started
    - engineer_completed
    - git_delta_analyzed
    - review_requested
    - review_completed
    - peer_discussion_requested
    - peer_discussion_completed
    - disagreement_unresolved
    - rework_requested
    - model_escalated
    - approved
    - blocked
    - failed
    - user_waived
    - completion_allowed
    - completion_blocked

loop_policy:
  max_review_iterations: 3
  max_peer_discussion_rounds_per_iteration: 1
  max_invalid_output_retries: 1
  max_tool_retries: 1
  on_loop_limit_exceeded: block_and_escalate_to_user

model_escalation_policy:
  enabled: true
  rules:
    - condition: reviewer_blockers_persist_after_2_iterations
      target_subagent: engineer
      escalate_to_model_class: senior_coding
    - condition: reviewer_output_invalid_twice
      target_subagent: reviewer
      escalate_to_model_class: senior_review
    - condition: disagreement_unresolved_after_allowed_peer_discussion
      target_subagent: decisive_subagent_or_arbitrator
      escalate_to_model_class: senior_reasoning
  on_escalation_unavailable: block_and_escalate_to_user

communication_policy:
  subagents_may_exchange_messages: true
  mediated_by_pipeline: true
  max_rounds: 1
  allowed_cases:
    - engineer_disagrees_with_reviewer
    - reviewer_needs_clarification_from_engineer
    - security_auditor_requests_explanation
  final_decision_owner: pipeline

disagreement_policy:
  enabled: true
  decisive_subagent: hermes_code_reviewer
  arbitrator_subagent: null
  escalation_allowed: true
  escalation_trigger: disagreement_unresolved_after_allowed_peer_discussion
  rules:
    - engineer_may_object_to_reviewer_once_per_iteration
    - reviewer_must_respond_with_revised_or_maintained_verdict
    - if_reviewer_maintains_blocker_then_pipeline_treats_blocker_as_authoritative
    - if_evidence_conflict_remains_then_escalate_or_block
  on_unresolved_disagreement: block_and_escalate_to_user

prompt_cache_policy:
  stable_prompt_prefix_required: true
  preserve_original_prompt_position: true
  dynamic_additions_append_only: true
  rationale: >
    In cyclic pipelines, repeated invocations should keep the stable prompt prefix and original
    user task in the same order. Iteration-specific additions such as reviewer findings,
    peer messages, and escalation notes should be appended after the stable base prompt to
    maximize inference prompt caching where supported.

completion_rules:
  can_say_done:
    any:
      - no_material_changes_detected
      - reviewer_verdict == approved
      - explicit_user_waiver_valid == true
  cannot_say_done:
    any:
      - reviewer_unavailable
      - reviewer_verdict == changes_requested
      - reviewer_verdict == blocked
      - disagreement_unresolved
      - loop_limit_exceeded_without_approval
      - invalid_structured_output_unresolved

reporting:
  final_execution_report_required: true
  include:
    - pipeline_id
    - pipeline_session_id
    - router_model
    - router_decision
    - subagent_invocations
    - provider_model_per_invocation
    - token_usage_per_invocation
    - cache_usage_per_invocation
    - total_token_usage
    - tool_calls_summary
    - changed_files
    - tests_run
    - review_iterations
    - peer_messages
    - disagreements
    - decisive_subagent
    - model_escalations
    - final_verdict
    - user_waiver_if_any
```

---

## 5. Pipeline Routing

### 5.1. Why Routing Must Be LLM-Based

Pipeline selection is a semantic task.

A user request can be ambiguous:

```text
"Посмотри почему reviewer не вызывается и дай решение"
```

This could mean:

- architecture discussion;
- read-only audit;
- engineering implementation;
- documentation task;
- request to external coding agent;
- bugfix request requiring code mutation.

Hardcoded `if` rules are useful as guardrails, but they are not sufficient for robust routing.

Therefore Hermes should use a Pipeline Router subagent with a defined model and structured output.

### 5.2. Pipeline Registry

The Pipeline Router must route only among known registered pipelines.

Pipeline Registry entry:

```yaml
id: engineering_review_pipeline
description: >
  Handles code/config/test/script changes with engineer and reviewer subagents.
entry_examples:
  - "почини Hermes"
  - "добавь тесты"
  - "исправь gateway"
  - "сделай patch"
exclusion_examples:
  - "обсудим архитектуру без правок"
  - "дай промпт для внешнего агента"
  - "оцени вакансию"
risk_notes:
  - may mutate repository
  - requires git baseline
  - requires review for material changes
```

### 5.3. Router Decision Schema

```json
{
  "router_subagent": "hermes_pipeline_router",
  "actual_provider": "openai-codex",
  "actual_model": "gpt-5.4-mini",
  "status": "selected",
  "pipeline_id": "architecture_discussion_pipeline",
  "confidence": 0.88,
  "reasoning_summary": "The user explicitly asked not to change code and wants conceptual architecture discussion.",
  "requires_clarification": false,
  "fallback_used": false
}
```

### 5.4. No Pipeline Selected

If no specialized pipeline is selected:

```text
router_status = no_specialized_pipeline
  -> default_conversation_pipeline
```

If the router has low confidence:

```text
router_status = needs_clarification
  -> ask user OR use default_conversation_pipeline if safe
```

If the router fails:

```text
router_status = routing_failed
  -> default_conversation_pipeline if safe
  -> otherwise block with transparent error
```

Default path must be logged as a pipeline selection.

---

## 6. Inter-Subagent Communication

Subagents may communicate with each other, but only through pipeline-mediated messages.

They must not enter uncontrolled open-ended chat.

The pipeline owns:

- when communication is allowed;
- how many rounds are allowed;
- what message schema is used;
- whether communication changes the pipeline state;
- when to stop and escalate;
- which subagent has decisive authority when disagreement persists.

Example use case:

```text
Reviewer: blocked because implementation appears to ignore edge case X.
Engineer: disagrees and explains that edge case X is already covered by function Y and test Z.
Reviewer: accepts explanation and changes verdict to approved, or maintains blocker with clarified reason.
Pipeline: decides next state based on final reviewer verdict and disagreement policy.
```

Message schema:

```json
{
  "message_id": "peer_msg_...",
  "pipeline_session_id": "eng_...",
  "from_subagent": "hermes_engineer_core",
  "to_subagent": "hermes_code_reviewer",
  "type": "disagreement",
  "related_verdict_id": "review_...",
  "content": {
    "summary": "Engineer disagrees with blocker B2.",
    "arguments": [
      "The reviewer says untracked files are missed, but the new detector includes --untracked-files=all.",
      "Test test_untracked_material_change covers this behavior."
    ],
    "evidence": [
      "agent/engineering_pipeline.py",
      "tests/agent/test_engineering_pipeline.py"
    ]
  },
  "requires_response": true
}
```

Communication rules:

```text
1. The pipeline may allow engineer -> reviewer objection.
2. The reviewer may revise or maintain verdict.
3. Each pipeline with possible disagreement must define a decisive subagent or arbitrator policy.
4. Peer discussion rounds are bounded.
5. Peer messages are logged and included in the final report.
```

---

## 7. Disagreement Resolution

Any pipeline that allows disagreement must define how disagreement is resolved.

Required fields:

```yaml
disagreement_policy:
  enabled: true
  decisive_subagent: hermes_code_reviewer
  arbitrator_subagent: null
  escalation_allowed: true
  max_rounds: 1
  on_unresolved_disagreement: block_and_escalate_to_user
```

Possible patterns:

### 7.1. Decisive Specialist

One subagent has final authority.

Example:

```text
engineer vs code reviewer
  -> reviewer is decisive for review verdict
```

### 7.2. Arbitrator Subagent

A third subagent resolves disagreement.

Example:

```text
engineer vs security auditor
  -> senior_security_reviewer arbitrates
```

### 7.3. Model Escalation

The same role may be rerun with a higher-class model.

Example:

```text
base reviewer and engineer disagree
  -> escalate reviewer to senior_review model
```

### 7.4. User Escalation

If disagreement remains unresolved or exceeds loop limits:

```text
pipeline blocks and asks user for decision
```

The decisive rule must be explicit. No pipeline may leave final authority implicit.

---

## 8. Model Escalation

Model selection is dynamic within policy.

A pipeline may start with a baseline model and escalate if the loop is not converging, if structured output repeatedly fails, or if models disagree.

Example engineering policy:

```text
Iteration 1:
  engineer = openrouter / xiaomi/mimo-v2.5-pro
  reviewer = openai-codex / gpt-5.5

Iteration 2:
  engineer = openrouter / xiaomi/mimo-v2.5-pro
  reviewer = openai-codex / gpt-5.5

If reviewer blockers persist after two engineer attempts:
  engineer escalates to openai-codex / gpt-5.5

If engineer and reviewer still disagree after allowed peer discussion:
  escalate decisive subagent or arbitrator model
```

Escalation principles:

```text
1. Escalation must be explicit and logged.
2. Escalation must stay within allowed models for the subagent.
3. Escalation must include an escalation_reason.
4. Escalation may be triggered by persistent blockers, invalid output, low confidence, or unresolved disagreement.
5. Escalation does not reset loop counters unless pipeline explicitly allows it.
6. If escalation target is unavailable, pipeline blocks and asks user.
```

Escalation event:

```json
{
  "event": "model_escalated",
  "pipeline_session_id": "eng_...",
  "target_subagent": "hermes_engineer_core",
  "from": {
    "provider": "openrouter",
    "model": "xiaomi/mimo-v2.5-pro"
  },
  "to": {
    "provider": "openai-codex",
    "model": "gpt-5.5"
  },
  "reason": "reviewer_blockers_persist_after_2_iterations"
}
```

---

## 9. Loop Limits

Every pipeline with a cycle must define a maximum number of executions.

No unbounded loops are allowed.

Loop limits apply to:

- engineer/reviewer rework cycles;
- peer discussion rounds;
- disagreement resolution rounds;
- retry attempts after invalid structured output;
- model escalation cycles;
- tool retry loops;
- research/refinement loops;
- user clarification loops where applicable.

Required fields:

```yaml
loop_policy:
  max_iterations: 3
  max_peer_discussion_rounds: 1
  max_disagreement_rounds: 1
  max_invalid_output_retries: 1
  max_tool_retries: 1
  on_limit_exceeded: block_and_escalate_to_user
```

If a loop limit is exceeded, pipeline must stop and produce:

```text
status = blocked
reason = loop_limit_exceeded
user_action_required = true
```

---

## 10. Prompt Cache Discipline in Cyclic Pipelines

For cyclic pipelines, prompt layout must be designed to maximize provider-side inference cache reuse where available.

Rule:

```text
stable content first
dynamic additions after stable base prompt
append iteration-specific context after original task and stable schemas
```

Recommended prompt order:

```text
1. System prompt
2. Stable role contract
3. Stable tool/permission policy
4. Stable output schema
5. Original user task
6. Stable pipeline context
7. Baseline artifacts or stable references
8. Dynamic additions:
   - previous attempt summary
   - reviewer findings
   - peer messages
   - disagreement notes
   - escalation reason
   - retry instructions
```

Why:

```text
If the original task and stable instructions remain in the same prefix position,
repeated cyclic calls can reuse cached inference prefix where supported.
```

Pipeline invariant:

```text
dynamic_additions_append_only = true
```

The pipeline should not rewrite the original user task on every iteration.

It should pass the original task unchanged and append new context after it.

---

## 11. Final Execution Report

After every pipeline run, the orchestrator must produce an execution report.

For normal user-facing responses, this report can be concise.

For engineering/admin/debug tasks, the report should be explicit.

Required report contents:

```json
{
  "pipeline": {
    "id": "engineering_review_pipeline",
    "session_id": "eng_...",
    "status": "completion_allowed"
  },
  "routing": {
    "router_subagent": "hermes_pipeline_router",
    "provider": "openai-codex",
    "model": "gpt-5.4-mini",
    "input_tokens": 1100,
    "output_tokens": 180,
    "selected_pipeline": "engineering_review_pipeline",
    "confidence": 0.93,
    "fallback_used": false
  },
  "subagent_invocations": [
    {
      "subagent": "hermes_engineer_core",
      "iteration": 1,
      "provider": "openrouter",
      "model": "xiaomi/mimo-v2.5-pro",
      "actual_provider": "openrouter",
      "actual_model": "xiaomi/mimo-v2.5-pro",
      "input_tokens": 12000,
      "output_tokens": 1800,
      "total_tokens": 13800,
      "cache_read_tokens": 9000,
      "cache_write_tokens": 1200,
      "tool_calls": 12,
      "status": "done"
    },
    {
      "subagent": "hermes_code_reviewer",
      "iteration": 1,
      "provider": "openai-codex",
      "model": "gpt-5.5",
      "actual_provider": "openai-codex",
      "actual_model": "gpt-5.5",
      "input_tokens": 9000,
      "output_tokens": 1400,
      "total_tokens": 10400,
      "cache_read_tokens": 0,
      "cache_write_tokens": 0,
      "tool_calls": 0,
      "status": "approved"
    }
  ],
  "totals": {
    "input_tokens": 22100,
    "output_tokens": 3380,
    "total_tokens": 25480,
    "cache_read_tokens": 9000,
    "cache_write_tokens": 1200
  },
  "review": {
    "iterations": 1,
    "final_verdict": "approved"
  },
  "disagreements": [],
  "decisive_subagent": "hermes_code_reviewer",
  "model_escalations": [],
  "peer_messages": [],
  "changed_files": [
    "agent/engineering_pipeline.py",
    "tests/agent/test_engineering_pipeline.py"
  ],
  "tests_run": [
    "pytest tests/agent/test_engineering_pipeline.py"
  ],
  "completion": {
    "allowed": true,
    "reason": "reviewer_approved"
  }
}
```

Minimum human-readable summary:

```text
Pipeline: engineering_review_pipeline
Router: openai-codex / gpt-5.4-mini
Result: approved
Subagents:
- engineer: openrouter / xiaomi/mimo-v2.5-pro, tokens 13.8k
- reviewer: openai-codex / gpt-5.5, tokens 10.4k
Review iterations: 1
Disagreements: none
Decisive subagent: hermes_code_reviewer
Model escalations: none
Prompt cache: 9.0k read / 1.2k written
Total tokens: 25.5k
Completion: allowed
```

---

## 12. Engineering Pipeline — Reference Example

### 12.1. Goal

Safely execute engineering tasks that may mutate the Hermes repo.

### 12.2. Flow

```text
task_received
  -> orchestrator calls Pipeline Router
  -> Pipeline Router selects engineering_review_pipeline
  -> if no specialized pipeline selected, default_conversation_pipeline
  -> engineering_review_pipeline selected
  -> baseline git snapshot
  -> engineer subagent invocation
  -> engineer structured output
  -> post-engineer git snapshot
  -> material change delta computed from baseline
  -> if no material changes:
       completion_allowed
  -> if material changes:
       reviewer packet built
       reviewer subagent invocation
  -> if reviewer approved:
       completion_allowed
  -> if reviewer changes_requested:
       optional engineer objection / reviewer clarification
       if still changes_requested:
         engineer rework invocation
  -> repeat up to max_review_iterations
  -> if blockers persist after configured attempts:
       escalate engineer model if allowed
  -> if disagreement persists after allowed peer discussion:
       decisive subagent decides OR arbitrator/escalated model invoked
  -> if still blocked or loop limit exceeded:
       completion_blocked, user decision required
```

### 12.3. Engineer/Reviewer Disagreement

If engineer disagrees with reviewer:

```text
engineer returns status = disagree_with_reviewer
pipeline allows one peer message round
engineer sends structured objection to reviewer
reviewer responds with revised_or_maintained_verdict
if reviewer is decisive and maintains blocker:
  pipeline treats blocker as authoritative
if evidence conflict remains and escalation is allowed:
  pipeline escalates reviewer/arbitrator model
if still unresolved:
  pipeline blocks and escalates to user
```

Reviewer is decisive in this reference pipeline unless the pipeline config defines a separate arbitrator.

### 12.4. Commit/Push

Commit/push are not part of the initial execution loop.

They are separate gated actions.

Allowed only if:

```text
reviewer_approved == true
```

or:

```text
explicit_user_waiver_valid == true
```

and user requested commit/push.

---

## 13. Migration Implications for Current Hermes

Current problematic pattern:

```text
Gateway creates AIAgent from session/default runtime
  -> conversation_loop starts
  -> role context selected inside loop
  -> model policy logged as metadata
  -> actual SDK client may remain session/default
```

Target pattern:

```text
Gateway receives message
  -> Orchestrator calls Pipeline Router subagent
  -> Pipeline Router selects pipeline or default fallback
  -> Pipeline selects subagent
  -> Runtime Factory creates role-specific actual runtime
  -> Subagent Runner invokes role-specific agent
  -> Pipeline evaluates output and gates completion
```

Required migration direction:

```text
1. Move authoritative pipeline routing above conversation_loop.
2. Use LLM-based Pipeline Router constrained by Pipeline Registry.
3. Keep deterministic rules as guardrails, not as the whole router.
4. Add default_conversation_pipeline for unmatched requests.
5. Move authoritative role/subagent selection into pipelines.
6. Keep conversation_loop as reusable single-subagent executor, not top-level orchestrator.
7. Ensure Runtime Factory creates actual provider/model/client per subagent invocation.
8. Treat existing review_gate/turn_finalizer as utilities or legacy safety net, not primary workflow.
9. Add structured pipeline/session logs.
10. Add token and prompt-cache accounting per router/subagent invocation.
```

---

## 14. First Architecture Deliverables Before Coding

Before implementation, produce these artifacts:

1. `docs/architecture/subagents.md`
   - conceptual architecture;
   - subagent schema;
   - communication rules;
   - disagreement rules;
   - model policy rules;
   - prompt-cache policy;
   - observability/reporting contract.

2. `docs/architecture/pipelines.md`
   - pipeline schema;
   - pipeline routing;
   - default fallback path;
   - state machine model;
   - loop limits;
   - escalation rules;
   - disagreement decisive-authority rules;
   - completion rules.

3. `docs/architecture/orchestrator.md`
   - orchestrator responsibilities;
   - Pipeline Router responsibilities;
   - Runtime Factory responsibilities;
   - Subagent Runner responsibilities;
   - final response/reporting responsibilities.

4. `docs/architecture/engineering-pipeline.md`
   - concrete engineering pipeline;
   - engineer/reviewer interaction;
   - baseline git strategy;
   - review/rework cycle;
   - disagreement handling;
   - model escalation;
   - commit/push gating.

5. Optional machine-readable examples:
   - `config/subagents/hermes_pipeline_router.yaml`
   - `config/subagents/hermes_engineer_core.yaml`
   - `config/subagents/hermes_code_reviewer.yaml`
   - `config/pipelines/default_conversation_pipeline.yaml`
   - `config/pipelines/engineering_review_pipeline.yaml`

---

## 15. Non-Negotiable Invariants

1. Pipeline routing must happen before specialized execution.
2. Pipeline routing must use an LLM-backed Pipeline Router constrained by a Pipeline Registry, not only hardcoded `if` rules.
3. If no specialized pipeline is selected, Hermes must use a default fallback pipeline.
4. Role/subagent selection must happen before actual subagent runtime creation.
5. Selected role/model must map to actual provider/model/client.
6. Subagent output must be structured.
7. Pipelines with cycles must define loop limits.
8. Subagent-to-subagent messages must be mediated and bounded by pipeline.
9. Any pipeline that allows disagreement must define decisive authority or arbitrator policy.
10. Model escalation must be explicit, policy-bound, and logged.
11. Model escalation may be used for persistent blockers, invalid outputs, low confidence, or unresolved disagreement.
12. In cyclic pipelines, dynamic prompt additions must be appended after the stable base prompt and original task where feasible.
13. Final response must include pipeline, subagents, models, token usage, and relevant cache usage summary.
14. Engineering material changes must not be called done without approval or explicit user waiver.
15. Commit/push must be separate gated actions.
16. Orchestrator chooses pipelines; pipelines run workflows; subagents execute specialist steps.

---

## 16. Recommended Next Step

Do not implement the engineering pipeline yet.

Next task should be architecture documentation only:

```text
Create the architecture docs and machine-readable draft specs for:
- Subagent Spec
- Pipeline Spec
- Pipeline Router
- Default Conversation Pipeline
- Orchestrator responsibilities
- Runtime Factory responsibilities
- Engineering Review Pipeline example
```

After that, use these documents as the source of truth for implementation planning.


---

## CTLV8 Milestone — 2026-06-19

- Status: GREEN
- controlled_manual live validation passed
- gateway intercept confirmed (normal AIAgent fallback skipped)
- effective_pipeline_id=engineering_review_pipeline via controlled_manual_trigger_override
- rollback confirmed: config.yaml all-disabled post-validation
- production/autonomous execution: STILL DISABLED
