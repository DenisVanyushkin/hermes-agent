"""Синхронная часть хода не должна выполняться на потоке цикла событий.

`observe_gateway_turn` -- граница async/sync: всё ниже неё синхронно и до
этого слайса крутилось прямо на потоке цикла (`gateway/run.py`, вызов внутри
`_run_agent_inner`). Пока пайплайновый ход шёл, цикл не обслуживал ни Slack,
ни Telegram, ни typing-индикаторы, а достаточно долгий ход добивался сторожем
(`gateway/shutdown_watchdog.py`) как «замёрзший цикл».

Разговорный маршрут этой болезнью не болел: `agent.run_conversation` уже
уходит в гейтвейный пул через `_run_in_executor_with_context`. Тесты ниже
требуют того же от пайплайнового маршрута.

Диагностика при провале:

* `test_observe_gateway_turn_runs_off_the_event_loop_thread` падает на
  сравнении потоков -- вызов остался на цикле;
* `test_event_loop_is_served_while_the_pipeline_turn_runs` **не виснет**:
  двойник ждёт отметку цикла с таймаутом и возвращает False, если цикл за это
  время не сделал ни одного тика. Провал по assert, а не по watchdog'у pytest.
"""

from __future__ import annotations

import asyncio
import contextvars
import importlib
import sys
import threading
import types
from types import SimpleNamespace

import pytest

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.session import SessionSource


# Сколько двойник готов ждать тика цикла, прежде чем признать цикл
# заблокированным. Щедро: на нагруженной машине планировщик может задержать
# рабочий поток, и короткий таймаут дал бы мигающий тест. Цена ошибки
# асимметрична -- заблокированный цикл провалит тест и через 10 с.
_LOOP_TICK_TIMEOUT = 10.0

_probe_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "hermes_off_loop_probe", default="unset"
)


class _CapturingAgent:
    """Разговорный агент, который в этих тестах не должен запускаться."""

    run_calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        self.tools = []
        self.context_compressor = SimpleNamespace(last_prompt_tokens=0, context_length=0)
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.model = "fake-model"

    def run_conversation(self, user_message, conversation_history=None, task_id=None, persist_user_message=None):
        type(self).run_calls.append({"user_message": user_message})
        return {
            "final_response": "normal agent reply",
            "messages": [],
            "api_calls": 1,
            "completed": True,
        }


def _install_fake_agent(monkeypatch):
    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = _CapturingAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)


def _make_runner():
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner._ephemeral_system_prompt = ""
    runner._prefill_messages = []
    runner._reasoning_config = None
    runner._service_tier = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._running_agents = {}
    runner._pending_model_notes = {}
    runner._pending_skills_reload_notes = {}
    runner._session_db = None
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._session_model_overrides = {}
    runner._session_run_generation = {}
    runner.hooks = SimpleNamespace(loaded_hooks=False)
    runner.config = SimpleNamespace(
        streaming=None,
        thread_sessions_per_user=False,
        group_sessions_per_user=False,
        stt_enabled=False,
    )
    runner.session_store = SimpleNamespace(
        get_or_create_session=lambda source: SimpleNamespace(session_id="session-1"),
        load_transcript=lambda session_id: [],
        _entries={},
        _save=lambda: None,
    )
    runner._get_or_create_gateway_honcho = lambda session_key: (None, None)
    runner._is_session_run_current = lambda session_key, run_generation: True
    runner._consume_pending_native_image_paths = lambda session_key: []
    return runner


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="user-1",
    )


def _controlled_report(final_response_text: str = "safe controlled reply"):
    pipeline_id = "engineering_review_pipeline"
    return SimpleNamespace(
        state=SimpleNamespace(
            pipeline_id=pipeline_id,
            selected_pipeline_id=pipeline_id,
            router_status="selected",
        ),
        execution_report=SimpleNamespace(executed=True, execution_mode="controlled_manual"),
        pipeline_execution_controller=SimpleNamespace(
            actual_execution_invoked=True,
            execution_mode="controlled_manual",
            final_response_text=final_response_text,
            completion_allowed=True,
            blocked_reason=None,
            report_artifacts=None,
        ),
    )


_CONFIG = {
    "pipelines": {
        "enabled": True,
        "orchestrator": {"mode": "controlled_manual"},
        "execution": {"mode": "controlled_manual"},
    }
}


def _patch_gateway_env(monkeypatch, tmp_path):
    _install_fake_agent(monkeypatch)
    _CapturingAgent.run_calls = []

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_env_path", tmp_path / ".env")
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: _CONFIG)
    monkeypatch.setattr(gateway_run, "_load_gateway_runtime_config", lambda: _CONFIG)
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda config=None: "gpt-5.4")
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "***",
        },
    )

    import hermes_cli.tools_config as tools_config

    monkeypatch.setattr(tools_config, "_get_platform_tools", lambda user_config, platform_key: {"core"})


