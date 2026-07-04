"""Feedback tracking integrity: cards must be recorded with the real Slack
(channel_id, message_ts) so reaction events can be attributed, and fabricated
identities must never be written (see 2026-07 feedback-loss RCA)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from job_intel import cli
from job_intel.models import Vacancy
from job_intel.store import JobIntelStore


def _make_vacancy() -> Vacancy:
    return Vacancy(
        source="greenhouse",
        source_id="fti-1",
        company="Acme",
        title="Head of Product",
        location="Remote",
        url="https://example.com/jobs/fti-1",
        description="Own product strategy and roadmap.",
    )


def _seed_store(tmp_path, monkeypatch) -> tuple[JobIntelStore, Vacancy, int]:
    db_path = tmp_path / "job_intel.sqlite3"
    monkeypatch.setattr(cli, "resolve_db_path", lambda: db_path)
    store = JobIntelStore(db_path)
    store.bootstrap()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO runs (id, mode, started_at, status, run_type) VALUES (1, 'daily', '2026-07-01T00:00:00+00:00', 'ok', 'test')"
        )
    vacancy = _make_vacancy()
    vacancy_id = store.upsert_vacancy(vacancy, "greenhouse:acme:fti-1")
    return store, vacancy, vacancy_id


def _evaluation() -> SimpleNamespace:
    return SimpleNamespace(
        score=85, recommendation="strong_fit", tier="strong_fit",
        concerns=[], reasons=[], matched_signals=["product leadership"], salary_tier="unknown",
    )


def _delivery(**kwargs) -> "cli.SlackDeliveryResult":
    defaults = dict(success=True, attempts=1, error=None, status="sent")
    defaults.update(kwargs)
    return cli.SlackDeliveryResult(**defaults)


def _tracked_rows(store: JobIntelStore) -> list[dict]:
    with store.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT slack_channel, slack_message_ts FROM vacancy_slack_messages")]


def test_delivery_without_ts_never_records_fabricated_identity(tmp_path, monkeypatch) -> None:
    store, vacancy, vacancy_id = _seed_store(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_deliver_to_slack", lambda *a, **k: _delivery(message_ts=None, channel_id=None))

    cli._deliver_vacancy_notifications(store, 1, "executive_search_report", [(vacancy, _evaluation(), vacancy_id)])

    assert _tracked_rows(store) == []  # no fake-ts row
    with store.connect() as conn:
        status = conn.execute("SELECT status FROM vacancies WHERE id=?", (vacancy_id,)).fetchone()[0]
    assert status == "notified"  # delivery itself still succeeded


def test_alias_channel_resolves_to_real_id_before_recording(tmp_path, monkeypatch) -> None:
    store, vacancy, vacancy_id = _seed_store(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_deliver_to_slack", lambda *a, **k: _delivery(message_ts="1783000000.000100", channel_id=None))

    cli._deliver_vacancy_notifications(store, 1, "executive_search_report", [(vacancy, _evaluation(), vacancy_id)])

    rows = _tracked_rows(store)
    assert len(rows) == 1
    assert rows[0]["slack_channel"] == "C0B4MM6D52A"  # real ID, never the alias
    assert rows[0]["slack_message_ts"] == "1783000000.000100"


def test_reaction_lookup_finds_card_by_real_identity(tmp_path, monkeypatch) -> None:
    store, vacancy, vacancy_id = _seed_store(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_deliver_to_slack", lambda *a, **k: _delivery(message_ts="1783000001.000200", channel_id="C0TESTCHAN"))

    cli._deliver_vacancy_notifications(store, 1, "executive_search_report", [(vacancy, _evaluation(), vacancy_id)])

    result = json.loads(cli.run_feedback_event({
        "type": "reaction_added",
        "user": "U_TEST",
        "reaction": "-1",
        "item": {"channel": "C0TESTCHAN", "ts": "1783000001.000200"},
        "event_ts": "1783000002.000001",
    }))
    assert result["status"] == "ok"
    assert result["vacancy_id"] == vacancy_id
    with store.connect() as conn:
        state = conn.execute("SELECT feedback_type, active FROM vacancy_feedback_state").fetchone()
    assert (state["feedback_type"], state["active"]) == ("not_interesting", 1)


def test_reaction_lookup_falls_back_to_alias_for_legacy_rows(tmp_path, monkeypatch) -> None:
    store, vacancy, vacancy_id = _seed_store(tmp_path, monkeypatch)
    # Legacy row recorded pre-fix with the alias but a real ts.
    store.insert_vacancy_slack_message(
        vacancy_id=vacancy_id, run_id=1, vacancy_key="greenhouse:acme:fti-1",
        canonical_url=vacancy.url, card_key=vacancy.url, notification_id=None,
        slack_channel="executive_search_report", slack_message_ts="1783000003.000300",
        message_type="vacancy_card", company=vacancy.company, title=vacancy.title,
        score=85, recommendation="strong_fit", url=vacancy.url,
        sent_at="2026-07-01T00:00:00+00:00",
    )
    result = json.loads(cli.run_feedback_event({
        "type": "reaction_added",
        "user": "U_TEST",
        "reaction": "eyes",
        "item": {"channel": "C0B4MM6D52A", "ts": "1783000003.000300"},
        "event_ts": "1783000004.000001",
    }))
    assert result["status"] == "ok"
    assert result["vacancy_id"] == vacancy_id
