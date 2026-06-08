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
    assert decision.route_chain[0].model_tier == "reasoning"
    assert decision.route_chain[0].provider == "openrouter"
    assert decision.route_chain[0].model == "anthropic/claude-opus-4.6"
    assert decision.route_chain[0].model_resolution_status == "fallback_available_by_policy"


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


def test_route_researcher_for_current_info_terms():
    decision = _route("weather news company research current facts digest report")

    assert decision.primary_profile == "researcher"
    assert _hop_ids(decision) == ["researcher"]
    assert decision.route_chain[0].model_tier == "standard"


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
    assert resolved.model == "anthropic/claude-opus-4.6"
    assert resolved.provider == "openrouter"
    assert general_resolved.model_tier == "standard"
    assert general_resolved.model == "anthropic/claude-sonnet-4.6"


def test_decision_to_dict_preserves_route_chain():
    decision = _route("weather news company research")
    payload = decision_to_dict(decision)

    assert payload["route_chain"][0]["profile_id"] == "researcher"
    assert payload["selected_profiles"] == ["researcher"]
    assert payload["coordinator_profile"] == "chief_hermes"
