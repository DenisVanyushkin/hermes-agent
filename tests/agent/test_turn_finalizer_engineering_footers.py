"""Инженерные футеры не уходят на канал конечного пользователя.

Наблюдение 2026-08-02: на просьбу добавить продукты в список покупок Амине
пришёл блок «Где выполнялись проверки» с путями внутри контейнера -- длиннее,
чем сам ответ. Триггер блока -- не «инженерный ход», а факт вызова `terminal`,
через который идёт весь бытовой CRUD.
"""

import json
from types import SimpleNamespace

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

    def _format_turn_completion_explanation(self, reason):
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
