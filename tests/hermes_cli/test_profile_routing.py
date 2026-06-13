from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from hermes_cli.profile_routing import (
    RoutingError,
    decision_to_json,
    decision_to_dict,
    load_model_policy,
    load_profile_registry,
    resolve_profile_model,
    route_task,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO_ROOT / "config" / "hermes-profiles.yaml"
POLICY_PATH = REPO_ROOT / "config" / "hermes-model-policy.yaml"


def _hop_ids(decision) -> list[str]:
    return [hop.profile_id for hop in decision.route_chain]


def _route(task: str, *, registry_path: Path = REGISTRY_PATH, policy_path: Path = POLICY_PATH):
    return route_task(task, registry_path=registry_path, policy_path=policy_path)


def test_route_engineer_for_infra_runtime_change():
    decision = _route("WebUI deploy docker systemd smoke rollback logs db runtime monitoring")

    assert decision.primary_profile == "engineer"
    assert _hop_ids(decision) == ["engineer"]
    assert decision.selected_profiles == ["engineer"]
    assert decision.route_chain[0].model_tier == "coding"
    assert decision.route_chain[0].provider == "openrouter"
    assert decision.route_chain[0].model == "xiaomi/mimo-v2.5-pro"
    assert decision.route_chain[0].model_resolution_status == "fallback_available_by_policy"


@pytest.mark.parametrize(
    "task",
    [
        "Закоммить все незакоммиченные изменения и пушни в ориджин",
        "Закоммить изменения",
        "Запушь изменения в origin",
        "Запуши в ориджин",
        "Make git commit and git push",
        "Смерджи origin/local/customizations",
        "Проверь git status и закоммить",
    ],
)
def test_route_git_repo_prompts_to_engineer(task: str):
    decision = _route(task)

    assert decision.primary_profile == "engineer"
    assert _hop_ids(decision) == ["engineer"]
    assert decision.selected_profiles == ["engineer"]
def test_route_security_auditor_for_security_sensitive_terms():
    decision = _route("auth secrets exposure cloudflare firewall scheduler tool-boundary")

    assert decision.primary_profile == "security_auditor"
    assert _hop_ids(decision) == ["security_auditor"]
    assert decision.route_chain[0].model_tier == "critical"
    assert decision.route_chain[0].model_resolution_status == "no_fallback_stop_and_escalate"
    assert decision.route_chain[0].fallback_status == "stop_and_escalate"


def test_route_career_strategist_for_job_intel_terms():
    decision = _route("vacancy CV cover letter recruiter job-intel apply strategy")

    assert decision.primary_profile == "career_strategist"
    assert _hop_ids(decision) == ["career_strategist"]
    assert decision.route_chain[0].model_tier == "standard"


def test_route_scribe_for_docs_and_state_terms():
    decision = _route("docs runbook state decision open question profile handoff")

    assert decision.primary_profile == "scribe"
    assert _hop_ids(decision) == ["scribe"]
    assert decision.route_chain[0].model_tier == "standard"


@pytest.mark.parametrize(
    "task",
    [
        "Зафиксируй итог сегодняшней работы по ролям Hermes",
        "Запиши итог сегодняшней работы в документацию",
        "Write a handoff for today's Hermes role work",
    ],
)
def test_route_scribe_for_durable_memory_and_handoff_terms(task: str):
    decision = _route(task)

    assert decision.primary_profile == "scribe"
    assert _hop_ids(decision) == ["scribe"]


def test_route_scribe_for_final_status_with_trading_guardrail():
    decision = _route("Зафиксируй финальный статус Hermes roles runtime MVP. Do not touch Trading.")

    assert decision.primary_profile == "scribe"
    assert "trading_observer_trader" not in decision.selected_profiles
    assert "trading_observer_trader_deferred" not in decision.selected_profiles


@pytest.mark.parametrize(
    "task",
    [
        "Оцени вакансию Head of Product для меня",
        "Стоит ли откликаться на VP Product role?",
        "Подготовь CV под эту вакансию",
    ],
)
def test_route_career_strategist_for_vacancy_and_job_fit_terms(task: str):
    decision = _route(task)

    assert decision.primary_profile == "career_strategist"
    assert _hop_ids(decision) == ["career_strategist"]


def test_route_general_operator_for_personal_admin_tasks():
    decision = _route("Book me a haircut")

    assert decision.primary_profile == "general_operator"
    assert _hop_ids(decision) == ["general_operator"]
    assert decision.route_chain[0].model_tier == "standard"


def test_route_general_operator_for_safe_unclear_task():
    decision = _route("Help me with a simple safe admin task")

    assert decision.primary_profile == "general_operator"
    assert _hop_ids(decision) == ["general_operator"]
    assert decision.route_chain[0].model_tier == "standard"


def test_route_engineer_for_investigation_with_negative_trading_guardrail():
    decision = _route("Investigate approval-gate regression. Do not activate Trading.")

    assert decision.primary_profile == "engineer"
    assert "trading_observer_trader" not in decision.selected_profiles
    assert "trading_observer_trader_deferred" not in decision.selected_profiles


def test_route_thread_context_does_not_add_security_overlay_for_read_only_report_redesign():
    decision = _route(
        "[Replying to: hermes-rebase-local-customizations]\n"
        "[Thread context from Slack thread]\n"
        "[thread parent] Cronjob Response: hermes-rebase-local-customizations\n"
        "[thread reply] provider credentials changed, gateway deploy, auth json conflicts\n"
        "[End of thread context]\n\n"
        "отчет вызывает у меня двоякое ощущение. давай его полностью переделаем. сделай план и покажи мне"
    )

    assert decision.primary_profile == "engineer"
    assert decision.selected_profiles == ["engineer"]


def test_route_researcher_for_current_info_terms():
    decision = _route("weather news company research current facts digest report")

    assert decision.primary_profile == "researcher"
    assert _hop_ids(decision) == ["researcher"]
    assert decision.route_chain[0].model_tier == "standard"


@pytest.mark.parametrize(
    "task",
    [
        "Где я могу наиболее выгодно купить BTC?",
        "Compare Binance Kazakhstan vs Coinbase fees for BTC",
    ],
)
def test_route_crypto_research_prompts_without_trading_role(task: str):
    decision = _route(task)

    assert decision.primary_profile in {"researcher", "general_operator"}
    assert "trading_observer_trader" not in decision.selected_profiles
    assert "trading_observer_trader_deferred" not in decision.selected_profiles


@pytest.mark.parametrize(
    "task",
    [
        "Make a BTC trade",
        "Buy BTC",
        "Activate trading",
    ],
)
def test_route_trade_execution_prompts_do_not_select_trading_role(task: str):
    decision = _route(task)

    assert decision.primary_profile != "trading_observer_trader"
    assert decision.primary_profile != "trading_observer_trader_deferred"
    assert "trading_observer_trader" not in decision.selected_profiles
    assert "trading_observer_trader_deferred" not in decision.selected_profiles


def test_route_mixed_webui_exposure_change_adds_security_and_scribe():
    decision = _route("production WebUI exposure change")

    assert decision.primary_profile == "engineer"
    assert _hop_ids(decision) == ["engineer", "security_auditor", "scribe"]
    assert decision.confidence == "medium"
    assert decision.max_chain_limit_applied is False
    assert any("security" in reason.lower() for reason in decision.ambiguity_reasons) or decision.ambiguity_reasons


def test_route_mixed_job_review_adds_researcher():
    decision = _route("job opportunity review requiring company research and a CV update")

    assert decision.primary_profile == "career_strategist"
    assert _hop_ids(decision) == ["career_strategist", "researcher"]
    assert decision.route_chain[1].routing_reason


def test_route_mixed_operational_change_adds_scribe():
    decision = _route("operational change with durable state impact")

    assert decision.primary_profile == "engineer"
    assert _hop_ids(decision) == ["engineer", "scribe"]


def test_route_decision_serializes_to_json_schema():
    decision = _route("Deploy WebUI and document the result")
    payload = json.loads(decision_to_json(decision))

    assert payload["coordinator_profile"] == "chief_hermes"
    assert payload["primary_profile"] == "engineer"
    assert payload["selected_profiles"] == ["engineer", "scribe"]
    assert payload["validation_status"] == "passed"
    assert payload["confidence"] in {"high", "medium", "low"}
    assert payload["ambiguity_reasons"]
    assert isinstance(payload["route_chain"], list)
    assert payload["route_chain"][0]["profile_id"] == "engineer"
    assert payload["route_chain"][0]["model_resolution_status"]
    assert payload["route_chain"][0]["fallback_status"]


def test_route_task_fails_closed_on_malformed_registry(tmp_path: Path):
    registry_path = tmp_path / "bad-registry.yaml"
    policy_path = tmp_path / "policy.yaml"
    registry_path.write_text("profiles: [", encoding="utf-8")
    policy_path.write_text(POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(RoutingError):
        route_task("Deploy WebUI", registry_path=registry_path, policy_path=policy_path)


def test_route_task_fails_closed_on_policy_mismatch(tmp_path: Path):
    registry_data = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    policy_data = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    policy_data["profile_tiers"]["security_auditor"] = "standard"

    registry_path = tmp_path / "registry.yaml"
    policy_path = tmp_path / "policy.yaml"
    registry_path.write_text(yaml.safe_dump(registry_data, sort_keys=False), encoding="utf-8")
    policy_path.write_text(yaml.safe_dump(policy_data, sort_keys=False), encoding="utf-8")

    with pytest.raises(RoutingError):
        route_task("auth secrets exposure", registry_path=registry_path, policy_path=policy_path)


def test_profile_routing_import_boundary():
    code = textwrap.dedent(r'''
        import sys
        import hermes_cli.profile_routing  # noqa: F401
        forbidden = [name for name in sys.modules if name.startswith(('agent', 'gateway', 'cron.scheduler', 'run_agent'))]
        print('\\n'.join(sorted(forbidden)))
    ''')
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    forbidden = [line for line in result.stdout.splitlines() if line.strip()]
    assert forbidden == []


def test_loaders_and_model_resolution_round_trip():
    registry = load_profile_registry(REGISTRY_PATH)
    policy = load_model_policy(POLICY_PATH)

    assert any(profile["id"] == "engineer" for profile in registry["profiles"])
    assert any(profile["id"] == "general_operator" for profile in registry["profiles"])
    resolved = resolve_profile_model("security_auditor", policy)
    general_resolved = resolve_profile_model("general_operator", policy)
    assert resolved.model_tier == "critical"
    assert resolved.model_resolution_status == "no_fallback_stop_and_escalate"
    assert resolved.model == "gpt-5.5"
    assert resolved.provider == "openai-codex"
    assert general_resolved.model_tier == "standard"
    assert general_resolved.model == "gpt-5.4-mini"


def test_decision_to_dict_preserves_route_chain():
    decision = _route("weather news company research")
    payload = decision_to_dict(decision)

    assert payload["route_chain"][0]["profile_id"] == "researcher"
    assert payload["selected_profiles"] == ["researcher"]
    assert payload["coordinator_profile"] == "chief_hermes"


# ---------------------------------------------------------------------------
# Slice 2C: YAML-backed routing path tests
# ---------------------------------------------------------------------------

import hermes_cli.profile_routing as _routing_mod


@pytest.fixture(autouse=True)
def _clear_routing_cache():
    """Clear the active routing terms cache before and after each test for isolation."""
    _routing_mod._clear_routing_terms_cache()
    yield
    _routing_mod._clear_routing_terms_cache()


def test_yaml_routing_path_active_for_unique_trigger(tmp_path, monkeypatch):
    """route_task() routes via YAML when YAML is valid.

    A docs trigger present only in the injected YAML causes scribe routing.
    If constants were used instead, the result would be general_operator.
    """
    unique = "__hermes_yaml_2c_docs_trigger__"
    yaml_path = tmp_path / "triggers.yaml"
    content = (
        "schema_version: 1\n"
        "domains:\n"
        "  security:\n    triggers:\n      en: []\n      ru: []\n"
        "  infra:\n    triggers:\n      en: []\n      ru: []\n"
        "  career:\n    triggers:\n      en: []\n      ru: []\n"
        "  docs:\n    triggers:\n      en: ['" + unique + "']\n      ru: []\n"
        "  research:\n    triggers:\n      en: []\n      ru: []\n"
        "docs_first_markers:\n  en: []\n  ru: []\n"
    )
    yaml_path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(_routing_mod, "_DEFAULT_ROUTING_TRIGGERS_PATH", yaml_path)
    _routing_mod._clear_routing_terms_cache()

    decision = _route(unique)
    assert decision.primary_profile == "scribe", (
        "Expected 'scribe' via YAML-injected docs trigger, got "
        + repr(decision.primary_profile)
        + ". route_task() may still be reading Python constants."
    )


def test_routing_falls_back_to_constants_when_yaml_missing(monkeypatch):
    """When YAML is missing, route_task() uses Python constants without raising."""
    monkeypatch.setattr(
        _routing_mod, "_DEFAULT_ROUTING_TRIGGERS_PATH", Path("/nonexistent/routing-triggers.yaml")
    )
    _routing_mod._clear_routing_terms_cache()

    decision = _route("deploy docker to production host")
    assert decision.primary_profile == "engineer"


def test_routing_falls_back_to_constants_when_yaml_malformed(tmp_path, monkeypatch):
    """When YAML is malformed, route_task() uses Python constants without raising."""
    bad_yaml = tmp_path / "bad-triggers.yaml"
    bad_yaml.write_text("{ this is: [not valid yaml", encoding="utf-8")
    monkeypatch.setattr(_routing_mod, "_DEFAULT_ROUTING_TRIGGERS_PATH", bad_yaml)
    _routing_mod._clear_routing_terms_cache()

    decision = _route("оцени вакансию")
    assert decision.primary_profile == "career_strategist"


# ---------------------------------------------------------------------------
# Slice 2D: YAML routing policy tests
# ---------------------------------------------------------------------------

def test_yaml_policy_max_chain_length_caps_route_chain(tmp_path, monkeypatch):
    """route_task() honours max_chain_length from YAML policy.

    Inject a YAML with max_chain_length=1 and verify a naturally-3-hop task
    is truncated to 1 hop with max_chain_limit_applied=True.
    """
    import yaml as _yaml
    import hermes_cli.profile_routing as _mod

    real_path = REPO_ROOT / "config" / "hermes-routing-triggers.yaml"
    real_data = _yaml.safe_load(real_path.read_text(encoding="utf-8"))
    real_data["policy"]["overlays"]["max_chain_length"] = 1

    tmp_yaml = tmp_path / "triggers.yaml"
    tmp_yaml.write_text(_yaml.dump(real_data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(_mod, "_DEFAULT_ROUTING_TRIGGERS_PATH", tmp_yaml)
    _mod._clear_routing_terms_cache()
    _mod._clear_routing_policy_cache()

    # 3-hop task: engineer + security_auditor + scribe
    decision = route_task(
        "production WebUI exposure change",
        registry_path=REGISTRY_PATH,
        policy_path=POLICY_PATH,
    )
    chain = [h.profile_id for h in decision.route_chain]
    assert len(chain) == 1, f"Expected chain of 1 with cap; got {chain}"
    assert decision.max_chain_limit_applied is True


def test_yaml_policy_overlay_rule_disable_removes_overlay(tmp_path, monkeypatch):
    """Removing the engineer→security_auditor overlay rule from YAML suppresses that overlay.

    Without the rule, 'production WebUI exposure change' should NOT add security_auditor.
    """
    import yaml as _yaml
    import hermes_cli.profile_routing as _mod

    real_path = REPO_ROOT / "config" / "hermes-routing-triggers.yaml"
    real_data = _yaml.safe_load(real_path.read_text(encoding="utf-8"))

    # Strip the engineer→security_auditor overlay rule
    rules = real_data["policy"]["overlays"]["rules"]
    real_data["policy"]["overlays"]["rules"] = [
        r for r in rules
        if not (r.get("when_primary") == "engineer" and r.get("add_profile") == "security_auditor")
    ]

    tmp_yaml = tmp_path / "triggers.yaml"
    tmp_yaml.write_text(_yaml.dump(real_data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(_mod, "_DEFAULT_ROUTING_TRIGGERS_PATH", tmp_yaml)
    _mod._clear_routing_terms_cache()
    _mod._clear_routing_policy_cache()

    decision = route_task(
        "production WebUI exposure change",
        registry_path=REGISTRY_PATH,
        policy_path=POLICY_PATH,
    )
    assert "security_auditor" not in decision.selected_profiles, (
        "engineer→security_auditor overlay rule was removed from YAML; overlay must not appear"
    )


def test_yaml_policy_fallback_on_missing_policy_section(tmp_path, monkeypatch):
    """YAML without 'policy' section falls back to hardcoded policy; route_task() still works."""
    import yaml as _yaml
    import hermes_cli.profile_routing as _mod

    real_path = REPO_ROOT / "config" / "hermes-routing-triggers.yaml"
    real_data = _yaml.safe_load(real_path.read_text(encoding="utf-8"))
    del real_data["policy"]

    tmp_yaml = tmp_path / "triggers_no_policy.yaml"
    tmp_yaml.write_text(_yaml.dump(real_data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(_mod, "_DEFAULT_ROUTING_TRIGGERS_PATH", tmp_yaml)
    _mod._clear_routing_terms_cache()
    _mod._clear_routing_policy_cache()

    # Should still work via fallback
    decision = route_task(
        "production WebUI exposure change",
        registry_path=REGISTRY_PATH,
        policy_path=POLICY_PATH,
    )
    assert decision.primary_profile == "engineer"
    assert "security_auditor" in decision.selected_profiles


def test_clear_routing_policy_cache_exists():
    """_clear_routing_policy_cache() must be importable and callable (needed by test fixtures)."""
    from hermes_cli.profile_routing import _clear_routing_policy_cache
    _clear_routing_policy_cache()  # must not raise
