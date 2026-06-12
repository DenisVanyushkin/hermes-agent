from __future__ import annotations

import json
import os

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


def seed_vacancy(store: JobIntelStore) -> tuple[Vacancy, int]:
    vacancy = Vacancy(
        source="linkedin",
        source_id="example-1",
        company="Acme",
        title="Head of Product",
        location="Remote",
        url="https://example.com/jobs/1",
        description="Acme is hiring a Head of Product.",
    )
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
        vacancy_id = int(conn.execute("SELECT id FROM vacancies WHERE vacancy_key=?", ("linkedin:example:1",)).fetchone()[0])
    return vacancy, vacancy_id


def test_delivery_creates_opportunity_and_mapping_without_downgrading_existing_status(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    vacancy, vacancy_id = seed_vacancy(store)
    service = CRMService.from_store(store)
    opportunity = service.ensure_opportunity_for_vacancy(vacancy=vacancy, vacancy_id=vacancy_id)
    service.transition_opportunity(
        opportunity_id=opportunity["id"],
        new_status="evaluated",
        source="manual_command",
        actor="Denis",
        reason="reopen",
        payload={"command": "reopen"},
    )
    monkeypatch.setattr(
        cli,
        "_deliver_to_slack",
        lambda body, channel, prefer_gateway=True: cli.SlackDeliveryResult(
            success=True,
            attempts=1,
            status="sent",
            message_ts="1760000000.123456",
            channel_id="C123",
            platform_message_id="C123:1760000000.123456",
        ),
    )

    cli._deliver_vacancy_notifications(
        store=store,
        run_id=None,
        channel="executive_search_report",
        items=[(vacancy, Evaluation(score=95, tier="strong_fit", recommendation="strong_fit"), vacancy_id)],
    )

    row = service.get_opportunity(opportunity["id"])
    assert row["status"] == "evaluated"
    assert service.find_opportunity_by_slack_message("C123", "1760000000.123456")["id"] == opportunity["id"]
    legacy = store.find_vacancy_message(slack_channel="C123", slack_message_ts="1760000000.123456")
    assert legacy is not None
    assert row["vacancy_id"] == legacy["vacancy_id"]


def test_successful_delivery_creates_legacy_row_and_crm_mapping(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    vacancy, vacancy_id = seed_vacancy(store)
    monkeypatch.setattr(
        cli,
        "_deliver_to_slack",
        lambda body, channel, prefer_gateway=True: cli.SlackDeliveryResult(
            success=True,
            attempts=1,
            status="sent",
            message_ts="1760000000.123456",
            channel_id="C123",
            platform_message_id="C123:1760000000.123456",
        ),
    )

    deliveries = cli._deliver_vacancy_notifications(
        store=store,
        run_id=None,
        channel="executive_search_report",
        items=[(vacancy, Evaluation(score=95, tier="strong_fit", recommendation="strong_fit"), vacancy_id)],
    )

    service = CRMService.from_store(store)
    opportunity = service.find_opportunity_by_slack_message("C123", "1760000000.123456")
    assert deliveries[0]["message_ts"] == "1760000000.123456"
    assert opportunity is not None
    assert store.find_vacancy_message(slack_channel="C123", slack_message_ts="1760000000.123456") is not None


def test_delivery_failure_does_not_create_crm_mapping(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    vacancy, vacancy_id = seed_vacancy(store)
    monkeypatch.setattr(
        cli,
        "_deliver_to_slack",
        lambda body, channel, prefer_gateway=True: cli.SlackDeliveryResult(
            success=False,
            attempts=1,
            status="failed",
            error="boom",
        ),
    )

    cli._deliver_vacancy_notifications(
        store=store,
        run_id=None,
        channel="executive_search_report",
        items=[(vacancy, Evaluation(score=95, tier="strong_fit", recommendation="strong_fit"), vacancy_id)],
    )

    service = CRMService.from_store(store)
    assert service.find_opportunity_by_slack_message("C123", "1760000000.123456") is None
    with store.connect(read_only=True) as conn:
        assert int(conn.execute("SELECT COUNT(*) FROM slack_message_map").fetchone()[0]) == 0


def test_delivery_mapping_uses_real_channel_id_not_alias(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    vacancy, vacancy_id = seed_vacancy(store)
    monkeypatch.setattr(
        cli,
        "_deliver_to_slack",
        lambda body, channel, prefer_gateway=True: cli.SlackDeliveryResult(
            success=True,
            attempts=1,
            status="sent",
            message_ts="1760000000.123456",
            channel_id="C0B4MM6D52A",
            platform_message_id="C0B4MM6D52A:1760000000.123456",
        ),
    )

    cli._deliver_vacancy_notifications(
        store=store,
        run_id=None,
        channel="executive_search_report",
        items=[(vacancy, Evaluation(score=95, tier="strong_fit", recommendation="strong_fit"), vacancy_id)],
    )

    service = CRMService.from_store(store)
    assert service.find_opportunity_by_slack_message("C0B4MM6D52A", "1760000000.123456") is not None
    assert store.find_vacancy_message(slack_channel="C0B4MM6D52A", slack_message_ts="1760000000.123456") is not None


def test_delivery_mapping_idempotent(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    vacancy, vacancy_id = seed_vacancy(store)
    monkeypatch.setattr(
        cli,
        "_deliver_to_slack",
        lambda body, channel, prefer_gateway=True: cli.SlackDeliveryResult(
            success=True,
            attempts=1,
            status="sent",
            message_ts="1760000000.123456",
            channel_id="C123",
            platform_message_id="C123:1760000000.123456",
        ),
    )

    items = [(vacancy, Evaluation(score=95, tier="strong_fit", recommendation="strong_fit"), vacancy_id)]
    cli._deliver_vacancy_notifications(store=store, run_id=None, channel="executive_search_report", items=items)
    cli._deliver_vacancy_notifications(store=store, run_id=None, channel="executive_search_report", items=items)

    with store.connect(read_only=True) as conn:
        assert int(conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]) == 1
        assert int(conn.execute("SELECT COUNT(*) FROM slack_message_map").fetchone()[0]) == 1


def test_reaction_after_delivery_mapping_hits_crm(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    vacancy, vacancy_id = seed_vacancy(store)
    monkeypatch.setattr(
        cli,
        "_deliver_to_slack",
        lambda body, channel, prefer_gateway=True: cli.SlackDeliveryResult(
            success=True,
            attempts=1,
            status="sent",
            message_ts="1760000000.123456",
            channel_id="C123",
            platform_message_id="C123:1760000000.123456",
        ),
    )

    cli._deliver_vacancy_notifications(
        store=store,
        run_id=None,
        channel="executive_search_report",
        items=[(vacancy, Evaluation(score=95, tier="strong_fit", recommendation="strong_fit"), vacancy_id)],
    )

    service = CRMService.from_store(store)
    service.handle_slack_reaction_event(
        slack_channel_id="C123",
        slack_message_ts="1760000000.123456",
        reaction="eyes",
        event_type="reaction_added",
        actor="U_TEST",
        payload={"reaction": "eyes"},
    )

    opportunity = service.find_opportunity_by_slack_message("C123", "1760000000.123456")
    assert opportunity is not None
    assert service.get_opportunity(opportunity["id"])["status"] == "watchlist"
    tasks = [task for task in service.repo.list_tasks(opportunity["id"]) if task["task_type"] == "review_opportunity" and task["status"] == "open"]
    assert len(tasks) == 1


def test_deliver_to_slack_bootstraps_hermes_env_before_send(monkeypatch):
    monkeypatch.delenv("JOB_INTEL_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

    calls: list[str] = []

    def fake_bootstrap():
        calls.append("bootstrap")
        os.environ["SLACK_BOT_TOKEN"] = "xoxb-test-token"

    def fake_send_message_tool(args):
        calls.append("send")
        assert os.environ.get("SLACK_BOT_TOKEN") == "xoxb-test-token"
        return json.dumps({"success": True, "chat_id": "C0B4MM6D52A", "message_id": "1780675333.782909"})

    monkeypatch.setattr(cli, "_ensure_hermes_env_loaded_for_outbound_delivery", fake_bootstrap)
    monkeypatch.setattr(cli, "send_message_tool", fake_send_message_tool)

    result = cli._deliver_to_slack("hello", "executive_search_report", prefer_gateway=True)

    assert result.success is True
    assert result.channel_id == "C0B4MM6D52A"
    assert result.message_ts == "1780675333.782909"
    assert calls == ["bootstrap", "send"]