def _install_observe_double(monkeypatch, fake):
    orchestrator = importlib.import_module("hermes_cli.orchestrator")
    monkeypatch.setattr(orchestrator, "observe_gateway_turn", fake)


async def _run_agent(runner):
    return await runner._run_agent(
        message="HERMES CONTROLLED PIPELINE VALIDATION - run controlled engineering e2e dry-run",
        context_prompt="",
        history=[],
        source=_make_source(),
        session_id="session-1",
        session_key="agent:main:telegram:dm:12345",
    )


@pytest.mark.asyncio
async def test_observe_gateway_turn_runs_off_the_event_loop_thread(monkeypatch, tmp_path):
    _patch_gateway_env(monkeypatch, tmp_path)
    runner = _make_runner()
    seen: dict = {}

    def _fake_observe(**kwargs):
        seen["thread"] = threading.current_thread()
        return _controlled_report()

    _install_observe_double(monkeypatch, _fake_observe)

    loop_thread = threading.current_thread()
    result = await _run_agent(runner)

    assert result["final_response"] == "safe controlled reply"
    assert seen["thread"] is not loop_thread, (
        "observe_gateway_turn выполнился на потоке цикла событий -- "
        "весь пайплайновый ход снова блокирует гейтвей"
    )


@pytest.mark.asyncio
async def test_event_loop_is_served_while_the_pipeline_turn_runs(monkeypatch, tmp_path):
    """Пока идёт ход, цикл обслуживает другую работу (сообщение в другой чат)."""

    _patch_gateway_env(monkeypatch, tmp_path)
    runner = _make_runner()
    turn_started = threading.Event()
    loop_ticked = threading.Event()
    seen: dict = {}

    def _fake_observe(**kwargs):
        turn_started.set()
        # Настоящий признак живого цикла -- не «корутина создалась», а
        # «цикл дошёл до её продолжения после sleep», то есть отработал
        # и колбэки, и таймеры.
        seen["loop_served_other_work"] = loop_ticked.wait(timeout=_LOOP_TICK_TIMEOUT)
        return _controlled_report()

    _install_observe_double(monkeypatch, _fake_observe)

    async def _other_chat_work():
        # Ждём начала хода, не блокируя цикл, затем делаем настоящий await.
        while not turn_started.is_set():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        loop_ticked.set()

    other = asyncio.create_task(_other_chat_work())
    result = await _run_agent(runner)
    await other

    assert result["final_response"] == "safe controlled reply"
    assert seen["loop_served_other_work"] is True, (
        f"цикл не обслужил другую работу за {_LOOP_TICK_TIMEOUT} с, пока шёл "
        "пайплайновый ход -- значит ход всё ещё держит поток цикла"
    )


@pytest.mark.asyncio
async def test_session_contextvars_reach_the_worker_thread(monkeypatch, tmp_path):
    """ContextVars переезжают в рабочий поток.

    Апрув опасных команд в гейтвее держится на ContextVars (`tools.approval`,
    `gateway.session_context`), а не на потоко-локальных колбэках. Голый поток
    стартует с пустым контекстом, и `check_dangerous_command` уходит в
    неинтерактивную ветку автоаппрува. Перенос обязан идти через
    `copy_context()`.
    """

    _patch_gateway_env(monkeypatch, tmp_path)
    runner = _make_runner()
    seen: dict = {}

    def _fake_observe(**kwargs):
        seen["probe"] = _probe_var.get()
        return _controlled_report()

    _install_observe_double(monkeypatch, _fake_observe)

    _probe_var.set("session-scoped-value")
    await _run_agent(runner)

    assert seen["probe"] == "session-scoped-value", (
        "ContextVars не доехали до рабочего потока -- апрув опасных команд "
        "провалится в неинтерактивную ветку"
    )


@pytest.mark.asyncio
async def test_observe_failure_in_the_worker_thread_is_still_swallowed(monkeypatch, tmp_path):
    """Падение оркестратора в чужом потоке остаётся мягким.

    До переноса исключение ловил `except Exception` вокруг прямого вызова.
    После переноса оно приезжает через future -- ловить обязано то же место,
    иначе сломавшийся пайплайн станет убивать обычный разговорный ход.
    """

    _patch_gateway_env(monkeypatch, tmp_path)
    runner = _make_runner()

    def _fake_observe(**kwargs):
        raise RuntimeError("orchestrator exploded")

    _install_observe_double(monkeypatch, _fake_observe)

    result = await _run_agent(runner)

    # Пайплайн не дал отчёта -> ход идёт обычным разговорным маршрутом.
    assert result["final_response"] == "normal agent reply"
    assert len(_CapturingAgent.run_calls) == 1
