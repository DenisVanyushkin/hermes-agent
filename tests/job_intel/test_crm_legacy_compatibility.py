from __future__ import annotations

import json

from job_intel import cli
from job_intel.crm_service import CRMService
from job_intel.models import Evaluation, Vacancy
from job_intel.store import JobIntelStore


def make_store(tmp_path, monkeypatch):
    db_path = tmp_path / "job_intel.sqlite3"
    monkeypatch.setattr(cli, "resolve_db_path", lambda: db_path)
    store = JobIntelStore(db_path)
    store.bootstrap()
    return store


def seed_vacancy(store: JobIntelStore) -> int:
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO vacancies (
                vacancy_key, source, source_id, company, title, location, url, description,
                posted_at, scraped_at, salary, company_url, metadata_json, first_seen_at,
                last_seen_at, repost_count, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "linkedin:example:1",
                "linkedin",
                "example-1",
                "Acme",
                "Head of Product",
                "Remote",
                "https://example.com/jobs/1",
                "Acme is hiring a Head of Product.",
                None,
                None,
                None,
                None,
                None,
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T00:00:00+00:00",
                0,
                "active",
            ),
        )
        return int(conn.execute("SELECT id FROM vacancies WHERE vacancy_key=?", ("linkedin:example:1",)).fetchone()[0])


def seed_legacy_message_and_crm_mapping(store: JobIntelStore) -> tuple[int, str]:
    vacancy_id = seed_vacancy(store)
    slack_message_ts = "1760000000.123456"
    store.record_vacancy_slack_message(
        vacancy_id=vacancy_id,
        run_id=None,
        vacancy_key="linkedin:example:1",
        canonical_url="https://example.com/jobs/1",
        card_key="card:1",
        notification_id=None,
        slack_channel="C123",
        slack_channel_id="C123",
        slack_message_ts=slack_message_ts,
        message_type="vacancy_card",
        company="Acme",
        title="Head of Product",
        score=95,
        recommendation="strong_fit",
        url="https://example.com/jobs/1",
    )
    service = CRMService.from_store(store)
    opportunity = service.ensure_opportunity_for_vacancy(
        vacancy=Vacancy(
            source="linkedin",
            source_id="example-1",
            company="Acme",
            title="Head of Product",
            location="Remote",
            url="https://example.com/jobs/1",
            description="Acme is hiring a Head of Product.",
        ),
        vacancy_id=vacancy_id,
    )
    service.link_slack_message_to_opportunity(
        opportunity_id=opportunity["id"],
        slack_channel_id="C123",
        slack_message_ts=slack_message_ts,
        slack_thread_ts=None,
    )
    return vacancy_id, slack_message_ts


def legacy_feedback_count(store: JobIntelStore) -> int:
    with store.connect(read_only=True) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM vacancy_feedback").fetchone()[0])


def test_run_feedback_event_returns_ok_when_crm_branch_raises(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    vacancy_id, slack_message_ts = seed_legacy_message_and_crm_mapping(store)
    assert vacancy_id > 0
    monkeypatch.setattr(cli, "_crm_handle_feedback_event", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("crm boom")))

    result = json.loads(
        cli.run_feedback_event(
            {
                "type": "reaction_added",
                "user": "U_TEST",
                "reaction": "+1",
                "item": {"channel": "C123", "ts": slack_message_ts},
                "event_ts": "1760001111.000001",
            }
        )
    )

    assert result["status"] == "ok"
    assert result["crm_status"] == "error"
    assert legacy_feedback_count(store) == 1
