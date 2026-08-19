"""Оператору видны все раунды, а не последний.

12.08 прогон умер с двумя формальными замечаниями на экране, тогда как первым
замечанием первого раунда была настоящая ошибка -- классификация исключений по
имени класса. Показ одного последнего раунда искажает причину провала.
"""
from __future__ import annotations

from hermes_cli.pipeline_rework_loop import ReworkLoopIterationRecord, render_round_chronology


def _record(index, findings, status="needs_review"):
    return ReworkLoopIterationRecord(
        iteration_index=index,
        engineer_message="",
        reviewer_message="",
        engineer_runner_status="succeeded",
        reviewer_runner_status="succeeded",
        engineer_evaluation_status="succeeded",
        reviewer_evaluation_status=status,
        reviewer_blockers=[],
        loop_limit_snapshot={},
        reviewer_findings=findings,
    )


def test_no_history_renders_nothing():
    assert render_round_chronology(
        iteration_history=[], packet_repair_history=[], rounds_exhausted=False
    ) == []


def test_a_single_approved_round_is_one_line():
    lines = render_round_chronology(
        iteration_history=[_record(1, [], status="approved")],
        packet_repair_history=[],
        rounds_exhausted=False,
    )
    assert lines[0] == "━━ Как шла работа ━━"
    assert lines[1] == "Раунд 1 · одобрено"


def test_findings_are_listed_and_cleared_ones_counted():
    history = [
        _record(1, [
            {"code": "exception_classification", "summary": "классификация по имени класса"},
            {"code": "missing_test", "summary": "нет теста на permanent error"},
        ]),
        _record(2, [
            {"code": "exception_classification", "summary": "классификация по имени класса"},
        ]),
    ]
    lines = render_round_chronology(
        iteration_history=history, packet_repair_history=[], rounds_exhausted=True
    )
    text = "\n".join(lines)
    assert "Раунд 1 · ревьюер: доработка — 2 замечания" in text
    assert "  • классификация по имени класса" in text
    assert "Раунд 2 · ревьюер: доработка — 1 замечание (снято 1)" in text
    assert "раунды исчерпаны" in text


def test_a_repair_round_is_marked_as_not_costing_a_round():
    lines = render_round_chronology(
        iteration_history=[_record(1, [], status="approved")],
        packet_repair_history=[{"attempt": 1, "undescribed_paths": ["adapter.py"]}],
        rounds_exhausted=False,
    )
    text = "\n".join(lines)
    assert "Починка пакета ×1 (раунд не тратится): adapter.py" in text
