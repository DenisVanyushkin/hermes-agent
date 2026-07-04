"""Selected-role package context activation for Hermes Profile Architecture.

Activation model (selected_roles):
1. Built-in routing selects a role — this module does NOT alter that.
2. If selected role is in active_roles, load its package manifest from package_path.
3. Build role context text from the manifest.
4. On any failure, fallback to built-in role context (fail-open).

This module is intentionally pure and import-light. It does not import
gateway/agent/scheduler stacks, read .env, or access auth.json.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hermes_cli.role_packages import validate_manifest_path

logger = logging.getLogger(__name__)

# Built-in role ID → package directory name under package_path
_ROLE_TO_PACKAGE_DIR: dict[str, str] = {
    "scribe": "hermes-scribe-core",
    "researcher": "hermes-researcher-core",
    "engineer": "hermes-engineer-core",
    "security_auditor": "hermes-security-auditor-core",
    "career_strategist": "hermes-career-strategist-core",
    "artist": "hermes-artist-core",
}

# Package directory name → expected role.id in manifest (must match exactly)
_PACKAGE_DIR_TO_EXPECTED_ROLE_ID: dict[str, str] = {
    "hermes-scribe-core": "hermes_scribe_core",
    "hermes-researcher-core": "hermes_researcher_core",
    "hermes-engineer-core": "hermes_engineer_core",
    "hermes-security-auditor-core": "hermes_security_auditor_core",
    "hermes-career-strategist-core": "hermes_career_strategist_core",
    "hermes-artist-core": "hermes_artist_core",
}


@dataclass(frozen=True)
class RolePackageRoutingConfig:
    enabled: bool
    activation_mode: str
    active_roles: list[str]
    package_path: str | None
    fallback_to_builtin: bool


@dataclass(frozen=True)
class PackageActivationResult:
    builtin_role: str
    package_role_id: str
    package_name: str
    activated: bool
    fallback: bool
    # reason values: activated | role_not_enabled | package_missing |
    #                validation_error | exception | disabled
    reason: str
    context_text: str = ""


def load_role_package_routing_config(config: dict[str, Any]) -> RolePackageRoutingConfig:
    """Parse role_packages.routing from a hermes config dict. Always safe."""
    routing = config.get("role_packages", {}).get("routing", {})
    if not isinstance(routing, dict):
        routing = {}
    enabled = bool(routing.get("enabled", False))
    activation_mode = str(routing.get("activation_mode") or "selected_roles")
    active_roles_raw = routing.get("active_roles", [])
    active_roles: list[str] = list(active_roles_raw) if isinstance(active_roles_raw, list) else []
    raw_path = routing.get("package_path")
    package_path: str | None = str(raw_path) if raw_path else None
    fallback_to_builtin = bool(routing.get("fallback_to_builtin", True))
    return RolePackageRoutingConfig(
        enabled=enabled,
        activation_mode=activation_mode,
        active_roles=active_roles,
        package_path=package_path,
        fallback_to_builtin=fallback_to_builtin,
    )


def _prompt_hash(prompt_text: str) -> str:
    return "sha256:" + hashlib.sha256(
        prompt_text.encode("utf-8", errors="replace")
    ).hexdigest()[:12]


def _log_activation_event(
    *,
    activation_mode: str,
    builtin_primary_role: str,
    package_role_id: str,
    package_name: str,
    activated: bool,
    fallback: bool,
    reason: str,
    prompt_text: str,
) -> None:
    payload = {
        "event": "package_role_activation",
        "activation_mode": activation_mode,
        "builtin_primary_role": builtin_primary_role,
        "package_role_id": package_role_id,
        "package_name": package_name,
        "activated": activated,
        "fallback": fallback,
        "reason": reason,
        "prompt_hash": _prompt_hash(prompt_text),
        "ts": int(time.time()),
    }
    logger.info("PACKAGE_ROLE_ACTIVATION_EVENT %s", json.dumps(payload, ensure_ascii=False))


def render_package_role_context(manifest: dict[str, Any], builtin_role: str) -> str:
    """Render compact role context text from a validated package manifest.

    The output must NOT claim enforced_tools, package triggers, secrets_read,
    or production deploy without approval.
    """
    pkg = manifest.get("package") or {}
    role = manifest.get("role") or {}
    tools_section = manifest.get("tools") or role.get("tools") or {}

    pkg_name = str(pkg.get("name") or "").strip()
    pkg_version = str(pkg.get("version") or "").strip()
    role_id = str(role.get("id") or "").strip()
    display_name = str(role.get("display_name") or role_id).strip()
    role_family = str(role.get("role_family") or "").strip()
    purpose = str(role.get("purpose_summary") or "").strip()
    persona = str(role.get("persona") or "").strip()
    model_tier = str(role.get("model_tier_request") or "standard").strip()
    boundary_mode = str(
        manifest.get("boundary_mode") or role.get("boundary_mode") or "advisory"
    ).strip()
    allowed_cats_raw = tools_section.get("allowed_categories") or []
    allowed_cats = (
        ", ".join(str(c) for c in allowed_cats_raw)
        if isinstance(allowed_cats_raw, list)
        else ""
    )

    lines: list[str] = [
        f"You are acting as Hermes role: {display_name}.",
        "[Package role context active via selected-role activation]",
        f"Package: {pkg_name} v{pkg_version}",
        f"Package role id: {role_id}",
        f"Role family: {role_family}",
    ]
    if purpose:
        lines.extend(["", "Purpose:", purpose])
    if persona:
        lines.extend(["", "Persona:", persona])

    boundary_bits: list[str] = [
        f"boundary_mode: {boundary_mode} (advisory metadata; enforced_tools is NOT active)",
    ]
    if allowed_cats:
        boundary_bits.append(f"allowed tool categories (advisory): {allowed_cats}")

    # Immutable safety disclaimers — must always appear regardless of role
    boundary_bits.append("Package triggers are NOT active.")
    boundary_bits.append("enforced_tools is NOT active.")
    boundary_bits.append("secrets_read is NOT allowed.")
    boundary_bits.append("Production deploy requires explicit approval.")

    # Role-specific boundaries matching built-in policy
    if builtin_role == "engineer":
        boundary_bits.append(
            "Repo/code mutation is allowed. Production/runtime mutation requires explicit approval."
        )
    elif builtin_role == "security_auditor":
        boundary_bits.append("Security Auditor is a reviewer, not a universal blocker.")
    elif builtin_role == "scribe":
        boundary_bits.append(
            "Use Scribe only for meaningful durable outcomes; do not create noise."
        )
    elif builtin_role == "career_strategist":
        boundary_bits.append(
            "Do not auto-submit applications without explicit approval."
        )

    lines.extend(["", "Boundaries:", *[f"- {bit}" for bit in boundary_bits]])
    lines.append(f"\nmodel_tier_request: {model_tier} (metadata only)")

    return "\n".join(lines).strip()


def activate_package_for_role(
    builtin_role: str,
    routing_config: RolePackageRoutingConfig,
    prompt_text: str = "",
) -> PackageActivationResult:
    """Attempt to activate a package for the given built-in role. Never raises.

    Returns PackageActivationResult with activated=True on success,
    fallback=True on any failure path.
    """
    package_dir_name = _ROLE_TO_PACKAGE_DIR.get(builtin_role)
    expected_role_id = (
        _PACKAGE_DIR_TO_EXPECTED_ROLE_ID.get(package_dir_name or "")
        if package_dir_name
        else None
    )

    def _fail(reason: str, *, log: bool = True) -> PackageActivationResult:
        if log:
            _log_activation_event(
                activation_mode=routing_config.activation_mode,
                builtin_primary_role=builtin_role,
                package_role_id=expected_role_id or "",
                package_name=package_dir_name or "",
                activated=False,
                fallback=True,
                reason=reason,
                prompt_text=prompt_text,
            )
        return PackageActivationResult(
            builtin_role=builtin_role,
            package_role_id=expected_role_id or "",
            package_name=package_dir_name or "",
            activated=False,
            fallback=True,
            reason=reason,
        )

    # Guard: config disabled
    if not routing_config.enabled:
        return _fail("disabled", log=False)

    # Guard: wrong activation_mode
    if routing_config.activation_mode != "selected_roles":
        return _fail("disabled", log=False)

    # Guard: role not in active list
    if builtin_role not in routing_config.active_roles:
        return _fail("role_not_enabled")

    # Guard: no package dir mapping (general_operator, etc.) or no package_path
    if not package_dir_name or not routing_config.package_path:
        return _fail("package_missing")

    try:
        pkg_path = Path(routing_config.package_path) / package_dir_name

        if not pkg_path.exists():
            return _fail("package_missing")

        manifest, errors, _ = validate_manifest_path(pkg_path)
        if errors or manifest is None:
            return _fail("validation_error")

        role_section = manifest.get("role") or {}
        actual_role_id = str(role_section.get("id") or "").strip()
        if actual_role_id != expected_role_id:
            logger.warning(
                "package role id mismatch: expected=%s actual=%s pkg=%s",
                expected_role_id,
                actual_role_id,
                package_dir_name,
            )
            return _fail("validation_error")

        context_text = render_package_role_context(manifest, builtin_role)
        if not context_text:
            return _fail("validation_error")

        pkg_name = str((manifest.get("package") or {}).get("name") or package_dir_name)
        _log_activation_event(
            activation_mode=routing_config.activation_mode,
            builtin_primary_role=builtin_role,
            package_role_id=actual_role_id,
            package_name=pkg_name,
            activated=True,
            fallback=False,
            reason="activated",
            prompt_text=prompt_text,
        )
        return PackageActivationResult(
            builtin_role=builtin_role,
            package_role_id=actual_role_id,
            package_name=pkg_name,
            activated=True,
            fallback=False,
            reason="activated",
            context_text=context_text,
        )

    except Exception as exc:
        logger.warning(
            "package_role_activation exception: builtin_role=%s exc=%s",
            builtin_role,
            exc,
        )
        return _fail("exception", log=True)
