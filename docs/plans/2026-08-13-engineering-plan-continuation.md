# Reliable Engineering Plan Continuation

## Overview
- Исправить сценарий Slack-треда `план -> подтверждение -> engineering_review_pipeline`, в котором пользователь подтверждает исполнение уже согласованного плана короткой репликой, а инженер получает только текущую реплику и обрезанную цитату родительского сообщения.
- Передавать инженеру отдельный типизированный `EngineeringTaskEnvelope`: полный согласованный план из канонической session history, текущую операторскую инструкцию отдельно, идентификаторы источника и стабильный hash.
- Не использовать общий `conversation_context` как источник исполняемой задачи: он остаётся вспомогательным контекстом для recruiter/general flows, но не является надёжным task store.
- Сохранять текущие гарантии безопасности: новая конкретная задача исполняется напрямую; короткое продолжение исполняется только при однозначно найденном плане в той же session/thread; неоднозначность блокируется до вызова модели.

## Context (from discovery)
- Live target: `/home/hermes/.hermes/hermes-agent`, branch `local/customizations`, observed baseline `29b44ad156a5bf210a1e0ddb01f4708fd00b176e` on 2026-08-13. Re-check branch, HEAD and worktree before implementation.
- Incident session: `20260813_094938_5a0c16b9`; pipeline run: `8d733d47d6fd48ac867484f492f9a47d`; Slack thread: `C0B55FPG5B7 / 1786449672.479599`.
- Canonical `state.db` contains the complete revised assistant plan as one 13,089-character message. Slack split delivery did not lose it.
- The triggering pipeline input was 588 characters: a 500-character quote of the thread parent plus `ок, пусть инженер исполняет`. Slack correctly reports the thread parent as `reply_to_text`; it cannot identify the immediately preceding assistant message as the reply target.
- `gateway.run._pipeline_conversation_context()` applies `history[-8:]` before filtering roles. In this incident the last eight records were seven tool records and the final assistant plan, so only one conversational message survived.
- The surviving 13,089-character plan was truncated to its first 4,000 characters plus a truncation marker. The omitted 9,089 characters contained later requirements and deliverables.
- `gateway.run` passes `conversation_context` through `hermes_cli.orchestrator` and `pipeline_execution_controller`, but `execute_engineering_review_helper(..., **_kwargs)` discards it and calls `execute_bounded_rework_loop()` with `user_message` only.
- `pipeline_rework_loop._compose_engineer_message()` returns that current `user_message` unchanged on the first iteration. The engineer therefore saw the incomplete cron quote, not the approved plan.
- Router selection was correct (`engineering_review_pipeline`, confidence `0.98`), preflight passed all 13 checks, the isolated git baseline was clean, mutation-capable tools were available, and `gpt-5.6-terra` returned a valid fail-closed result without tool calls.
- Reviewer non-invocation was correct because there were no material changes and the engineer reported blocked. The autonomous terminal response guard also behaved as designed.
- Existing recruiter code deliberately consumes `conversation_context`; commit `430da03ae4` explicitly left engineering unaffected. Existing tests cover recruiter context and truncation markers but not engineering continuation.

### Hypothesis disposition

| Hypothesis | Result | Evidence / consequence |
|---|---|---|
| Slack lost the generated plan | Rejected | Full 13,089-character plan is present in `messages.content`. |
| Slack quoted the wrong message | Confirmed contributing condition | Thread replies quote the parent cron post, not the latest assistant plan; adapter behavior is consistent with Slack thread semantics. |
| Router selected the wrong pipeline | Rejected | Engineering pipeline selected at 0.98 confidence. |
| Pipeline preflight, permissions, or dirty worktree blocked execution | Rejected | All preflight checks passed; clean baseline; real executor was ready. |
| Engineer/model ignored a sufficient task | Rejected | The engineer received an insufficient current-message payload and correctly failed closed. |
| Reviewer caused the block | Rejected | Reviewer correctly stayed off because no changes existed. |
| Passing existing `conversation_context` into engineering is sufficient | Rejected | It was already truncated to 4,059 characters and is an untyped mixture of history. |
| Increasing context limits fixes the problem | Rejected | It would remain Slack-specific, ambiguous, vulnerable to irrelevant/tool-heavy history, and would not bind approval to a task. |
| A dedicated resolved-task contract fixes the failure | Supported | Canonical history has the full plan; the task can be resolved before model invocation and carried unchanged through engineer/reviewer iterations. |

