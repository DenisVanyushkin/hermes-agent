"""Validation for the Hermes profile architecture MVP.

This module validates the machine-readable profile registry and model policy
configs used by the profile architecture. It is intentionally fail-closed:
malformed YAML, missing required fields, unknown tiers, or risky policy
mismatches are all reported as errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE_REGISTRY_PATH = REPO_ROOT / "config" / "hermes-profiles.yaml"
DEFAULT_MODEL_POLICY_PATH = REPO_ROOT / "config" / "hermes-model-policy.yaml"

ACTIVE_PROFILE_IDS = {
    "chief_hermes",
    "engineer",
    "career_strategist",
    "scribe",
    "researcher",
    "security_auditor",
    "general_operator",
}
DEFERRED_PROFILE_IDS = {"trading_observer_trader"}

CANONICAL_ACTIVE_PROFILE_IDS = {
    "chief_coordinator",
    "engineer",
    "career_strategist",
    "scribe",
    "researcher",
    "security_auditor",
    "general_operator",
}
CANONICAL_DEFERRED_PROFILE_IDS = {"trading_observer_trader_deferred"}
CANONICAL_PROFILE_IDS = CANONICAL_ACTIVE_PROFILE_IDS | CANONICAL_DEFERRED_PROFILE_IDS

PROFILE_ID_ALIASES = {
    "chief_hermes": "chief_coordinator",
    "trading_observer_trader": "trading_observer_trader_deferred",
}

MODEL_TIERS = {"standard", "reasoning", "critical"}
DEFAULT_BASE_MODEL = "gpt-5.4-mini"
CANONICAL_TOOL_CATEGORIES = {
    "repo_read",
    "repo_write",
    "git_status_diff",
    "test_runner",
    "shell_local",
    "docker_diagnostics",
    "production_deploy",
    "service_restart",
    "cloudflare_dns_proxy",
    "secrets_read",
    "secrets_write",
    "scheduler_modify",
    "db_migration",
    "web_search",
    "browser",
    "calendar",
    "contacts",
    "email_draft",
    "email_send",
    "slack_send",
    "docs_read",
    "docs_write",
    "job_intel_read",
    "trading_market_read",
    "trading_execute",
}

REQUIRED_PROFILE_FIELDS = {
    "id",
    "default_model",
    "allowed_tools",
    "denied_tools",
    "requires_approval_for",
    "may_read_paths",
    "may_write_paths",
    "scribe_hook",
    "scribe_hook_condition",
    "security_review_hook",
    "security_review_hook_condition",
    "output_artifacts",
}
REQUIRED_DOCUMENTATION_PATHS = {
    "docs/hermes-profile-architecture.md",
    "docs/hermes-operator-runbook.md",
    "docs/hermes-webui-security-audit.md",
    "docs/job-intel-runtime.md",
    "docs/job-intel-architecture.md",
    "docs/state/",
    "docs/decisions/",
    "docs/profile-handoffs/",
}
FUTURE_ONLY_PATHS = {
    "docs/job-intel/",
    "docs/profiles/",
}
REQUIRED_ENGINEER_APPROVALS = {
    "any_production_host_mutation",
    "service_start_stop_restart_reload_on_production",
    "production_config_change",
    "production_db_migration_or_repair",
    "production_build_or_deploy",
    "production_rollback",
    "firewall_cloudflare_reverse_proxy_change",
    "changing_scheduler_or_timer_behavior",
    "changing_tool_permissions",
    "changing_auth_or_secret_handling",
}
REQUIRED_PROFILE_CONTRACT_FIELDS = {
    "canonical_id",
    "display_name",
    "role_family",
    "purpose_summary",
    "spec_ref",
    "personality_summary",
    "tool_contract",
    "action_contract",
    "escalation_targets",
    "memory_policy_summary",
    "review_policy_summary",
}
REQUIRED_TOOL_CONTRACT_FIELDS = {
    "allowed_by_default",
    "allowed_with_confirmation",
    "forbidden",
}
REQUIRED_ACTION_CONTRACT_FIELDS = {
    "allowed_by_default",
    "allowed_with_confirmation",
    "forbidden",
}
REQUIRED_MODEL_GOVERNANCE_FIELDS = {
    "default_base_model",
    "base_model_owner",
    "escalation_model_owner",
    "free_fallback_owner",
    "runtime_selection_order",
    "critical_action_free_fallback_not_final_authority",
    "observability_fields",
}
REQUIRED_FALLBACK_REFRESH_FIELDS = {
    "source",
    "cadence",
    "cache_path",
    "update_source_config",
    "do_not_dirty_git_worktree",
    "on_failure",
}
REQUIRED_FALLBACK_CANDIDATE_ATTRIBUTES = {
    "healthStatus",
    "latencyMs",
    "contextLength",
    "maxCompletionTokens",
    "supportsTools",
    "supportsToolChoice",
    "supportsStructuredOutputs",
    "supportsResponseFormat",
    "supportsReasoning",
    "supportsIncludeReasoning",
    "liteEvalScore",
    "evalSummary",
    "instabilityPenalty",
    "rankingConfidence",
}
REQUIRED_OBSERVABILITY_FIELDS = {
    "role_id",
    "selected_model",
    "base_model",
    "escalation_model",
    "fallback_model",
    "model_tier",
    "fallback_used",
    "fallback_source",
    "fallback_selection_reason",
    "escalation_used",
    "escalation_reason",
    "critical_action_blocked_due_to_model_unavailable",
    "model_selection_timestamp",
}
REQUIRED_CRITICAL_ACTION_GUARDS = {
    "production_runtime_mutation_approval",
    "security_pass_on_high_risk_changes",
    "secrets_auth_tool_permission_changes",
    "database_migration_or_repair",
    "production_data_deletion",
    "trading_execution",
    "financial_legal_medical_material_decision",
}


@dataclass(frozen=True)
class ValidationIssue:
    """A single validation finding."""

    severity: str
    message: str
    path: str = ""


def _issue(severity: str, message: str, path: str = "") -> ValidationIssue:
    return ValidationIssue(severity=severity, message=message, path=path)


def _normalize_list(value: Any) -> list[Any] | None:
    if value is None or not isinstance(value, list):
        return None
    return value


def _validate_nonempty_string_list(value: Any, field_name: str, owner: str, *, path: str = "") -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    values = _normalize_list(value)
    if values is None:
        issues.append(_issue("error", f"{owner} field {field_name} must be a list of strings", path))
        return issues
    if not values:
        issues.append(_issue("error", f"{owner} field {field_name} must not be empty", path))
        return issues
    for item in values:
        if not isinstance(item, str) or not item.strip():
            issues.append(_issue("error", f"{owner} field {field_name} contains a non-string or empty entry: {item!r}", path))
    return issues


def _validate_string(value: Any, field_name: str, owner: str, *, path: str = "") -> list[ValidationIssue]:
    if not isinstance(value, str) or not value.strip():
        return [_issue("error", f"{owner} field {field_name} must be a non-empty string", path)]
    return []


def _load_yaml_document(path: Path) -> tuple[Any | None, list[ValidationIssue]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [_issue("error", f"Failed to read YAML file {path}: {exc}")]
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return None, [_issue("error", f"Failed to parse YAML file {path}: {exc}")]
    if data is None:
        return None, [_issue("error", f"YAML file {path} is empty")]
    return data, []


def _validate_profile_contract(profile: dict[str, Any], *, path: str) -> tuple[list[ValidationIssue], str | None]:
    issues: list[ValidationIssue] = []
    contract = profile.get("profile_contract")
    if not isinstance(contract, dict):
        issues.append(_issue("error", "profile_contract must be a mapping", path))
        return issues, None

    missing = REQUIRED_PROFILE_CONTRACT_FIELDS - contract.keys()
    if missing:
        issues.append(_issue("error", "profile_contract is missing required fields: " + ", ".join(sorted(missing)), path))

    expected_canonical = PROFILE_ID_ALIASES.get(str(profile.get("id")), str(profile.get("id")))
    canonical_id = contract.get("canonical_id")
    if canonical_id != expected_canonical:
        issues.append(_issue("error", f"profile_contract.canonical_id must be {expected_canonical!r}", path))
    elif canonical_id not in CANONICAL_PROFILE_IDS:
        issues.append(_issue("error", f"profile_contract.canonical_id {canonical_id!r} is not a known canonical role", path))

    for field in ("display_name", "role_family", "purpose_summary", "spec_ref"):
        issues.extend(_validate_string(contract.get(field), f"profile_contract.{field}", f"profile {profile.get('id', path)}", path=path))

    issues.extend(_validate_nonempty_string_list(contract.get("personality_summary"), "profile_contract.personality_summary", f"profile {profile.get('id', path)}", path=path))
    issues.extend(_validate_nonempty_string_list(contract.get("escalation_targets"), "profile_contract.escalation_targets", f"profile {profile.get('id', path)}", path=path))
    issues.extend(_validate_nonempty_string_list(contract.get("memory_policy_summary"), "profile_contract.memory_policy_summary", f"profile {profile.get('id', path)}", path=path))
    issues.extend(_validate_nonempty_string_list(contract.get("review_policy_summary"), "profile_contract.review_policy_summary", f"profile {profile.get('id', path)}", path=path))

    tool_contract = contract.get("tool_contract")
    if not isinstance(tool_contract, dict):
        issues.append(_issue("error", "profile_contract.tool_contract must be a mapping", path))
    else:
        missing_tool_fields = REQUIRED_TOOL_CONTRACT_FIELDS - tool_contract.keys()
        if missing_tool_fields:
            issues.append(_issue("error", "profile_contract.tool_contract is missing required fields: " + ", ".join(sorted(missing_tool_fields)), path))
        for field in REQUIRED_TOOL_CONTRACT_FIELDS:
            values = tool_contract.get(field)
            if not isinstance(values, list) or not values:
                issues.append(_issue("error", f"profile_contract.tool_contract.{field} must be a non-empty list", path))
                continue
            for item in values:
                if not isinstance(item, str) or not item.strip():
                    issues.append(_issue("error", f"profile_contract.tool_contract.{field} contains a non-string or empty entry: {item!r}", path))
                elif item not in CANONICAL_TOOL_CATEGORIES:
                    issues.append(_issue("error", f"profile_contract.tool_contract.{field} contains unknown canonical tool category {item!r}", path))

    action_contract = contract.get("action_contract")
    if not isinstance(action_contract, dict):
        issues.append(_issue("error", "profile_contract.action_contract must be a mapping", path))
    else:
        missing_action_fields = REQUIRED_ACTION_CONTRACT_FIELDS - action_contract.keys()
        if missing_action_fields:
            issues.append(_issue("error", "profile_contract.action_contract is missing required fields: " + ", ".join(sorted(missing_action_fields)), path))
        for field in REQUIRED_ACTION_CONTRACT_FIELDS:
            issues.extend(_validate_nonempty_string_list(action_contract.get(field), f"profile_contract.action_contract.{field}", f"profile {profile.get('id', path)}", path=path))

    return issues, str(canonical_id) if isinstance(canonical_id, str) else None


def validate_profile_registry(data: Any) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(data, dict):
        return [_issue("error", "profile registry must be a mapping")]

    schema_version = data.get("schema_version")
    if not isinstance(schema_version, int) or schema_version < 1:
        issues.append(_issue("error", "profile registry schema_version must be a positive integer"))

    documentation_policy = data.get("documentation_policy")
    if not isinstance(documentation_policy, dict):
        issues.append(_issue("error", "profile registry documentation_policy must be a mapping"))
    else:
        runtime_required = _normalize_list(documentation_policy.get("runtime_required_paths"))
        future_only = _normalize_list(documentation_policy.get("future_only_paths"))
        if runtime_required is None:
            issues.append(_issue("error", "documentation_policy.runtime_required_paths must be a list"))
        if future_only is None:
            issues.append(_issue("error", "documentation_policy.future_only_paths must be a list"))
        if runtime_required is not None:
            for required_path in REQUIRED_DOCUMENTATION_PATHS:
                if required_path not in runtime_required:
                    issues.append(_issue("error", f"documentation_policy.runtime_required_paths is missing required path {required_path}"))
            for forbidden_path in FUTURE_ONLY_PATHS:
                if forbidden_path in runtime_required:
                    issues.append(_issue("error", f"documentation_policy.runtime_required_paths must not require future-only path {forbidden_path}"))
        if future_only is not None:
            for future_path in FUTURE_ONLY_PATHS:
                if future_path not in future_only:
                    issues.append(_issue("error", f"documentation_policy.future_only_paths is missing {future_path}"))

    profiles = data.get("profiles")
    if not isinstance(profiles, list):
        issues.append(_issue("error", "profile registry profiles must be a list"))
        profiles = []

    seen_runtime_ids: set[str] = set()
    seen_canonical_ids: set[str] = set()
    for index, profile in enumerate(profiles):
        path = f"profiles[{index}]"
        if not isinstance(profile, dict):
            issues.append(_issue("error", f"{path} must be a mapping", path))
            continue

        missing = REQUIRED_PROFILE_FIELDS - profile.keys()
        if missing:
            issues.append(_issue("error", f"profile {profile.get('id', path)} is missing required fields: {', '.join(sorted(missing))}", path))

        profile_id = profile.get("id")
        if not isinstance(profile_id, str) or not profile_id.strip():
            issues.append(_issue("error", f"{path} id must be a non-empty string", path))
            continue
        if profile_id in seen_runtime_ids:
            issues.append(_issue("error", f"duplicate profile id {profile_id}", path))
        seen_runtime_ids.add(profile_id)
        if profile_id not in ACTIVE_PROFILE_IDS:
            issues.append(_issue("error", f"profile {profile_id} is not an active profile", path))

        default_model = profile.get("default_model")
        if default_model not in MODEL_TIERS:
            issues.append(_issue("error", f"profile {profile_id} default_model {default_model!r} is not a known model tier", path))

        for field in ("allowed_tools", "denied_tools", "requires_approval_for", "may_read_paths", "may_write_paths", "output_artifacts"):
            issues.extend(_validate_nonempty_string_list(profile.get(field), field, f"profile {profile_id}", path=path))

        if profile_id == "engineer":
            approvals = set(_normalize_list(profile.get("requires_approval_for")) or [])
            missing_approvals = REQUIRED_ENGINEER_APPROVALS - approvals
            if missing_approvals:
                issues.append(_issue("error", "engineer profile is missing required production mutation approval requirements: " + ", ".join(sorted(missing_approvals)), path))
        if profile_id == "scribe":
            artifacts = set(_normalize_list(profile.get("output_artifacts")) or [])
            if "handoff" in artifacts or "handoff_report" in artifacts:
                issues.append(_issue("error", "scribe output_artifacts must distinguish handoff_complete from handoff_incomplete; generic handoff artifacts are not allowed", path))
            if not {"handoff_complete", "handoff_incomplete"}.issubset(artifacts):
                issues.append(_issue("error", "scribe output_artifacts must include both handoff_complete and handoff_incomplete", path))
        if profile_id == "security_auditor" and default_model != "critical":
            issues.append(_issue("error", "security_auditor must use default_model critical", path))

        contract_issues, canonical_id = _validate_profile_contract(profile, path=path)
        issues.extend(contract_issues)
        if canonical_id:
            if canonical_id in seen_canonical_ids:
                issues.append(_issue("error", f"duplicate canonical profile id {canonical_id}", path))
            seen_canonical_ids.add(canonical_id)

    missing_active = ACTIVE_PROFILE_IDS - seen_runtime_ids
    extra_active = seen_runtime_ids - ACTIVE_PROFILE_IDS
    if missing_active:
        issues.append(_issue("error", "profile registry is missing active profiles: " + ", ".join(sorted(missing_active))))
    if extra_active:
        issues.append(_issue("error", "profile registry contains unexpected profiles: " + ", ".join(sorted(extra_active))))

    if missing_canonical := (CANONICAL_ACTIVE_PROFILE_IDS - seen_canonical_ids):
        issues.append(_issue("error", "profile registry is missing canonical active profiles: " + ", ".join(sorted(missing_canonical))))
    extra_canonical = seen_canonical_ids - CANONICAL_PROFILE_IDS
    if extra_canonical:
        issues.append(_issue("error", "profile registry contains unexpected canonical profiles: " + ", ".join(sorted(extra_canonical))))

    deferred_profiles = data.get("deferred_profiles", [])
    if not isinstance(deferred_profiles, list):
        issues.append(_issue("error", "deferred_profiles must be a list"))
    else:
        for index, profile in enumerate(deferred_profiles):
            path = f"deferred_profiles[{index}]"
            if not isinstance(profile, dict):
                issues.append(_issue("error", f"{path} must be a mapping", path))
                continue
            if profile.get("id") not in DEFERRED_PROFILE_IDS:
                issues.append(_issue("error", f"{path} references unknown deferred profile {profile.get('id')!r}", path))
            if profile.get("status") != "deferred":
                issues.append(_issue("error", f"{path} must have status deferred", path))
            contract_issues, canonical_id = _validate_profile_contract(profile, path=path)
            issues.extend(contract_issues)
            if canonical_id:
                seen_canonical_ids.add(canonical_id)
                if canonical_id not in CANONICAL_DEFERRED_PROFILE_IDS:
                    issues.append(_issue("error", f"{path} canonical role must be deferred", path))

    if missing_deferred := (CANONICAL_DEFERRED_PROFILE_IDS - seen_canonical_ids):
        issues.append(_issue("error", "profile registry is missing canonical deferred profiles: " + ", ".join(sorted(missing_deferred))))
    extra_canonical = seen_canonical_ids - CANONICAL_PROFILE_IDS
    if extra_canonical:
        issues.append(_issue("error", "profile registry contains unexpected canonical profiles: " + ", ".join(sorted(extra_canonical))))

    return issues


def _validate_model_governance(data: Any) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(data, dict):
        return [_issue("error", "model_governance must be a mapping")]

    missing = REQUIRED_MODEL_GOVERNANCE_FIELDS - data.keys()
    if missing:
        issues.append(_issue("error", "model_governance is missing required fields: " + ", ".join(sorted(missing))))

    if data.get("default_base_model") != DEFAULT_BASE_MODEL:
        issues.append(_issue("error", f"model_governance.default_base_model must be {DEFAULT_BASE_MODEL!r}"))
    for field in ("base_model_owner", "escalation_model_owner", "free_fallback_owner"):
        issues.extend(_validate_string(data.get(field), f"model_governance.{field}", "model_governance"))

    order = data.get("runtime_selection_order")
    if not isinstance(order, list) or not order:
        issues.append(_issue("error", "model_governance.runtime_selection_order must be a non-empty list"))
    else:
        expected_order = [
            "base_model",
            "escalation_model_if_policy_conditions_match",
            "free_fallback_if_allowed_and_needed",
            "stop_and_escalate_if_no_safe_model",
        ]
        if order != expected_order:
            issues.append(_issue("error", "model_governance.runtime_selection_order must match the declared execution order"))

    guards = data.get("critical_action_free_fallback_not_final_authority")
    issues.extend(_validate_nonempty_string_list(guards, "critical_action_free_fallback_not_final_authority", "model_governance"))
    if isinstance(guards, list):
        missing_guards = REQUIRED_CRITICAL_ACTION_GUARDS - set(guards)
        if missing_guards:
            issues.append(_issue("error", "model_governance.critical_action_free_fallback_not_final_authority is missing required guards: " + ", ".join(sorted(missing_guards))))

    observability = data.get("observability_fields")
    issues.extend(_validate_nonempty_string_list(observability, "observability_fields", "model_governance"))
    if isinstance(observability, list):
        missing_observability = REQUIRED_OBSERVABILITY_FIELDS - set(observability)
        if missing_observability:
            issues.append(_issue("error", "model_governance.observability_fields is missing required fields: " + ", ".join(sorted(missing_observability))))

    return issues


def _validate_fallback_selection_policy(data: Any) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(data, dict):
        return [_issue("error", "fallback_selection_policy must be a mapping")]

    missing = {"mode", "refresh", "provider_level_fallback", "candidate_attributes", "global_filters"} - data.keys()
    if missing:
        issues.append(_issue("error", "fallback_selection_policy is missing required fields: " + ", ".join(sorted(missing))))

    if data.get("mode") != "capability_based":
        issues.append(_issue("error", "fallback_selection_policy.mode must be capability_based"))

    refresh = data.get("refresh")
    if not isinstance(refresh, dict):
        issues.append(_issue("error", "fallback_selection_policy.refresh must be a mapping"))
    else:
        missing_refresh = REQUIRED_FALLBACK_REFRESH_FIELDS - refresh.keys()
        if missing_refresh:
            issues.append(_issue("error", "fallback_selection_policy.refresh is missing required fields: " + ", ".join(sorted(missing_refresh))))
        for field in ("source", "cadence", "cache_path", "provider_level_fallback"):
            pass
        if refresh.get("update_source_config") is not False:
            issues.append(_issue("error", "fallback_selection_policy.refresh.update_source_config must be false"))
        if refresh.get("do_not_dirty_git_worktree") is not True:
            issues.append(_issue("error", "fallback_selection_policy.refresh.do_not_dirty_git_worktree must be true"))
        issues.extend(_validate_nonempty_string_list(refresh.get("on_failure"), "fallback_selection_policy.refresh.on_failure", "fallback_selection_policy"))
        if isinstance(refresh.get("on_failure"), list) and refresh["on_failure"] != [
            "use_last_known_good_if_available",
            "use_provider_level_fallback_if_available",
            "mark_fallback_unavailable",
        ]:
            issues.append(_issue("error", "fallback_selection_policy.refresh.on_failure must preserve the documented fallback order"))
        for field in ("source", "cadence", "cache_path"):
            issues.extend(_validate_string(refresh.get(field), f"fallback_selection_policy.refresh.{field}", "fallback_selection_policy"))

    issues.extend(_validate_string(data.get("provider_level_fallback"), "provider_level_fallback", "fallback_selection_policy"))

    candidate_attributes = data.get("candidate_attributes")
    issues.extend(_validate_nonempty_string_list(candidate_attributes, "candidate_attributes", "fallback_selection_policy"))
    if isinstance(candidate_attributes, list):
        missing_attrs = REQUIRED_FALLBACK_CANDIDATE_ATTRIBUTES - set(candidate_attributes)
        if missing_attrs:
            issues.append(_issue("error", "fallback_selection_policy.candidate_attributes is missing required candidate fields: " + ", ".join(sorted(missing_attrs))))

    global_filters = data.get("global_filters")
    if not isinstance(global_filters, dict):
        issues.append(_issue("error", "fallback_selection_policy.global_filters must be a mapping"))
    else:
        hard = global_filters.get("hard")
        prefer = global_filters.get("prefer")
        if not isinstance(hard, dict):
            issues.append(_issue("error", "fallback_selection_policy.global_filters.hard must be a mapping"))
        else:
            reject_health_status = hard.get("reject_health_status")
            issues.extend(_validate_nonempty_string_list(reject_health_status, "global_filters.hard.reject_health_status", "fallback_selection_policy"))
            if isinstance(reject_health_status, list) and "timeout_or_error" not in reject_health_status:
                issues.append(_issue("error", "fallback_selection_policy.global_filters.hard.reject_health_status must reject timeout_or_error"))
            if hard.get("min_context_length") != 32768:
                issues.append(_issue("error", "fallback_selection_policy.global_filters.hard.min_context_length must be 32768"))
            if hard.get("min_max_completion_tokens") != 4096:
                issues.append(_issue("error", "fallback_selection_policy.global_filters.hard.min_max_completion_tokens must be 4096"))
        if not isinstance(prefer, dict):
            issues.append(_issue("error", "fallback_selection_policy.global_filters.prefer must be a mapping"))
        else:
            if prefer.get("healthStatus") != "passed":
                issues.append(_issue("error", "fallback_selection_policy.global_filters.prefer.healthStatus must be passed"))
            if prefer.get("supportsStructuredOutputs") is not True:
                issues.append(_issue("error", "fallback_selection_policy.global_filters.prefer.supportsStructuredOutputs must be true"))
            if prefer.get("supportsResponseFormat") is not True:
                issues.append(_issue("error", "fallback_selection_policy.global_filters.prefer.supportsResponseFormat must be true"))

    return issues


def _validate_role_policies(data: Any) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(data, dict):
        return [_issue("error", "role_policies must be a mapping")]

    missing_roles = CANONICAL_PROFILE_IDS - data.keys()
    if missing_roles:
        issues.append(_issue("error", "role_policies is missing required roles: " + ", ".join(sorted(missing_roles))))
    extra_roles = set(data) - CANONICAL_PROFILE_IDS
    if extra_roles:
        issues.append(_issue("error", "role_policies contains unexpected roles: " + ", ".join(sorted(extra_roles))))

    for role_id, role_policy in data.items():
        path = f"role_policies[{role_id}]"
        if not isinstance(role_policy, dict):
            issues.append(_issue("error", f"{path} must be a mapping", path))
            continue
        if role_id == "trading_observer_trader_deferred":
            if role_policy.get("status") != "deferred":
                issues.append(_issue("error", f"{path}.status must be deferred", path))
            if role_policy.get("base_model") != "deferred":
                issues.append(_issue("error", f"{path}.base_model must be deferred", path))
        elif role_policy.get("base_model") != DEFAULT_BASE_MODEL:
            issues.append(_issue("error", f"{path}.base_model must be {DEFAULT_BASE_MODEL!r}", path))

        escalation = role_policy.get("escalation")
        if not isinstance(escalation, dict):
            issues.append(_issue("error", f"{path}.escalation must be a mapping", path))
        else:
            if not isinstance(escalation.get("model_family"), str) or not str(escalation.get("model_family")).strip():
                issues.append(_issue("error", f"{path}.escalation.model_family must be a non-empty string", path))
            if "conditions" in escalation:
                issues.extend(_validate_nonempty_string_list(escalation.get("conditions"), f"{path}.escalation.conditions", role_id, path=path) if escalation.get("conditions") else [])
            if role_id == "engineer":
                if escalation.get("model_family") != "specialized_coding":
                    issues.append(_issue("error", f"{path}.escalation.model_family must be specialized_coding", path))
                if escalation.get("example_model") != "gpt-5.3-codex":
                    issues.append(_issue("error", f"{path}.escalation.example_model must be gpt-5.3-codex", path))
            elif role_id in {"chief_coordinator", "security_auditor", "researcher", "career_strategist"}:
                if escalation.get("model_family") != "strong_reasoning":
                    issues.append(_issue("error", f"{path}.escalation.model_family must be strong_reasoning", path))
            elif role_id in {"scribe", "general_operator"}:
                if escalation.get("model_family") != "none":
                    issues.append(_issue("error", f"{path}.escalation.model_family must be none", path))
            elif role_id == "trading_observer_trader_deferred":
                if escalation.get("model_family") != "deferred":
                    issues.append(_issue("error", f"{path}.escalation.model_family must be deferred", path))

        free_fallback = role_policy.get("free_fallback")
        if not isinstance(free_fallback, dict):
            issues.append(_issue("error", f"{path}.free_fallback must be a mapping", path))
        else:
            issues.extend(_validate_nonempty_string_list(free_fallback.get("allowed_for"), f"{path}.free_fallback.allowed_for", role_id, path=path))
            issues.extend(_validate_nonempty_string_list(free_fallback.get("not_final_authority_for"), f"{path}.free_fallback.not_final_authority_for", role_id, path=path))
            role_filters = free_fallback.get("role_filters")
            if not isinstance(role_filters, dict):
                issues.append(_issue("error", f"{path}.free_fallback.role_filters must be a mapping", path))
            else:
                require = role_filters.get("require")
                prefer = role_filters.get("prefer")
                if not isinstance(require, dict):
                    issues.append(_issue("error", f"{path}.free_fallback.role_filters.require must be a mapping", path))
                if not isinstance(prefer, dict):
                    issues.append(_issue("error", f"{path}.free_fallback.role_filters.prefer must be a mapping", path))
            if role_id == "general_operator":
                if role_filters and isinstance(role_filters, dict) and role_filters.get("prefer", {}).get("latencyMs") != "low":
                    issues.append(_issue("error", f"{path} should prefer low latency fallback candidates", path))
            if role_id == "trading_observer_trader_deferred":
                if "trading_execution" not in (free_fallback.get("not_final_authority_for") or []):
                    issues.append(_issue("error", f"{path}.free_fallback.not_final_authority_for must include trading_execution", path))
            if role_id in {"chief_coordinator", "engineer", "security_auditor", "researcher", "career_strategist", "general_operator"}:
                if not free_fallback.get("allowed_for"):
                    issues.append(_issue("error", f"{path}.free_fallback.allowed_for must not be empty", path))

    return issues


def validate_model_policy(data: Any, active_profile_ids: set[str] | None = None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if active_profile_ids is None:
        active_profile_ids = ACTIVE_PROFILE_IDS

    if not isinstance(data, dict):
        return [_issue("error", "model policy must be a mapping")]

    schema_version = data.get("schema_version")
    if not isinstance(schema_version, int) or schema_version < 1:
        issues.append(_issue("error", "model policy schema_version must be a positive integer"))

    tiers = data.get("tiers")
    if not isinstance(tiers, dict):
        issues.append(_issue("error", "model policy tiers must be a mapping"))
        tiers = {}

    for tier_name in MODEL_TIERS:
        tier = tiers.get(tier_name)
        if not isinstance(tier, dict):
            issues.append(_issue("error", f"model policy tier {tier_name} must be a mapping"))
            continue
        required_fields = ["provider", "model"]
        if tier_name != "critical":
            required_fields.append("fallback_behavior")
        for field in required_fields:
            if field not in tier or not isinstance(tier[field], str) or not tier[field].strip():
                issues.append(_issue("error", f"model policy tier {tier_name} missing required field {field}"))
        if tier_name == "critical":
            if tier.get("allow_fallback") is not False:
                issues.append(_issue("error", "critical tier must set allow_fallback to false"))
            if tier.get("unavailable_behavior") != "stop_and_escalate":
                issues.append(_issue("error", "critical tier must set unavailable_behavior to stop_and_escalate"))
            fallback_models = tier.get("fallback_models")
            if not isinstance(fallback_models, list):
                issues.append(_issue("error", "critical tier fallback_models must be a list"))
            elif fallback_models:
                issues.append(_issue("error", "critical tier fallback_models must be empty"))
            fallback_behavior = tier.get("fallback_behavior")
            if fallback_behavior is not None and fallback_behavior != "stop_and_escalate":
                issues.append(_issue("error", "critical tier fallback_behavior is legacy/derived and must match stop_and_escalate if present"))

    profile_tiers = data.get("profile_tiers")
    if not isinstance(profile_tiers, dict):
        issues.append(_issue("error", "model policy profile_tiers must be a mapping"))
        profile_tiers = {}

    unknown_profiles = set(profile_tiers) - active_profile_ids
    if unknown_profiles:
        issues.append(_issue("error", "model policy references unknown active profiles: " + ", ".join(sorted(unknown_profiles))))

    missing_profiles = active_profile_ids - set(profile_tiers)
    if missing_profiles:
        issues.append(_issue("error", "model policy is missing tier mapping for active profiles: " + ", ".join(sorted(missing_profiles))))

    for profile_id, tier_name in profile_tiers.items():
        if not isinstance(tier_name, str) or tier_name not in MODEL_TIERS:
            issues.append(_issue("error", f"model policy profile_tiers[{profile_id!r}] references unknown model tier {tier_name!r}"))
            continue
        if tier_name == "critical":
            tier = tiers.get(tier_name) or {}
            if tier.get("allow_fallback") is not False:
                issues.append(_issue("error", f"profile {profile_id} uses critical tier, but critical.allow_fallback is not false"))
            if tier.get("unavailable_behavior") != "stop_and_escalate":
                issues.append(_issue("error", f"profile {profile_id} uses critical tier, but critical.unavailable_behavior is not stop_and_escalate"))
            fallback_models = tier.get("fallback_models")
            if not isinstance(fallback_models, list):
                issues.append(_issue("error", f"profile {profile_id} uses critical tier, but critical.fallback_models is not a list"))
            elif fallback_models:
                issues.append(_issue("error", f"profile {profile_id} uses critical tier, but critical.fallback_models is not empty"))

    issues.extend(_validate_model_governance(data.get("model_governance")))
    issues.extend(_validate_fallback_selection_policy(data.get("fallback_selection_policy")))
    issues.extend(_validate_role_policies(data.get("role_policies")))

    return issues


def validate_profile_architecture(
    registry_path: Path = DEFAULT_PROFILE_REGISTRY_PATH,
    policy_path: Path = DEFAULT_MODEL_POLICY_PATH,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    registry_data, registry_load_issues = _load_yaml_document(Path(registry_path))
    issues.extend(registry_load_issues)
    policy_data, policy_load_issues = _load_yaml_document(Path(policy_path))
    issues.extend(policy_load_issues)

    if registry_load_issues or policy_load_issues:
        return issues

    registry_issues = validate_profile_registry(registry_data)
    issues.extend(registry_issues)
    policy_issues = validate_model_policy(policy_data, active_profile_ids=ACTIVE_PROFILE_IDS)
    issues.extend(policy_issues)

    if isinstance(registry_data, dict) and isinstance(policy_data, dict):
        registry_profiles = {
            profile["id"]: profile
            for profile in registry_data.get("profiles", [])
            if isinstance(profile, dict) and isinstance(profile.get("id"), str)
        }
        tier_map = policy_data.get("profile_tiers", {})
        if isinstance(tier_map, dict):
            for profile_id, profile in registry_profiles.items():
                registry_tier = profile.get("default_model")
                policy_tier = tier_map.get(profile_id)
                if profile_id in ACTIVE_PROFILE_IDS and registry_tier != policy_tier:
                    issues.append(_issue("error", f"profile {profile_id} default_model {registry_tier!r} does not match policy tier {policy_tier!r}"))

    return issues


def format_issues(issues: list[ValidationIssue]) -> str:
    if not issues:
        return "profile architecture validation passed"
    lines = []
    for issue in issues:
        prefix = issue.severity.upper()
        location = f" [{issue.path}]" if issue.path else ""
        lines.append(f"{prefix}{location}: {issue.message}")
    return "\\n".join(lines)
