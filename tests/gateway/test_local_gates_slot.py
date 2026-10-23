"""Fork operator gates answer from upstream's pending-reply slot (gateway/local_gates.py).

Real GatewayRunner, DiscordAdapter and SessionAuthority over SQLite, as in
test_messaging_slash_command_fifo; the turn body is a stand-in and the ops
executor is replaced, so nothing runs git.
"""
import asyncio
from types import SimpleNamespace

import pytest

from tests.waiting import async_wait_until

OPERATOR = '42'


def _owner(monkeypatch, mode=None):
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.base import SendResult
    from gateway.run import GatewayRunner
    from plugins.platforms.discord.adapter import DiscordAdapter

    monkeypatch.setenv('DISCORD_ALLOWED_USERS', OPERATOR)
    runner = GatewayRunner(GatewayConfig())
    if mode is not None:
        runner._busy_input_mode = mode
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


class _Agent:
    interrupted = False

    def interrupt(self, *args, **kwargs):
        self.interrupted = True

    def steer(self, *args, **kwargs):
        raise AssertionError('a gate answer must never be steered into the running turn')


def _arm_ops(monkeypatch, tmp_path):
    """One pending mutate step; returns the executor's call log."""
    from hermes_cli import ops_gate_message, ops_gate_service, ops_executor

    state = {'pending': {
        'plan': [{'op_id': 'git_branch_create', 'risk': 'mutate', 'argv': ['git', 'branch', 'x'],
                  'description': 'create a branch'}],
        'repo_path': str(tmp_path),
    }}
    calls = []
    monkeypatch.setattr(ops_gate_service, 'get_pending', lambda: state['pending'])
    monkeypatch.setattr(ops_gate_service, 'clear_pending', lambda: state.update(pending=None))
    monkeypatch.setattr(ops_gate_message, 'resolve_operation_cwd', lambda raw: raw)

    def execute(operation, cwd):
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        calls.append(SimpleNamespace(op_id=operation.op_id, cwd=str(cwd), on_loop=on_loop))
        return {'status': 0, 'output': ''}
    monkeypatch.setattr(ops_executor, 'execute_operation', execute)
    return calls


async def _start_turn(monkeypatch, mode, *, user_id=OPERATOR):
    """Turn A is running (agent built, waiting on ``finish``)."""
    from gateway.platforms.event import MessageEvent
    from gateway.session_authority import initialize_session_authority

    runner, adapter, sent = _owner(monkeypatch, mode)
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='owner')
    runner._wire_adapter_handlers(adapter)
    source = adapter.build_source(chat_id='42', chat_type='dm', user_id=user_id)
    rig = SimpleNamespace(runner=runner, adapter=adapter, sent=sent, authority=authority, source=source,
                          key=runner.session_store._generate_session_key(source), turns=[],
                          agent=_Agent(), running=asyncio.Event(), finish=asyncio.Event())

    async def turn(event, source, quick_key, run_generation):
        rig.turns.append(event.message_id)
        if event.message_id == 'A':
            runner._session_state(quick_key).turn.agent = rig.agent
            rig.running.set()
            await rig.finish.wait()
        return 'reply ' + str(event.message_id)
    monkeypatch.setattr(runner, '_handle_message_with_agent', turn)
    await adapter.handle_message(MessageEvent(text='A', source=source, message_id='A'))
    await asyncio.wait_for(rig.running.wait(), 10)
    return rig


async def _handle(rig, event):
    """``_handle_message`` bounded: a gate answer must come back without waiting on turn A."""
    return await asyncio.wait_for(rig.runner._handle_message(event), 15)


async def _intercept(rig, event):
    return await asyncio.wait_for(rig.runner._hm_pending_reply_intercepts(event, rig.source, rig.key), 15)


def _rows(rig):
    from hermes_state_runtime import list_session_admissions
    sid = rig.runner.session_store.peek_session_id(rig.key)
    return [row['request_id'] for row in list_session_admissions(rig.authority.db, session_id=sid, pending_only=False)]


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['interrupt', 'steer', 'queue'])
async def test_an_answer_during_a_running_turn_executes_the_plan_and_leaves_the_turn_alone(
        monkeypatch, tmp_path, mode):
    from gateway.platforms.event import MessageEvent

    calls = _arm_ops(monkeypatch, tmp_path)
    rig = await _start_turn(monkeypatch, mode)
    try:
        reply = await _handle(rig, MessageEvent(text='выполни', source=rig.source, message_id='G'))
        assert reply and 'git_branch_create' in reply, reply
        assert [c.op_id for c in calls] == ['git_branch_create']
        assert not rig.agent.interrupted
        assert 'G' not in _rows(rig) and 'G' not in rig.turns
    finally:
        rig.finish.set()


