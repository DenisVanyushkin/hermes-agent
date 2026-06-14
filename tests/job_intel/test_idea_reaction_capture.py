from __future__ import annotations

import json
from pathlib import Path

import job_intel.idea_reaction_capture as mod


def test_process_event_dry_run_appends_expected_text(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("SLACK_IDEA_REACTION_CHANNELS", "C123")
    monkeypatch.setenv("SLACK_IDEA_DOC_ID", "doc-123")

    event = {
        "type": "reaction_added",
        "reaction": "thumbsup",
        "item": {"channel": "C123", "ts": "123.456"},
    }
    message = {"text": "Build a better ideas backlog", "user": "U_BOT", "bot_id": "B123"}

    result = mod.process_event(event, message=message, bot_user_id="U_BOT", dry_run=True)

    assert result["status"] == "dry_run"
    assert result["doc_id"] == "doc-123"
    assert "Build a better ideas backlog" in result["append_text"]


def test_process_event_ignores_non_bot_message(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("SLACK_IDEA_REACTION_CHANNELS", "C123")

    event = {
        "type": "reaction_added",
        "reaction": "thumbsup",
        "item": {"channel": "C123", "ts": "123.456"},
    }
    message = {"text": "Nice idea", "user": "U123"}

    result = mod.process_event(event, message=message, bot_user_id="U_BOT", dry_run=True)

    assert result["status"] == "ignored"
    assert result["reason"] == "not_bot_authored"


def test_process_event_persists_dedupe_state_on_real_append(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("SLACK_IDEA_REACTION_CHANNELS", "C123")
    monkeypatch.setenv("SLACK_IDEA_DOC_ID", "doc-123")

    captured: dict[str, list[str]] = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd

        class Proc:
            returncode = 0
            stdout = json.dumps({"status": "appended", "documentId": "doc-123"})
            stderr = ""

        return Proc()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    event = {
        "type": "reaction_added",
        "reaction": "+1",
        "item": {"channel": "C123", "ts": "123.456"},
    }
    message = {"text": "Capture this idea", "user": "U_BOT", "bot_id": "B123"}

    result = mod.process_event(event, message=message, bot_user_id="U_BOT", dry_run=False)

    assert result["status"] == "ok"
    assert captured["cmd"][2] == "docs"
    state_path = Path(hermes_home) / "state" / "slack_idea_reactions.json"
    assert state_path.exists()
