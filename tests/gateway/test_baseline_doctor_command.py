"""Text-command baseline-doctor path: works on every platform, unlike reactions
(handled only by the Slack adapter) and unlike 🧹 (premium-only on WhatsApp)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from gateway.run import GatewayRunner


def _source(platform="whatsapp", user_id="U1"):
    return SimpleNamespace(platform=platform, user_id=user_id)


def test_run_command_runs_doctor_and_reports():
    result = {"clean": False, "fixed": [], "remaining": [
        {"path": "scripts/x.py", "category": "untracked", "hint": "script"}]}
    recorded = {}
    with (
        patch("hermes_cli.baseline_doctor_service.run_doctor", return_value=result),
        patch("hermes_cli.baseline_doctor_service.record_pending",
              side_effect=lambda ts, rem: recorded.update({ts: rem})),
    ):
        ack = GatewayRunner._build_baseline_doctor_ack("почисти", _source())
    assert ack is not None
    assert "scripts/x.py" in ack
    assert recorded  # remaining parked for a follow-up action command


def test_action_command_applies_pending():
    applied = {}
    with (
        patch("hermes_cli.baseline_doctor_service.pop_pending",
              return_value=[{"path": "scripts/x.py", "category": "untracked"}]),
        patch("hermes_cli.baseline_doctor_service.apply_action",
              side_effect=lambda repo, action, remaining: applied.update({"action": action})
              or {"applied": action, "paths": ["scripts/x.py"], "ok": True, "detail": "done"}),
        patch("hermes_cli.baseline_doctor_service.run_doctor",
              return_value={"clean": True, "fixed": [], "remaining": []}),
    ):
        ack = GatewayRunner._build_baseline_doctor_ack("gitignore", _source())
    assert applied["action"] == "gitignore"
    assert ack is not None


def test_action_command_without_pending_falls_through():
    with patch("hermes_cli.baseline_doctor_service.pop_pending", return_value=None):
        assert GatewayRunner._build_baseline_doctor_ack("stash", _source()) is None


def test_ordinary_message_falls_through():
    assert GatewayRunner._build_baseline_doctor_ack("почисти базу данных", _source()) is None
    assert GatewayRunner._build_baseline_doctor_ack("привет", _source()) is None
