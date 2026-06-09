from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hermes_cli.profile_execution import (
    RoleExecutionPlan,
    build_role_execution_plan,
    classify_operation_category,
    classify_external_commitment,
    classify_production_runtime_mutation,
    classify_sensitive_diff_triggers,
    execution_plan_to_dict,
    execution_plan_to_json,
)
from hermes_cli.profile_routing import route_task


REPO_ROOT = Path(__file__).resolve().parents[2]


def _plan(task: str, **kwargs):
    return build_role_execution_plan(task, **kwargs)


@pytest.mark.parametrize(
    "task,expected_role",
    [
        ("Investigate failing pytest suite and fix the code", "engineer"),
        ("Perform a security review of auth cookies and secrets exposure", "security_auditor"),
        ("Write a handoff note for the docs and current state", "scribe"),
        ("Research current sources and summarize findings", "researcher"),
        ("Review my CV and vacancy strategy", "career_strategist"),
        ("Зафиксируй итог сегодняшней работы по ролям Hermes", "scribe"),
        ("Оцени вакансию Head of Product для меня", "career_strategist"),
        ("Book me a haircut", "general_operator"),
        ("Create a calendar event for tomorrow", "general_operator"),
        ("Create a reminder for the dentist", "general_operator"),
        ("Make a restaurant reservation", "general_operator"),
        ("Prepare a simple checklist for my errands", "general_operator"),
    ],
)
def test_role_selection_by_intent(task: str, expected_role: str):
    plan = _plan(task)
    assert plan.selected_role == expected_role


def test_ordinary_personal_admin_tasks_do_not_require_reviewer_or_scribe():
    plan = _plan("Book me a haircut")
    assert plan.ordinary_personal_admin is True
    assert plan.requires_reviewer is False
    assert plan.reviewer_profile is None
    assert plan.requires_scribe is False
    assert plan.scribe_reason == ""


@pytest.mark.parametrize(
    "task,expected_external",
    [
        ("Book me a haircut", True),
        ("Create a calendar event for tomorrow", True),
        ("Create a reminder for the dentist", True),
        ("Draft a message to my barber", False),
    ],
)
def test_external_commitment_metadata(task: str, expected_external: bool):
    plan = _plan(task)
    assert plan.external_commitment is expected_external
    assert classify_external_commitment(task) is expected_external


@pytest.mark.parametrize(
    "task,expect_explicit,expected_reason_contains",
    [
        ("Please pay an invoice", True, "money"),
        ("Update my identity document details", True, "identity"),
        ("Deploy the service to production", True, "production/runtime mutation"),
        ("Check WebUI status and inspect logs", False, ""),
    ],
)
def test_confirmation_and_escalation_metadata(task: str, expect_explicit: bool, expected_reason_contains: str):
    plan = _plan(task)
    assert plan.requires_explicit_approval is expect_explicit
    if expect_explicit:
        assert expected_reason_contains.lower() in plan.approval_reason.lower()


@pytest.mark.parametrize(
    "task,expected_mutation",
    [
        ("Update docs and code in the repo", False),
        ("Deploy the service to production", True),
        ("Restart the production service", True),
        ("Change Cloudflare firewall rules", True),
        ("Run a database migration in production", True),
        ("Execute trading orders", False),
    ],
)
def test_classify_production_runtime_mutation(task: str, expected_mutation: bool):
    assert classify_production_runtime_mutation(task) is expected_mutation


@pytest.mark.parametrize(
    "task,expected_trigger_subset",
    [
        ("Change auth cookies and secrets env", {"auth/session/cookies", "secrets/tokens/env"}),
        ("Update Cloudflare reverse proxy firewall for WebUI public access", {"Cloudflare/reverse proxy/firewall", "WebUI public access"}),
        ("Adjust gateway cron scheduler tool permissions", {"gateway", "cron/scheduler", "tool permissions"}),
        ("Review SSH browser profile upload permissions", {"SSH", "browser profiles", "file manager / shell / terminal / git / upload permissions"}),
        ("Update production deploy scripts and database migration paths", {"production deploy scripts", "database migrations"}),
        ("Modify trading risk execution paths", set()),
        ("Store untrusted external content persistently", {"persistent storage of untrusted external content"}),
    ],
)
def test_sensitive_diff_trigger_classification(task: str, expected_trigger_subset: set[str]):
    triggers = set(classify_sensitive_diff_triggers(task))
    assert expected_trigger_subset.issubset(triggers)


