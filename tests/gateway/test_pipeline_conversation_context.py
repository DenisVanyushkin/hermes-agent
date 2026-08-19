"""Обрезка контекста обязана называть себя.

Прогон d61985bf (2026-07-29): инженеру передали утренний отчёт, обрезанный до
1500 символов на сообщении. Обрыв пришёлся на «Память:», инженер догадался о
неполноте по оборванному тексту и честно заблокировался, попросив «текст после
Памя…». Догадка сработала — но она была догадкой: в переданном тексте ничто не
сообщало, что он неполон.

Вторая обрезка была ещё тише: общий лимит оставлял ПОСЛЕДНИЕ 6000 символов,
молча выбрасывая начало разговора.
"""
from __future__ import annotations

import importlib

run = importlib.import_module("gateway.run")
_context = run._pipeline_conversation_context


def test_a_short_message_is_passed_through_untouched():
    ctx = _context([{"role": "user", "content": "почини проблемы"}])

    assert ctx == "user: почини проблемы"


def test_a_long_message_says_how_much_was_cut():
    text = "П" * 9000
    ctx = _context([{"role": "user", "content": text}])

    assert "truncated" in ctx.lower()
    assert any(ch.isdigit() for ch in ctx), "число отброшенных символов обязано быть названо"
    assert len(ctx) < 9000


def test_dropping_older_messages_is_stated_too():
    history = [{"role": "user", "content": "A" * 3000} for _ in range(8)]
    ctx = _context(history)

    assert "omitted" in ctx.lower() or "truncated" in ctx.lower()


def test_roles_other_than_user_and_assistant_are_skipped():
    ctx = _context([
        {"role": "system", "content": "секрет"},
        {"role": "user", "content": "видно"},
    ])

    assert "секрет" not in ctx
    assert "видно" in ctx


def test_tool_rows_do_not_crow_dialogue_out_of_recent_window():
    history = [
        {"role": "assistant", "content": "полный согласованный план"},
        *({"role": "tool", "content": f"tool-{index}"} for index in range(8)),
    ]

    ctx = _context(history)

    assert ctx == "assistant: полный согласованный план"


def test_structured_content_is_flattened():
    ctx = _context([{"role": "user", "content": [{"text": "часть один"}, {"text": "часть два"}]}])

    assert "часть один" in ctx
    assert "часть два" in ctx


def test_empty_history_yields_nothing():
    assert _context([]) is None
    assert _context(None) is None
    assert _context([{"role": "user", "content": "   "}]) is None
