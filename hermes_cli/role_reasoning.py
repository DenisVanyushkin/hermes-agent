"""Effort a role policy demands, applied to the turn that runs under it.

``select_model_policy()`` has returned a ``reasoning_level`` per policy from the
start, and nothing ever applied it: a turn under ``coding_high_reasoning`` still
went to the provider on whatever ``config.yaml`` said, while the log printed the
policy name and implied otherwise. This module closes that half of the gap --
``apply_role_model()`` closed the model half in 2026-07.

The policy sets a FLOOR, never a value: effective effort is
``max(config, policy)`` on the ``VALID_REASONING_EFFORTS`` scale. An operator who
configured ``xhigh`` is not clamped down to the engineer policy's ``high``, and a
session that explicitly asked for ``low`` via ``/reasoning`` is exempt outright --
an explicit human decision for this session beats automatic policy.

Lives outside ``model_selection.py`` on purpose: that module documents itself as
selecting "without mutating provider resolution, fallback chains, or runtime
model state", and applying a floor mutates the agent. Keeping the mutator here
also lets ``chat_completion_helpers`` import it without an import cycle through
``conversation_loop``.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Policy ``reasoning_level`` values that name a real reasoning effort.
#:
#: The policy vocabulary is wider than the effort scale: "default", "balanced"
#: and "stable" describe a posture, not a level, and inventing an effort for
#: them would be a decision with nothing behind it. A level absent from this
#: table means the policy has NO OPINION about effort -- it does not mean medium.
POLICY_EFFORT_FLOORS: dict[str, str] = {"high": "high"}


def resolve_role_effort_floor(reasoning_level: str | None) -> str | None:
    """Return the effort this policy demands as a minimum, or None."""
    return POLICY_EFFORT_FLOORS.get(str(reasoning_level or "").strip().lower())


def raise_to_floor(current: dict[str, Any] | None, floor: str | None) -> dict[str, Any] | None:
    """Raise a reasoning config to *floor*, never lower it.

    ``current`` is whatever ``resolve_reasoning_config()`` produced (per-model
    override > global ``agent.reasoning_effort``), or None when nothing is
    configured and the provider's own default applies.

    Returns ``current`` unchanged -- the same object, so callers can compare by
    identity -- whenever the floor does not apply.
    """
    from hermes_constants import VALID_REASONING_EFFORTS

    if not floor or floor not in VALID_REASONING_EFFORTS:
        return current
    if current is None:
        return {"enabled": True, "effort": floor}
    if not isinstance(current, dict):
        return current
    if current.get("enabled") is False:
        # Thinking was explicitly disabled. parse_reasoning_effort() carries the
        # same rule: a YAML ``false`` means off, never "fall back to default".
        return current
    effort = str(current.get("effort") or "").strip().lower()
    if effort not in VALID_REASONING_EFFORTS:
        return {**current, "enabled": True, "effort": floor}
    if VALID_REASONING_EFFORTS.index(effort) >= VALID_REASONING_EFFORTS.index(floor):
        return current
    return {**current, "enabled": True, "effort": floor}


def effort_label(reasoning_config: Any) -> str:
    """Render a reasoning config for the log: a level, ``off``, or ``-``."""
    if not isinstance(reasoning_config, dict):
        return "-"
    if reasoning_config.get("enabled") is False:
        return "off"
    return str(reasoning_config.get("effort") or "-")
