"""The reviewer must require a per-file description for every changed file.

The commit-gate message shows the engineer's own per-file text to the operator
(see _render_change_section); when the engineer omits it, the operator is asked
to approve a commit whose contents nobody described. Malformed engineer metadata
fails the whole run rather than triggering rework, so the reviewer is the only
place that can ask for a redo.
"""
from __future__ import annotations

from pathlib import Path


def _prompt_text() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    return (repo_root / "prompts/subagents/hermes_code_reviewer.md").read_text(encoding="utf-8")


def test_reviewer_prompt_requires_per_file_change_descriptions():
    text = _prompt_text().lower()
    assert "undescribed_changed_file" in text
    assert "per-file" in text or "per file" in text


def test_reviewer_prompt_keeps_undescribed_files_as_rework_not_block():
    """A missing description is a documentation gap, not a safety incident --
    it must not escalate to status="blocked" and halt the pipeline."""
    text = _prompt_text()
    idx = text.find("undescribed_changed_file")
    assert idx != -1
    window = text[max(0, idx - 600):idx + 600]
    assert "needs_review" in window
    assert "rework" in window
