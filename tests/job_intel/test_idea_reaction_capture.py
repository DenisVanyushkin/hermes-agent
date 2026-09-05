from __future__ import annotations

import json
import os
import sys
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

    def fake_run(cmd, capture_output, text, env):
        captured["cmd"] = cmd
        captured["env"] = env

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
    assert captured["cmd"][0] == sys.executable
    assert captured["env"]["HERMES_HOME"] == str(hermes_home)
    assert captured["env"]["PATH"] == os.environ["PATH"]
    state_path = Path(hermes_home) / "state" / "slack_idea_reactions.json"
    assert state_path.exists()


def test_append_to_doc_surfaces_google_cli_stderr(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    def fake_run(cmd, capture_output, text, env):
        class Proc:
            returncode = 1
            stdout = ""
            stderr = "Google credentials are unavailable"

        return Proc()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    try:
        mod._append_to_doc("- idea\n")
    except RuntimeError as exc:
        assert "Google credentials are unavailable" in str(exc)
    else:  # pragma: no cover - assertion keeps the failure actionable
        raise AssertionError("Google CLI failure must preserve stderr")


def test_append_to_doc_falls_back_to_sandbox_google_home(monkeypatch, tmp_path):
    """The gateway runs on the host, but the Google token lives in the sandbox home.

    Regression guard for the 2026-08-20 incident: every 👍 capture failed with
    "Not authenticated" because HERMES_HOME on the host has no google_token.json.
    """

    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    sandbox_home = hermes_home / "sandboxes" / "docker" / "default" / "home" / ".hermes"
    sandbox_home.mkdir(parents=True)
    (sandbox_home / "google_token.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("GOOGLE_WORKSPACE_HERMES_HOME", raising=False)

    captured: dict[str, object] = {}

    def fake_run(cmd, capture_output, text, env):
        captured["env"] = env

        class Proc:
            returncode = 0
            stdout = "{}"
            stderr = ""

        return Proc()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod._append_to_doc("- idea\n")

    assert captured["env"]["HERMES_HOME"] == str(sandbox_home)


def test_append_to_doc_prefers_host_home_when_token_present(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "google_token.json").write_text("{}", encoding="utf-8")
    sandbox_home = hermes_home / "sandboxes" / "docker" / "default" / "home" / ".hermes"
    sandbox_home.mkdir(parents=True)
    (sandbox_home / "google_token.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("GOOGLE_WORKSPACE_HERMES_HOME", raising=False)

    captured: dict[str, object] = {}

    def fake_run(cmd, capture_output, text, env):
        captured["env"] = env

        class Proc:
            returncode = 0
            stdout = "{}"
            stderr = ""

        return Proc()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod._append_to_doc("- idea\n")

    assert captured["env"]["HERMES_HOME"] == str(hermes_home)


def test_append_to_doc_honours_explicit_google_home_override(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    override = tmp_path / "elsewhere"
    override.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("GOOGLE_WORKSPACE_HERMES_HOME", str(override))

    captured: dict[str, object] = {}

    def fake_run(cmd, capture_output, text, env):
        captured["env"] = env

        class Proc:
            returncode = 0
            stdout = "{}"
            stderr = ""

        return Proc()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod._append_to_doc("- idea\n")

    assert captured["env"]["HERMES_HOME"] == str(override)


CRON_POST = (
    "Cronjob Response: idle-idea-prompt (job_id: 4645b2d6a977) ------------- "
    "* [Финансы] История цены перед крупной покупкой - Записывай цену. "
    "Чтобы остановить или изменить эту задачу, отправь новое сообщение "
    "(например: „останови напоминание idle-idea-prompt“). "
    'To stop or manage this job, send me a new message (e.g. "stop reminder idle-idea-prompt").'
)


def _dry_run(monkeypatch, tmp_path, message, ts="1787205694.242879"):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("SLACK_IDEA_REACTION_CHANNELS", "C123")
    monkeypatch.setenv("SLACK_IDEA_DOC_ID", "doc-123")
    event = {"type": "reaction_added", "reaction": "+1", "item": {"channel": "C123", "ts": ts}}
    return mod.process_event(event, message=message, bot_user_id="U_BOT", dry_run=True)


def test_cron_banner_and_stop_trailer_are_stripped(tmp_path, monkeypatch):
    """The idea is the payload; the cron envelope around it is noise."""
    result = _dry_run(monkeypatch, tmp_path, {"text": CRON_POST, "bot_id": "B1"})
    text = result["append_text"]

    assert "Cronjob Response" not in text
    assert "job_id" not in text
    assert "Чтобы остановить" not in text
    assert "To stop or manage this job" not in text
    assert "История цены перед крупной покупкой" in text


def test_bullet_is_dated_from_the_idea_not_from_capture_time(tmp_path, monkeypatch):
    """Backfilled ideas must carry their own date, not the day we repaired auth."""
    result = _dry_run(monkeypatch, tmp_path, {"text": "Идея", "bot_id": "B1"})
    assert result["append_text"].startswith("- [2026-08-20 11:01")  # 06:01 UTC in Asia/Almaty


def test_message_without_cron_envelope_is_untouched(tmp_path, monkeypatch):
    result = _dry_run(monkeypatch, tmp_path, {"text": "Просто идея без обёртки", "bot_id": "B1"})
    assert "Просто идея без обёртки" in result["append_text"]
