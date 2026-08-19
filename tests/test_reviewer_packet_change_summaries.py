"""Описание файла обязано доживать до ревьюера.

Правило `undescribed_changed_file` (2026-07-22) требует у каждого изменённого
файла запись в `changes` с непустым `summary` -- оператор видит именно эти
описания в запросе на коммит. А `_sanitize_changes` собирал запись из `path` и
`kind`, отбрасывая `summary`, так что ревьюер не мог увидеть его НИКОГДА, что бы
инженер ни написал.

2026-07-29, два прогона подряд: инженер менял файлы, тесты проходили, ревьюер
трижды требовал описания, раунды доработки заканчивались блокировкой. Требование
было невыполнимым по построению, а не по небрежности инженера.

Второй дефект той же функции: запись без `kind` выбрасывалась целиком. Осмысленный
минимум для ревьюера -- это путь и объяснение; вид изменения полезен, но его
отсутствие не повод скрыть от ревьюера описание.
"""
from __future__ import annotations

from hermes_cli.pipeline_reviewer_packet import (
    _sanitize_changes,
    _sanitize_engineer_output_payload,
)


def test_the_summary_reaches_the_reviewer():
    entries = _sanitize_changes([
        {"path": "scripts/x.py", "kind": "modified", "summary": "вернул удалённую строку"}
    ])

    assert entries == [
        {"path": "scripts/x.py", "kind": "modified", "summary": "вернул удалённую строку"}
    ]


def test_an_entry_without_kind_is_kept_because_the_summary_is_what_matters():
    entries = _sanitize_changes([{"path": "scripts/x.py", "summary": "почему изменено"}])

    assert len(entries) == 1
    assert entries[0]["path"] == "scripts/x.py"
    assert entries[0]["summary"] == "почему изменено"


def test_an_entry_without_a_path_is_useless_and_dropped():
    assert _sanitize_changes([{"kind": "modified", "summary": "нечего сопоставить"}]) == []


def test_an_entry_with_a_path_but_no_summary_is_still_carried():
    # Ревьюер обязан увидеть, что файл заявлен без объяснения, и сказать об этом.
    # Молча выбросить запись -- значит показать ему то же, что и полное молчание.
    entries = _sanitize_changes([{"path": "scripts/x.py", "kind": "modified"}])

    assert entries == [{"path": "scripts/x.py", "kind": "modified"}]


def test_a_long_summary_is_truncated_not_dropped():
    entries = _sanitize_changes([{"path": "a.py", "summary": "и" * 5000}])

    assert len(entries) == 1
    assert 0 < len(entries[0]["summary"]) < 5000


def test_non_mappings_and_junk_are_ignored():
    assert _sanitize_changes([None, "x", 5, {"path": "", "summary": ""}]) == []
    assert _sanitize_changes("not a list") == []
    assert _sanitize_changes(None) == []


def test_the_engineer_payload_the_reviewer_sees_carries_the_descriptions():
    payload = _sanitize_engineer_output_payload({
        "status": "succeeded",
        "summary": "починил",
        "validation_status": "valid",
        "changes": [{"path": "scripts/x.py", "kind": "modified", "summary": "вернул строку"}],
    })

    assert payload["changes"][0]["summary"] == "вернул строку"


def test_nine_changed_files_all_reach_the_reviewer():
    # Потолок в восемь записей делал полноту недостижимой: девятый файл не имел
    # шанса получить описание, что бы инженер ни написал.
    from hermes_cli.pipeline_reviewer_packet import _sanitize_engineer_output_payload

    payload = {
        "status": "succeeded",
        "changes": [
            {"path": f"file_{index}.py", "summary": f"Правка номер {index}."}
            for index in range(9)
        ],
    }
    sanitized = _sanitize_engineer_output_payload(payload)
    assert len(sanitized["changes"]) == 9
    assert "changes_truncated" not in sanitized


def test_truncation_is_declared_and_not_silent():
    from hermes_cli.pipeline_reviewer_packet import _sanitize_engineer_output_payload

    payload = {
        "status": "succeeded",
        "changes": [
            {"path": f"file_{index}.py", "summary": f"Правка номер {index}."}
            for index in range(70)
        ],
    }
    sanitized = _sanitize_engineer_output_payload(payload)
    assert len(sanitized["changes"]) == 64
    assert sanitized["changes_truncated"] == 6
