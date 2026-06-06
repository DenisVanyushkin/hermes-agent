from __future__ import annotations

from job_intel.crm_service import CRMService
from job_intel.store import JobIntelStore


def make_service(tmp_path):
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()
    return CRMService.from_store(store)


def seed_opportunity(service: CRMService, status: str = "notified") -> int:
    return service.repo.create_opportunity(
        company="Acme",
        title="Head of Product",
        location="Remote",
        source="linkedin",
        canonical_url=f"https://example.com/jobs/{status}",
        status=status,
    )


def latest_task(service: CRMService, opportunity_id: int) -> dict:
    return service.repo.list_tasks(opportunity_id)[-1]


def latest_artifact(service: CRMService, opportunity_id: int) -> dict:
    return service.repo.list_artifacts(opportunity_id)[-1]


def latest_event(service: CRMService, opportunity_id: int) -> dict:
    return service.repo.list_events(opportunity_id)[-1]


def event_types(service: CRMService, opportunity_id: int) -> list[str]:
    return [row["event_type"] for row in service.repo.list_events(opportunity_id)]


def test_eyes_sets_watchlist_and_review_task(tmp_path):
    service = make_service(tmp_path)
    opportunity_id = seed_opportunity(service, status="notified")
    service.repo.link_slack_message_to_opportunity(
        opportunity_id=opportunity_id,
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        slack_thread_ts=None,
    )

    service.handle_slack_reaction_event(
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        reaction="eyes",
        event_type="reaction_added",
        actor="U_TEST",
        payload={"reaction": "eyes"},
    )

    assert service.get_opportunity(opportunity_id)["status"] == "watchlist"
    assert latest_task(service, opportunity_id)["task_type"] == "review_opportunity"


def test_plus_one_with_stub_evaluator_stays_evaluation_requested(tmp_path):
    service = make_service(tmp_path)
    opportunity_id = seed_opportunity(service, status="notified")
    service.repo.link_slack_message_to_opportunity(
        opportunity_id=opportunity_id,
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        slack_thread_ts=None,
    )

    service.handle_slack_reaction_event(
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        reaction="+1",
        event_type="reaction_added",
        actor="U_TEST",
        payload={"reaction": "+1"},
    )

    assert service.get_opportunity(opportunity_id)["status"] == "evaluation_requested"
    assert latest_task(service, opportunity_id)["task_type"] == "review_opportunity"
    assert "evaluation_requested" in event_types(service, opportunity_id)


def test_fire_creates_placeholder_artifact_and_generate_artifacts_task_without_marking_ready(tmp_path):
    service = make_service(tmp_path)
    opportunity_id = seed_opportunity(service, status="evaluated")
    service.repo.link_slack_message_to_opportunity(
        opportunity_id=opportunity_id,
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        slack_thread_ts=None,
    )

    service.handle_slack_reaction_event(
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        reaction="fire",
        event_type="reaction_added",
        actor="U_TEST",
        payload={"reaction": "fire"},
    )

    row = service.get_opportunity(opportunity_id)
    assert row["status"] == "artifact_requested"
    assert row["artifact_bundle_id"] is None
    assert latest_task(service, opportunity_id)["task_type"] == "generate_artifacts"
    assert latest_artifact(service, opportunity_id)["artifact_type"] == "application_bundle_placeholder"
    assert latest_artifact(service, opportunity_id)["qa_status"] == "stub"


def test_rocket_from_notified_records_priority_signal_without_application_planned(tmp_path):
    service = make_service(tmp_path)
    opportunity_id = seed_opportunity(service, status="notified")
    service.repo.link_slack_message_to_opportunity(
        opportunity_id=opportunity_id,
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        slack_thread_ts=None,
    )

    service.handle_slack_reaction_event(
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        reaction="rocket",
        event_type="reaction_added",
        actor="U_TEST",
        payload={"reaction": "rocket"},
    )

    assert service.get_opportunity(opportunity_id)["status"] == "notified"
    assert latest_task(service, opportunity_id)["task_type"] == "review_opportunity"
    assert "priority_signal" in event_types(service, opportunity_id)


def test_decline_reaction_moves_to_declined_by_me(tmp_path):
    service = make_service(tmp_path)
    opportunity_id = seed_opportunity(service, status="watchlist")
    service.repo.link_slack_message_to_opportunity(
        opportunity_id=opportunity_id,
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        slack_thread_ts=None,
    )

    service.handle_slack_reaction_event(
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        reaction="-1",
        event_type="reaction_added",
        actor="U_TEST",
        payload={"reaction": "-1"},
    )

    assert service.get_opportunity(opportunity_id)["status"] == "declined_by_me"


def test_question_reaction_requests_evaluation_and_review_task(tmp_path):
    service = make_service(tmp_path)
    opportunity_id = seed_opportunity(service, status="watchlist")
    service.repo.link_slack_message_to_opportunity(
        opportunity_id=opportunity_id,
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        slack_thread_ts=None,
    )

    service.handle_slack_reaction_event(
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        reaction="question",
        event_type="reaction_added",
        actor="U_TEST",
        payload={"reaction": "question"},
    )

    assert service.get_opportunity(opportunity_id)["status"] == "evaluation_requested"
    assert latest_task(service, opportunity_id)["task_type"] == "review_opportunity"


def test_mailbox_reaction_sets_outreach_planned_and_send_outreach_task(tmp_path):
    service = make_service(tmp_path)
    opportunity_id = seed_opportunity(service, status="evaluated")
    service.repo.link_slack_message_to_opportunity(
        opportunity_id=opportunity_id,
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        slack_thread_ts=None,
    )

    service.handle_slack_reaction_event(
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        reaction="mailbox_with_mail",
        event_type="reaction_added",
        actor="U_TEST",
        payload={"reaction": "mailbox_with_mail"},
    )

    assert service.get_opportunity(opportunity_id)["status"] == "outreach_planned"
    assert latest_task(service, opportunity_id)["task_type"] == "send_outreach"


def test_repeated_eyes_reaction_does_not_duplicate_open_review_task(tmp_path):
    service = make_service(tmp_path)
    opportunity_id = seed_opportunity(service, status="notified")
    service.repo.link_slack_message_to_opportunity(
        opportunity_id=opportunity_id,
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        slack_thread_ts=None,
    )

    service.handle_slack_reaction_event(
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        reaction="eyes",
        event_type="reaction_added",
        actor="U_TEST",
        payload={"reaction": "eyes"},
    )
    service.handle_slack_reaction_event(
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        reaction="eyes",
        event_type="reaction_added",
        actor="U_TEST",
        payload={"reaction": "eyes"},
    )

    tasks = [task for task in service.repo.list_tasks(opportunity_id) if task["task_type"] == "review_opportunity" and task["status"] == "open"]
    assert len(tasks) == 1
