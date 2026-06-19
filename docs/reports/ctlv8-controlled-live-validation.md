# CTLV8 — Controlled Live Validation Report

**Status: GREEN**
**Date:** 2026-06-19
**HEAD:** b28d9e123c8f60ac2e42ab3a846582c5b758bf44
**Milestone:** Controlled Manual Live Vertical Slice — Engineering Review Pipeline

---

## Summary

CTLV8 achieved GREEN status for a controlled live execution of the Hermes subagent
pipeline on the production gateway. A single vertical slice of the `engineering_review_pipeline`
was executed under tightly controlled conditions. Normal AIAgent fallback was intentionally
bypassed via the gateway execution controller (gateway intercept path).

This proves the controlled_manual live execution path only. Production/autonomous pipeline
execution remains fully disabled.

---

## Gateway Stability

- **Gateway PID:** 457037 — stable throughout the entire validation (no restart)
- **Live config path:** `/home/hermes/.hermes/config.yaml`

---

## Config States

### Pre-Validation State (all-disabled baseline)

```yaml
pipelines:
  enabled: false
  router:
    mode: disabled
  orchestrator:
    mode: disabled
  execution:
    mode: disabled
```

### Temporary Controlled Config Applied During Validation

The following fields were temporarily set in `/home/hermes/.hermes/config.yaml` to enable
the controlled manual slice:

```yaml
pipelines:
  enabled: true        # only for duration of validation
  router:
    mode: disabled     # router NOT used for this controlled run (see Router Nuance below)
  orchestrator:
    mode: disabled
  execution:
    mode: controlled_manual
    enable_gateway_execution_controller: true
    allow_actual_subagent_invocation: true
    allow_actual_reviewer_invocation: true
    allow_pipelines:
      - engineering_review_pipeline
    allowed_subagents:
      - hermes_engineer_core
      - hermes_code_reviewer
```

### Post-Validation Rollback State (confirmed)

Config was rolled back to all-disabled immediately after validation:

```yaml
pipelines:
  enabled: false
  router:
    mode: disabled
  orchestrator:
    mode: disabled
  execution:
    mode: disabled
```

Git status was clean throughout. No commits or pushes were made during validation.

---

## Success Evidence Fields

| Field | Value |
|-------|-------|
| `config_path` | `/home/hermes/.hermes/config.yaml` |
| `execution_mode` | `controlled_manual` |
| `enable_gateway_execution_controller` | `true` |
| `allow_actual_subagent_invocation` | `true` |
| `allow_actual_reviewer_invocation` | `true` |
| `allow_pipelines` | `[engineering_review_pipeline]` |
| `allowed_subagents` | `[hermes_engineer_core, hermes_code_reviewer]` |
| `effective_pipeline_id` | `engineering_review_pipeline` |
| `actual_execution_invoked` | `true` |
| `blocked_reason` | `null` |
| `status` | `completed` |
| `execution_allowed` | `true` |
| `provider_execution_mode` | `fake_real_provider_client` |
| `network_access` | `disabled` |
| `sdk_import_mode` | `not_used` |
| `generated_test_command` | `python -m pytest -q tests/test_generated_example.py` |
| `tests` | `passed 1/1` |
| `reviewer_invoked` | `true` |
| `reviewer_approved` | `true` |
| `final_response_text` | non-empty (see below) |
| `gateway_intercept_used` | `yes` |
| `normal_AIAgent_skipped` | `yes` |

---

## Controlled Final Response (verbatim)

```
Controlled pipeline validation completed.
status: completed
completion_allowed: True
pipeline: engineering_review_pipeline
runtime: fake_real_provider_client
mutation: applied_count=1 denied_count=0
tests: passed 1/1
workspace: 56aae8e98592493cbc182809b180bc32
```

---

## Gateway Intercept — Normal AIAgent Bypassed

The gateway execution controller intercepted the task before it reached the normal `AIAgent`
code path. This means:

- The standard AIAgent (which handles all production tasks) was **skipped entirely**.
- The pipeline was invoked directly via the gateway intercept / controlled execution path.
- This is the designed behavior for `execution_mode=controlled_manual`.

This validates that the gateway execution controller correctly routes controlled runs
without disturbing the normal AIAgent flow for all other live tasks.

---

## Router Nuance — controlled_manual_trigger_override

Raw router decision logged:
- `selected_pipeline_id=null`
- `no_specialized_pipeline` (router returned no match)

**This is expected and correct.** The pipeline router was in `mode: disabled` for this
controlled run. The orchestrator applied `controlled_manual_trigger_override` to force
`effective_pipeline_id=engineering_review_pipeline`.

This is NOT a sign of production routing readiness. It means:

- The router is not yet live/tuned for production use.
- The controlled_manual mode deliberately bypasses routing and directly invokes the
  specified pipeline.
- When production routing is enabled, the router must independently select the correct
  pipeline — that validation is a separate future milestone.

---

## Explicit Scope Boundaries

1. **Production/autonomous execution is still disabled.** Config remains fully rolled back.
2. **This proves controlled_manual live vertical slice only.** Not full production autonomy.
3. **No live code was modified.** No .py files changed. No runtime config is permanently altered.
4. **No commit or push was made** during or after validation.
5. **Gateway was not restarted.** PID 457037 was stable throughout.

---

## What This Milestone Proves

- The gateway execution controller is wired up and intercepts correctly.
- The `engineering_review_pipeline` can execute a complete vertical slice:
  engineer subagent → test generation → test execution (1/1 pass) → reviewer subagent → approval → final response.
- The `fake_real_provider_client` execution mode works end-to-end under live gateway conditions.
- Rollback to all-disabled is clean and confirmed.

## What This Milestone Does NOT Prove

- Production routing (router was disabled, controlled_manual_trigger_override was used).
- Autonomous pipeline selection by LLM router.
- Network-enabled subagent execution (network_access=disabled).
- SDK-based provider invocation (sdk_import_mode=not_used).
- Multi-task or concurrent pipeline execution.
