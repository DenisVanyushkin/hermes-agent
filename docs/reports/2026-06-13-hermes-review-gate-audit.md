# Hermes Review Gate Audit

Date: 2026-06-13

## Scope

This audit covered two coupled layers:

1. Hermes-compatible adaptation of the existing software-development / superpowers-style workflows.
2. A runtime-enforced review gate for material engineering changes.

The target rollout shape for this task was:

- repo/default behavior: `review_gate.mode=observe`
- blocking path implemented and tested in `enforce`
- no live enablement to `enforce`
- no gateway restart as part of this task

## Inventory

Runtime inventory on `hermes-agent` showed:

- repo-local built-in skills under `skills/software-development/*`
- user-owned skills under `/home/hermes/.hermes/skills/software-development/*`
- no current standalone `software-development-denis` skill directory in the inspected active inventory

Decision:

- do not silently delete anything
- treat `software-development-denis` as not currently active in the discovered inventory
- add Hermes-prefixed repo skills instead of vendoring the full upstream superpowers tree

## Implemented Runtime Gate

Primary logic:

- `hermes_cli/review_gate.py`

Primary integration/data surface:

- `hermes_cli/profile_execution.py`

Runtime enforcement seam:

- `agent/turn_finalizer.py`

Supporting surfaces:

- `hermes_cli/profile_preview.py`
- `hermes_cli/config.py`
- `config/hermes-model-policy.yaml`
- `hermes_cli/profile_validation.py`

## State Machine

Modes:

- `disabled`
- `observe`
- `enforce`

Verdicts:

- `pending`
- `approved`
- `changes_requested`
- `blocked`
- `waived`
- `not_required`

Behavior:

- `disabled`: no warning, no block
- `observe`: emits review-required signal/packet, never blocks completion
- `enforce`: blocks final completion for material engineering changes unless verdict is `approved` or `waived`

Non-goals preserved:

- read-only inspection does not require review
- planning-only tasks do not require review
- production approval gating remains separate and stronger
- review approval does not grant deploy/restart/secrets access

## Reviewer Policy

Configured target reviewer tier:

- `code_review`

Configured reviewer model:

- `openrouter / anthropic/claude-opus-4.6`

Fallback implementation note:

- `code_review` is added as a first-class tier in model policy and validation for review-gate use
- engineer execution routing remains unchanged: `coding -> openrouter/xiaomi/mimo-v2.5-pro`

## Skills Added

Repo-local Hermes-prefixed skills added:

- `skills/software-development/hermes-review-gate-enforcement/SKILL.md`
- `skills/software-development/hermes-sliced-review-delivery/SKILL.md`

Purpose:

- make the review-gated delivery workflow explicit in Hermes terminology
- keep the adaptation narrow rather than vendoring all upstream superpowers content

## Tests and Verification

Targeted tests added/updated:

- `tests/hermes_cli/test_profile_execution.py`
- `tests/agent/test_role_context_injection.py`

Verification slices:

- review-gate pure decision logic
- observe mode non-blocking behavior
- enforce mode blocking behavior
- approved/waived verdict unblocks helper state
- runtime tail enforcement in `finalize_turn()`
- no regressions in profile preview / model selection / routing targeted suites

## Live Enablement Status

Not performed in this task:

- no live `config.yaml` enablement to `enforce`
- no gateway restart
- no production rollout mutation beyond repo implementation/testing

