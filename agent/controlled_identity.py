"""Fork: block a controlled subagent's request whose identity is not the one its spec allows.

The engineering pipeline builds its subagents (engineer, reviewer) with a pinned
provider/model/api_mode/transport and records the allowed identities on the agent
(``_controlled_allowed_request_identities``, see hermes_cli/pipeline_aiagent_executor.py).
Upstream's ``build_api_kwargs`` (agent/chat_completion_helpers.py) calls
``check_controlled_request_identity`` before it builds anything, so a request that
drifted to another role's model, a wrong provider or a mismatched transport family
fails before any provider call.

The check is the guard block of the pre-v0.22.5 ``build_api_kwargs``, unchanged.
"""

from __future__ import annotations

from agent.base_url_family import _normalize_base_url_family


def check_controlled_request_identity(agent) -> None:
    """Raise ``RuntimeError("controlled_subagent_identity_mismatch: ...")`` on a disallowed identity."""
    runtime_request = getattr(agent, "_turn_runtime_request", None)
    if isinstance(runtime_request, dict):
        runtime_api_mode = runtime_request.get("actual_api_mode") or runtime_request.get("api_mode") or agent.api_mode
        runtime_provider = runtime_request.get("actual_provider") or runtime_request.get("provider") or agent.provider
        runtime_model = runtime_request.get("actual_model") or runtime_request.get("model") or agent.model
        runtime_base_url = runtime_request.get("actual_base_url") or runtime_request.get("base_url") or agent.base_url
        runtime_reasoning_config = runtime_request.get("reasoning_config") or agent.reasoning_config
        runtime_request_overrides = runtime_request.get("request_overrides") or agent.request_overrides
        runtime_session_id = runtime_request.get("session_id") or getattr(agent, "session_id", None)
        runtime_is_anthropic_oauth = runtime_request.get("is_anthropic_oauth")
    else:
        runtime_api_mode = agent.api_mode
        runtime_provider = agent.provider
        runtime_model = agent.model
        runtime_base_url = agent.base_url
        runtime_reasoning_config = agent.reasoning_config
        runtime_request_overrides = agent.request_overrides
        runtime_session_id = getattr(agent, "session_id", None)
        runtime_is_anthropic_oauth = agent._is_anthropic_oauth

    if getattr(agent, '_skip_role_model_selection', False):
        # Validate outbound (provider, model, api_mode, base_url_family) against the
        # role-local allowed identity set. Each entry in the set was derived from the
        # subagent spec at _build_agent time and covers primary + configured fallbacks.
        # Stale drift from another role, wrong provider, or mismatched transport family
        # is blocked here before any provider call is made.
        _allowed_ids = list(getattr(agent, '_controlled_allowed_request_identities', None) or [])
        if not _allowed_ids:
            # Defensive: rebuild from constructor attrs if allowed list was not populated
            _cm = getattr(agent, '_constructor_model', None)
            _ca = getattr(agent, '_constructor_api_mode', None)
            _cp = getattr(agent, '_constructor_provider', None)
            _cb = getattr(agent, '_constructor_base_url', None)
            if _cm and _ca:
                _allowed_ids = [{
                    "provider": _cp or "",
                    "model": _cm,
                    "api_mode": _ca,
                    "base_url_family": _normalize_base_url_family(_cp, _ca, _cb),
                }]
        if _allowed_ids:
            _runtime_family = _normalize_base_url_family(
                runtime_provider, runtime_api_mode, runtime_base_url
            )
            _identity_allowed = any(
                _id.get("provider") == runtime_provider
                and _id.get("model") == runtime_model
                and _id.get("api_mode") == runtime_api_mode
                and _id.get("base_url_family") == _runtime_family
                for _id in _allowed_ids
            )
            if not _identity_allowed:
                _allowed_safe = [
                    {k: v for k, v in _id.items() if k != "base_url"}
                    for _id in _allowed_ids
                ]
                _details = (
                    f"got_provider={runtime_provider!r} got_model={runtime_model!r} "
                    f"got_api_mode={runtime_api_mode!r} got_base_url_family={_runtime_family!r} "
                    f"allowed={_allowed_safe!r}"
                )
                raise RuntimeError(
                    f"controlled_subagent_identity_mismatch: {_details}. "
                    "Request blocked to prevent cross-role provider/model contamination."
                )
