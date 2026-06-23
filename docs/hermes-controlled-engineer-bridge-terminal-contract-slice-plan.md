# Hermes Controlled Engineer Bridge Terminal Contract — Slice Plan

Status: draft SoT for the next implementation phase  
Target host: `ssh hermes-agent`  
Target repo: `/home/hermes/.hermes/hermes-agent`  
Current expected HEAD at plan start: `db0e0bd0440afe1e8b9de8b845f68027d6e1fce9`  
Related SoT documents:

- `docs/hermes-subagent-architecture-source-of-truth-v2.md`
- `docs/hermes-controlled-engineering-runtime-slice-plan.md`

## 1. Problem Statement

We are no longer debugging a single smoke failure. The repeated live smokes exposed a broader contract gap in the controlled engineering runtime.

The target architecture is:

```text
router
→ orchestrator
→ execution controller
→ runtime factory
→ subagent runner / AIAgent bridge
→ reviewer gate
→ controlled report / final response
```

For the controlled engineering path, once the system reaches:

```text
real_provider_bridge_invoked=true
subagent_id=hermes_engineer_core
```

the pipeline must never end as raw `missing_structured_output` merely because the model did not return a `StructuredOutputEnvelope`.

Every terminal engineer bridge result must become one of:

```text
A. valid model StructuredOutputEnvelope
B. synthesized blocked envelope for text/plain output without envelope
C. synthesized blocked envelope for provider/API/fallback terminal error with no output
D. explicit max-iterations blocked diagnostic envelope
E. explicit fallback-exhausted blocked envelope
```

The bridge may still end blocked. That is acceptable. The problem is uncontrolled, ambiguous, or stale failure classification such as raw `missing_structured_output` after the bridge actually ran.

## 2. Why We Are Here

### Smoke 005

`HERMES-AUTO-SMOKE-20260623-ALMATY-005`

Failure:

```text
router LLM timeout
selected_pipeline_id=null
actual_execution_invoked=false
```

Root cause:

```text
heuristic timeout fallback was too narrow
```

Fix:

```text
89ab7ced1c24af94b9c1eb2e4164f9039989d578
fix(pipelines): broaden strict router timeout fallback
```

Result:

```text
Router can now select engineering_review_pipeline for strong autonomous engineering runtime-analysis prompts after LLM timeout.
```

### Smoke 006

`HERMES-AUTO-SMOKE-20260623-ALMATY-006`

Progress:

```text
router selected engineering_review_pipeline
execution controller invoked
helper/subagent bridge invoked
find_files/read_file used
normal fallback blocked
```

Failure:

```text
engineer bridge ended with plain text, not StructuredOutputEnvelope
outer report blocked as missing_structured_output
```

Initial fix:

```text
db0e0bd0440afe1e8b9de8b845f68027d6e1fce9
fix(pipelines): synthesize blocked engineer envelope
```

But the initial synthesis handled only one artificial shape:

```text
completion_reason starts with text_response
output_text non-empty
no structured_output
```

### Smoke 007

`HERMES-AUTO-SMOKE-20260623-ALMATY-007`

Progress:

```text
router selected engineering_review_pipeline
execution controller invoked
engineer bridge reached
normal fallback blocked
```

Failure:

```text
OpenRouter HTTP 402 occurred
fallback to Codex was not visible in evidence
outer report still blocked as missing_structured_output
synthesis did not trigger
```

Latest RCA:

```text
Real run_conversation terminal results use turn_exit_reason, not completion_reason.
Real plain-text output may arrive as final_response, not output_text.
Provider-error/no-output terminal shapes skip current synthesis.
Fallback may happen inside AIAgent.run_conversation(), but bridge reporting mostly shows constructor provider/model and does not preserve enough fallback metadata.
```

Conclusion:

```text
We fixed individual branches, not the full terminal contract.
The next phase must implement a complete branch invariant.
```

## 3. Non-Goals

Do not do the following in this phase:

```text
- Do not redesign router architecture.
- Do not change DB/report persistence.
- Do not touch the preserved stash:
  stash@{0}: On local/customizations: codex-preserve-db-persistence-before-controlled-manual-sot-slice
- Do not run live smoke before deterministic branch tests are in place.
- Do not change live config except in the final validation smoke.
- Do not weaken StructuredOutputEnvelope validation.
- Do not mark plain text, provider errors, or fallback failures as success.
- Do not synthesize engineer envelopes for reviewer/router/default AIAgent paths.
```

## 4. Core Invariants

