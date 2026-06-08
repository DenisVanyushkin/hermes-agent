"""Validation for the Hermes profile architecture MVP.

This module validates the machine-readable profile registry and model policy
configs used by PR-1. It is intentionally fail-closed: malformed YAML,
missing required fields, unknown tiers, or risky policy mismatches are all
reported as errors.
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
MODEL_TIERS = {"standard", "reasoning", "critical"}
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


@dataclass(frozen=True)
class ValidationIssue:
    """A single validation finding."""

    severity: str
    message: str
    path: str = ""


def _issue(severity: str, message: str, path: str = "") -> ValidationIssue:
    return ValidationIssue(severity=severity, message=message, path=path)


def _normalize_list(value: Any) -> list[Any] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    return value


def _validate_string_list(value: Any, field_name: str, owner: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    values = _normalize_list(value)
    if values is None:
        issues.append(
            _issue("error", f"profile {owner} field {field_name} must be a list of strings")
        )
        return issues
    if not values:
        issues.append(_issue("error", f"profile {owner} field {field_name} must not be empty"))
        return issues
    for item in values:
        if not isinstance(item, str) or not item.strip():
            issues.append(
                _issue(
                    "error",
                    f"profile {owner} field {field_name} contains a non-string or empty entry: {item!r}",
                )
            )
    return issues


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
        runtime_required = documentation_policy.get("runtime_required_paths")
        future_only = documentation_policy.get("future_only_paths")
        runtime_required_values = _normalize_list(runtime_required)
        future_only_values = _normalize_list(future_only)
        if runtime_required_values is None:
            issues.append(_issue("error", "documentation_policy.runtime_required_paths must be a list"))
        if future_only_values is None:
            issues.append(_issue("error", "documentation_policy.future_only_paths must be a list"))
        if runtime_required_values is not None:
            for required_path in REQUIRED_DOCUMENTATION_PATHS:
                if required_path not in runtime_required_values:
                    issues.append(
                        _issue(
                            "error",
                            f"documentation_policy.runtime_required_paths is missing required path {required_path}",
                        )
                    )
            for forbidden_path in FUTURE_ONLY_PATHS:
                if forbidden_path in runtime_required_values:
                    issues.append(
                        _issue(
                            "error",
                            f"documentation_policy.runtime_required_paths must not require future-only path {forbidden_path}",
                        )
                    )
        if future_only_values is not None:
            for future_path in FUTURE_ONLY_PATHS:
                if future_path not in future_only_values:
                    issues.append(
                        _issue(
                            "error",
                            f"documentation_policy.future_only_paths is missing {future_path}",
                        )
                    )

    profiles = data.get("profiles")
    if not isinstance(profiles, list):
        issues.append(_issue("error", "profile registry profiles must be a list"))
        profiles = []

    seen_ids: set[str] = set()
    for index, profile in enumerate(profiles):
        path = f"profiles[{index}]"
        if not isinstance(profile, dict):
            issues.append(_issue("error", f"{path} must be a mapping", path))
            continue

        missing = REQUIRED_PROFILE_FIELDS - profile.keys()
        if missing:
            issues.append(
                _issue(
                    "error",
                    f"profile {profile.get('id', path)} is missing required fields: {', '.join(sorted(missing))}",
                    path,
                )
            )
            # keep validating the fields that are present so one broken profile
            # doesn't hide the rest of the issues.

        profile_id = profile.get("id")
        if not isinstance(profile_id, str) or not profile_id.strip():
            issues.append(_issue("error", f"{path} id must be a non-empty string", path))
            continue
        if profile_id in seen_ids:
            issues.append(_issue("error", f"duplicate profile id {profile_id}", path))
        seen_ids.add(profile_id)
        if profile_id not in ACTIVE_PROFILE_IDS:
            issues.append(_issue("error", f"profile {profile_id} is not an active profile", path))

        default_model = profile.get("default_model")
        if default_model not in MODEL_TIERS:
            issues.append(
                _issue(
                    "error",
                    f"profile {profile_id} default_model {default_model!r} is not a known model tier",
                    path,
                )
            )

        for field in ("allowed_tools", "denied_tools", "requires_approval_for", "may_read_paths", "may_write_paths", "output_artifacts"):
            issues.extend(_validate_string_list(profile.get(field), field, profile_id))

        for field in ("scribe_hook", "scribe_hook_condition", "security_review_hook", "security_review_hook_condition"):
            if field not in profile:
                continue
        if profile_id == "engineer":
            approvals = set(_normalize_list(profile.get("requires_approval_for")) or [])
            missing_approvals = REQUIRED_ENGINEER_APPROVALS - approvals
            if missing_approvals:
                issues.append(
                    _issue(
                        "error",
                        "engineer profile is missing required production mutation approval requirements: "
                        + ", ".join(sorted(missing_approvals)),
                        path,
                    )
                )
        if profile_id == "scribe":
            artifacts = set(_normalize_list(profile.get("output_artifacts")) or [])
            if "handoff" in artifacts or "handoff_report" in artifacts:
                issues.append(
                    _issue(
                        "error",
                        "scribe output_artifacts must distinguish handoff_complete from handoff_incomplete; generic handoff artifacts are not allowed",
                        path,
                    )
                )
            if not {"handoff_complete", "handoff_incomplete"}.issubset(artifacts):
                issues.append(
                    _issue(
                        "error",
                        "scribe output_artifacts must include both handoff_complete and handoff_incomplete",
                        path,
                    )
                )
        if profile_id == "security_auditor" and default_model != "critical":
            issues.append(
                _issue(
                    "error",
                    "security_auditor must use default_model critical",
                    path,
                )
            )

    missing_active = ACTIVE_PROFILE_IDS - seen_ids
    extra_active = seen_ids - ACTIVE_PROFILE_IDS
    if missing_active:
        issues.append(
            _issue("error", "profile registry is missing active profiles: " + ", ".join(sorted(missing_active)))
        )
    if extra_active:
        issues.append(
            _issue("error", "profile registry contains unexpected profiles: " + ", ".join(sorted(extra_active)))
        )

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
            if "allow_fallback" not in tier or tier.get("allow_fallback") is not False:
                issues.append(
                    _issue(
                        "error",
                        "critical tier must set allow_fallback to false",
                    )
                )
            if tier.get("unavailable_behavior") != "stop_and_escalate":
                issues.append(
                    _issue(
                        "error",
                        "critical tier must set unavailable_behavior to stop_and_escalate",
                    )
                )
            fallback_models = tier.get("fallback_models")
            if not isinstance(fallback_models, list):
                issues.append(
                    _issue(
                        "error",
                        "critical tier fallback_models must be a list",
                    )
                )
            elif fallback_models:
                issues.append(
                    _issue(
                        "error",
                        "critical tier fallback_models must be empty",
                    )
                )
            fallback_behavior = tier.get("fallback_behavior")
            if fallback_behavior is not None and fallback_behavior != "stop_and_escalate":
                issues.append(
                    _issue(
                        "error",
                        "critical tier fallback_behavior is legacy/derived and must match stop_and_escalate if present",
                    )
                )

    profile_tiers = data.get("profile_tiers")
    if not isinstance(profile_tiers, dict):
        issues.append(_issue("error", "model policy profile_tiers must be a mapping"))
        profile_tiers = {}

    unknown_profiles = set(profile_tiers) - active_profile_ids
    if unknown_profiles:
        issues.append(
            _issue(
                "error",
                "model policy references unknown active profiles: " + ", ".join(sorted(unknown_profiles)),
            )
        )

    missing_profiles = active_profile_ids - set(profile_tiers)
    if missing_profiles:
        issues.append(
            _issue(
                "error",
                "model policy is missing tier mapping for active profiles: " + ", ".join(sorted(missing_profiles)),
            )
        )

    for profile_id, tier_name in profile_tiers.items():
        if not isinstance(tier_name, str) or tier_name not in MODEL_TIERS:
            issues.append(
                _issue(
                    "error",
                    f"model policy profile_tiers[{profile_id!r}] references unknown model tier {tier_name!r}",
                )
            )
            continue
        if tier_name == "critical":
            tier = tiers.get(tier_name) or {}
            if tier.get("allow_fallback") is not False:
                issues.append(
                    _issue(
                        "error",
                        f"profile {profile_id} uses critical tier, but critical.allow_fallback is not false",
                    )
                )
            if tier.get("unavailable_behavior") != "stop_and_escalate":
                issues.append(
                    _issue(
                        "error",
                        f"profile {profile_id} uses critical tier, but critical.unavailable_behavior is not stop_and_escalate",
                    )
                )
            fallback_models = tier.get("fallback_models")
            if not isinstance(fallback_models, list):
                issues.append(
                    _issue(
                        "error",
                        f"profile {profile_id} uses critical tier, but critical.fallback_models is not a list",
                    )
                )
            elif fallback_models:
                issues.append(
                    _issue(
                        "error",
                        f"profile {profile_id} uses critical tier, but critical.fallback_models is not empty",
                    )
                )

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

    active_ids = set(ACTIVE_PROFILE_IDS)
    if isinstance(registry_data, dict):
        profiles = registry_data.get("profiles")
        if isinstance(profiles, list):
            active_ids = {profile.get("id") for profile in profiles if isinstance(profile, dict) and isinstance(profile.get("id"), str)}
            active_ids = {profile_id for profile_id in active_ids if profile_id in ACTIVE_PROFILE_IDS}

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
                    issues.append(
                        _issue(
                            "error",
                            f"profile {profile_id} default_model {registry_tier!r} does not match policy tier {policy_tier!r}",
                        )
                    )

    return issues


def format_issues(issues: list[ValidationIssue]) -> str:
    if not issues:
        return "profile architecture validation passed"
    lines = []
    for issue in issues:
        prefix = issue.severity.upper()
        location = f" [{issue.path}]" if issue.path else ""
        lines.append(f"{prefix}{location}: {issue.message}")
    return "\n".join(lines)
