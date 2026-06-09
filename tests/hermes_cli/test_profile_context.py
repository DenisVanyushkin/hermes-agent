from __future__ import annotations

import ast
from pathlib import Path

from hermes_cli.profile_context import (
    build_role_context_for_task,
    get_profile_contract,
    load_profile_contracts,
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
    assert "Engineer" in result.context_text
    assert "Production/runtime mutation requires explicit approval" in result.context_text


def test_trading_role_renders_deferred_and_inactive():
    result = build_role_context_for_task("Make a BTC trade")
    assert result.selected_role == "trading_observer_trader"
    assert result.canonical_role == "trading_observer_trader_deferred"
    assert result.profile_context_used is True
    assert "deferred" in result.context_text.lower()
    assert "inactive" in result.context_text.lower() or "do not activate trading" in result.context_text.lower()


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
