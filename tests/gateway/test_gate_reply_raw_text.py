"""Гейты обязаны читать то, что напечатал оператор, а не то, что собрано для модели.

Прогон 1947fc44 (2026-07-29): инженерный пайплайн попросил апрув на коммит, оператор
ответил в треде словом «коммить» — и ничего не произошло, дважды. Разбор: когда ответ
идёт реплаем, гейтвей приклеивает спереди блок дизамбигуации (`gateway/run.py`,
`[Replying to: "<до 500 символов цитаты>"]`), и до интерцептов доезжает уже склейка.
Три из четырёх гейт-парсеров сверяют СООБЩЕНИЕ ЦЕЛИКОМ (лимит длины / точное
равенство), поэтому склейка их глушит — молча, без исключения и без строки в логе.

Четвёртый, upstream-sync, устроен наоборот: он сканирует текст регуляркой и потому
вычитывает решения ИЗ ЦИТАТЫ — то есть из отчёта, на который отвечают. Оператор
печатает одно, гейт применяет другое.

Инъекция сделана для модели: она подсказывает, на какое сообщение отвечают. Парсер
гейта моделью не является. Отсюда правило: интерцепты получают сырой `event.text`.
"""
from __future__ import annotations

import contextlib
import types

import pytest

from gateway.run import GatewayRunner, _operator_reply_text
from hermes_cli.baseline_doctor_service import parse_doctor_command
from hermes_cli.commit_gate_service import parse_commit_reply
from hermes_cli.ops_gate_service import parse_ops_reply
from hermes_cli.upstream_sync_reply import parse_upstream_sync_decision_reply

#: Реальная форма цитаты из `gateway/run.py` (ветка reply_to_text), сокращённая.
_QUOTE = "Cronjob Response: morning-diagnostics-report — отчёт за сутки, 7 пунктов"


def _as_reply(word: str, quote: str = _QUOTE) -> str:
    return f'[Replying to: "{quote}"]\n\n{word}'


def _source(platform="telegram", user_id="U123"):
    return types.SimpleNamespace(platform=platform, user_id=user_id)


@pytest.mark.parametrize(
    "parser, word",
    [
        (parse_commit_reply, "коммить"),
        (parse_ops_reply, "выполни"),
        (parse_doctor_command, "почисти"),
    ],
)
def test_a_quoted_reply_hides_the_operator_word_from_whole_message_parsers(parser, word):
    """Почему сырой текст обязателен: склейка глушит парсер, который смотрит на всё сообщение."""
    assert parser(word) is not None, "сырое слово обязано распознаваться"
    assert parser(_as_reply(word)) is None, (
        "склейка перестала глушить парсер — если лимит длины ослабили, "
        "то цитируемая речь снова может поднять гейт"
    )


def test_a_quoted_reply_feeds_the_scanning_parser_decisions_nobody_typed():
    """Обратная сторона: upstream-sync вычитывает решения из цитаты отчёта."""
    quoted_report = "Конфликты: 2: merge-both, 3: keep-local"

    assert parse_upstream_sync_decision_reply(_as_reply("1: take-upstream", quoted_report)) == {
        1: "take upstream",
        2: "merge both",
        3: "keep local",
    }, "цитата больше не парсится — тогда этот тест надо переписать под новое поведение"

    assert parse_upstream_sync_decision_reply("1: take-upstream") == {1: "take upstream"}


def test_the_operator_text_is_preferred_over_the_assembled_one():
    assert _operator_reply_text(_as_reply("коммить"), "коммить") == "коммить"


def test_the_assembled_text_is_used_when_there_is_no_raw_one():
    composed = _as_reply("коммить")

    assert _operator_reply_text(composed, None) == "коммить"
    assert _operator_reply_text(composed, "") == "коммить"
    assert _operator_reply_text(composed, "   ") == "коммить"


@pytest.mark.asyncio
async def test_run_agent_hands_the_raw_text_down_to_the_intercepts():
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = types.SimpleNamespace(multiplex_profiles=False)
    captured: dict = {}

    async def _inner(message, context_prompt, history, source, session_id, **kwargs):
        captured["message"] = message
        captured["raw_message"] = kwargs.get("raw_message")
        return {"final_response": "ok"}

    runner._run_agent_inner = _inner
    composed = _as_reply("коммить")

    await runner._run_agent(
        message=composed,
        context_prompt="",
        history=[],
        source=_source(),
        session_id="sess-1",
        raw_message="коммить",
    )

    assert captured["message"] == composed, "модель по-прежнему получает склейку"
    assert captured["raw_message"] == "коммить"


@pytest.mark.asyncio
async def test_a_turn_without_a_raw_text_still_runs():
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = types.SimpleNamespace(multiplex_profiles=False)
    captured: dict = {}

    async def _inner(message, context_prompt, history, source, session_id, **kwargs):
        captured["raw_message"] = kwargs.get("raw_message", "absent")
        return {"final_response": "ok"}

    runner._run_agent_inner = _inner

    await runner._run_agent(
        message="привет", context_prompt="", history=[], source=_source(), session_id="sess-1"
    )

    assert captured["raw_message"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("multiplex_profiles", [False, True])
async def test_run_agent_preserves_internal_event_identity(
    monkeypatch, multiplex_profiles
):
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = types.SimpleNamespace(multiplex_profiles=multiplex_profiles)
    captured: dict = {}

    async def _inner(message, context_prompt, history, source, session_id, **kwargs):
        captured["internal_event"] = kwargs.get("internal_event", "absent")
        return {"final_response": "ok"}

    runner._run_agent_inner = _inner
    if multiplex_profiles:
        runner._resolve_profile_home_for_source = lambda _source: "/tmp/profile"
        monkeypatch.setattr(
            "gateway.run._profile_runtime_scope",
            lambda _profile_home: contextlib.nullcontext(),
        )

    await runner._run_agent(
        message="[ASYNC DELEGATION BATCH COMPLETE — deleg_test]",
        context_prompt="",
        history=[],
        source=_source(),
        session_id="sess-internal",
        internal_event=True,
    )

    assert captured["internal_event"] is True
