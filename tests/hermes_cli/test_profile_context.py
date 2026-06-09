from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hermes_cli.profile_context import (
    build_role_context_for_task,
    get_profile_contract,
    inject_role_execution_debug_header,
    load_profile_contracts,
    render_explicit_approval_request,
    render_role_debug_header,
    render_role_execution_debug_header,
    role_debug_header_enabled,
    render_role_context,
)


def test_load_profile_contracts_contains_canonical_roles():
    contracts = load_profile_contracts()
    assert {
        "chief_coordinator",
        "engineer",
        "career_strategist",
        "scribe",
        "researcher",
        "security_auditor",
        "general_operator",
        "trading_observer_trader_deferred",
    }.issubset(contracts)


def test_general_operator_role_context_mentions_personal_admin_guidance():
    contract = get_profile_contract("general_operator")
    context = render_role_context(contract, selected_role="general_operator", canonical_role="general_operator")
    assert "General Operator" in context
    assert "ordinary personal/admin tasks" in context.lower()
    assert "confirmation before external commitment" in context.lower()


def test_engineer_role_context_mentions_repo_and_runtime_boundaries():
    contract = get_profile_contract("engineer")
    context = render_role_context(contract, selected_role="engineer", canonical_role="engineer")
    assert "Engineer" in context
    assert "Repo/code mutation is allowed" in context
    assert "Production/runtime mutation requires explicit approval" in context


def test_security_auditor_role_context_is_reviewer_not_universal_blocker():
    contract = get_profile_contract("security_auditor")
    context = render_role_context(contract, selected_role="security_auditor", canonical_role="security_auditor")
    assert "Security Auditor" in context
    assert "reviewer" in context.lower()
    assert "universal blocker" in context.lower() or "not a universal blocker" in context.lower()


def test_scribe_role_context_emphasizes_durable_outcomes_only():
    contract = get_profile_contract("scribe")
    context = render_role_context(contract, selected_role="scribe", canonical_role="scribe")
    assert "Scribe" in context
    assert "durable outcomes" in context.lower()
    assert "noise" in context.lower() or "not noise" in context.lower()


def test_role_context_is_compact_and_not_full_docs():
    contract = get_profile_contract("engineer")
    context = render_role_context(contract, selected_role="engineer", canonical_role="engineer")
    assert len(context) < 1000
    assert "schema_version" not in context
    assert "profiles:" not in context
    assert "model_governance" not in context
    assert "operating_model_ref" not in context


def test_missing_profile_contract_returns_empty_context_without_exception():
    assert render_role_context(None, selected_role="unknown", canonical_role="unknown") == ""


def test_unknown_role_fails_soft():
    assert get_profile_contract("unknown_role") is None


def test_runtime_alias_chief_hermes_maps_to_chief_coordinator():
    contract = get_profile_contract("chief_hermes")
    assert contract is not None
    assert contract["canonical_id"] == "chief_coordinator"


def test_scribe_durable_memory_task_gets_scribe_context():
    result = build_role_context_for_task("Зафиксируй итог сегодняшней работы по ролям Hermes")
    assert result.selected_role == "scribe"
    assert result.profile_context_used is True
    assert "Scribe" in result.context_text


def test_career_vacancy_task_gets_career_strategist_context():
    result = build_role_context_for_task("Оцени вакансию Head of Product для меня")
    assert result.selected_role == "career_strategist"
    assert result.profile_context_used is True
    assert "Career Strategist" in result.context_text


def test_engineer_read_only_diagnostics_gets_engineer_context_without_approval_signal():
    result = build_role_context_for_task("Проверь статус WebUI и логи")
    assert result.selected_role == "engineer"
    assert result.profile_context_used is True
    assert result.operation_category == "read_only_investigation"
    assert "Engineer" in result.context_text
    assert "Production/runtime mutation requires explicit approval" in result.context_text


def test_debug_header_is_absent_by_default(monkeypatch):
    monkeypatch.delenv("HERMES_PROFILE_DEBUG_HEADER", raising=False)
    result = build_role_context_for_task("Зафиксируй итог сегодняшней работы по ролям Hermes")

    assert render_role_execution_debug_header(result) == ""


@pytest.mark.parametrize(
    "task, expected_role",
    [
        ("Зафиксируй итог сегодняшней работы по ролям Hermes", "scribe"),
        ("Запиши меня на стрижку", "general_operator"),
        ("Оцени вакансию Head of Product для меня", "career_strategist"),
    ],
)
def test_debug_header_is_present_when_enabled(monkeypatch, task: str, expected_role: str):
    monkeypatch.setenv("HERMES_PROFILE_DEBUG_HEADER", "1")
    result = build_role_context_for_task(task)

    header = render_role_execution_debug_header(result)

    assert header
    assert f"Hermes role: {expected_role}" in header
    assert "Role context: used" in header


