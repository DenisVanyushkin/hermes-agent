from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import textwrap

import pytest

from hermes_cli.profile_approval import (
    ApprovalError,
    ApprovalRequest,
    build_approval_request,
    classify_engineer_approval,
    decision_to_dict,
    decision_to_json,
)
from hermes_cli.profile_routing import route_task


REPO_ROOT = Path(__file__).resolve().parents[2]


def _preview(task: str, **kwargs):
    return classify_engineer_approval(task, **kwargs)


def _route(task: str):
    return route_task(task)


@pytest.mark.parametrize(
    "task, expected_action_type",
    [
        ("deploy WebUI to production", "deploy"),
        ("rollback the failed release", "rollback"),
        ("systemctl restart hermes-webui", "service_control"),
        ("systemctl reload hermes-webui", "service_control"),
        ("update production config for the WebUI", "production_config_change"),
        ("production DB migration for job-intel", "production_db_migration_or_repair"),
        ("repair the production database", "production_db_migration_or_repair"),
        ("change Cloudflare and firewall rules for public exposure", "public_exposure_change"),
        ("change the scheduler timer for nightly jobs", "scheduler_timer_change"),
        ("change tool permissions for browser access", "tool_permission_change"),
        ("rotate auth secret and update credentials for WebUI", "auth_secret_handling_change"),
    ],
)
def test_mutation_classification_requires_approval(task: str, expected_action_type: str):
    preview = _preview(task)

    assert preview.action_type == expected_action_type
    assert preview.approval_applicability == "applicable"
    assert preview.requires_approval is True
    assert preview.blocked_until_approved is True
    assert "approval required" in preview.classification_reason.lower()


@pytest.mark.parametrize(
    "task, expected_action_type",
    [
        ("check status of WebUI", "status"),
        ("health check the host", "health_check"),
        ("inspect logs from the last run", "log_inspection"),
        ("read config and verify settings for WebUI", "config_read"),
        ("git status and git diff for Hermes WebUI", "git_status_diff_read"),
        ("systemctl status hermes-webui", "systemctl_status"),
        ("docker ps and docker logs for WebUI", "docker_inspect"),
        ("smoke check the WebUI and inspect logs", "smoke_check"),
        ("Проверь статус WebUI и логи", "log_inspection"),
        ("Show WebUI logs", "log_inspection"),
    ],
)
def test_read_only_classification_does_not_require_approval(task: str, expected_action_type: str):
    preview = _preview(task)

    assert preview.action_type == expected_action_type
    assert preview.approval_applicability == "applicable"
    assert preview.requires_approval is False
    assert preview.blocked_until_approved is False
    assert "read-only" in preview.classification_reason.lower() or "non-mutating" in preview.classification_reason.lower()


@pytest.mark.parametrize(
    "task",
    [
        "Show WebUI logs",
        "Check WebUI status and inspect logs",
    ],
)
def test_show_webui_logs_does_not_require_approval(task: str):
    preview = _preview(task)

    assert preview.requires_approval is False
    assert preview.blocked_until_approved is False
    assert preview.action_type in {"log_inspection", "status", "health_check", "smoke_check"}


def test_smoke_check_plain_is_read_only():
    preview = _preview("smoke check the WebUI and inspect logs")

    assert preview.action_type == "smoke_check"
    assert preview.requires_approval is False
    assert preview.blocked_until_approved is False


@pytest.mark.parametrize("task", ["deploy WebUI smoke check", "restart WebUI smoke check"])
def test_smoke_with_mutation_requires_approval(task: str):
    preview = _preview(task)

    assert preview.requires_approval is True
    assert preview.blocked_until_approved is True
    assert any("smoke" in reason.lower() for reason in preview.ambiguity_reasons)


def test_route_decision_integration_engineer_route_applicable():
    route_decision = _route("Deploy WebUI and document the result")
    preview = _preview("Deploy WebUI and document the result", route_decision=route_decision)

    assert preview.approval_applicability == "applicable"
    assert preview.profile == "engineer"
    assert preview.requires_approval is True


