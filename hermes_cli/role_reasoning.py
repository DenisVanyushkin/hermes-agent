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


def base_reasoning_config(agent: Any) -> Any:
    """The reasoning config as it was before any policy floor was applied.

    A surface that PERSISTS a session's settings must record what the human
    configured, not a floor that belonged to one turn's role: the TUI copies
    ``agent.reasoning_config`` into the stored session runtime, and a delegated
    child inherits it. Both want this value, not the raised one.
    """
    current = getattr(agent, "reasoning_config", None)
    if current is not None and current is getattr(agent, "_reasoning_floor_applied", None):
        return getattr(agent, "_reasoning_pre_floor_config", current)
    return current


def apply_reasoning_floor(agent: Any, floor: str | None) -> str:
    """Put the turn on at least the effort its role calls for.

    Returns the effort in effect, for the log line. Mirrors
    ``apply_role_model()`` in being unable to break a turn: any failure leaves
    the agent as it was.

    Three things happen here, in order.

    First the previous turn's raise is undone. The gateway's main path assigns a
    fresh ``reasoning_config`` every turn, but CLI chat and the TUI build one
    agent and reuse it forever -- there, without an undo, a single engineer
    question would pin the whole session to ``high``. Only OUR raise is undone,
    recognised by object identity, so a ``/reasoning`` typed between turns is
    never clobbered.

    Then an explicit session level wins outright, and is RE-APPLIED rather than
    merely respected: ``switch_model()`` re-resolves ``reasoning_config`` from
    config.yaml on every role model switch, so by the time we run, the human's
    value is already gone. Restoring it is the only way ``/reasoning low``
    survives an engineer turn.

    Only then does the floor raise anything.
    """
    try:
        current = getattr(agent, "reasoning_config", None)
        if current is not None and current is getattr(agent, "_reasoning_floor_applied", None):
            current = getattr(agent, "_reasoning_pre_floor_config", current)
            agent.reasoning_config = current
            agent._reasoning_floor_applied = None
    except Exception as exc:  # noqa: BLE001
        logger.warning("reasoning floor: could not undo the previous raise: %s", exc)
        current = getattr(agent, "reasoning_config", None)

    try:
        session_override = getattr(agent, "_reasoning_session_override", None)
    except Exception:  # noqa: BLE001
        session_override = None

    if session_override is not None:
        try:
            # Copy unconditionally. The gateway stamps the override and the
            # agent's config from the SAME stored object, so a conditional copy
            # leaves reasoning_config aliased to the session's saved setting --
            # exactly the aliasing this copy exists to prevent.
            agent.reasoning_config = dict(session_override)
        except Exception as exc:  # noqa: BLE001
            logger.warning("reasoning floor: could not restore the session level: %s", exc)
        return effort_label(getattr(agent, "reasoning_config", None))

    try:
        agent._reasoning_effort_floor = floor
        updated = raise_to_floor(current, floor)
        if updated is not current:
            agent._reasoning_pre_floor_config = current
            agent.reasoning_config = updated
            agent._reasoning_floor_applied = updated
        return effort_label(updated)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reasoning floor not applied, staying on current effort: %s", exc)
        return effort_label(getattr(agent, "reasoning_config", None))
