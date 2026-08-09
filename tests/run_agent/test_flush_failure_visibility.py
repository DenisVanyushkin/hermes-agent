"""Потеря транскрипта не должна быть молчаливой (спека 2026-07-30)."""

import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

SESSION_ID = "test-flush-visibility"


def _make_agent(session_db, session_id=SESSION_ID):
    """Тот же паттерн, что в tests/run_agent/test_identity_flush.py."""
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            model="test/model",
            quiet_mode=True,
            session_db=session_db,
            session_id=session_id,
            skip_context_files=True,
            skip_memory=True,
        )
    agent._ensure_db_session()
    return agent


def _break_append(db, exc):
    """Заставить запись сообщений детерминированно падать — форма бага 27.07.

    Флаш пишет партиями через ``append_messages_batch``; одиночный
    ``append_message`` остался для других путей. Ломаем оба, иначе тест
    молча перестаёт проверять что-либо, как только апстрим меняет путь
    записи.
    """

    def _raise(*args, **kwargs):
        raise exc

    db.append_message = _raise
    db.append_messages_batch = _raise


def test_single_failure_stays_warning(caplog):
    from hermes_state import SessionDB

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SessionDB(db_path=Path(tmpdir) / "t.db")
        try:
            agent = _make_agent(db)
            _break_append(db, TypeError("boom"))

            with caplog.at_level(logging.WARNING, logger="run_agent"):
                agent._flush_messages_to_session_db(
                    [{"role": "user", "content": "one"}], []
                )

            assert agent._consecutive_flush_failures == 1
            assert any(r.levelno == logging.WARNING for r in caplog.records)
            assert not any(r.levelno >= logging.ERROR for r in caplog.records)
        finally:
            db.close()


def test_two_consecutive_failures_escalate_to_error(caplog):
    from hermes_state import SessionDB

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SessionDB(db_path=Path(tmpdir) / "t.db")
        try:
            agent = _make_agent(db)
            _break_append(db, TypeError("boom"))

            with caplog.at_level(logging.WARNING, logger="run_agent"):
                agent._flush_messages_to_session_db(
                    [{"role": "user", "content": "one"}], []
                )
                agent._flush_messages_to_session_db(
                    [{"role": "user", "content": "two"}], []
                )

            assert agent._consecutive_flush_failures == 2
            errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
            assert len(errors) == 1
            assert "boom" in errors[0].getMessage()
        finally:
            db.close()


def test_success_between_failures_resets_the_counter(caplog):
    from hermes_state import SessionDB

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SessionDB(db_path=Path(tmpdir) / "t.db")
        try:
            agent = _make_agent(db)
            healthy_append = db.append_message
            healthy_batch = db.append_messages_batch

            _break_append(db, TypeError("boom"))
            agent._flush_messages_to_session_db([{"role": "user", "content": "a"}], [])
            assert agent._consecutive_flush_failures == 1

            db.append_message = healthy_append
            db.append_messages_batch = healthy_batch
            agent._flush_messages_to_session_db([{"role": "user", "content": "b"}], [])
            assert agent._consecutive_flush_failures == 0

            _break_append(db, TypeError("boom"))
            with caplog.at_level(logging.WARNING, logger="run_agent"):
                agent._flush_messages_to_session_db(
                    [{"role": "user", "content": "c"}], []
                )

            assert agent._consecutive_flush_failures == 1
            assert not any(r.levelno >= logging.ERROR for r in caplog.records)
        finally:
            db.close()


def test_fresh_agent_starts_at_zero():
    from hermes_state import SessionDB

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SessionDB(db_path=Path(tmpdir) / "t.db")
        try:
            agent = _make_agent(db)
            assert agent._consecutive_flush_failures == 0
        finally:
            db.close()



def _capture_alerts(monkeypatch, channel="telegram:79564752"):
    """Подменить конфиг и отправку; вернуть список отправленных текстов."""
    import run_agent

    sent = []
    monkeypatch.setattr(
        run_agent, "get_alert_config",
        lambda cfg: {"channel": channel, "dedup_minutes": 15, "include_user_message": True},
    )
    monkeypatch.setattr(
        run_agent, "send_operator_alert", lambda ch, text: sent.append((ch, text))
    )
    monkeypatch.setattr(run_agent, "dedup_decision", lambda sig, now, window: (True, 0))
    return sent


