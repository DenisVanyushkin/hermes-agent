"""A controlled pipeline subagent's tool calls reach its bridge through upstream's real executors.

The engineering pipeline arms a context-local bridge (agent/tool_dispatch_context.py) for its
subagents, so their tool calls run in the restricted pipeline executor. Both of upstream's
dispatch sites — the sequential executor and invoke_tool (the concurrent path) — must honour it;
without a bridge the global dispatcher runs as before.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.tool_dispatch_context import reset_bridge_dispatch, set_bridge_dispatch
from tests.agent.test_tool_call_incremental_persistence import _make_agent, _mock_tool_call


def _bridge(calls):
    def dispatch(function_name, function_args):
        calls.append(function_name)
        return json.dumps({"bridge": function_name})
    return dispatch


def test_the_sequential_executor_goes_to_the_bridge():
    agent = _make_agent()
    agent._flush_messages_to_session_db = MagicMock()
    calls, messages = [], []
    message = SimpleNamespace(content="", tool_calls=[
        _mock_tool_call(name="web_search", arguments='{"query": "x"}', call_id="c1")])
    token = set_bridge_dispatch(_bridge(calls))
    try:
        with patch("model_tools.handle_function_call", side_effect=AssertionError("bypassed the bridge")):
            agent._execute_tool_calls_sequential(message, messages, "task-1")
    finally:
        reset_bridge_dispatch(token)
    assert calls == ["web_search"]
    [tool] = [m for m in messages if m.get("role") == "tool"]
    assert "bridge" in str(tool["content"])


def test_invoke_tool_goes_to_the_bridge():
    agent = _make_agent()
    calls = []
    token = set_bridge_dispatch(_bridge(calls))
    try:
        with patch("model_tools.handle_function_call", side_effect=AssertionError("bypassed the bridge")):
            result = agent._invoke_tool("web_search", {"query": "x"}, "task-1")
    finally:
        reset_bridge_dispatch(token)
    assert calls == ["web_search"] and "bridge" in result


def test_without_a_bridge_the_global_dispatcher_runs():
    agent = _make_agent()
    with patch("model_tools.handle_function_call", return_value='{"ok": true}') as handle:
        result = agent._invoke_tool("web_search", {"query": "x"}, "task-1")
    assert handle.called and '"ok"' in result
