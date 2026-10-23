"""Fork pipeline router: the turn routes, a long execution runs in the background.

The orchestrator is a stand-in that either returns at once (a quick outcome) or
signals ``on_execution_start`` and then blocks until the test releases it, which
is the shape of a real engineering run. Delivery, mirror and the run records
are the real code; the runner is a minimal stand-in exposing what
``gateway.local_pipelines`` uses.
"""
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.session import SessionSource

PIPELINE_CONFIG = {'pipelines': {'enabled': True, 'orchestrator': {'mode': 'autonomous'}}}


def _source(chat='C1', thread='T1', user='U1'):
    return SessionSource(platform=Platform.SLACK, chat_id=chat, thread_id=thread, user_id=user, chat_type='channel')


class _Runner:
    def __init__(self):
        self.notices = []
        self.agent_calls = []
        self.adapters = {}

    async def _run_in_executor_with_context(self, func, *args):
        return await asyncio.to_thread(func, *args)

    async def _deliver_platform_notice(self, source, content):
        self.notices.append((source.chat_id, source.thread_id, content))

    def _get_proxy_url(self):
        return None

    async def _run_agent_inner(self, message, context_prompt, history, source, session_id, **kwargs):
        from gateway.local_pipelines import turn_toolsets
        self.agent_calls.append(SimpleNamespace(message=message, context_prompt=context_prompt,
                                                toolsets=turn_toolsets(['terminal', 'session_search', 'clarify'])))
        return {'final_response': 'agent reply', 'messages': [], 'api_calls': 1, 'tools': [],
                'history_offset': len(history), 'session_id': session_id}


def _report(text='Готово: ветка hermes-run/x, коммит ждёт апрува.'):
    return SimpleNamespace(pipeline_execution_controller=SimpleNamespace(
        actual_execution_invoked=True, subagent_execution_invoked=True, real_provider_bridge_invoked=False,
        final_response_text=text, blocked_reason=None, status='executed'))


@pytest.fixture
def wiring(monkeypatch):
    """Config, router and orchestrator stand-ins; returns a handle to drive the orchestrator."""
    import gateway.local_pipelines as lp
    import gateway.mirror as mirror
    import hermes_cli.orchestrator as orchestrator
    import hermes_cli.pipeline_observe as observe

    monkeypatch.setattr(lp, '_load_gateway_config', lambda: PIPELINE_CONFIG)
    monkeypatch.setattr(lp, '_resolve_gateway_engineering_task_context', lambda **_: None)
    h = SimpleNamespace(router_on_loop=[], mode='quick', release=threading.Event(), started=threading.Event(),
                        report=_report(), mirrors=[], orchestrator_calls=0, raise_in_helper=None)

    def router(**kwargs):
        try:
            asyncio.get_running_loop()
            h.router_on_loop.append(True)
        except RuntimeError:
            h.router_on_loop.append(False)
        return SimpleNamespace(route='engineering', selected_pipeline_id='engineering_review_pipeline')
    monkeypatch.setattr(observe, 'observe_pipeline_router_decision', router)

    def observe_turn(*, on_execution_start=None, **kwargs):
        h.orchestrator_calls += 1
        if h.mode == 'quick':
            return None
        on_execution_start('ps-' + str(h.orchestrator_calls))
        h.started.set()
        h.release.wait(20)
        if h.raise_in_helper:
            raise h.raise_in_helper
        return h.report
    monkeypatch.setattr(orchestrator, 'observe_gateway_turn', observe_turn)

    def fake_mirror(platform, chat_id, message_text, source_label='cli', thread_id=None, user_id=None,
                    role='assistant', session_id=None):
        h.mirrors.append(SimpleNamespace(platform=platform, chat_id=chat_id, text=message_text, role=role,
                                         session_id=session_id, thread_id=thread_id))
        return True
    monkeypatch.setattr(mirror, 'mirror_to_session', fake_mirror)
    return h


async def _turn(runner, text='сделай фичу X', source=None, display_kind=None):
    from gateway.local_pipelines import routed_turn
    return await asyncio.wait_for(routed_turn(
        runner, text, '', [], source or _source(), 'sess-1',
        {'session_key': 'agent:main:slack:C1:T1', 'persist_user_display_kind': display_kind}), 15)


def _records():
    from gateway.local_pipelines import runs_dir
    return [json.loads(p.read_text()) for p in sorted(runs_dir().glob('*.json'))]


async def _until(predicate, what):
    for _ in range(300):
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError('timed out waiting for ' + what)


@pytest.mark.asyncio
async def test_a_quick_outcome_falls_through_to_the_normal_agent(wiring):
    runner = _Runner()
    result = await _turn(runner)
    assert result['final_response'] == 'agent reply'
    assert len(runner.agent_calls) == 1 and runner.notices == [] and _records() == []


@pytest.mark.asyncio
async def test_the_router_runs_off_the_event_loop(wiring):
    await _turn(_Runner())
    assert wiring.router_on_loop == [False]