def test_second_failure_sends_one_alert(monkeypatch):
    from hermes_state import SessionDB

    sent = _capture_alerts(monkeypatch)

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SessionDB(db_path=Path(tmpdir) / "t.db")
        try:
            agent = _make_agent(db)
            _break_append(
                db,
                TypeError(
                    "SessionDB.append_message() got an unexpected keyword "
                    "argument 'compression_lock_holder'"
                ),
            )

            agent._flush_messages_to_session_db([{"role": "user", "content": "a"}], [])
            assert sent == []

            agent._flush_messages_to_session_db([{"role": "user", "content": "b"}], [])

            assert len(sent) == 1
            channel, text = sent[0]
            assert channel == "telegram:79564752"
            assert SESSION_ID in text
            assert "compression_lock_holder" in text
        finally:
            db.close()


def test_no_alert_when_channel_not_configured(monkeypatch):
    from hermes_state import SessionDB
    import run_agent

    sent = []
    monkeypatch.setattr(run_agent, "get_alert_config", lambda cfg: None)
    monkeypatch.setattr(
        run_agent, "send_operator_alert", lambda ch, text: sent.append((ch, text))
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SessionDB(db_path=Path(tmpdir) / "t.db")
        try:
            agent = _make_agent(db)
            _break_append(db, TypeError("boom"))

            agent._flush_messages_to_session_db([{"role": "user", "content": "a"}], [])
            agent._flush_messages_to_session_db([{"role": "user", "content": "b"}], [])

            assert sent == []
            assert agent._consecutive_flush_failures == 2
        finally:
            db.close()


def test_alert_crash_does_not_break_the_flush(monkeypatch, caplog):
    from hermes_state import SessionDB
    import run_agent

    def _explode(cfg):
        raise RuntimeError("alert machinery is broken")

    monkeypatch.setattr(run_agent, "get_alert_config", _explode)

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SessionDB(db_path=Path(tmpdir) / "t.db")
        try:
            agent = _make_agent(db)
            _break_append(db, TypeError("boom"))

            with caplog.at_level(logging.WARNING, logger="run_agent"):
                agent._flush_messages_to_session_db(
                    [{"role": "user", "content": "a"}], []
                )
                agent._flush_messages_to_session_db(
                    [{"role": "user", "content": "b"}], []
                )

            assert agent._consecutive_flush_failures == 2
            assert any(
                "persistence-failure alert" in r.getMessage() for r in caplog.records
            )
        finally:
            db.close()


def test_dedup_suppresses_repeat_alerts(monkeypatch):
    from hermes_state import SessionDB
    import run_agent

    sent = []
    monkeypatch.setattr(
        run_agent, "get_alert_config",
        lambda cfg: {"channel": "telegram:1", "dedup_minutes": 15, "include_user_message": True},
    )
    monkeypatch.setattr(
        run_agent, "send_operator_alert", lambda ch, text: sent.append((ch, text))
    )
    decisions = iter([(True, 0), (False, 1), (False, 2)])
    monkeypatch.setattr(
        run_agent, "dedup_decision", lambda sig, now, window: next(decisions)
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SessionDB(db_path=Path(tmpdir) / "t.db")
        try:
            agent = _make_agent(db)
            _break_append(db, TypeError("boom"))

            for content in ("a", "b", "c", "d"):
                agent._flush_messages_to_session_db(
                    [{"role": "user", "content": content}], []
                )

            assert len(sent) == 1
        finally:
            db.close()


def test_turn_error_alerts_is_imported_at_module_level():
    """Сторож: ленивый импорт вернул бы алертеру ровно ту уязвимость,
    ради которой он пишется — модуль на пути сбоя читался бы с диска
    в момент вызова и мог оказаться другой версии, чем весь процесс."""
    import subprocess
    import sys

    code = (
        "import sys; import run_agent; "
        "print('gateway.turn_error_alerts' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=180
    )
    assert result.stdout.strip().endswith("True"), result.stdout + result.stderr