def test_sensitive_tasks_require_reviewer_and_may_require_scribe():
    plan = _plan("Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения")
    assert plan.selected_role == "engineer"
    assert plan.requires_reviewer is True
    assert plan.reviewer_profile == "security_auditor"
    assert plan.requires_scribe is False
    assert plan.durable_outcome_expected is False
    assert plan.production_runtime_mutation is True
    assert plan.requires_explicit_approval is True
    assert "Cloudflare/reverse proxy/firewall" in plan.sensitive_diff_triggers


def test_sensitive_task_without_mitigation_is_not_passive():
    plan = _plan("Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения")
    assert plan.post_change_review_policy["invoke_security_auditor"] is True
    assert plan.post_change_review_policy["summarize_diff"] is True
    assert plan.post_change_review_policy["run_relevant_tests"] is True


def test_durable_outcome_triggers_scribe_recommendation():
    plan = _plan("Implement a repo fix and document the handoff")
    assert plan.requires_scribe is True
    assert plan.durable_outcome_expected is True
    assert "durable" in plan.scribe_reason.lower() or "handoff" in plan.scribe_reason.lower()


def test_read_only_status_and_logs_do_not_require_scribe_or_approval():
    plan = _plan("Проверь статус WebUI и логи")
    assert plan.selected_role == "engineer"
    assert plan.operation_category == "read_only_investigation"
    assert plan.requires_reviewer is False
    assert plan.requires_scribe is False
    assert plan.requires_explicit_approval is False
    assert plan.critical_approval_required is False
    assert plan.ordinary_personal_admin is False
    assert plan.durable_outcome_expected is False
    assert plan.post_change_review_policy["invoke_scribe"] is False


@pytest.mark.parametrize(
    "task",
    [
        "Restart WebUI",
        "Deploy WebUI",
    ],
)
def test_mutation_tasks_require_explicit_approval(task: str):
    plan = _plan(task)
    assert plan.selected_role == "engineer"
    assert plan.requires_explicit_approval is True
    assert plan.critical_approval_required is True
    assert plan.production_runtime_mutation is True


def test_ephemeral_task_does_not_trigger_scribe():
    plan = _plan("Check WebUI status and inspect logs")
    assert plan.requires_scribe is False
    assert plan.durable_outcome_expected is False


def test_repo_code_mutation_allowed_for_engineer():
    plan = _plan("Fix the failing pytest suite in the repository")
    assert plan.selected_role == "engineer"
    assert plan.production_runtime_mutation is False
    assert plan.requires_explicit_approval is False
    assert plan.post_change_review_policy["summarize_diff"] is True


@pytest.mark.parametrize(
    "task",
    [
        "Make a BTC trade",
        "Buy BTC",
        "Activate trading",
    ],
)
def test_trade_prompts_do_not_select_trading_role(task: str):
    plan = _plan(task)
    assert plan.selected_role != "trading_observer_trader"
    assert plan.selected_role != "trading_observer_trader_deferred"
    assert plan.trading_deferred is False


def test_cloudflare_public_exposure_requires_narrow_critical_hard_stop():
    plan = _plan("Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения")
    assert plan.selected_role == "engineer"
    assert plan.requires_explicit_approval is True
    assert plan.critical_approval_required is True
    assert plan.external_commitment is False
    assert plan.production_runtime_mutation is True


def test_docs_only_scribe_update_with_cloudflare_evidence_does_not_require_critical_approval():
    plan = _plan(
        "Зафиксируй финальный статус Hermes roles runtime MVP после live smoke. "
        "Cloudflare/public exposure prompt PASS. "
        "Update docs/profile-handoffs/2026-06-09-hermes-role-work.md and "
        "docs/state/current-operational-state.md. "
        "Do not change code. Do not deploy. Do not restart gateway."
    )
    assert plan.selected_role == "scribe"
    assert plan.requires_reviewer is False
    assert plan.requires_explicit_approval is False
    assert plan.critical_approval_required is False
    assert plan.production_runtime_mutation is False


