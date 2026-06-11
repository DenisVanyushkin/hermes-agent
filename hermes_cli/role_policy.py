"""observe/warn role boundary policy computation -- Slice 6.

This module is observe-only. Invariants that must hold in this slice:
- No tool call is ever blocked.
- evaluate_role_tool_policy() is a pure function (no side effects).
- observe_and_log() wraps evaluation in try/except and always returns None.
- Every RolePolicyDecision.enforced is False.

Dispatch integration is structurally present but live-dormant until active
package role context is wired (see agent/tool_executor.py TODO(slice-routing)).

Out of scope for this slice:
- ContextVar enforcement
- MCP/plugin default deny
- Argument-level or path-scope enforcement
- Package role routability

TODO(slice-v1-enforce): Wire enforced_tools blocking via ContextVar when
                        enforced_tools production support is implemented.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_TOOL_MAP_PATH = Path(__file__).parent.parent / "config" / "hermes-role-tool-map.yaml"

# tool_name → category_name; loaded once per process.
# Tests that patch _TOOL_MAP_PATH must reset _CATEGORY_MAP to None for isolation.
_CATEGORY_MAP: dict[str, str] | None = None


def _load_tool_category_map() -> dict[str, str]:
    """Return {tool_name: category_name} mapping loaded from the YAML config."""
    global _CATEGORY_MAP
    if _CATEGORY_MAP is not None:
        return _CATEGORY_MAP

    result: dict[str, str] = {}
    try:
        raw = yaml.safe_load(_TOOL_MAP_PATH.read_text(encoding="utf-8"))
        categories = raw.get("categories", {})
        for cat_name, cat_data in categories.items():
            for tool_name in (cat_data.get("tools") or []):
                result[str(tool_name)] = str(cat_name)
    except Exception as exc:
        logger.warning(
            "role_policy: failed to load tool category map from %s: %s",
            _TOOL_MAP_PATH,
            exc,
        )

    _CATEGORY_MAP = result
    return result


def _get_tool_category(tool_name: str) -> str | None:
    """Return the category name for *tool_name*, or None if unmapped."""
    return _load_tool_category_map().get(tool_name)


@dataclass
class RolePolicyDecision:
    """Result of evaluating role boundary policy for one tool call.

    ``enforced`` is always False in Slice 6 -- no tool is ever blocked.
    """

    boundary_mode: str
    tool_name: str
    category: str | None
    allowed: bool
    would_block: bool
    enforced: bool  # always False in Slice 6
    reasons: list[str] = field(default_factory=list)


def evaluate_role_tool_policy(
    role_manifest: dict[str, Any],
    tool_name: str,
    tool_args: dict[str, Any],
) -> RolePolicyDecision:
    """Compute the effective policy decision for *tool_name* under *role_manifest*.

    Rules:
    - advisory: would_block=False always.
    - observe_warn: would_block=True when tool category is denied or not in allowlist.
    - enforced_tools: compute same as observe_warn; enforced=False (Slice 6, no blocking).
    - Unknown tool category: no block; reasons contains a warning message.
    - denied wins over allowed when same category appears in both lists.
    """
    boundary_mode = str(role_manifest.get("boundary_mode", "advisory"))
    role = role_manifest.get("role", {}) if isinstance(role_manifest, dict) else {}
    role_tools = role.get("tools") if isinstance(role, dict) else None

    category = _get_tool_category(tool_name)
    reasons: list[str] = []

    # advisory: never compute policy
    if boundary_mode == "advisory":
        return RolePolicyDecision(
            boundary_mode=boundary_mode,
            tool_name=tool_name,
            category=category,
            allowed=True,
            would_block=False,
            enforced=False,
        )

    # observe_warn or enforced_tools: compute policy
    if role_tools is None or not isinstance(role_tools, dict):
        return RolePolicyDecision(
            boundary_mode=boundary_mode,
            tool_name=tool_name,
            category=category,
            allowed=True,
            would_block=False,
            enforced=False,
        )

    denied_cats: list[str] = list(role_tools.get("denied_categories") or [])
    _raw_allowed = role_tools.get("allowed_categories")
    allowed_cats: list[str] | None = list(_raw_allowed) if _raw_allowed is not None else None

    if category is None:
        reasons.append(
            f"tool {tool_name!r} is not mapped to any known category; "
            "policy check skipped (future work: argument-level classification)"
        )
        return RolePolicyDecision(
            boundary_mode=boundary_mode,
            tool_name=tool_name,
            category=None,
            allowed=True,
            would_block=False,
            enforced=False,
            reasons=reasons,
        )

    # denied wins over allowed when same category is in both
    if category in denied_cats:
        reasons.append(f"category {category!r} is in denied_categories")
        return RolePolicyDecision(
            boundary_mode=boundary_mode,
            tool_name=tool_name,
            category=category,
            allowed=False,
            would_block=True,
            enforced=False,
            reasons=reasons,
        )

    # allowlist: if declared and category not in it -> would_block
    if allowed_cats is not None and category not in allowed_cats:
        reasons.append(
            f"category {category!r} is not in allowed_categories {allowed_cats}"
        )
        return RolePolicyDecision(
            boundary_mode=boundary_mode,
            tool_name=tool_name,
            category=category,
            allowed=False,
            would_block=True,
            enforced=False,
            reasons=reasons,
        )

    return RolePolicyDecision(
        boundary_mode=boundary_mode,
        tool_name=tool_name,
        category=category,
        allowed=True,
        would_block=False,
        enforced=False,
    )


def observe_and_log(
    *,
    role_manifest: dict[str, Any] | None,
    role_package: str,
    tool_name: str,
    tool_args: dict[str, Any],
) -> None:
    """Evaluate role policy and log would_block events. Never raises; never blocks.

    Always returns None. All exceptions are caught and logged at DEBUG level so
    policy observation can never interrupt tool dispatch.

    Dispatch integration is structurally present but live-dormant until active
    package role context is wired.
    """
    try:
        if not isinstance(role_manifest, dict):
            return
        decision = evaluate_role_tool_policy(role_manifest, tool_name, tool_args)
        if decision.would_block:
            role_id = (
                role_manifest.get("role", {}).get("id", "unknown")
                if isinstance(role_manifest.get("role"), dict)
                else "unknown"
            )
            logger.warning(
                "role_policy_would_block boundary_mode=%s role_package=%s role_id=%s "
                "tool_name=%s category=%s reasons=%r decision=would_block enforced=false",
                decision.boundary_mode,
                role_package,
                role_id,
                tool_name,
                decision.category,
                decision.reasons,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("role_policy observe_and_log error (non-fatal): %s", exc)
