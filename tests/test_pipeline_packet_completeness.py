"""Полнота пакета вычисляется, а не угадывается ревьюером.

Правило `undescribed_changed_file` (2026-07-22) требует у каждого изменённого
файла непустое описание. Свойство вычислимо из git-дельты и `changes[]`, но до
2026-08-12 его проверял LLM-ревьюер, и каждая находка стоила раунда из трёх.
"""
from __future__ import annotations

from hermes_cli.pipeline_packet_completeness import evaluate_packet_completeness


def test_a_described_file_is_complete():
    result = evaluate_packet_completeness(
        changed_files=["plugins/platforms/telegram/adapter.py"],
        changes=[
            {
                "path": "plugins/platforms/telegram/adapter.py",
                "summary": "Отдельный таймаут и ограниченный retry для входящих медиа.",
            }
        ],
    )
    assert result.status == "complete"
    assert result.undescribed_paths == []


def test_a_changed_file_with_no_entry_is_incomplete():
    result = evaluate_packet_completeness(
        changed_files=["a.py", "b.py"],
        changes=[{"path": "a.py", "summary": "Описано."}],
    )
    assert result.status == "incomplete"
    assert result.undescribed_paths == ["b.py"]


def test_an_empty_summary_counts_as_missing():
    result = evaluate_packet_completeness(
        changed_files=["a.py"],
        changes=[{"path": "a.py", "summary": "   "}],
    )
    assert result.undescribed_paths == ["a.py"]


def test_a_summary_the_sanitizer_eats_counts_as_missing():
    # Санитайзер пакета выбрасывает строку целиком, если в ней есть подстрока
    # token/secret/env/credential или маркер диффа. Такое описание доедет до
    # ревьюера пустым, значит для полноты его нет -- проверять надо
    # пост-санитайзерное значение, а не то, что написал инженер.
    result = evaluate_packet_completeness(
        changed_files=["a.py"],
        changes=[{"path": "a.py", "summary": "Читаем GITHUB_TOKEN из окружения."}],
    )
    assert result.undescribed_paths == ["a.py"]


def test_paths_are_normalized_before_comparison():
    result = evaluate_packet_completeness(
        changed_files=["a.py"],
        changes=[{"path": "./a.py", "summary": "Описано."}],
    )
    assert result.status == "complete"


def test_a_malformed_changes_payload_is_not_a_crash():
    result = evaluate_packet_completeness(changed_files=["a.py"], changes="не список")
    assert result.status == "incomplete"
    assert result.undescribed_paths == ["a.py"]


def test_no_changed_files_is_complete():
    result = evaluate_packet_completeness(changed_files=[], changes=[])
    assert result.status == "complete"