def test_external_commitment_requires_confirmation_but_not_critical_hard_stop():
    plan = _plan("Запиши меня на стрижку")
    assert plan.selected_role == "general_operator"
    assert plan.external_commitment is True
    assert plan.requires_explicit_approval is True
    assert plan.critical_approval_required is False


def test_investigation_prompt_does_not_require_critical_hard_stop():
    plan = _plan("Investigate approval-gate regression")
    assert plan.requires_explicit_approval is False
    assert plan.critical_approval_required is False


def test_docs_only_cloudflare_smoke_update_does_not_require_critical_approval():
    plan = _plan("Update docs/state/current-operational-state.md with Cloudflare smoke PASS. Do not deploy. Do not touch Cloudflare.")
    assert plan.selected_role == "scribe"
    assert plan.requires_reviewer is False
    assert plan.requires_explicit_approval is False
    assert plan.critical_approval_required is False


def test_cloudflare_investigation_without_change_does_not_require_critical_approval():
    plan = _plan("Investigate approval-gate regression around Cloudflare. Do not change Cloudflare.")
    assert plan.selected_role == "engineer"
    assert plan.requires_explicit_approval is False
    assert plan.critical_approval_required is False
    assert plan.production_runtime_mutation is False


def test_read_only_systemd_timer_inspection_stays_engineer_without_hard_stop():
    plan = _plan("Inspect systemctl --user status, list timers, list services, and journalctl logs for Hermes fallback refresh timer")
    assert plan.selected_role == "engineer"
    assert plan.operation_category == "read_only_investigation"
    assert plan.reviewer_profile is None
    assert plan.requires_reviewer is False
    assert plan.requires_explicit_approval is False
    assert plan.critical_approval_required is False
    assert plan.production_runtime_mutation is False


def test_user_level_fallback_refresh_timer_install_is_normal_operational_mutation_without_hard_stop():
    plan = _plan(
        "Install a user-level systemd timer for "
        "/home/hermes/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main fallback refresh "
        "with WorkingDirectory=/home/hermes/.hermes/hermes-agent and state path "
        "~/.hermes/state/model_fallbacks.json. "
        "Use systemctl --user. No gateway restart. No config/auth/provider mutation. "
        "No public exposure. No secrets. No Trading."
    )
    assert plan.selected_role == "engineer"
    assert plan.operation_category == "normal_operational_mutation"
    assert plan.reviewer_profile is None
    assert plan.requires_reviewer is False
    assert plan.requires_explicit_approval is False
    assert plan.critical_approval_required is False
    assert plan.production_runtime_mutation is False


def test_root_system_wide_timer_install_is_higher_risk():
    plan = _plan(
        "Install a root system-wide systemd timer in /etc/systemd/system "
        "for Hermes maintenance and enable it globally."
    )
    assert plan.selected_role == "engineer"
    assert plan.operation_category == "security_critical_mutation"
    assert plan.requires_explicit_approval is True
    assert plan.critical_approval_required is True
    assert plan.production_runtime_mutation is True


def test_scheduler_job_that_restarts_gateway_requires_critical_approval():
    plan = _plan("Create a daily scheduler job that restarts the Hermes gateway every day")
    assert plan.selected_role == "engineer"
    assert plan.operation_category == "security_critical_mutation"
    assert plan.requires_explicit_approval is True
    assert plan.critical_approval_required is True
    assert plan.production_runtime_mutation is True


def test_scheduler_job_that_touches_cloudflare_or_public_exposure_requires_security_auditor():
    plan = _plan("Create a scheduler job that updates Cloudflare tunnel and firewall rules for public exposure")
    assert plan.selected_role == "engineer"
    assert plan.operation_category == "security_critical_mutation"
    assert plan.reviewer_profile == "security_auditor"
    assert plan.requires_reviewer is True
    assert plan.requires_explicit_approval is True
    assert plan.critical_approval_required is True


