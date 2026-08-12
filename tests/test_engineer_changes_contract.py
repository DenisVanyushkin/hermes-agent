"""Инженер и ревьюер обязаны говорить одно и то же про `changes`.

2026-07-29, прогон в 18:03: инженер приложил `findings` без `changes` -- строго
по своему контракту, где `changes` лежит в optional_fields, а заметка гласит
«include at least one of findings or changes». Ревьюер обязан такой файл
заблокировать: правило `undescribed_changed_file` заведено 2026-07-22, чтобы
оператор не одобрял коммит, содержимое которого никто не описал.

Три раунда доработки ушли впустую: инженеру неоткуда было узнать, что надо
иначе -- его собственная инструкция разрешала так. Тупик по построению для
любого прогона, который меняет файлы.

Правило ревьюера намеренное, поэтому сходятся на нём. Тест держит оба документа
вместе: разойтись молча они больше не могут.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINEER_SPEC = REPO_ROOT / "config" / "subagents" / "hermes_engineer_core.yaml"
ENGINEER_PROMPT = REPO_ROOT / "prompts" / "subagents" / "hermes_engineer_core.md"
REVIEWER_PROMPT = REPO_ROOT / "prompts" / "subagents" / "hermes_code_reviewer.md"

FINDING_CODE = "undescribed_changed_file"


def _contract_notes() -> str:
    spec = yaml.safe_load(ENGINEER_SPEC.read_text(encoding="utf-8"))
    return "\n".join(str(note) for note in spec["output_schema"].get("contract_notes") or [])


def test_the_reviewer_still_blocks_an_undescribed_changed_file():
    # Если это правило когда-нибудь снимут, проверки ниже станут бессмысленными,
    # и тест должен упасть первым, а не тихо охранять пустоту.
    assert FINDING_CODE in REVIEWER_PROMPT.read_text(encoding="utf-8")


def test_the_engineer_contract_requires_changes_for_changed_files():
    notes = _contract_notes().lower()
    assert "changes is required" in notes
    assert "changed path" in notes


def test_the_engineer_prompt_says_the_same_thing():
    text = ENGINEER_PROMPT.read_text(encoding="utf-8").lower()
    assert "`changes` is required" in text
    assert FINDING_CODE in text


def test_the_engineer_is_told_why_and_not_merely_ordered():
    # Требование без причины -- первое, что выкидывают при переписывании
    # промпта. Причина здесь и есть содержание: оператор видит именно эти
    # описания в запросе на коммит.
    for source in (_contract_notes(), ENGINEER_PROMPT.read_text(encoding="utf-8")):
        assert "operator" in source.lower()


def test_the_permissive_note_no_longer_stands_alone():
    notes = _contract_notes().lower()
    assert "at least one of findings or changes" in notes, "мягкое правило остаётся верным, когда файлов не меняли"
    assert notes.index("at least one of findings or changes") < notes.index("changes is required"), (
        "уточнение обязано идти после общего правила, иначе читается как противоречие"
    )


def test_the_reviewer_stands_down_after_the_system_already_tried():
    # Полноту пакета теперь проверяет код и сам просит инженера её закрыть.
    # Если бюджет починки исчерпан, повторная находка ревьюера стоила бы раунда
    # за то, на что раунды уже потрачены.
    text = REVIEWER_PROMPT.read_text(encoding="utf-8")
    assert "incomplete_after_repair" in text
