"""The research cheap-path heuristic has to work in the language it is asked in.

_SIMPLE_RESEARCH_HINTS was an English word list. This operator writes Russian, so
nothing ever matched and every research turn took the reasoning tier — the cheap
half of the heuristic was dead for them.

The test phrasings below are taken from the real gateway log, not invented.

Both lists have to become bilingual together. Adding only the simple hints would
introduce a new bug: "сделай дайджест с анализом противоречивых источников" would
match "дайджест" and take the cheap path, because the complex list — which is
checked first and wins — still only speaks English.
"""
import pytest

from hermes_cli.model_selection import _research_prefers_fast_lookup


# ── Real phrasings from the log ─────────────────────────────────────────────

@pytest.mark.parametrize("task", [
    "найди мне какая организационная структура в Kolesa? там есть CPO или Head of product",
    "проверь, почему не было такого дайджеста сегодня",
    "я каждый день получаю одну и ту же историю в этом дайджесте",
    "мне только заголовков мало, добавь краткий саммари статьи",
])
def test_real_russian_lookups_take_the_cheap_path(task):
    assert _research_prefers_fast_lookup(task) is True


# ── The two lists must stay in step ─────────────────────────────────────────

def test_a_complex_russian_request_beats_a_simple_russian_word():
    """The bug that adding only the simple list would have created."""
    assert _research_prefers_fast_lookup(
        "сделай дайджест с анализом противоречивых источников"
    ) is False


def test_russian_deep_research_is_not_a_lookup():
    assert _research_prefers_fast_lookup("проведи глубокое исследование рынка") is False
    assert _research_prefers_fast_lookup(
        "сопоставь данные из нескольких источников и сделай выводы"
    ) is False


@pytest.mark.parametrize("task", [
    "какая погода в Алматы",
    "покажи новости за сегодня",
    "сравни две вакансии",
    "узнай комиссии биржи",
])
def test_ordinary_russian_lookups(task):
    assert _research_prefers_fast_lookup(task) is True


# ── English behaviour is unchanged ──────────────────────────────────────────

def test_english_still_works_exactly_as_before():
    assert _research_prefers_fast_lookup("what is the weather in Almaty") is True
    assert _research_prefers_fast_lookup("give me the news digest") is True
    assert _research_prefers_fast_lookup(
        "deep research: synthesize conflicting sources into a brief"
    ) is False


def test_an_empty_task_is_still_treated_as_a_lookup():
    assert _research_prefers_fast_lookup("") is True
    assert _research_prefers_fast_lookup(None) is True


def test_an_unrecognised_request_still_gets_the_reasoning_tier():
    """No match in either list keeps the conservative default."""
    assert _research_prefers_fast_lookup("расскажи про устройство рынка труда") is False


# ── ё is not a different letter for matching purposes ───────────────────────

def test_yo_and_ye_spellings_both_match():
    """Otherwise every ё-word needs listing twice and one spelling silently misses."""
    assert _research_prefers_fast_lookup("сделай отчёт по вакансиям") is True
    assert _research_prefers_fast_lookup("сделай отчет по вакансиям") is True
