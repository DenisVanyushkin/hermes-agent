from pathlib import Path


def test_engineer_prompt_forbids_monkeypatch_and_requires_in_place_edits():
    text = Path("prompts/subagents/hermes_engineer_core.md").read_text(encoding="utf-8")
    assert "EDIT THE EXISTING FILE" in text
    assert "sitecustomize" in text  # explicitly forbids the monkey-patch workaround
    assert "git_diff" in text       # cross-iteration self-inspection guidance
    assert "file not writable" in text  # report concrete blocker, not workaround
