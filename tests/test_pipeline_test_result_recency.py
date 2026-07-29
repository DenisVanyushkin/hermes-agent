"""Повторный прогон той же команды перекрывает прошлый результат.

Прогон 7fd2db56 (2026-07-29): инженер запустил pytest, тот упёрся в 120-секундный
бюджет свежего worktree и вернулся таймаутом -- при том, что в stdout лежало
`18 passed in 2.94s`. Инженер повторил ТУ ЖЕ команду, она прошла за 36 секунд с
exit_code 0. Оба результата записаны в отчёте.

Пайплайн всё равно отчитался `blocked` по первому: цикл по вызовам инструментов
возвращал первый же pytest и дальше не смотрел. Зелёный результат был и был
выброшен, ревьюер не вызывался, оператор получил заглушку.

Берём последний результат ПО КАЖДОЙ КОМАНДЕ, а не последний вообще: иначе провал
широкой команды можно было бы спрятать, запустив следом узкую.
"""
from __future__ import annotations

import importlib

loop = importlib.import_module("hermes_cli.pipeline_rework_loop")

CMD = ["python", "-m", "pytest", "-q", "tests/scripts/test_nightly_diagnostics_collect.py"]
OTHER = ["python", "-m", "pytest", "-q", "tests/scripts/test_other.py"]


def _pytest_call(command, status, exit_code, *, blocked_reason=None):
    return {
        "tool_name": "pytest",
        "call_count": 1,
        "status": "succeeded" if status == "passed" else "failed",
        "result_payload": {
            "status": status,
            "blocked_reason": blocked_reason,
            "summary": blocked_reason or "1 test command passed",
            "results": [{"command": command, "status": status, "exit_code": exit_code}],
        },
    }


def test_a_rerun_of_the_same_command_supersedes_the_earlier_timeout():
    runner_result = {
        "tool_call_summaries": [
            _pytest_call(CMD, "timeout", None, blocked_reason="test_command_timeout"),
            _pytest_call(CMD, "passed", 0),
        ]
    }

    summary = loop._machine_captured_test_summary(runner_result)

    assert summary is not None
    assert summary["status"] == "passed"
    assert summary["blocked_reason"] is None
    assert summary["exit_code"] == 0


def test_a_later_narrower_command_cannot_hide_an_earlier_failure():
    runner_result = {
        "tool_call_summaries": [
            _pytest_call(CMD, "failed", 1, blocked_reason="test_command_failed"),
            _pytest_call(OTHER, "passed", 0),
        ]
    }

    summary = loop._machine_captured_test_summary(runner_result)

    assert summary is not None
    assert summary["status"] == "failed", "провал другой команды не перекрывается"
    assert summary["blocked_reason"] == "test_command_failed"


def test_a_single_passing_run_is_reported_as_passing():
    runner_result = {"tool_call_summaries": [_pytest_call(CMD, "passed", 0)]}

    summary = loop._machine_captured_test_summary(runner_result)

    assert summary["status"] == "passed"


def test_a_single_timeout_still_blocks():
    runner_result = {
        "tool_call_summaries": [
            _pytest_call(CMD, "timeout", None, blocked_reason="test_command_timeout")
        ]
    }

    summary = loop._machine_captured_test_summary(runner_result)

    assert summary["status"] == "timeout"
    assert summary["blocked_reason"] == "test_command_timeout"


def test_a_rerun_that_fails_again_still_blocks():
    runner_result = {
        "tool_call_summaries": [
            _pytest_call(CMD, "passed", 0),
            _pytest_call(CMD, "failed", 1, blocked_reason="test_command_failed"),
        ]
    }

    summary = loop._machine_captured_test_summary(runner_result)

    assert summary["status"] == "failed", "последний результат команды -- решающий"


def test_no_pytest_calls_means_nothing_captured():
    assert loop._machine_captured_test_summary({"tool_call_summaries": []}) is None
    assert loop._machine_captured_test_summary(None) is None