## Development Approach
- **Testing approach**: TDD.
- Complete each task fully before moving to the next.
- Make small, focused changes; do not modify Slack thread-parent semantics.
- Every task that changes code must add or update tests for success and failure paths.
- All targeted tests must pass before starting the next task.
- Update this plan if implementation discovers a different message-persistence or routing contract.
- Preserve recruiter behavior and direct concrete engineering-task behavior.

## Testing Strategy
- **Pure unit tests**: continuation classification, plan selection, size validation, hash stability, and deterministic fail-closed reasons.
- **Gateway tests**: raw operator instruction is preserved separately from reply enrichment; role filtering happens before history slicing; full approved plan reaches the orchestrator envelope.
- **Orchestrator/helper tests**: the engineering helper receives and selects the resolved task while recruiter helpers retain bounded `conversation_context` behavior.
- **Rework-loop tests**: engineer and reviewer prompts use the same immutable resolved task across iterations.
- **Regression fixture**: model the incident with seven trailing tool messages, a 13,089-character assistant plan, a Slack parent quote ending at `Создай`, and the short approval phrase.
- **No live execution during unit verification**: fake the provider bridge/runner and assert the exact payload passed to it.

## Progress Tracking
- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with `➕` prefix.
- Document issues/blockers with `⚠️` prefix.
- Keep this plan synchronized with the actual live branch and test paths.

## What Goes Where
- **Implementation Steps** (`[ ]` checkboxes): code, tests, and repository documentation.
- **Post-Completion** (no checkboxes): host deployment, gateway restart, and controlled Slack verification.

## Implementation Steps

### Task 1: Lock the incident into red regression tests
- [x] Extend `tests/gateway/test_pipeline_conversation_context.py` with a failing test proving that user/assistant messages must be filtered before selecting the recent-message window; seven tool rows must not crowd a plan out of context.
- [x] Add `tests/gateway/test_engineering_task_continuation.py` with an incident-shaped history fixture containing a complete 13,089-character plan and the enriched Slack parent quote.
- [x] Assert that the fixture preserves the exact full plan and a stable SHA-256, including a sentinel requirement located after character 4,000.
- [x] Assert that the thread-parent cron quote is never selected as the engineering task.
- [x] Assert deterministic blocking when the current message is a continuation but no qualifying plan exists, and no resolution for ordinary non-execution chat.
- [x] Run `venv/bin/python -m pytest -q tests/gateway/test_pipeline_conversation_context.py tests/gateway/test_engineering_task_continuation.py`; confirm the new tests fail for the intended reasons before implementation.

### Task 2: Introduce a typed engineering task resolver
- [x] Create `hermes_cli/engineering_task_context.py` with an immutable `EngineeringTaskEnvelope` (or schema-equivalent mapping) containing `task_text`, `operator_instruction`, `source_kind`, `source_session_id`, optional source message identity, `task_sha256`, and explicit resolution status.
- [x] Implement a pure resolver that distinguishes a direct concrete engineering request from an execution continuation such as `пусть инженер исполняет`.
- [x] For continuations, search only non-empty user/assistant dialogue in the same canonical session, choose the latest qualifying engineer-plan response associated with a preceding plan request, and preserve its complete text.
- [x] Set an explicit maximum approved-task size large enough for the 13,089-character incident plan (for example 32 KiB); reject oversized plans with `approved_task_too_large` instead of silently truncating them.
- [x] Reject missing, multiple equally plausible, truncated, cross-session, or non-plan candidates before any model/provider call.
- [x] Treat history as data: compose fixed envelope sections for `Approved engineering task` and `Current operator instruction`; never concatenate arbitrary history as executable instructions.
- [x] Add unit tests for direct task, valid continuation, missing plan, ambiguous candidates, oversized plan, cross-session candidate, and hash stability.
- [x] Run `venv/bin/python -m pytest -q tests/gateway/test_engineering_task_continuation.py`; all Task 2 tests must pass.

