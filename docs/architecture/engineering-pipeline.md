# Engineering Review Pipeline

Status: Draft v0.2
Canonical source: `docs/hermes-subagent-architecture-source-of-truth-v2.md` from the architecture track

## Goal

Provide the reference pipeline for engineering tasks that may mutate the Hermes repository while keeping review, disagreement handling, escalation limits, and completion gates explicit.

## Pipeline shape

Primary subagents:

- `hermes_engineer_core`;
- `hermes_code_reviewer`;
- optional `hermes_security_auditor` for security-sensitive changes.

This pipeline is selected for tasks that require code, config, script, or test mutation.

## Baseline git strategy

Before the engineer runs, the pipeline should capture a git baseline. Minimum baseline packet:

- `git status --short --untracked-files=all`;
- `git diff --stat`;
- `git log --oneline -10`.

After the engineer finishes, the pipeline captures a second snapshot and computes the material delta from the baseline. If there are no material changes, the pipeline may complete without review.

## Reference flow

```text
task_received
  -> router selects engineering_review_pipeline
  -> baseline git snapshot
  -> engineer invocation
  -> engineer structured output
  -> post-engineer git snapshot
  -> material delta analysis
  -> if no material changes: completion_allowed
  -> else build reviewer packet
  -> reviewer invocation
  -> if reviewer approved: completion_allowed
  -> if reviewer changes_requested: allow bounded rework loop
  -> if blockers persist: escalate per model policy
  -> if disagreement persists: decisive reviewer or escalated arbitrator
  -> if still unresolved: completion_blocked and user decision required
```

## Engineer and reviewer interaction

The engineer changes files and returns structured output that includes summary, changed files, tests run, risks, and confidence. The reviewer evaluates the delta and returns one of:

- `approved`;
- `changes_requested`;
- `blocked`;
- `failed`.

Invalid structured output is handled through failure policy and pipeline retries. It is not a separate required reviewer status enum in this draft.

The pipeline, not the subagents, decides whether another iteration is allowed.

## Required limits

The reference pipeline requires explicit limits:

- maximum 3 review iterations;
- maximum 1 peer discussion round per iteration;
- maximum 1 invalid-output retry;
- maximum 1 tool retry before escalation or blocking;
- maximum 1 model escalation;
- maximum 0 clarification rounds inside this pipeline.

If loop limits are exceeded, the pipeline must stop and escalate to the user. Model escalation does not reset loop counters unless the pipeline explicitly opts into that behavior. The default here is `false`.

## Disagreement handling

If the engineer disagrees with a review finding, the pipeline may allow one structured objection round. The engineer sends a bounded evidence-backed message to the reviewer. The reviewer must either revise or maintain the verdict.

In the reference pipeline, the reviewer is decisive. That means:

- if the reviewer revises to approved, the pipeline may continue to completion;
- if the reviewer maintains a blocker, the blocker remains authoritative unless the pipeline escalates to a defined arbitrator or higher-class review model.

If disagreement remains unresolved after the allowed round, the pipeline blocks and asks the user. If a security-sensitive change needs another perspective, `hermes_security_auditor` may provide a read-first opinion through the same pipeline-mediated message channel, but the decisive or arbitrated final authority must still be explicit.

## Model escalation

Example policy:

- engineer starts on a base coding model;
- reviewer starts on a stronger review model;
- if reviewer blockers persist after two engineer iterations, escalate the engineer to a senior coding model;
- if disagreement remains unresolved, escalate the decisive subagent or arbitrator if policy allows it.

Every escalation event must be logged with selected and actual model information before and after escalation, plus the explicit reason.

## Completion gates

Material engineering changes are not done until one of these is true:

- reviewer approved the delta;
- no material delta exists;
- the user explicitly waived the review gate and the pipeline considers the waiver valid.

Reviewer unavailability, unresolved disagreement, or loop-limit exhaustion must block completion.

## Commit and push gating

Commit and push are separate gated actions outside the main execution loop. They are allowed only after review approval or an explicit valid user waiver, and only when the user asked for those actions.

## Final report

The final engineering report should include:

- baseline and post-change git summary;
- changed files;
- tests run;
- router subagent and router selected versus actual provider/model;
- selected versus actual provider/model for each subagent invocation;
- token and cache usage per invocation;
- review iterations;
- disagreements and peer messages;
- decisive subagent;
- model escalations;
- completion allowed flag;
- completion blocked reason if any;
- final verdict.
