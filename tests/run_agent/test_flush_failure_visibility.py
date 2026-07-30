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
    """Заставить append_message детерминированно падать — форма бага 27.07."""

    def _raise(*args, **kwargs):
        raise exc

    db.append_message = _raise


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

            _break_append(db, TypeError("boom"))
            agent._flush_messages_to_session_db([{"role": "user", "content": "a"}], [])
            assert agent._consecutive_flush_failures == 1

            db.append_message = healthy_append
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
