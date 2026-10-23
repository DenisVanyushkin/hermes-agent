"""A background pipeline run through the real inbound path frees the chat.

Real GatewayRunner, DiscordAdapter and SessionAuthority over SQLite: the message
goes adapter → _handle_message → _handle_message_with_agent → upstream
_run_agent → gateway.local_pipelines.routed_turn. Only the agent turn body
(_run_agent_inner), the router and the orchestrator are stand-ins.
"""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from tests.waiting import async_wait_until


def _owner(monkeypatch):
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.base import SendResult
    from gateway.run import GatewayRunner
    from plugins.platforms.discord.adapter import DiscordAdapter

    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
    runner = GatewayRunner(GatewayConfig())
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False))
    sent = []

    async def send(chat_id, content, reply_to=None, metadata=None):
        sent.append(content)
        return SendResult(success=True, message_id=f's{len(sent)}')
    monkeypatch.setattr(adapter, 'send', send)
    adapter.gateway_runner = runner
    runner.adapters = {Platform.DISCORD: adapter}
    monkeypatch.setattr(runner, '_read_user_config', lambda: {'approvals': {'destructive_slash_confirm': False}})
    return runner, adapter, sent


@pytest.mark.asyncio
async def test_a_detached_run_frees_the_chat_for_the_next_message(monkeypatch):
    import gateway.local_pipelines as lp
    import gateway.mirror as mirror
    import hermes_cli.orchestrator as orchestrator
    import hermes_cli.pipeline_observe as observe
    from gateway.platforms.event import MessageEvent
    from gateway.session_authority import initialize_session_authority

    runner, adapter, sent = _owner(monkeypatch)
    await initialize_session_authority(runner, profile_id='default', instance_id='owner')
    runner._wire_adapter_handlers(adapter)
    source = adapter.build_source(chat_id='42', chat_type='dm', user_id='42')

    agent_turns = []

    async def agent_turn(message, context_prompt, history, source, session_id, **kwargs):
        agent_turns.append(message)
        return {'final_response': 'agent: ' + message, 'messages': [], 'api_calls': 1, 'tools': [],
                'history_offset': len(history), 'session_id': session_id}
    monkeypatch.setattr(runner, '_run_agent_inner', agent_turn)
    monkeypatch.setattr(lp, '_load_gateway_config',
                        lambda: {'pipelines': {'enabled': True, 'orchestrator': {'mode': 'autonomous'}}})
    monkeypatch.setattr(lp, '_resolve_gateway_engineering_task_context', lambda **_: None)
    monkeypatch.setattr(observe, 'observe_pipeline_router_decision', lambda **_: SimpleNamespace(route='x'))
    mirrored = []
    monkeypatch.setattr(mirror, 'mirror_to_session', lambda *a, **k: mirrored.append((a, k)) or True)

    release = threading.Event()

    def observe_turn(*, user_message, on_execution_start=None, **_):
        if 'фичу' not in user_message:
            return None  # quick outcome: the ordinary agent answers
        on_execution_start('run-a')
        release.wait(20)
        return SimpleNamespace(pipeline_execution_controller=SimpleNamespace(
            actual_execution_invoked=True, subagent_execution_invoked=True, real_provider_bridge_invoked=False,
            final_response_text='Фича готова, коммит ждёт апрува.', blocked_reason=None))
    monkeypatch.setattr(orchestrator, 'observe_gateway_turn', observe_turn)

    try:
        await adapter.handle_message(MessageEvent(text='сделай фичу X', source=source, message_id='A'))
        await async_wait_until(lambda: any('run-a' in s and 'Запустил' in s for s in sent),
                               what='the run-started answer')
        await adapter.handle_message(MessageEvent(text='как дела?', source=source, message_id='B'))
        await async_wait_until(lambda: any(s == 'agent: как дела?' for s in sent), timeout=15,
                               what='an ordinary turn while the run is still going')
        assert not release.is_set() and agent_turns == ['как дела?']
    finally:
        release.set()
    await async_wait_until(lambda: any('Фича готова' in s for s in sent), timeout=15, what='the run result')
    assert mirrored and mirrored[0][1]['role'] == 'user'
