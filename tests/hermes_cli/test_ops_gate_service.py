import time

import pytest

from hermes_cli import ops_gate_service


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def _record(created_at=None):
    ops_gate_service.record_pending(
        session_id="s1",
        repo_path="/repo",
        plan=[{"op_id": "git_push", "risk": "mutate", "argv": ["git", "push", "origin", "main"]}],
        original_task="запушь текущую ветку в origin",
        created_at=created_at,
    )


def test_pending_round_trips(home):
    _record()
    pending = ops_gate_service.get_pending()
    assert pending["original_task"] == "запушь текущую ветку в origin"
    assert pending["plan"][0]["op_id"] == "git_push"


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