### 4.1 Engineer Bridge Terminal Invariant

For `hermes_engineer_core` only:

```text
After real_provider_bridge_invoked=true,
raw missing_structured_output is not an acceptable final terminal state
when diagnostic terminal data exists.
```

All terminal shapes must normalize to one controlled outcome:

| Terminal shape | Required outcome |
|---|---|
| Valid `StructuredOutputEnvelope` | Validate and pass through |
| `turn_exit_reason=text_response` + `final_response` | Synthesize blocked engineer envelope |
| `completion_reason=text_response` + `output_text` | Synthesize blocked engineer envelope |
| Provider/API error, no output | Synthesize provider-error blocked envelope |
| Fallback exhausted/unavailable | Synthesize fallback-exhausted blocked envelope |
| Max iterations / tool loop | Explicit max-iterations blocked envelope |
| Malformed JSON / parse failure | Explicit malformed-structured-output blocked envelope |
| Empty output / no diagnostic | Explicit empty-output blocked envelope |
| Reviewer plain text | No engineer synthesis |
| Router failure | Router-specific handling only |

### 4.2 Fallback Invariant

For `hermes_engineer_core` primary provider failures:

```text
OpenRouter/xiaomi provider failures eligible for fallback must either:
1. activate configured fallback openai-codex/gpt-5.4 with synced runtime request, or
2. produce a schema-valid blocked envelope explaining fallback failure/exhaustion.
```

Fallback metadata must make this visible:

```text
initial_provider
initial_model
effective_provider
effective_model
fallback_attempted
fallback_activated
fallback_provider
fallback_model
fallback_base_url
fallback_api_mode
fallback_error
fallback_result
providers_used_effective
```

### 4.3 Separation Invariant

The following must remain separate:

```text
router LLM fallback
engineer bridge provider fallback
reviewer bridge behavior
normal/default AIAgent fallback
```

Specifically:

```text
- router failures must not produce engineer envelopes
- reviewer bridge must not synthesize hardcoded engineer envelopes
- normal/default AIAgent path must not use engineer synthesis
- controlled bridge AIAgent logs may appear under agent.conversation_loop, but that is not itself a normal fallback leak
```

## 5. Slice Plan

## Slice 0 — Freeze Live Smokes and Confirm Baseline

### Goal

Stop live validation until deterministic branch matrix is implemented and reviewed.

### Scope

Read-only checks only.

### Actions

On `ssh hermes-agent` in `/home/hermes/.hermes/hermes-agent`:

```bash
git rev-parse HEAD
git status --short --untracked-files=all
git stash list | head
grep -nA20 '^pipelines:' /home/hermes/.hermes/config.yaml || true
systemctl --user status hermes-gateway.service --no-pager
```

### Acceptance Criteria

```text
- HEAD is db0e0bd0440afe1e8b9de8b845f68027d6e1fce9 or later intentional slice commit.
- Worktree is clean before new implementation.
- Live config is disabled.
- Gateway is running.
- DB/report persistence stash is untouched.
```

### Non-Goals

```text
- No code changes.
- No restart.
- No live config changes.
- No provider calls.
```

## Slice 1 — Deterministic Branch Matrix Tests

### Goal

Create no-provider-call tests for all known terminal shapes before implementation.

### Files

Likely files:

```text
tests/test_pipeline_aiagent_executor.py
tests/test_pipeline_one_step_execution.py
tests/test_pipeline_rework_loop.py
tests/run_agent/test_provider_fallback.py
tests/test_pipeline_router.py
```

### Required Test Cases

#### 1. Real plain-text terminal shape

Input shape:

```text
turn_exit_reason="text_response(finish_reason=stop)"
final_response="plain engineer diagnostic"
no structured_output
```

Expected:

```text
synthesized blocked engineer envelope
status=blocked
validation_status=valid
blockers include missing_structured_output or plain_text_without_structured_output
```

#### 2. Artificial plain-text terminal shape

Input shape:

```text
completion_reason="text_response(finish_reason=stop)"
output_text="plain engineer diagnostic"
no structured_output
```

Expected:

```text
existing synthesis still works
```

#### 3. Provider error / HTTP 402 terminal shape

Input shape:

```text
failed=true or error/failure_reason present
HTTP 402 metadata
no output_text
no final_response
no structured_output
```

Expected:

```text
schema-valid provider-error blocked envelope
not raw missing_structured_output
```

#### 4. Fallback exhausted / unavailable

Input shape:

```text
fallback_exhausted=true or fallback_error present
no structured_output
```

Expected:

```text
schema-valid fallback-exhausted blocked envelope
```

#### 5. Max iterations via real key

Input shape:

```text
turn_exit_reason="max_iterations_reached(...)"
diagnostic text present
```

Expected:

```text
explicit max-iterations blocked diagnostic envelope
```

#### 6. Valid envelope pass-through

Input shape:

```text
raw_metadata.structured_output present and valid
```

Expected:

```text
no synthesis
validator passes
original envelope preserved
```

#### 7. Malformed JSON / parse failure

Input shape:

```text
model output contains malformed structured-output candidate
parse error metadata present
```

Expected:

```text
schema-valid malformed-structured-output blocked envelope
or explicitly documented controlled fail-closed envelope
```

#### 8. Empty output / no diagnostic

Input shape:

```text
no output_text
no final_response
no structured_output
no provider diagnostic
```

Expected:

```text
schema-valid empty-output blocked envelope
not raw missing_structured_output
```

#### 9. Reviewer plain text

Input shape:

```text
AIAgentReviewerExecutorBridge
plain text result
no structured_output
```

Expected:

```text
no engineer envelope synthesis
reviewer path remains reviewer-specific fail-closed
```

#### 10. Router timeout fallback unchanged

Expected:

```text
existing router timeout fallback tests still pass
```

### Acceptance Criteria

```text
- Tests exist for all branch shapes above.
- Tests are deterministic.
- No provider calls.
- Expected red tests are documented before implementation.
```

### Validation

```bash
venv/bin/python -m pytest -q tests/test_pipeline_aiagent_executor.py -k "terminal or structured or envelope or plain_text or synthesis or provider or fallback or reviewer or turn_exit"
venv/bin/python -m pytest -q tests/test_pipeline_one_step_execution.py -k "structured or envelope or provider or fallback or terminal"
venv/bin/python -m pytest -q tests/test_pipeline_rework_loop.py -k "structured or missing or blocked or final_response or envelope or provider or fallback"
venv/bin/python -m pytest -q tests/run_agent/test_provider_fallback.py
```

## Slice 2 — Normalize Real AIAgent Terminal Keys

### Goal

Make bridge normalization understand real `run_conversation()` terminal result shapes.

### Files

Primary:

```text
hermes_cli/pipeline_aiagent_executor.py
```

Possible downstream tests:

```text
tests/test_pipeline_aiagent_executor.py
tests/test_pipeline_one_step_execution.py
tests/test_pipeline_rework_loop.py
```

### Required Behavior

Normalize:

```text
turn_exit_reason → completion_reason
final_response → output_text
```

Rules:

```text
- If completion_reason is missing or generic/default, use turn_exit_reason.
- If output_text is absent and final_response is a non-empty string, use final_response.
- Preserve original terminal fields in raw_metadata.
```

Suggested metadata:

```text
original_turn_exit_reason
original_completion_reason
original_final_response_present
normalized_completion_reason_source
normalized_output_text_source
```

### Acceptance Criteria

```text
- Real plain-text shape now reaches text synthesis path.
- Artificial completion_reason/output_text shape still works.
- Valid envelope pass-through unaffected.
- Reviewer path unaffected.
```

### Non-Goals

```text
- Do not add provider-error synthesis in this slice unless tests are already isolated.
- Do not change router behavior.
```

## Slice 3 — Engineer-Only Terminal Envelope Synthesis

### Goal

Enforce the complete engineer terminal invariant.

### Files

Primary:

```text
hermes_cli/pipeline_aiagent_executor.py
```

Possible downstream:

```text
hermes_cli/pipeline_one_step_execution.py
hermes_cli/pipeline_rework_loop.py
tests/test_pipeline_aiagent_executor.py
tests/test_pipeline_one_step_execution.py
tests/test_pipeline_rework_loop.py
```

### Required Behavior

For `self._supported_subagent_id() == "hermes_engineer_core"` only, synthesize a schema-valid blocked envelope for:

```text
plain_text_without_structured_output
provider_error_without_structured_output
fallback_exhausted_without_structured_output
max_iterations_without_structured_output
malformed_structured_output
empty_engineer_output_without_structured_output
```

All synthesized envelopes must have:

```text
schema_version: current schema version
subagent_id: hermes_engineer_core
role: engineer
status: blocked
blockers: non-empty
artifacts: []
confidence: 0.0
requires_review: false unless existing semantics require otherwise
next_action: safe retry/inspect action
findings or changes: schema-valid
```

### Required Metadata

