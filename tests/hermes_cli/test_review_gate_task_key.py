"""Долг ревью ключуется по задаче, а не по прогону.

Разбор 2026-07-28: раунды доработки шли новыми сессиями (20260728_110558,
_110922, _111538), каждая грузила пустой ledger и честно рапортовала «долгов
нет», пока пять возражений от сессии _101519 висели неразрешёнными -- в том
числе в момент, когда работа отдавалась владельцу со словами «исправил
замечания review».
"""
import json

from hermes_cli.review_gate import ReviewGateState, evaluate_review_requirement


def test_debt_survives_a_new_run_of_the_same_task(tmp_path):
    first = ReviewGateState(task_key="agent:main:slack:group:C0B3JFDM6NB:1778823633.1")
    first.record_verdict(
        "changes_requested",
        changed_paths=["job_intel/cli.py"],
        findings=["выровнять внешний таймаут с внутренним бюджетом"],
    )
    first.save(tmp_path)

    # Следующий раунд доработки -- другой прогон, тот же чат и та же задача.
    second = ReviewGateState.load("agent:main:slack:group:C0B3JFDM6NB:1778823633.1", tmp_path)

    assert second.outstanding_paths == ["job_intel/cli.py"]
    assert evaluate_review_requirement(second).reason == "unresolved_changes_requested"


def test_a_different_task_does_not_inherit_the_debt(tmp_path):
    state_a = ReviewGateState(task_key="chat-a")
    state_a.record_verdict("changes_requested", changed_paths=["a.py"], findings=["про a"])
    state_a.save(tmp_path)

    assert ReviewGateState.load("chat-b", tmp_path).outstanding_paths == []


def test_findings_are_scoped_to_the_path_they_belong_to(tmp_path):
    state = ReviewGateState(task_key="chat-a")
    state.record_verdict(
        "changes_requested",
        changed_paths=["a.py", "b.py"],
        findings_by_path={"a.py": ["только про a"], "b.py": ["только про b"]},
    )

    # Прежнее поведение вешало все находки на каждый путь: 5 находок по трём
    # файлам превращались в 15 записей, и по ledger'у нельзя было понять,
    # к чему относится претензия.
    assert state.outstanding["a.py"] == ["только про a"]
    assert state.outstanding["b.py"] == ["только про b"]


def test_findings_without_a_per_path_split_still_apply_to_every_path(tmp_path):
    state = ReviewGateState(task_key="chat-a")
    state.record_verdict(
        "changes_requested", changed_paths=["a.py", "b.py"], findings=["общая находка"]
    )

    assert state.outstanding["a.py"] == ["общая находка"]
    assert state.outstanding["b.py"] == ["общая находка"]


def test_approving_one_path_does_not_discharge_another(tmp_path):
    state = ReviewGateState(task_key="chat-a")
    state.record_verdict(
        "changes_requested", changed_paths=["a.py", "b.py"], findings=["общая находка"]
    )
    state.record_verdict("approved", changed_paths=["a.py"])

    assert state.outstanding_paths == ["b.py"]


def test_a_legacy_file_written_with_the_session_key_is_still_readable(tmp_path):
    # Файлы на диске писались с ключом "session". Прочитать их обязаны:
    # нечитаемый файл долга = молча списанное возражение ревьюера.
    (tmp_path / "legacy-task.json").write_text(
        json.dumps({"session": "legacy-task", "outstanding": {"c.py": ["старая находка"]}}),
        encoding="utf-8",
    )

    state = ReviewGateState.load("legacy-task", tmp_path)

    assert state.outstanding == {"c.py": ["старая находка"]}
    assert state.task_key == "legacy-task"


def test_debt_key_prefers_the_stable_per_chat_key():
    from hermes_cli.review_gate import debt_key

    assert debt_key(
        gateway_session_key="agent:main:slack:group:C0B3:1778823633.1",
        session_id="20260728_110558_818767",
    ) == "agent:main:slack:group:C0B3:1778823633.1"


def test_debt_key_falls_back_to_the_run_id_when_there_is_no_gateway():
    from hermes_cli.review_gate import debt_key

    assert debt_key(gateway_session_key=None, session_id="20260728_110558_818767") == (
        "20260728_110558_818767"
    )
    assert debt_key(gateway_session_key="   ", session_id="run-1") == "run-1"


def test_debt_key_never_returns_empty():
    from hermes_cli.review_gate import debt_key

    assert debt_key() == "task"
