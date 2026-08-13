# Engineering Continuation Intent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recognize safe execution-continuation wording such as `пусть инженер выполнит план` without delegating execution authorization to an LLM.

**Architecture:** Keep the existing two-stage boundary: the router may use an LLM to select the engineering pipeline, while `engineering_task_context` deterministically authorizes reuse of a canonical same-session plan. Replace exact tense-specific phrases with anchored actor/action grammar built from Russian verb stems; questions, negation, conditionals, and informational sentences remain fail-closed.

**Tech Stack:** Python 3.11, `re`, pytest.

## Global Constraints

- Do not call an LLM to authorize execution of a prior plan.
- Require an explicit execution construction with the engineer as actor or recipient.
- Preserve same-session, ambiguity, size, hash, and explicit-not-ready checks.
- Do not deploy or restart the gateway without separate operator authorization.

---

### Task 1: Generalize continuation intent grammar

**Files:**
- Modify: `hermes_cli/engineering_task_context.py`
- Test: `tests/gateway/test_engineering_task_continuation.py`

**Interfaces:**
- Consumes: `is_engineering_execution_continuation(text: str) -> bool`
- Produces: the same public function and `resolve_engineering_task_context(...)` behavior, with broader safe Russian wording support.

- [ ] **Step 1: Write the failing tests**

Add table-driven positive cases including `пусть инженер выполнит план`, future and imperative execution forms, start/proceed forms, and plan-transfer forms. Add negative cases for questions, conditionals, negation, and informational statements.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
/home/hermes/.hermes/hermes-agent/venv/bin/python -m pytest -q tests/gateway/test_engineering_task_continuation.py
```

Expected: the new positive cases fail because the current regex accepts only a narrow list of exact word forms; existing tests and negative cases remain green.

- [ ] **Step 3: Implement minimal structured grammar**

Define reusable regex fragments for optional acknowledgement, engineer actor, execution actions, and transfer actions. Keep every continuation pattern anchored at the start and require an explicit affirmative construction.

- [ ] **Step 4: Run focused and adjacent tests**

Run:

```bash
/home/hermes/.hermes/hermes-agent/venv/bin/python -m pytest -q tests/gateway/test_engineering_task_continuation.py tests/gateway/test_orchestrator_observe.py tests/test_pipeline_execution_controller.py tests/test_pipeline_one_step_execution.py
```

Expected: all tests pass.

- [ ] **Step 5: Run static checks and commit**

Run Ruff on both changed Python files and `git diff --check`, inspect the exact diff, then commit only the plan, resolver, and regression tests with a Conventional Commit message.
