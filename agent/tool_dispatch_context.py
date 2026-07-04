"""Context-local tool dispatch override for executor bridges.

A pipeline bridge (e.g. the engineering subagent executor) restricts which
tools its conversation may call. Previously it monkey-patched the
module-level ``handle_function_call`` globals, which leaked the restricted
dispatcher into concurrent sessions running in other threads. The override
now lives in a ``ContextVar``: it is visible only in the bridge's own
execution context (and in worker threads that copy it via
``tools.thread_context.propagate_context_to_thread``).
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Callable, Mapping

_BridgeDispatch = Callable[[str, Mapping[str, Any] | None], str]

_active_bridge_dispatch: ContextVar[_BridgeDispatch | None] = ContextVar(
    "active_bridge_tool_dispatch", default=None
)


def set_bridge_dispatch(dispatch: _BridgeDispatch) -> Token:
    return _active_bridge_dispatch.set(dispatch)


def reset_bridge_dispatch(token: Token) -> None:
    _active_bridge_dispatch.reset(token)


def get_bridge_dispatch() -> _BridgeDispatch | None:
    return _active_bridge_dispatch.get()


def dispatch_function_call(function_name: str, function_args: Any, *args: Any, **kwargs: Any) -> str:
    """Route a tool call to the context-active bridge, else the global dispatcher."""
    bridge = get_bridge_dispatch()
    if bridge is not None:
        return bridge(function_name, function_args)
    import run_agent

    return run_agent.handle_function_call(function_name, function_args, *args, **kwargs)
