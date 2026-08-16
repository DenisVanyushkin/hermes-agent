"""Tests for the Slack plugin's interactive_setup wizard.

These cover the home-channel save logic that previously lived in
``hermes_cli/setup.py::_setup_slack`` before the Slack adapter migrated to a
bundled plugin (#41112). ``interactive_setup`` lazy-imports its CLI helpers
from ``hermes_cli.config`` (get_env_value / save_env_value / remove_env_value)
and ``hermes_cli.cli_output`` (prompt / prompt_yes_no / print_*), so we patch
those source modules.
"""
import asyncio

from unittest.mock import AsyncMock

import hermes_cli.config as config_mod
import hermes_cli.cli_output as cli_output_mod
from plugins.platforms.slack.adapter import SlackAdapter, interactive_setup


class _EventRegistry:
    """Minimal AsyncApp.event facade for registration tests."""

    def __init__(self):
        self.handlers = {}

    def event(self, event_name):
        def register(handler):
            self.handlers.setdefault(event_name, []).append(handler)
            return handler

        return register


def _patch_setup_io(monkeypatch, prompts, saved, removed, existing):
    """Wire interactive_setup's lazy-imported CLI helpers to test doubles."""
    prompt_iter = iter(prompts)
    monkeypatch.setattr(config_mod, "get_env_value", lambda key: existing.get(key, ""))
    monkeypatch.setattr(config_mod, "save_env_value", lambda k, v: saved.update({k: v}))

    # Mirror remove_env_value's real semantics: True if removed, False if absent.
    def _remove(key):
        removed.append(key)
        return existing.pop(key, None) is not None

    monkeypatch.setattr(config_mod, "remove_env_value", _remove)
    monkeypatch.setattr(cli_output_mod, "prompt", lambda *_a, **_kw: next(prompt_iter))
    monkeypatch.setattr(cli_output_mod, "prompt_yes_no", lambda *_a, **_kw: False)
    for name in ("print_header", "print_info", "print_success", "print_warning"):
        monkeypatch.setattr(cli_output_mod, name, lambda *_a, **_kw: None)
    # Manifest writing reaches out to hermes_cli.slack_cli + filesystem; stub it.
    import hermes_cli.slack_cli as slack_cli_mod
    monkeypatch.setattr(slack_cli_mod, "_build_full_manifest", lambda **_kw: {"display_information": {}})


def test_interactive_setup_saves_home_channel(monkeypatch, tmp_path):
    """interactive_setup() saves SLACK_HOME_CHANNEL when the user provides one."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    saved, removed = {}, []
    # prompts: bot token, app token, allowed users (empty), home channel
    _patch_setup_io(
        monkeypatch,
        ["«redacted:xox…»", "xapp-test-token", "", "C01ABC2DE3F"],
        saved,
        removed,
        existing={},
    )

    interactive_setup()

    assert saved.get("SLACK_HOME_CHANNEL") == "C01ABC2DE3F"
    assert "SLACK_HOME_CHANNEL" not in removed


def test_reaction_handlers_are_registered_once_per_logical_pipeline():
    """Each Slack reaction event must reach each intended pipeline exactly once."""
    adapter = SlackAdapter.__new__(SlackAdapter)
    adapter._app = _EventRegistry()
    adapter._handle_reaction_event = AsyncMock()
    adapter._handle_slack_reaction = AsyncMock()

    adapter._register_reaction_handlers()

    assert len(adapter._app.handlers["reaction_added"]) == 2
    assert len(adapter._app.handlers["reaction_removed"]) == 2

    added = {"type": "reaction_added", "reaction": "+1"}
    for handler in adapter._app.handlers["reaction_added"]:
        asyncio.run(handler(added, None))
    adapter._handle_reaction_event.assert_awaited_once_with(added)
    adapter._handle_slack_reaction.assert_awaited_once_with(added)

    adapter._handle_reaction_event.reset_mock()
    adapter._handle_slack_reaction.reset_mock()
    removed = {"type": "reaction_removed", "reaction": "+1"}
    for handler in adapter._app.handlers["reaction_removed"]:
        asyncio.run(handler(removed, None))
    adapter._handle_reaction_event.assert_awaited_once_with(removed)
    adapter._handle_slack_reaction.assert_awaited_once_with(removed, removed=True)


class TestSlackHomeChannelClear:
    """Blank home-channel answer must clear SLACK_HOME_CHANNEL (#12423)."""

    def test_blank_removes_existing_home_channel(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        saved, removed = {}, []
        _patch_setup_io(
            monkeypatch,
            ["«redacted:xox…»", "xapp-test-token", "", ""],
            saved,
            removed,
            existing={"SLACK_HOME_CHANNEL": "C01OLDHOMEXYZ"},
        )
        interactive_setup()
        assert "SLACK_HOME_CHANNEL" in removed
        assert "SLACK_HOME_CHANNEL" not in saved
