# Smoke 022 Report

- Baseline commit: `269179936`
- Classification: `GREEN_FULL_COMMIT_GATE_REACHED`
- Date: `2026-06-28`
- Scope: host-side milestone report after the successful controlled smoke `022`

## Summary

Smoke `022` reached the full controlled engineering commit gate on the real Telegram path while preserving the disabled baseline after rollback. This run proved the intended success path without performing commit or push and without reopening live configuration.

## Key Evidence

- Real Telegram ingress worked.
- Selected pipeline: `engineering_review_pipeline`.
- Router strategy/confidence: `deterministic_strong`, `0.93`.
- Controlled execution was invoked.
- Engineer and reviewer were both invoked.
- Controlled pytest evidence was captured and tests passed.
- Reviewer approved the candidate.
- `completion_allowed=true`.
- `blocked_reason=null`.
- Success `final_response_text` was delivered back to Telegram.
- Normal fallback remained blocked.
- Commit gate was reached.
- No commit or push was performed.
- Rollback restored the disabled baseline.

## Cleanup Notes

- Smoke `022` left temporary residue files as untracked worktree artifacts:
  - `hermes_cli/smoke_square.py`
  - `tests/test_smoke_square.py`
- These files are disposable smoke artifacts and must be removed during host-side cleanup.

## Environment Notes

- OpenRouter `402` observed during related environment activity is an expected no-balance condition for this environment.
- The `402` is not classified as a smoke blocker and is not a bug for this slice.

## Constraints Preserved

- No secrets, tokens, raw auth payloads, or large raw logs are included here.
- No provider/model identity changes were made.
- No fallback behavior changes were made.
- No autonomous enable/disable config was altered.
- No commit/push behavior was altered.
