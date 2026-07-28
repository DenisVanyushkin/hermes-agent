"""The reviewer must account for every item of a plan the operator approved wholesale.

On 2026-07-28 the agent proposed five fixes and the operator answered "вот сделай
все что ты предлагаешь". Three were done, one silently dropped, and one -- "fix the
browser desktop environment" -- came back as a guard that marks the browser checks
skipped, i.e. the opposite of what was asked for. Nothing in the loop noticed: an
item that was never implemented leaves no trace in the diff, and the diff is all
the review reads. The reviewer, which also sees the conversation, is the only
reader that can catch it.
"""
from __future__ import annotations

from pathlib import Path


def _prompt_text() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    return (repo_root / "prompts/subagents/hermes_code_reviewer.md").read_text(encoding="utf-8")


def test_reviewer_prompt_requires_every_approved_plan_item_to_be_accounted_for():
    text = _prompt_text().lower()
    assert "unaccounted_promised_item" in text
    assert "approved" in text


def test_reviewer_prompt_names_the_three_legitimate_outcomes():
    """"Not done" and "done differently" are legitimate answers -- but only with a
    reason. Without one it is an omission, which is the case the rule exists for."""
    text = _prompt_text().lower()
    idx = text.find("unaccounted_promised_item")
    assert idx != -1
    window = text[max(0, idx - 900):idx + 400]
    assert "done" in window
    assert "reason" in window


def test_reviewer_prompt_keeps_unaccounted_items_as_rework_not_block():
    """An unaccounted item is an incomplete report, not a safety incident: it must
    not escalate to status="blocked" and halt the pipeline."""
    text = _prompt_text()
    idx = text.find("unaccounted_promised_item")
    assert idx != -1
    window = text[max(0, idx - 200):idx + 600]
    assert "needs_review" in window
    assert "rework" in window


def test_reviewer_prompt_treats_a_symptom_hiding_answer_as_unaccounted():
    """The failure mode that started this: an item answered by machinery that mutes
    the very signal it was supposed to repair must not pass as "done differently"."""
    text = _prompt_text().lower()
    assert "hides the condition it was meant to repair" in text