def test_scheduler_job_that_writes_secrets_or_provider_config_requires_security_auditor():
    plan = _plan("Create a scheduler job that rotates provider auth tokens and writes secrets into config")
    assert plan.selected_role == "engineer"
    assert plan.operation_category == "security_critical_mutation"
    assert plan.reviewer_profile == "security_auditor"
    assert plan.requires_reviewer is True
    assert plan.requires_explicit_approval is True
    assert plan.critical_approval_required is True


def test_investigation_prompt_with_negative_trading_guardrail_routes_to_engineer():
    plan = _plan("Investigate approval-gate regression. Do not activate Trading.")
    assert plan.selected_role == "engineer"
    assert plan.trading_deferred is False


def test_crypto_research_prompt_routes_without_trading_role():
    plan = _plan("Где я могу наиболее выгодно купить BTC?")
    assert plan.selected_role in {"researcher", "general_operator"}
    assert plan.selected_role != "trading_observer_trader"
    assert plan.trading_deferred is False


def test_execution_plan_serialization_round_trip():
    plan = _plan("Book me a haircut")
    payload = execution_plan_to_dict(plan)
    encoded = execution_plan_to_json(plan)
    assert json.loads(encoded) == payload
    assert payload["selected_role"] == "general_operator"
    assert payload["ordinary_personal_admin"] is True


def test_role_execution_plan_is_dataclass_like():
    plan = _plan("Book me a haircut")
    assert isinstance(plan, RoleExecutionPlan)


def test_import_boundary_does_not_pull_runtime_modules():
    code = (
        "import sys\n"
        "import hermes_cli.profile_execution  # noqa: F401\n"
        "forbidden = [name for name in sys.modules if name.startswith(('agent', 'gateway', 'run_agent', 'cron.scheduler'))]\n"
        "print('\\n'.join(sorted(forbidden)))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    forbidden = [line for line in result.stdout.splitlines() if line.strip()]
    assert forbidden == []


def test_general_operator_fallback_for_safe_unclear_task():
    plan = _plan("Help me with a simple safe admin task")
    assert plan.selected_role == "general_operator"
    assert plan.fallback_used is True
    assert plan.fallback_reason


def test_money_and_identity_tasks_escalate():
    money_plan = _plan("Pay an invoice for me")
    identity_plan = _plan("Update identity document details")
    assert money_plan.requires_explicit_approval is True
    assert identity_plan.requires_explicit_approval is True
    assert money_plan.external_commitment is False


def test_sensitive_task_route_and_execution_plan_align():
    decision = route_task("Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения")
    plan = _plan("Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения", route_decision=decision)
    assert plan.selected_role == decision.primary_profile
    assert plan.selected_role == "engineer"
    assert plan.requires_reviewer is True


def test_execution_plan_to_dict_contains_required_fields():
    plan = _plan("Book me a haircut")
    payload = execution_plan_to_dict(plan)
    for field in [
        "task",
        "selected_role",
        "role_intent",
        "fallback_used",
        "fallback_reason",
        "requires_reviewer",
        "reviewer_profile",
        "requires_scribe",
        "scribe_reason",
        "requires_explicit_approval",
        "critical_approval_required",
        "approval_reason",
        "operation_category",
        "ordinary_personal_admin",
        "external_commitment",
        "sensitive_diff_triggers",
        "production_runtime_mutation",
        "post_change_review_policy",
        "durable_outcome_expected",
        "trading_deferred",
    ]:
        assert field in payload


@pytest.mark.parametrize(
    "task,expected_category",
    [
        ("Inspect systemctl --user status and list timers for Hermes fallback refresh", "read_only_investigation"),
        (
            "Install a user-level systemd timer for /home/hermes/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main fallback refresh. "
            "Use systemctl --user. No gateway restart. No config/auth/provider mutation. No public exposure. No secrets.",
            "normal_operational_mutation",
        ),
        ("Install a root system-wide systemd timer in /etc/systemd/system for Hermes maintenance", "security_critical_mutation"),
    ],
)
def test_classify_operation_category(task: str, expected_category: str):
    assert classify_operation_category(task) == expected_category
