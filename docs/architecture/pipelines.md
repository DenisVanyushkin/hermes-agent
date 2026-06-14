# Pipelines Architecture

Status: Draft v0.3
Canonical source: `docs/hermes-subagent-architecture-source-of-truth-v2.md` from the architecture track

## Purpose

A pipeline is the workflow contract for resolving a class of user requests. It is not one model call. It owns the execution flow, state machine, gates, loop limits, clarification behavior, escalation rules, and completion rules.

Core rule:

```text
pipeline != one LLM call
pipeline = controlled workflow of subagent calls, checks, decisions, gates, and user interactions
```

## What a pipeline spec must define

A pipeline spec must declare:

- entry conditions;
- involved subagents;
- explicit state machine;
- decision rules and policy gates;
- loop limits and retry bounds;
- clarification-loop rules or an explicit statement that no clarification loop exists;
- model escalation policy and maximum escalation count;
- disagreement policy and explicit disagreement-loop bounds;
- user interaction points;
- completion and blocking rules;
- final reporting contract.

A subagent may report that its part is done, but only the pipeline may declare the full request complete.

## Conceptual schema

```yaml
id: engineering_review_pipeline
display_name: Engineering Review Pipeline
version: 1
purpose: Execute code-changing engineering tasks with mandatory review and bounded rework.
entry_conditions:
  any:
    - task_classification.domain == engineering
    - task_intent includes code_mutation
subagents:
  engineer: hermes_engineer_core
  reviewer: hermes_code_reviewer
  optional: [hermes_security_auditor]
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
    - rework_requested
    - model_escalated
    - approved
    - blocked
    - failed
    - completion_allowed
    - completion_blocked
loop_policy:
  max_review_iterations: 3
  max_peer_discussion_rounds_per_iteration: 1
  max_disagreement_rounds_per_iteration: 1
  max_invalid_output_retries: 1
  max_tool_retries: 1
  max_model_escalations: 1
  max_clarification_rounds: 0
  on_limit_exceeded: block_and_escalate_to_user
model_escalation_policy:
  enabled: true
  resets_loop_counters: false
communication_policy:
  subagents_may_exchange_messages: true
  mediated_by_pipeline: true
  max_rounds: 1
disagreement_policy:
  enabled: true
  decisive_subagent: hermes_code_reviewer
  arbitrator_subagent: null
completion_rules:
  can_say_done:
    any: [no_material_changes_detected, reviewer_verdict == approved, explicit_user_waiver_valid == true]
  cannot_say_done:
    any: [reviewer_verdict == changes_requested, reviewer_verdict == blocked, disagreement_unresolved]
reporting:
  final_execution_report_required: true
```

## Pipeline registry

Pipeline routing must happen before specialized execution. The router may choose only from a machine-readable registry.

Canonical registry file:

- `config/pipelines/registry.yaml`

Each registry entry should contain:

- `id`;
- `display_name`;
- description;
- entry examples;
- exclusion examples;
- risk notes;
- routing behavior or allowed router statuses;
- fallback eligibility;
- mutation risk;
- required subagents;
- config path.

Hardcoded rules are acceptable as guardrails, but they are not sufficient as the main routing algorithm. Routing must be LLM-assisted and schema-constrained against this registry.

## Default fallback path

Hermes must always have a safe default when no specialized pipeline matches.

```text
router_status = no_specialized_pipeline
  -> default_conversation_pipeline
  -> primary subagent = general_operator
```

A routing failure is different. The default fallback may be used on `routing_failed` only when the orchestrator can prove a guarded `fallback_safe == true` condition. Otherwise Hermes must block transparently rather than guessing.

If the router needs clarification, Hermes may ask the user only within the configured clarification limit.

## State machine model

Pipelines must expose named states rather than implicit control flow. For mutable engineering work the minimum useful states are:

- request intake and pipeline selection;
- baseline capture;
- worker invocation;
- post-change delta analysis;
- review and optional peer discussion;
- escalation, approval, or blocking;
- final reporting.

This is required for deterministic gates, resumability, and auditability.

## Required loop limits

No unbounded loops are allowed. Limits must be explicit for:

- rework iterations;
- peer discussion rounds;
- disagreement rounds where applicable;
- invalid output retries;
- tool retries;
- model escalation cycles;
- user clarification loops where applicable.

When a limit is exceeded, the pipeline must stop with `status = blocked` and surface the required user action.

## Escalation rules

Escalation is allowed only within policy and must be logged with:

- target subagent;
- selected provider and model before escalation;
- actual provider and model before escalation if available;
- selected provider and model after escalation;
- explicit escalation reason.

Escalation does not reset loop counters unless the pipeline config says so. The default architecture requires `resets_loop_counters: false`.

## Disagreement and decisive authority

Any pipeline that permits subagent disagreement must define one of these patterns:

1. a decisive specialist;
2. an arbitrator subagent;
3. escalation to a higher-class model;
4. direct user escalation.

For the reference engineering workflow, the reviewer is decisive unless the pipeline explicitly installs an arbitrator. If a security-sensitive change is under review, `hermes_security_auditor` may participate through pipeline-mediated messages, but final authority must still be explicit.

## Completion rules

A pipeline may allow completion only when its gates are satisfied. In mutable engineering flows, material changes are not done until review approves them or the user explicitly waives the gate. Commit and push are separate gated actions after completion approval, not part of the main execution loop.

## Reporting contract

Every pipeline run should produce a structured execution report that includes:

- pipeline identity and session id;
- router subagent id;
- router selected provider and model;
- router actual provider and model;
- router decision and confidence;
- subagent invocations;
- selected provider and model per invocation;
- actual provider and model per invocation;
- token and cache usage per invocation;
- tool-call summary;
- changed files and tests run;
- review iterations, disagreements, peer messages, and escalations;
- decisive subagent;
- completion allowed flag;
- completion blocked reason;
- final verdict.

For pipelines where peer discussion or escalation does not apply, the report should still include the fields with explicit empty values or not-applicable markers.
