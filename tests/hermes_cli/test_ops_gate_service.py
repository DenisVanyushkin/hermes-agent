import time

import pytest

from hermes_cli import ops_gate_service


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def _record(created_at=None, session_id="s1", op_id="git_push"):
    return ops_gate_service.record_pending(
        session_id=session_id,
        repo_path="/repo",
        plan=[{"op_id": op_id, "risk": "mutate", "argv": ["git", "push", "origin", "main"]}],
        original_task="запушь текущую ветку в origin",
        created_at=created_at,
    )


def test_pending_round_trips(home):
    _record()
    pending = ops_gate_service.get_pending()
    assert pending["original_task"] == "запушь текущую ветку в origin"
    assert pending["plan"][0]["op_id"] == "git_push"


def test_recording_refuses_to_replace_an_unexpired_marker(home):
    """Маркер один. Тихо переписав чужой, прогон B подставил бы свой план под
    «выполни», адресованное плану A: оператор одобрил бы не то, что видел."""
    assert _record(session_id="s1", op_id="git_push") is True

    assert _record(session_id="s2", op_id="git_branch_delete") is False

    pending = ops_gate_service.get_pending()
    assert pending["session_id"] == "s1"
    assert pending["plan"][0]["op_id"] == "git_push"


def test_recording_replaces_an_expired_marker(home):
    # Протухший маркер уже неотвечаем -- занимать им слот незачем.
    _record(created_at=time.time() - ops_gate_service.PENDING_TTL_SECONDS - 1, session_id="old")

    assert _record(session_id="new") is True
    assert ops_gate_service.get_pending()["session_id"] == "new"


def test_pending_expires_after_the_ttl(home):
    _record(created_at=time.time() - ops_gate_service.PENDING_TTL_SECONDS - 1)
    # Одобрение, пролежавшее час, относится к другому состоянию репозитория.
    assert ops_gate_service.get_pending() is None


@pytest.mark.parametrize("text", ["выполни", "Выполни", "да, выполняй", "execute"])
def test_parser_recognizes_execute(text):
    assert ops_gate_service.parse_ops_reply(text) == "execute"


@pytest.mark.parametrize("text", ["отмена", "отмени", "cancel"])
def test_parser_recognizes_cancel(text):
    assert ops_gate_service.parse_ops_reply(text) == "cancel"


@pytest.mark.parametrize(
    "text",
    ["да", "ок", "спасибо, выполнил задачу вчера", "", "a" * 100],
)
def test_parser_ignores_ordinary_messages(text):
    # Узкий парсер: обычная переписка не должна поднимать гейт.
    assert ops_gate_service.parse_ops_reply(text) is None


def test_destroy_requires_the_operation_id_not_a_bare_yes():
    assert ops_gate_service.parse_destroy_confirmation("выполни", "git_branch_delete") is False
    assert ops_gate_service.parse_destroy_confirmation(
        "подтверждаю git_branch_delete", "git_branch_delete"
    ) is True


def test_operator_check_is_closed_when_uid_is_unset(monkeypatch):
    monkeypatch.delenv("HERMES_OPERATOR_SLACK_UID", raising=False)
    assert ops_gate_service.is_operator("U123") is False


def test_destroy_confirmation_rejects_a_sentence_that_merely_mentions_the_op_id():
    # "confirmed" starts with "confirm", and the op_id appears in the text --
    # but this is a report of a past manual action, not an approval typed now.
    assert ops_gate_service.parse_destroy_confirmation(
        "confirmed, git_branch_delete was handled manually yesterday",
        "git_branch_delete",
    ) is False
    # "подтверждающий" starts with "подтверждаю" -- same trap in Russian.
    assert ops_gate_service.parse_destroy_confirmation(
        "подтверждающий документ для git_branch_delete прикреплён",
        "git_branch_delete",
    ) is False


def test_destroy_confirmation_op_id_match_is_word_bounded():
    # op_id "git_push" must not be satisfied by a message that confirms a
    # different, longer-named operation ("git_push_force") which merely
    # starts with the same characters.
    assert ops_gate_service.parse_destroy_confirmation(
        "подтверждаю git_push_force", "git_push"
    ) is False


def test_parser_ignores_quoted_or_reported_speech():
    # Reporting that someone else said the word is not an approval.
    assert ops_gate_service.parse_ops_reply("он написал: выполни") is None