### Task 3: Preserve raw operator intent at the gateway boundary
- [x] In `gateway/run.py`, retain the raw addressed operator text before `_prepare_inbound_message_text` adds Slack reply snippets, speaker labels, attachment notes, or other transport context.
- [x] Continue passing the enriched message to existing routing/default-conversation paths for backward compatibility.
- [x] Build `EngineeringTaskEnvelope` from raw operator text plus canonical session history and pass it separately to `observe_gateway_turn()`.
- [x] Fix `_pipeline_conversation_context()` to filter non-empty user/assistant messages before applying the last-eight-message limit; keep existing 4,000/6,000 truncation markers for its non-engineering consumers.
- [x] Add gateway tests proving that a Slack thread-parent quote remains available as contextual disambiguation but cannot replace `operator_instruction` or `task_text`.
- [x] Add regression tests proving recruiter URL/context behavior remains unchanged.
- [x] Run `venv/bin/python -m pytest -q tests/gateway/test_pipeline_conversation_context.py tests/gateway/test_engineering_task_continuation.py tests/hermes_cli/test_recruiter_decision_execution.py tests/hermes_cli/test_recruiter_application_package_execution.py`.

### Task 4: Wire the resolved task through orchestrator and helper
- [x] Extend `hermes_cli/orchestrator.py` to accept the typed envelope and add it to autonomous helper context without changing router input or generic `conversation_context` semantics.
- [x] Extend `hermes_cli/pipeline_execution_helpers.py::execute_engineering_review_helper` with an explicit `engineering_task_context` parameter instead of consuming it through `**_kwargs`.
- [x] For a valid continuation envelope, call `execute_bounded_rework_loop()` with the full resolved `task_text`; carry the short operator instruction as labelled metadata/context, not as the original task.
- [x] For a direct concrete request, preserve current `user_message` behavior byte-for-byte.
- [x] If continuation resolution is missing/invalid, return a deterministic blocked helper payload such as `engineering_task_context_missing` before constructing the engineer runtime.
- [x] Add safe observability fields only: source kind, resolution status, task length, and task hash prefix. Do not log full prompts or full plans.
- [x] Add/extend `tests/gateway/test_orchestrator_observe.py` and `tests/test_pipeline_execution_controller.py` to assert exact helper arguments and deterministic pre-model blocking.
- [x] Run `venv/bin/python -m pytest -q tests/gateway/test_orchestrator_observe.py tests/test_pipeline_execution_controller.py tests/test_pipeline_one_step_execution.py`.

### Task 5: Keep one immutable task across engineer/reviewer iterations
- [x] Update `hermes_cli/pipeline_rework_loop.py` only as needed to make the resolved original task explicit and immutable for the whole loop.
- [x] Ensure `_compose_engineer_message()`, `_compose_reviewer_message()`, peer review, model escalation, and reviewer packet `task_summary_hash` all derive from the same resolved task.
- [x] Keep rework guidance and prior git diff append-only after the original task; never replace the original task with the approval phrase on later iterations.
- [x] Add a regression test in `tests/test_pipeline_rework_loop.py` that runs at least one rework iteration and checks the post-4,000 sentinel remains in both engineer and reviewer inputs.
- [x] Assert the original task hash is stable across iterations and differs from the hash of the short confirmation message.
- [x] Run `venv/bin/python -m pytest -q tests/test_pipeline_rework_loop.py -k 'task or context or rework or reviewer'`.