```text
synthesized_envelope=true
synthesized_envelope_reason
structured_output_source=synthesized_...
repair_attempted=false
repair_succeeded=false
original_output_text_length if applicable
original_output_text_excerpt bounded if applicable
provider_error_summary bounded if applicable
fallback_error_summary bounded if applicable
```

### Acceptance Criteria

```text
- No terminal engineer bridge shape with diagnostic data ends as raw missing_structured_output.
- Synthesized envelopes pass validate_structured_output_envelope.
- Plain text is never success.
- Provider/fallback error is never success.
- Reviewer bridge cannot synthesize engineer envelope.
```

### Non-Goals

```text
- Do not implement a provider repair turn in this slice.
- Do not call providers during tests.
```

## Slice 4 — Fallback Metadata and Effective Provider Reporting

### Goal

Make fallback behavior observable and stop confusing constructor provider with effective provider.

### Files

Likely:

```text
agent/conversation_loop.py
agent/chat_completion_helpers.py
hermes_cli/pipeline_aiagent_executor.py
hermes_cli/pipeline_one_step_execution.py
hermes_cli/runtime_factory.py
tests/run_agent/test_provider_fallback.py
tests/test_pipeline_aiagent_executor.py
```

### Required Behavior

When fallback is attempted or activated, preserve metadata before runtime request is cleared:

```text
fallback_attempted
fallback_activated
fallback_provider
fallback_model
fallback_base_url
fallback_api_mode
fallback_error
fallback_result
initial_provider
initial_model
effective_provider
effective_model
effective_base_url
effective_api_mode
providers_used_effective
```

### Reporting Rules

```text
- constructor_provider/model may remain, but must not be presented as effective provider/model if fallback occurred.
- providers_used must include fallback provider if fallback actually ran, or be renamed if it only means initial provider.
- one-step report should expose both initial and effective provider data when available.
```

### Acceptance Criteria

```text
- Deterministic tests can prove fallback metadata propagation without provider calls.
- Smoke evidence no longer requires guessing from logs.
- be52a029 runtime request sync remains covered.
```

### Non-Goals

```text
- Do not change provider credentials.
- Do not alter production config.
```

## Slice 5 — Fallback Activation Invariant Tests

### Goal

Ensure the earlier fallback fix remains true for the engineer bridge.

### Files

Likely:

```text
tests/run_agent/test_provider_fallback.py
tests/test_pipeline_aiagent_executor.py
agent/chat_completion_helpers.py
agent/conversation_loop.py
```

### Required Tests

No provider calls. Simulate:

```text
OpenRouter HTTP 402
→ fallback activates to openai-codex/gpt-5.4
→ _turn_runtime_request switches to Codex provider/base_url/api_mode
→ fallback request does not reuse OpenRouter runtime data
→ fallback metadata is surfaced to bridge result
```

### Acceptance Criteria

```text
- If fallback is eligible, it activates.
- If fallback is exhausted/unavailable, controlled blocked envelope is returned.
- No raw missing_structured_output after fallback terminal errors.
```

### Note

If this cannot be fully tested without deeper agent internals, document the boundary explicitly and add the strongest available deterministic test.

## Slice 6 — End-to-End Controlled Report Integration

### Goal

Ensure normalized terminal results flow through one-step, rework loop, controller, and outer report correctly.

### Files

Likely:

```text
hermes_cli/pipeline_one_step_execution.py
hermes_cli/pipeline_rework_loop.py
hermes_cli/pipeline_execution_controller.py
hermes_cli/orchestrator.py
tests/test_pipeline_one_step_execution.py
tests/test_pipeline_rework_loop.py
tests/test_pipeline_execution_controller.py
tests/gateway/test_orchestrator_observe.py
```

### Required Behavior

For every blocked synthesized engineer envelope:

```text
one-step result validates structured_output
rework loop maps to controlled fail-closed reason
reviewer is not invoked as if engineer succeeded
controller final_response_text is non-empty
outer report is runtime-authoritative
outer report is not stale not_executed/execution_disabled/observe_preflight
```

### Acceptance Criteria

```text
- No valid blocked envelope is treated as success.
- No raw missing_structured_output for covered terminal shapes.
- Final response is controlled and safe.
```

## Slice 7 — Review and Commit

### Goal

Review the full invariant patch before live validation.

### Review Checklist

```text
- All terminal branch tests present.
- Engineer-only synthesis scope is preserved.
- Reviewer/router/default paths are not polluted.
- Fallback metadata is accurate.
- No provider calls in tests.
- No secrets or raw provider payloads leak.
- Full raw plain text is not sent to Telegram.
- Worktree contains only intended files.
- DB/report persistence stash untouched.
```