def test_debug_header_includes_sensitive_review_and_approval_info(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_DEBUG_HEADER", "1")
    result = build_role_context_for_task(
        "Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения"
    )

    header = render_role_execution_debug_header(result)

    assert result.selected_role == "engineer"
    assert result.reviewer_profile == "security_auditor"
    assert result.requires_explicit_approval is True
    assert result.critical_approval_required is True
    assert result.approval_reason
    assert "Reviewer: security_auditor" in header
    assert "Approval: required" in header


def test_debug_header_soft_fails_without_role_metadata(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_DEBUG_HEADER", "1")

    assert inject_role_execution_debug_header("assistant response", None) == "assistant response"


def test_debug_header_compat_aliases_match_current_helpers(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_DEBUG_HEADER", "1")
    result = build_role_context_for_task("Запиши меня на стрижку")

    assert role_debug_header_enabled() is True
    assert render_role_debug_header(result) == render_role_execution_debug_header(result)


def test_debug_header_does_not_change_selection_or_approval(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_DEBUG_HEADER", "1")
    result = build_role_context_for_task(
        "Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения"
    )

    assert result.selected_role == "engineer"
    assert result.reviewer_profile == "security_auditor"
    assert result.requires_explicit_approval is True
    assert result.critical_approval_required is True
    assert result.profile_context_used is True


def test_external_commitment_does_not_set_critical_hard_stop_metadata():
    result = build_role_context_for_task("Запиши меня на стрижку")

    assert result.selected_role == "general_operator"
    assert result.requires_explicit_approval is True
    assert result.critical_approval_required is False
    assert "external commitment" in result.context_text.lower()


def test_scribe_prompt_with_do_not_touch_trading_stays_scribe():
    result = build_role_context_for_task("Зафиксируй финальный статус Hermes roles runtime MVP. Do not touch Trading.")

    assert result.selected_role == "scribe"
    assert result.reviewer_profile is None
    assert result.selected_role != "trading_observer_trader"


def test_scribe_final_status_with_cloudflare_evidence_does_not_hard_stop():
    result = build_role_context_for_task(
        "Зафиксируй финальный статус Hermes roles runtime MVP после live smoke. "
        "Live smoke: Cloudflare/public exposure prompt PASS. "
        "Update docs/profile-handoffs/2026-06-09-hermes-role-work.md and "
        "docs/state/current-operational-state.md. "
        "Do not change code. Do not deploy. Do not restart gateway. Do not touch Trading."
    )

    assert result.selected_role == "scribe"
    assert result.reviewer_profile is None
    assert result.critical_approval_required is False
    assert result.requires_explicit_approval is False


def test_investigation_prompt_with_do_not_activate_trading_stays_engineer():
    result = build_role_context_for_task("Investigate approval-gate regression. Do not activate Trading.")

    assert result.selected_role == "engineer"
    assert result.selected_role != "trading_observer_trader"


def test_crypto_research_prompt_stays_useful_without_trading_role():
    result = build_role_context_for_task("Compare Binance Kazakhstan vs Coinbase fees for BTC")

    assert result.selected_role in {"researcher", "general_operator"}
    assert result.selected_role != "trading_observer_trader"
    assert "trading remains deferred" not in result.context_text.lower()


def test_docs_only_cloudflare_smoke_update_stays_scribe_without_hard_stop():
    result = build_role_context_for_task(
        "Update docs/state/current-operational-state.md with Cloudflare smoke PASS. "
        "Do not deploy. Do not touch Cloudflare."
    )

    assert result.selected_role == "scribe"
    assert result.reviewer_profile is None
    assert result.critical_approval_required is False
    assert result.requires_explicit_approval is False


def test_cloudflare_investigation_without_change_does_not_hard_stop():
    result = build_role_context_for_task("Investigate approval-gate regression around Cloudflare. Do not change Cloudflare.")

    assert result.selected_role == "engineer"
    assert result.critical_approval_required is False


def test_user_level_fallback_refresh_timer_context_keeps_normal_operational_mutation_without_hard_stop():
    result = build_role_context_for_task(
        "Install a user-level systemd timer for "
        "/home/hermes/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main fallback refresh. "
        "Use systemctl --user. No gateway restart. No config/auth/provider mutation. "
        "No public exposure. No secrets. No Trading."
    )

    assert result.selected_role == "engineer"
    assert result.operation_category == "normal_operational_mutation"
    assert result.reviewer_profile is None
    assert result.requires_explicit_approval is False
    assert result.critical_approval_required is False


def test_user_level_fallback_refresh_timer_context_ignores_negative_sensitive_guardrails():
    result = build_role_context_for_task(
        "Set up a user-level systemd timer for Hermes fallback refresh daily at 04:30. "
        "Use /home/hermes/.config/systemd/user/hermes-fallback-refresh.service and "
        "/home/hermes/.config/systemd/user/hermes-fallback-refresh.timer. "
        "Command: /home/hermes/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main fallback refresh. "
        "WorkingDirectory: /home/hermes/.hermes/hermes-agent. "
        "State file: /home/hermes/.hermes/state/model_fallbacks.json. "
        "Do not restart gateway. "
        "Do not touch config/auth/provider/Cloudflare/Trading. "
        "Validate with systemctl --user and journalctl --user."
    )

    assert result.selected_role == "engineer"
    assert result.operation_category == "normal_operational_mutation"
    assert result.reviewer_profile is None
    assert result.requires_explicit_approval is False
    assert result.critical_approval_required is False


def test_render_explicit_approval_request_for_critical_mutation():
    result = build_role_context_for_task(
        "Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения"
    )

    rendered = render_explicit_approval_request(
        result,
        task_text="Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения",
    )

    assert "explicit approval" in rendered.lower()
    assert "cloudflare" in rendered.lower()
    assert "reply with explicit approve" in rendered.lower()
    assert "before any mutation" in rendered.lower() or "before making changes" in rendered.lower()


@pytest.mark.parametrize(
    "task",
    [
        "Make a BTC trade",
        "Buy BTC",
        "Activate trading",
    ],
)
def test_trade_prompts_do_not_render_trading_role_context(task: str):
    result = build_role_context_for_task(task)
    assert result.selected_role != "trading_observer_trader"
    assert result.canonical_role != "trading_observer_trader_deferred"
    assert result.profile_context_used is True


def test_profile_context_module_does_not_import_gateway_cron_or_trading():
    source = Path(__file__).resolve().parents[2] / "hermes_cli" / "profile_context.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    forbidden_prefixes = ("gateway", "cron", "trading")
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(name.split(".", 1)[0] in forbidden_prefixes for name in imported)