@pytest.mark.asyncio
async def test_a_long_run_answers_at_once_and_frees_the_turn(wiring):
    wiring.mode = 'long'
    runner = _Runner()
    result = await _turn(runner)
    assert wiring.started.is_set() and not wiring.release.is_set(), 'the turn returned while the run is still going'
    assert 'ps-1' in result['final_response'] and runner.agent_calls == []
    assert result['messages'][-1] == {'role': 'assistant', 'content': result['final_response']}
    [record] = _records()
    assert record['status'] == 'running' and record['run_id'] == 'ps-1'
    assert (record['source']['chat_id'], record['source']['thread_id']) == ('C1', 'T1')
    wiring.release.set()
    await _until(lambda: runner.notices, 'the completion notice')


@pytest.mark.asyncio
async def test_the_result_lands_in_the_same_thread_verbatim_and_in_the_transcript(wiring):
    wiring.mode = 'long'
    runner = _Runner()
    await _turn(runner)
    wiring.release.set()
    await _until(lambda: _records() and _records()[0]['status'] == 'delivered', 'delivery')
    [(chat, thread, text)] = runner.notices
    assert (chat, thread) == ('C1', 'T1')
    assert 'Готово: ветка hermes-run/x, коммит ждёт апрува.' in text
    [m] = wiring.mirrors
    assert m.role == 'user' and m.session_id == 'sess-1' and 'ps-1' in m.text and 'Готово' in m.text


@pytest.mark.asyncio
async def test_a_second_run_in_the_same_chat_is_refused_while_the_first_runs(wiring):
    wiring.mode = 'long'
    runner = _Runner()
    await _turn(runner)
    second = await _turn(runner, 'и ещё фичу Y')
    assert 'ps-1' in second['final_response'] and wiring.orchestrator_calls == 2
    assert [r['run_id'] for r in _records()] == ['ps-1'], 'the refused run must not start or leave a record'
    wiring.release.set()
    await _until(lambda: runner.notices, 'the first run to finish')


@pytest.mark.asyncio
async def test_another_chat_is_not_blocked_by_a_run(wiring):
    wiring.mode = 'long'
    runner = _Runner()
    await _turn(runner)
    wiring.started.clear()
    other = await _turn(runner, source=_source(chat='C2', thread='T9'))
    assert 'ps-2' in other['final_response']
    wiring.release.set()
    await _until(lambda: len(runner.notices) == 2, 'both runs to finish')


@pytest.mark.asyncio
async def test_a_failed_run_is_reported_not_swallowed(wiring):
    wiring.mode = 'long'
    wiring.raise_in_helper = RuntimeError('boom')
    runner = _Runner()
    await _turn(runner)
    wiring.release.set()
    await _until(lambda: runner.notices, 'the failure notice')
    assert 'ps-1' in runner.notices[0][2] and 'RuntimeError' in runner.notices[0][2]
    assert _records()[0]['status'] in ('failed', 'delivered_failure')


@pytest.mark.asyncio
async def test_internal_events_never_route(wiring):
    from gateway.response_filters import INTERNAL_NOTIFICATION_DISPLAY_KIND

    runner = _Runner()
    await _turn(runner, display_kind=INTERNAL_NOTIFICATION_DISPLAY_KIND)
    assert wiring.router_on_loop == [] and wiring.orchestrator_calls == 0 and len(runner.agent_calls) == 1


@pytest.mark.asyncio
async def test_a_run_left_running_by_a_previous_process_is_reported_lost_once(wiring):
    from gateway import local_pipelines as lp

    lp.runs_dir().mkdir(parents=True, exist_ok=True)
    stale = {'run_id': 'ps-old', 'status': 'running', 'boot_id': 'previous-process',
             'chat': 'slack|C1|T1', 'session_id': 'sess-1',
             'source': {'platform': 'slack', 'chat_id': 'C1', 'thread_id': 'T1', 'user_id': 'U1',
                        'chat_type': 'channel'}}
    (lp.runs_dir() / 'ps-old.json').write_text(json.dumps(stale))
    runner = _Runner()
    await lp.recover_lost_runs(runner)
    await lp.recover_lost_runs(runner)
    assert len(runner.notices) == 1 and 'ps-old' in runner.notices[0][2]
    assert _records()[0]['status'] == 'lost_notified'
    # The lost run no longer holds the chat.
    wiring.mode = 'long'
    fresh = await _turn(runner)
    assert 'ps-1' in fresh['final_response']
    wiring.release.set()
    await _until(lambda: len(runner.notices) == 2, 'the fresh run to finish')


@pytest.mark.asyncio
async def test_a_live_run_of_this_process_is_not_reported_lost(wiring):
    from gateway import local_pipelines as lp

    wiring.mode = 'long'
    runner = _Runner()
    await _turn(runner)
    await lp.recover_lost_runs(runner)
    assert runner.notices == [] and _records()[0]['status'] == 'running'
    wiring.release.set()
    await _until(lambda: runner.notices, 'completion')


@pytest.mark.asyncio
async def test_router_degradation_runs_the_agent_without_tools(wiring, monkeypatch):
    import gateway.local_pipelines as lp

    monkeypatch.setattr(lp, '_pipeline_router_infra_degraded_notice', lambda self, report, **_: '⚠️ роутер недоступен')
    runner = _Runner()
    result = await _turn(runner)
    assert runner.agent_calls[0].toolsets == []
    assert result['final_response'].startswith('⚠️ роутер недоступен')
    assert lp.turn_toolsets(['terminal']) == ['terminal'], 'the override must not leak past the turn'
