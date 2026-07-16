"""Regression: review-gate blocked completions must not be delivered as
provider failures (2026-07-16: a rebase cron run blocked by the review gate
was reported to Slack as "provider timeout" because the blocked-completion
report quoted logs containing the word "timeout")."""

from __future__ import annotations

from cron.scheduler import _summarize_cron_failure_for_delivery

JOB = {"name": "hermes-rebase-local-customizations", "id": "6c8c8188f6f9"}

REVIEW_GATE_BLOCK = """Hermes role: scribe
Role context: used
Implementation: n/a / n/a
Reviewer: openai-codex / gpt-5.6-sol / changes_requested
Approval: required

Final completion is blocked by automatic review verdict.

Task summary: upstream sync triage
Review gate mode: enforce
Approval scope: session
Reviewer tier: code_review
Reviewer model: openai-codex / gpt-5.6-sol
Automatic review invoked: yes
Automatic review verdict: changes_requested

Reviewer findings:
- The LLM router call timed out once and used the retry result.
- No routing tests were run for this approval-sensitive change.
"""


def test_review_gate_block_is_not_labeled_provider_timeout() -> None:
    message = _summarize_cron_failure_for_delivery(JOB, REVIEW_GATE_BLOCK)

    assert "provider timeout" not in message
    assert "review gate blocked completion" in message
    assert "changes_requested" in message
    assert "approval is required" in message
    assert "hermes-rebase-local-customizations" in message


def test_plain_timeout_error_still_labeled_provider_timeout() -> None:
    message = _summarize_cron_failure_for_delivery(
        JOB, "ReadTimeout: HTTPSConnectionPool timed out"
    )

    assert "provider timeout" in message


def test_review_gate_block_without_approval_line() -> None:
    text = REVIEW_GATE_BLOCK.replace("Approval: required", "Approval: not_required")
    message = _summarize_cron_failure_for_delivery(JOB, text)

    assert "review gate blocked completion" in message
    assert "approval is required" not in message