def test_route_decision_integration_non_engineer_route_not_applicable():
    route_decision = _route("Weather/news digest for current market/company context")
    preview = _preview("Weather/news digest for current market/company context", route_decision=route_decision)

    assert preview.approval_applicability == "not_applicable"
    assert preview.requires_approval is False
    assert preview.blocked_until_approved is False
    assert "does not apply" in preview.classification_reason.lower()


def test_fail_closed_ambiguity_defaults_to_requires_approval():
    preview = _preview("maybe change something on the host")

    assert preview.approval_applicability == "applicable"
    assert preview.requires_approval is True
    assert preview.blocked_until_approved is True
    assert preview.ambiguity_reasons


def test_structured_field_priority_commands_override_free_text():
    preview = _preview(
        "check WebUI status",
        commands_or_control_script="systemctl restart hermes-webui",
    )

    assert preview.requires_approval is True
    assert preview.action_type == "service_control"
    assert any("commands_or_control_script" in reason for reason in preview.ambiguity_reasons)


def test_structured_field_priority_intended_change_override_free_text():
    preview = _preview(
        "inspect WebUI logs",
        intended_change="update production config",
    )

    assert preview.requires_approval is True
    assert preview.action_type == "production_config_change"
    assert any("intended_change" in reason for reason in preview.ambiguity_reasons)


def test_serialization_includes_required_fields():
    preview = _preview(
        "Deploy WebUI and document the result",
        target_host="hermes-vps",
        target_service="hermes-webui",
        intended_change="deploy WebUI release",
        rollback_plan="revert the release commit",
        evidence_before=["git status clean", "service currently healthy"],
    )
    payload = json.loads(decision_to_json(preview))

    required_fields = {
        "profile",
        "action_type",
        "target_host",
        "target_service",
        "intended_change",
        "commands_or_control_script",
        "expected_effect",
        "risk",
        "rollback_plan",
        "evidence_before",
        "requires_approval",
        "blocked_until_approved",
        "classification_reason",
        "confidence",
        "ambiguity_reasons",
        "approval_applicability",
    }

    assert required_fields.issubset(payload.keys())
    assert payload["profile"] == "engineer"
    assert payload["requires_approval"] is True
    assert payload["blocked_until_approved"] is True
    assert payload["approval_applicability"] == "applicable"
    assert payload["ambiguity_reasons"]
    assert payload["evidence_before"] == ["git status clean", "service currently healthy"]


def test_import_boundary_does_not_pull_runtime_modules():
    code = textwrap.dedent(
        r'''
        import sys
        import hermes_cli.profile_approval  # noqa: F401
        forbidden = [name for name in sys.modules if name.startswith(('agent', 'gateway', 'cron.scheduler', 'run_agent'))]
        print('\n'.join(sorted(forbidden)))
        '''
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    forbidden = [line for line in result.stdout.splitlines() if line.strip()]
    assert forbidden == []


@pytest.mark.parametrize(
    "task, expected_fragment",
    [
        ("Deploy WebUI and document the result", '"requires_approval": true'),
        ("Check WebUI status and inspect logs", '"requires_approval": false'),
    ],
)
def test_preview_script_prints_json(task: str, expected_fragment: str):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/preview_engineer_approval.py",
            "--task",
            task,
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert expected_fragment in result.stdout
    payload = json.loads(result.stdout)
    assert payload["profile"] == "engineer"


def test_preview_script_exits_non_zero_on_empty_input():
    result = subprocess.run(
        [sys.executable, "scripts/preview_engineer_approval.py", "--task", "   ", "--json"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode != 0
    assert "must not be empty" in result.stderr


def test_build_approval_request_rejects_malformed_route_decision():
    request = ApprovalRequest(task_text="Deploy WebUI")

    with pytest.raises(ApprovalError):
        build_approval_request(object(), request)  # type: ignore[arg-type]