@pytest.mark.asyncio
async def test_the_plan_runs_off_the_event_loop(monkeypatch, tmp_path):
    from gateway.platforms.event import MessageEvent

    calls = _arm_ops(monkeypatch, tmp_path)
    rig = await _start_turn(monkeypatch, 'interrupt')
    try:
        await _handle(rig, MessageEvent(text='выполни', source=rig.source, message_id='G'))
        assert calls and calls[0].on_loop is False
    finally:
        rig.finish.set()


@pytest.mark.asyncio
async def test_an_unauthorized_sender_cannot_answer_the_gate(monkeypatch, tmp_path):
    from gateway.platforms.event import MessageEvent

    calls = _arm_ops(monkeypatch, tmp_path)
    rig = await _start_turn(monkeypatch, 'interrupt')
    try:
        stranger = rig.adapter.build_source(chat_id='43', chat_type='dm', user_id='43')
        await _handle(rig, MessageEvent(text='выполни', source=stranger, message_id='X'))
        assert calls == []
    finally:
        rig.finish.set()


@pytest.mark.asyncio
async def test_an_event_injected_by_a_plugin_cannot_answer_the_gate(monkeypatch, tmp_path):
    from gateway.platforms.event import MessageEvent

    calls = _arm_ops(monkeypatch, tmp_path)
    rig = await _start_turn(monkeypatch, 'interrupt')
    try:
        injected = MessageEvent(text='выполни', source=rig.source, message_id='P', allow_gateway_control=False)
        assert await _intercept(rig, injected) is None
        assert calls == []
    finally:
        rig.finish.set()


@pytest.mark.asyncio
async def test_the_quoted_message_is_not_the_answer(monkeypatch, tmp_path):
    """A reply that quotes «отмена» but whose own text is «выполни» executes."""
    from gateway.platforms.event import MessageEvent

    calls = _arm_ops(monkeypatch, tmp_path)
    rig = await _start_turn(monkeypatch, 'interrupt')
    try:
        event = MessageEvent(text='выполни', source=rig.source, message_id='Q',
                             reply_to_message_id='plan-msg', reply_to_text='отмена')
        reply = await _intercept(rig, event)
        assert reply and 'git_branch_create' in reply
        assert [c.op_id for c in calls] == ['git_branch_create']
    finally:
        rig.finish.set()


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['он написал: выполни', 'привет', ''])
async def test_anything_but_an_exact_answer_falls_through(monkeypatch, tmp_path, text):
    from gateway.platforms.event import MessageEvent

    calls = _arm_ops(monkeypatch, tmp_path)
    rig = await _start_turn(monkeypatch, 'interrupt')
    try:
        event = MessageEvent(text=text, source=rig.source, message_id='N')
        assert await _intercept(rig, event) is None
        assert calls == []
    finally:
        rig.finish.set()


@pytest.mark.asyncio
async def test_without_an_armed_gate_the_word_is_an_ordinary_message(monkeypatch, tmp_path):
    from gateway.platforms.event import MessageEvent
    from hermes_cli import ops_gate_service

    calls = _arm_ops(monkeypatch, tmp_path)
    monkeypatch.setattr(ops_gate_service, 'get_pending', lambda: None)
    rig = await _start_turn(monkeypatch, 'interrupt')
    try:
        event = MessageEvent(text='выполни', source=rig.source, message_id='U')
        assert await _intercept(rig, event) is None
        assert calls == []
    finally:
        rig.finish.set()


def test_ops_is_asked_before_commit():
    """Strict parsers first; ops first because its cancel also clears the commit gate."""
    from gateway import local_gates

    order = [g.__name__ for g in local_gates._GATES]
    assert order[0] == 'ops_approval_ack'
    assert order.index('upstream_sync_triage_ack') < order.index('upstream_sync_decision_ack')
    assert order[-1] == 'commit_approval_ack'
