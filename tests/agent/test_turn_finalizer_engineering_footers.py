"""Инженерные футеры не уходят на канал конечного пользователя.

Наблюдение 2026-08-02: на просьбу добавить продукты в список покупок Амине
пришёл блок «Где выполнялись проверки» с путями внутри контейнера -- длиннее,
чем сам ответ. Триггер блока -- не «инженерный ход», а факт вызова `terminal`,
через который идёт весь бытовой CRUD.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.turn_finalizer import finalize_turn
from hermes_cli.run_evidence import SUPPRESSED_TURN_NOTICE


class _FooterAgent:
    """Минимальный агент, который переживает finalize_turn на здоровом ходу."""

    def __init__(self, *, platform="whatsapp", failed_mutations=None):
        self.platform = platform
        self.max_iterations = 60
        self.iteration_budget = SimpleNamespace(remaining=50, used=10, max_total=60)
        self.quiet_mode = True
        self.model = "test-model"
        self.provider = "test-provider"
        self.base_url = ""
        self.session_id = "sess-test"
        self.context_compressor = SimpleNamespace(last_prompt_tokens=0)
        self.session_input_tokens = 0
        self.session_output_tokens = 0
        self.session_cache_read_tokens = 0
        self.session_cache_write_tokens = 0
        self.session_reasoning_tokens = 0
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self.session_estimated_cost_usd = 0
        self.session_cost_status = "unknown"
        self.session_cost_source = "test"
        self._tool_guardrail_halt_decision = None
        self._interrupt_message = None
        self._response_was_previewed = False
        self._skill_nudge_interval = 0
        self._iters_since_skill = 0
        self.valid_tool_names = []
        self.persisted_messages = None
        self._turn_failed_file_mutations = failed_mutations or {}

    def _handle_max_iterations(self, messages, api_call_count):
        return "summary from extra call"

    def _emit_status(self, *_args, **_kwargs):
        pass

    def _safe_print(self, *_args, **_kwargs):
        pass

    def _save_trajectory(self, *_args, **_kwargs):
        pass

    def _cleanup_task_resources(self, *_args, **_kwargs):
        pass

    def _drop_trailing_empty_response_scaffolding(self, messages):
        pass

    def _persist_session(self, messages, conversation_history):
        self.persisted_messages = list(messages)

    def _file_mutation_verifier_enabled(self):
        return True

    def _format_file_mutation_failure_footer(self, _failed):
        return "⚠️ Some files were not written: /workspace/live-hermes/x.py"

    def _turn_completion_explainer_enabled(self):
        return True

    # *_args: the finalizer now also passes the persistence-error cause. A
    # frozen signature raises TypeError inside the explainer's `except
    # Exception`, so the explanation silently becomes empty instead of failing
    # loudly — the tests below would then assert against a blank string.
    def _format_turn_completion_explanation(self, reason, *_args):
        if str(reason).startswith("text_response"):
            return ""
        return "⚠️ No reply: switch model/provider or send `continue`."

    def _drain_pending_steer(self):
        return None

    def clear_interrupt(self):
        pass

    def _sync_external_memory_for_turn(self, **_kwargs):
        pass


def _terminal_call(command):
    return {
        "role": "assistant",
        "tool_calls": [
            {"function": {"name": "terminal", "arguments": json.dumps({"command": command})}}
        ],
    }


SHOP_MESSAGES = [
    {"role": "user", "content": "добавь гречку в список покупок"},
    _terminal_call('/workspace/live-hermes/custom/fam/bin/fam shop add "гречка" --json'),
]


@pytest.fixture(autouse=True)
def _no_plugin_hooks(monkeypatch):
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_kw: [])


@pytest.fixture(autouse=True)
def _pristine_session_context(monkeypatch):
    """Ни платформа, ни аудитория крона не должны протекать между тестами.

    Возврат делается в сентинел `_UNSET` («никогда не выставлялась»), а не в
    `""`: пустая строка -- отдельное состояние «явно очищено», подавляющее
    fallback на os.environ.
    """
    monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
    monkeypatch.delenv("HERMES_CRON_AUDIENCE", raising=False)
    yield
    import gateway.session_context as sc

    for var in sc._VAR_MAP.values():
        var.set(sc._UNSET)


@pytest.fixture
def suppressed(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.run_evidence.engineering_footers_suppressed",
        lambda *_a, **_kw: True,
    )


@pytest.fixture
def not_suppressed(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.run_evidence.engineering_footers_suppressed",
        lambda *_a, **_kw: False,
    )


def _finalize(agent, *, final_response, exit_reason="text_response(done)", messages=None):
    return finalize_turn(
        agent,
        final_response=final_response,
        api_call_count=2,
        interrupted=False,
        failed=False,
        messages=list(messages if messages is not None else SHOP_MESSAGES),
        conversation_history=[],
        effective_task_id="task",
        turn_id="turn",
        user_message="добавь гречку в список покупок",
        original_user_message="добавь гречку в список покупок",
        _should_review_memory=False,
        _turn_exit_reason=exit_reason,
    )


def test_locus_block_is_dropped_on_a_suppressed_channel(suppressed):
    result = _finalize(_FooterAgent(), final_response="Добавил гречку 🛒")

    assert result["final_response"] == "Добавил гречку 🛒"
    assert "песочниц" not in result["final_response"]


def test_locus_block_survives_on_an_engineering_channel(not_suppressed):
    """Половина проверки, без которой первый тест ничего не доказывает:
    блок мог исчезнуть не из-за канала, а из-за поломки самого блока."""
    result = _finalize(_FooterAgent(platform="telegram"), final_response="Готово")

    assert "Где выполнялись проверки" in result["final_response"]
    assert "fam shop add" in result["final_response"]


def test_file_mutation_footer_is_dropped_on_a_suppressed_channel(suppressed):
    agent = _FooterAgent(failed_mutations={"/workspace/live-hermes/x.py": {"tool": "patch"}})

    result = _finalize(agent, final_response="Готово")

    assert result["final_response"] == "Готово"


def test_file_mutation_footer_survives_on_an_engineering_channel(not_suppressed):
    agent = _FooterAgent(
        platform="telegram",
        failed_mutations={"/workspace/live-hermes/x.py": {"tool": "patch"}},
    )

    result = _finalize(agent, final_response="Готово")

    assert "were not written" in result["final_response"]


def test_empty_turn_gets_a_plain_notice_instead_of_the_english_explainer(suppressed):
    result = _finalize(_FooterAgent(), final_response="", exit_reason="empty_response_exhausted")

    assert result["final_response"] == SUPPRESSED_TURN_NOTICE


def test_engineering_channel_keeps_the_english_explainer(not_suppressed):
    result = _finalize(
        _FooterAgent(platform="telegram"),
        final_response="",
        exit_reason="empty_response_exhausted",
    )

    assert "No reply" in result["final_response"]
    assert SUPPRESSED_TURN_NOTICE not in result["final_response"]


def test_empty_response_on_healthy_exit_stays_empty_on_a_suppressed_channel(suppressed):
    """Пустой `final_response` при штатном `_turn_exit_reason` (`text_response(...)`)
    -- это нормальный, тихий выход: форматтер объяснялки сам возвращает пустую
    строку на этом префиксе (см. `_FooterAgent._format_turn_completion_explanation`
    и настоящую реализацию в `agent`), и подставлять вместо неё
    `SUPPRESSED_TURN_NOTICE` было бы неверно -- ход не оборвался, отвечать
    было просто нечем. Гейт `if _explanation and _suppress_eng_footers:` в
    `finalize_turn` обязан пропускать пустую `_explanation`, не превращая её
    в извинение. Если условие ослабить до `if _suppress_eng_footers:`, этот
    тест краснеет (проверено вручную -- см. отчёт task-2-report.md)."""
    result = _finalize(_FooterAgent(), final_response="", exit_reason="text_response(stop)")

    assert result["final_response"] == ""
    assert SUPPRESSED_TURN_NOTICE not in result["final_response"]


# --- два оставшихся триггера объяснялки ------------------------------------
#
# Спека объявляет три: пустой ответ (покрыт выше), обрывок <= 24 символов без
# финальной пунктуации и `partial_stream_recovery`. Последние два ведут себя
# иначе -- обрывок СОХРАНЯЕТСЯ, а объяснялка приписывается к нему, поэтому
# «работает как первый» их не проверяет.

_FRAGMENT = "Сейчас посмотрю"

#: Ход без вызовов песочницы. Триггер «обрывок» меряет длину УЖЕ СОБРАННОГО
#: ответа, поэтому на инженерном канале приклеенный блок про песочницу сам
#: уводит его за 24 символа и гасит объяснялку. Чтобы пара тестов ниже
#: сравнивала каналы, а не длину, ход не должен ничего выполнять.
PLAIN_MESSAGES = [{"role": "user", "content": "как дела"}]


def test_a_short_fragment_is_kept_and_gets_the_plain_notice(suppressed):
    result = _finalize(
        _FooterAgent(),
        final_response=_FRAGMENT,
        exit_reason="stream_truncated",
        messages=PLAIN_MESSAGES,
    )

    assert result["final_response"] == _FRAGMENT + "\n\n" + SUPPRESSED_TURN_NOTICE


def test_a_short_fragment_keeps_the_english_explainer_on_an_engineering_channel(
    not_suppressed,
):
    result = _finalize(
        _FooterAgent(platform="telegram"),
        final_response=_FRAGMENT,
        exit_reason="stream_truncated",
        messages=PLAIN_MESSAGES,
    )

    assert result["final_response"].startswith(_FRAGMENT)
    assert "No reply" in result["final_response"]
    assert SUPPRESSED_TURN_NOTICE not in result["final_response"]


def test_partial_stream_recovery_gets_the_plain_notice(suppressed):
    """Третий триггер не про длину: ответ может быть полноценным и с точкой,
    решает сам `_turn_exit_reason`."""
    result = _finalize(
        _FooterAgent(),
        final_response="Готово.",
        exit_reason="partial_stream_recovery",
    )

    assert result["final_response"] == "Готово.\n\n" + SUPPRESSED_TURN_NOTICE


def test_partial_stream_recovery_keeps_the_english_explainer_on_an_engineering_channel(
    not_suppressed,
):
    result = _finalize(
        _FooterAgent(platform="telegram"),
        final_response="Готово.",
        exit_reason="partial_stream_recovery",
    )

    assert "No reply" in result["final_response"]
    assert SUPPRESSED_TURN_NOTICE not in result["final_response"]


# --- cron: футеры глушим, объяснялку не трогаем ----------------------------


def _bind_cron_audience(value):
    from gateway.session_context import _VAR_MAP

    _VAR_MAP["HERMES_CRON_AUDIENCE"].set(value)


def _no_suppress_config():
    """Реальный предикат с пустым списком платформ: подавить может только крон."""
    return patch(
        "hermes_cli.config.load_config_readonly",
        return_value={"display": {"suppress_engineering_footers_platforms": []}},
    )


def test_cron_end_user_turn_drops_the_locus_block():
    _bind_cron_audience("end_user")
    with _no_suppress_config():
        result = _finalize(_FooterAgent(platform="cron"), final_response="• завтра: 🌤️ 22…36°C")

    assert result["final_response"] == "• завтра: 🌤️ 22…36°C"
    assert "песочниц" not in result["final_response"]


def test_cron_operator_turn_keeps_the_locus_block():
    """Половина, без которой первый тест ничего не доказывает: гейт обязан
    смотреть на аудиторию, а не просто выключаться на всяком cron-ходе."""
    _bind_cron_audience("operator")
    with _no_suppress_config():
        result = _finalize(_FooterAgent(platform="cron"), final_response="Готово")

    assert "Где выполнялись проверки" in result["final_response"]


def test_cron_end_user_turn_keeps_the_explainer_text_verbatim():
    """Ловушка внутри подавления.

    `cron/scheduler.py` опознаёт аномально пустой ход сравнением НА РАВЕНСТВО
    с текстом того же форматтера:

        final_response.strip() == AIAgent._format_turn_completion_explanation(
            turn_exit_reason
        ).strip()

    и на совпадении обнуляет ответ, чтобы задание не доставило ничего. Подставь
    здесь `SUPPRESSED_TURN_NOTICE` -- равенство перестанет выполняться, и вместо
    тишины Амина получит извинение, а прогон будет помечен успешным. Тест
    воспроизводит ровно то сравнение, которое делает планировщик.
    """
    agent = _FooterAgent(platform="cron")
    _bind_cron_audience("end_user")
    with _no_suppress_config():
        result = _finalize(agent, final_response="", exit_reason="empty_response_exhausted")

    expected = agent._format_turn_completion_explanation("empty_response_exhausted")
    assert result["final_response"].strip() == expected.strip()
    assert SUPPRESSED_TURN_NOTICE not in result["final_response"]


def test_cron_end_user_fragment_is_not_rewritten_either():
    """Тот же запрет на втором триггере объяснялки: обрывок + английский футер.

    Сравнение планировщика тут не сработает в любом случае (ответ длиннее
    объяснялки), но подменять текст всё равно нельзя: подмена превратила бы
    инженерный хвост, который крон и так не доставляет целиком, в русское
    извинение внутри доставляемого ответа.
    """
    _bind_cron_audience("end_user")
    with _no_suppress_config():
        result = _finalize(
            _FooterAgent(platform="cron"),
            final_response=_FRAGMENT,
            exit_reason="stream_truncated",
        )

    assert "No reply" in result["final_response"]
    assert SUPPRESSED_TURN_NOTICE not in result["final_response"]


# --- проводка предиката ----------------------------------------------------


def _whatsapp_only_config():
    return patch(
        "hermes_cli.config.load_config_readonly",
        return_value={"display": {"suppress_engineering_footers_platforms": ["whatsapp"]}},
    )


def test_session_context_platform_outranks_the_agent_attribute_in_the_wiring():
    """Ловит подмену `fallback_platform=` на позиционный `platform=`.

    Остальные тесты файла патчат сам предикат, поэтому проводка в
    `finalize_turn` ими не проверяется вовсе: замена именованного аргумента на
    позиционный инвертировала бы задокументированный приоритет (атрибут агента
    начал бы побеждать контекст сессии) и оставила бы их все зелёными.

    Здесь предикат настоящий. Контекст сессии говорит `telegram`, атрибут
    агента -- `whatsapp`, подавляется только `whatsapp`. При правильной
    проводке побеждает контекст и блок остаётся; при позиционном аргументе
    победит атрибут агента, блок исчезнет и тест покраснеет.
    """
    from gateway.session_context import set_session_vars

    set_session_vars(platform="telegram", chat_id="79564752")
    with _whatsapp_only_config():
        result = _finalize(_FooterAgent(platform="whatsapp"), final_response="Готово")

    assert "Где выполнялись проверки" in result["final_response"]


def test_the_agent_attribute_is_used_when_the_session_context_is_empty():
    """Вторая половина: без контекста атрибут агента обязан РАБОТАТЬ.

    Без неё предыдущий тест проходил бы и на проводке, которая вообще
    перестала передавать платформу агента.
    """
    with _whatsapp_only_config():
        result = _finalize(_FooterAgent(platform="whatsapp"), final_response="Готово")

    assert result["final_response"] == "Готово"
