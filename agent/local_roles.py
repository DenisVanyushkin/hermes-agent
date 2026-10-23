"""Fork: per-turn role setup — role context, the model a turn runs on, the effort floor.

Called once per user turn from upstream's ``build_turn_context``
(agent/turn_context.py), right before the ``pre_llm_call`` plugins. The role
context it returns rides the same ephemeral channel as plugin context: it
reaches the model with the current user message and never the system prompt,
so the prompt cache prefix stays stable.

``apply_role_model`` and ``log_model_selection`` moved unchanged from the
pre-v0.22.5 ``agent/conversation_loop.py``; ``apply_turn_role`` is the role
block of that file's ``run_conversation`` without the retired approval-gate
replay.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.agent_runtime_helpers import switch_model
from hermes_cli import model_selection, profile_context

logger = logging.getLogger("agent.conversation_loop")


#: Models a role policy may repoint the runtime at. A stale or typo'd entry must
#: not be able to send a turn somewhere unsanctioned, so this is a closed set --
#: the same one profile_validation enforces for the policy file.
_ROLE_SWITCHABLE_MODELS = frozenset({"gpt-6-luna", "gpt-5.6-terra", "gpt-6-sol"})


def apply_role_model(agent: Any, *, preferred_model: str, preferred_provider: str) -> str:
    """Put the turn on the model its role calls for. Returns the model in effect.

    Until this existed, ``select_model_policy()`` was advisory in the strict
    sense -- it computed a per-role model that nothing ever applied, so every
    role ran on whatever the gateway config had set. The log said one thing and
    ``Turn ended`` said another.

    Deliberately conservative in three ways. Only models from the sanctioned
    lineup are honoured, so a stale policy entry cannot repoint the runtime. A
    role already on its model does not switch. And a failing switch leaves the
    turn on its current model rather than raising: choosing a model must never be
    able to break a turn that would otherwise have worked.
    """
    current = str(getattr(agent, "model", "") or "")

    if getattr(agent, "_skip_role_model_selection", False):
        # Контролируемый субагент: его модель задана спекой, и гард
        # controlled_subagent_identity_mismatch сверяет исходящую identity
        # именно с ней (chat_completion_helpers). Переключить его здесь --
        # значит гарантированно заблокировать каждый вызов. 2026-07-29 так и
        # вышло: инженер выжег 120 итераций и отчитался «tool-calling budget
        # exhausted», хотя причиной было расхождение спеки и политики роли.
        return current

    target = str(preferred_model or "").strip()
    if not target or target == current or target not in _ROLE_SWITCHABLE_MODELS:
        return current
    try:
        switch_model(agent, target, str(preferred_provider or getattr(agent, "provider", "")))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "role model switch failed, staying on %s: %s", current or "-", exc
        )
        return current
    return str(getattr(agent, "model", "") or target)


def log_model_selection(
    *,
    session: str,
    policy: str,
    model_class: str,
    role: str,
    provider: str,
    policy_model: Optional[str],
    effective_model: Optional[str],
    fallback: str = "",
    allow_fallback: bool = True,
    policy_effort_floor: str = "-",
    effective_effort: str = "-",
    floor_exempt: bool = False,
) -> None:
    """Log the model decision with the policy's intent and the runtime fact apart.

    ``select_model_policy()`` is advisory by construction — its own module
    docstring says it selects "without mutating provider resolution, fallback
    chains, or runtime model state". Logging its ``preferred_model`` as a bare
    ``model=`` therefore asserted a fact it had never established: the line read
    ``model=gpt-5.4`` on turns that ``Turn ended`` reported as
    ``model=gpt-6-luna``, and model decisions were being made off that.

    ``effective_model`` is ``agent.model`` — the model the turn actually starts
    on. Runtime fallback can still move it mid-turn (``chat_completion_helpers``
    assigns ``agent.model = fb_model``), so ``Turn ended`` stays the authority on
    where a turn finished; read the two lines together to see any drift.
    """
    logger.info(
        "model selection: session=%s policy=%s class=%s role=%s provider=%s "
        "policy_model=%s effective_model=%s fallback=%s allow_fallback=%s "
        "policy_effort_floor=%s effective_effort=%s effort_floor_exempt=%s",
        session or "-",
        policy,
        model_class,
        role,
        provider,
        policy_model or "unknown",
        effective_model or "unknown",
        fallback,
        allow_fallback,
        policy_effort_floor or "-",
        effective_effort or "-",
        "session" if floor_exempt else "-",
    )


def role_system_enabled() -> bool:
    """The fork's role system is on when ``role_routing`` is configured.

    Without it a turn runs exactly as upstream's: no role context, no model or
    effort change. An unreadable config counts as off.
    """
    try:
        from hermes_cli.config import load_config_readonly
        role_routing = (load_config_readonly() or {}).get("role_routing")
    except Exception as exc:  # noqa: BLE001
        logger.warning("role system: config unreadable, roles off for this turn: %s", exc)
        return False
    return isinstance(role_routing, dict) and bool(role_routing)


def apply_turn_role(agent: Any, original_user_message: Any) -> str:
    """Select this turn's role, put the agent on its model and effort floor; return its context.

    Never raises: choosing a role, a model or an effort must not be able to
    break a turn that would otherwise have worked.
    """
    if not role_system_enabled():
        return ""
    _role_user_context = ""
    _role_context_result = None
    _effective_task_for_routing = original_user_message
    try:
        _role_context_result = profile_context.build_role_context_for_task(_effective_task_for_routing)
        if _role_context_result.profile_context_used:
            _role_user_context = _role_context_result.context_text
            logger.info(
                "role context: session=%s selected_role=%s canonical_role=%s operation_category=%s used=%s",
                agent.session_id or "-",
                _role_context_result.selected_role,
                _role_context_result.canonical_role or "-",
                getattr(_role_context_result, "operation_category", "") or "-",
                _role_context_result.profile_context_used,
            )
        elif _role_context_result.fallback_reason:
            logger.warning(
                "role context omitted: session=%s selected_role=%s canonical_role=%s reason=%s",
                agent.session_id or "-",
                _role_context_result.selected_role,
                _role_context_result.canonical_role or "-",
                _role_context_result.fallback_reason,
            )
    except Exception as exc:
        logger.warning("role context build failed: %s", exc)

    try:
        # A block that fails here must not leave the previous turn's floor
        # behind for chat_completion_helpers' fallback path to re-apply to a
        # turn whose role never asked for it.
        agent._reasoning_effort_floor = None
        _model_selection_result = model_selection.select_model_policy(
            selected_role=getattr(_role_context_result, "selected_role", ""),
            canonical_role=getattr(_role_context_result, "canonical_role", None),
            task_text=_effective_task_for_routing if isinstance(_effective_task_for_routing, str) else "",
            critical_approval_required=bool(getattr(_role_context_result, "critical_approval_required", False)),
        )
        agent._model_selection = model_selection.model_selection_to_dict(_model_selection_result)
        _effective_model = apply_role_model(
            agent,
            preferred_model=agent._model_selection.get("preferred_model", ""),
            preferred_provider=agent._model_selection.get("preferred_provider", ""),
        )
        # The policy has always named an effort and nothing applied it. It is a
        # floor, not a value: it raises, never lowers, and a session-scoped
        # /reasoning override exempts the turn entirely.
        from hermes_cli.role_reasoning import (
            apply_reasoning_floor,
            resolve_role_effort_floor,
        )

        _effort_floor = resolve_role_effort_floor(
            agent._model_selection.get("reasoning_level", "")
        )
        _effective_effort = apply_reasoning_floor(agent, _effort_floor)
        log_model_selection(
            session=agent.session_id or "-",
            policy=agent._model_selection.get("policy_name", ""),
            model_class=agent._model_selection.get("policy_class", ""),
            role=agent._model_selection.get("effective_role", ""),
            provider=agent._model_selection.get("preferred_provider", ""),
            policy_model=agent._model_selection.get("preferred_model", ""),
            effective_model=_effective_model,
            fallback=agent._model_selection.get("fallback_chain_key", ""),
            allow_fallback=agent._model_selection.get("allow_fallback", True),
            policy_effort_floor=_effort_floor or "-",
            effective_effort=_effective_effort,
            floor_exempt=getattr(agent, "_reasoning_session_override", None) is not None,
        )
    except Exception as exc:
        logger.warning("model selection failed: %s", exc)
        agent._model_selection = {
            "policy_name": "unavailable",
            "selection_error": str(exc),
        }
    return _role_user_context