### Required Validation

```bash
venv/bin/python -m pytest -q tests/test_pipeline_aiagent_executor.py
venv/bin/python -m pytest -q tests/test_pipeline_rework_loop.py
venv/bin/python -m pytest -q tests/test_pipeline_execution_controller.py -k "final_response or blocked or autonomous or helper"
venv/bin/python -m pytest -q tests/gateway/test_orchestrator_observe.py
venv/bin/python -m pytest -q tests/test_pipeline_router.py
venv/bin/python -m pytest -q tests/run_agent/test_provider_fallback.py
venv/bin/ruff check hermes_cli agent tests
git diff --check
git status --short --untracked-files=all
git stash list | head
```

Known context:

```text
A broader full tests/test_pipeline_one_step_execution.py previously had 8 pre-existing failures on base db0e0bd/89ab7ced.
Do not treat those as blockers unless new evidence ties them to this patch.
```

### Commit Message

Suggested:

```text
fix(pipelines): enforce engineer bridge terminal envelopes
```

## Slice 8 — Single Live Smoke

### Goal

Run exactly one live smoke after local deterministic matrix is green and patch is reviewed/committed.

### Idempotency Key

Use next key, for example:

```text
HERMES-AUTO-SMOKE-20260623-ALMATY-008
```

### Expected Result

`GREEN_WITH_CONTROLLED_BLOCKED_DIAGNOSTIC` is acceptable.

Required:

```text
- router selected engineering_review_pipeline
- autonomous execution controller invoked
- helper/subagent bridge invoked
- find_files/read_file contract clean enough
- if model returns text/provider error/fallback error, terminal result becomes valid blocked envelope
- no raw missing_structured_output after bridge invocation
- fallback metadata is visible enough to explain what happened
- normal/default AIAgent final path does not take over
- outer report is runtime-authoritative
- final_response_text non-empty
- disabled baseline restored after completion
```

### Red Conditions

```text
- selected_pipeline_id=null for strong prompt
- bridge not invoked after router selected engineering pipeline
- raw missing_structured_output after bridge invocation
- fallback evidence still impossible to interpret
- reviewer bridge synthesizes engineer envelope
- outer report stale not_executed/execution_disabled
- normal/default AIAgent final response takes over
- live config left enabled
```

## 6. Implementation Task Template

Use this for the coding agent after this document is accepted.

```text
Host/repo:

ssh hermes-agent
cd /home/hermes/.hermes/hermes-agent

Task:

Implement the controlled engineer bridge terminal contract slice according to:
docs/hermes-controlled-engineer-bridge-terminal-contract-slice-plan.md

Current expected HEAD:
db0e0bd0440afe1e8b9de8b845f68027d6e1fce9
fix(pipelines): synthesize blocked engineer envelope

Hard constraints:
- Do not push.
- Do not commit until review.
- Do not restart gateway.
- Do not change live config.
- Do not run live validation.
- Do not make provider calls.
- Do not touch DB/report persistence stash:
  stash@{0}: On local/customizations: codex-preserve-db-persistence-before-controlled-manual-sot-slice

Implementation order:
1. Add deterministic branch-matrix tests first.
2. Normalize real terminal keys: turn_exit_reason and final_response.
3. Add engineer-only terminal envelope synthesis for text/provider/fallback/max-iteration/malformed/empty terminal shapes.
4. Preserve reviewer/router/default separation.
5. Add fallback/effective-provider metadata propagation where safely available.
6. Add deterministic fallback metadata/runtime sync tests where possible.
7. Validate with focused and regression tests.
8. Stop before commit and deliver review report.

Deliver:
- files changed
- branch invariant coverage table
- fallback coverage table
- test results
- remaining gaps
- git status
- stash status
- confirmation of no push/restart/config/live validation/provider calls
```

## 7. Backlog / Not in This Slice

```text
- Reviewer max-iterations label bug:
  reviewer may inherit engineer-labeled max-iterations metadata.
  This is pre-existing and should be fixed in a separate reviewer-specific cleanup slice.

- Repair turn:
  A provider/model repair turn that asks the model to convert prose to JSON may be useful later,
  but current plan uses local synthesis first for reliability and no extra provider calls.

- DB/report persistence stash:
  Remains untouched until explicitly resumed.

- Production enablement:
  No production/autonomous default enablement until final smoke is green enough and explicitly approved.
```
