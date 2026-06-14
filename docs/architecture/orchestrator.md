# Orchestrator Architecture

Status: Draft v0.3
Canonical source: `docs/hermes-subagent-architecture-source-of-truth-v2.md` from the architecture track

## Core rule

The orchestrator is the dispatcher and state-machine owner. It is not a specialist worker.

```text
orchestrator != engineer/reviewer/researcher
orchestrator = dispatcher + state machine owner + policy gatekeeper
```

## Responsibilities

The orchestrator receives the user or platform request and decides:

- whether clarification is required;
- which pipeline should handle the request;
- whether no specialized pipeline applies;
- whether a policy block or approval gate applies;
- how to create the pipeline session;
- how to present the final result and execution report.

The orchestrator should not perform deep specialist work directly. It should delegate routing to the Pipeline Router and execution to the selected pipeline.

## Pipeline Router

The Pipeline Router is a specialized routing subagent or routing component backed by an LLM. It evaluates:

- user message;
- platform and session context;
- available pipeline registry from `config/pipelines/registry.yaml`;
- pipeline descriptions and entry criteria;
- safety constraints;
- relevant recent state.

It returns a structured decision with statuses from this set:

- `selected`;
- `no_specialized_pipeline`;
- `needs_clarification`;
- `blocked_by_policy`;
- `routing_failed`.

When status is `selected`, the router must return `selected_pipeline_id`. For non-selected statuses it must not be forced to invent a pipeline id. Optional fields such as `fallback_pipeline_id`, `clarification_question`, `policy_block_reason`, and `routing_failure_reason` make the outcome explicit without overloading one field.

Routing must be LLM-assisted and schema-constrained. Deterministic rules may act as guardrails but must not be the entire router.

## Default fallback

If the router returns `no_specialized_pipeline`, the orchestrator must select `default_conversation_pipeline`. Hermes must not silently fail or guess an unrelated specialist flow.

The default fallback path must use an explicit `general_operator` subagent contract rather than inheriting the session default runtime.

If routing fails, the orchestrator may fall back only when the default path is clearly safe and `fallback_safe == true`; otherwise it must block with a transparent error.

## Runtime Factory

The Runtime Factory is responsible for turning a selected subagent contract into actual execution state. It constructs:

- selected provider and selected model;
- API client or runtime object;
- system prompt;
- tool set and permissions;
- working directory;
- env and secret access;
- prompt-cache layout;
- logging and token accounting hooks;
- safety gates.

This is the component that makes the selected role real. The chosen model policy must change the actual provider and model used for the invocation, not just logging metadata.

## Subagent Runner

The Subagent Runner invokes the runtime created by the Runtime Factory and returns structured output back to the pipeline. It should capture:

- selected provider and model;
- actual provider and model;
- prompt and output hashes where available;
- token and cache usage;
- tool calls;
- elapsed time;
- failure status;
- artifacts created.

The runner is the execution boundary between pipeline control flow and subagent behavior.

## Prompt artifacts in the source-of-truth bundle

Runtime contracts may reference prompt files, but those references must resolve inside the same architecture bundle. Draft prompt artifacts therefore live under `prompts/subagents/` so the contracts remain complete for migration planning rather than pointing at implicit future prompt construction.

## Final response and reporting

After the pipeline finishes, the orchestrator is responsible for producing:

- the user-facing final response;
- the execution report appropriate to the task risk;
- transparent reporting for pipeline choice, clarification loops, gates, escalations, and blocking reasons.

For engineering and admin work, the report should explicitly name the pipeline, router subagent, selected and actual models, review result, changed files, tests run, token and cache totals, decisive subagent, and any escalation events.

## Non-negotiable invariants

1. pipeline routing happens before specialized execution;
2. pipeline routing is constrained by `config/pipelines/registry.yaml`;
3. selected subagent and selected model are resolved before runtime creation;
4. the orchestrator does not bypass pipeline gates;
5. the orchestrator does not claim completion for mutable engineering work unless the pipeline permits it;
6. fallback routing is explicit and logged;
7. fallback execution uses a real `general_operator` runtime contract.