### Task 6: Verify acceptance criteria and compatibility
- [x] ➕ Support future untyped plan responses when the current continuation explicitly approves execution, while rejecting explicit not-ready/do-not-execute plans.
- [x] ➕ Carry the current operator instruction into engineer, reviewer, peer, and escalation prompts as labelled context without changing the immutable task hash.
- [x] Re-run the incident fixture end-to-end with a fake executor and assert the engineer receives the complete source-registry/lifecycle/collector plan rather than the cron parent excerpt.
- [x] Verify direct engineering requests, Slack non-thread messages, Telegram execution requests, recruiter continuation, and default conversation routing retain existing behavior.
- [x] Verify missing/ambiguous approved tasks fail before provider invocation with an actionable response asking for the concrete plan, not a generic model-generated blocker.
- [x] Run focused suites: `venv/bin/python -m pytest -q tests/gateway/test_pipeline_conversation_context.py tests/gateway/test_engineering_task_continuation.py tests/gateway/test_orchestrator_observe.py tests/test_pipeline_execution_controller.py tests/test_pipeline_one_step_execution.py tests/test_pipeline_rework_loop.py tests/hermes_cli/test_recruiter_decision_execution.py tests/hermes_cli/test_recruiter_application_package_execution.py`.
- [x] Run lint/type checks used by the live repository for all changed files; run `git diff --check`.
- [x] Capture `git status --short`, changed-file list, test commands, exit codes, and baseline HEAD in the implementation report.

### Task 7: [Final] Update operational documentation
- [x] Document the engineering continuation contract and fail-closed reasons in the relevant pipeline/operator documentation discovered during implementation.
- [x] Document that Slack `reply_to_text` identifies the thread parent and is contextual metadata, not the source of an approved engineering task.
- [x] Document safe telemetry fields and the maximum approved-task size.
- [x] Do not document or expose full task text in logs, reports, or metrics.

*Note: ralphex automatically moves completed plans to `docs/plans/completed/`.*

## Technical Details

### Proposed data contract

```python
@dataclass(frozen=True)
class EngineeringTaskEnvelope:
    schema_version: str
    resolution_status: str
    source_kind: str  # direct_request | approved_plan
    task_text: str
    operator_instruction: str
    source_session_id: str
    source_message_id: str | None
    task_sha256: str
```

- `task_text` is the only field used as `original_task` by the rework loop.
- `operator_instruction` records what the user said now, but never replaces a resolved plan.
- `source_message_id` may use the canonical DB message id when platform message ids are absent; it must not rely on Slack thread-parent `reply_to_text`.
- No truncation is allowed for approved engineering tasks. Over-limit input is a typed block, not a shortened prompt.

### Processing flow

1. Slack adapter emits the current event and correct thread-parent reply metadata.
2. Gateway preserves raw operator text and separately builds the normal enriched message.
3. Router selects the pipeline from the current request as today.
4. The engineering resolver uses raw operator text plus canonical same-session dialogue to produce a typed envelope.
5. Orchestrator forwards the envelope only to the engineering helper.
6. Helper validates it before provider construction and passes `task_text` as immutable `original_task`.
7. Engineer/reviewer/rework iterations keep the same task hash; logs expose only safe metadata.

### Explicit non-goals
- Do not change Slack's thread-parent reply semantics.
- Do not make the engineering helper consume arbitrary generic chat history.
- Do not increase context limits as the primary fix.
- Do not change router model selection, reviewer conditions, git/commit gates, service restart gates, or recruiter decision semantics.
- Do not deploy, restart the gateway, modify cron jobs, or replay the original implementation task as part of this plan-authoring step.

## Post-Completion
*Items requiring manual intervention or external systems. Do not add checkboxes.*

**Host deployment:**
- Review and commit the isolated implementation on the intended `local/customizations` lineage.
- Apply through the established runtime sync/rebase workflow; do not copy an uncommitted working tree over the live checkout.
- Restart the gateway only with explicit operator authorization.

**Controlled live verification:**
- Create a non-destructive Slack test thread with a plan longer than 4,000 characters and a distinctive requirement near the end.
- Reply with a short engineering continuation phrase.
- Verify telemetry shows `source_kind=approved_plan`, the expected task length/hash, engineer tool activity against the intended test task, and reviewer invocation only when material changes exist.
- Verify the original incident thread is not replayed automatically; rerun its source-registry implementation only after explicit operator instruction.

**Rollback:**
- Revert the implementation commit and restart the gateway through the normal controlled process.
- The previous fail-closed behavior is safe but loses continuation; no state/data migration rollback should be required for the proposed envelope-only design.
