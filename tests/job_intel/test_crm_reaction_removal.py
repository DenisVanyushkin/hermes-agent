from __future__ import annotations

from job_intel.crm_service import CRMService
from job_intel.store import JobIntelStore


def make_service(tmp_path):
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()
    return CRMService.from_store(store)


def seed_opportunity(service: CRMService, status: str) -> int:
    return service.repo.create_opportunity(
        company="Acme",
        title="Head of Product",
        location="Remote",
        source="linkedin",
        canonical_url=f"https://example.com/jobs/{status}",
        status=status,
    )


def latest_event(service: CRMService, opportunity_id: int) -> dict:
    return service.repo.list_events(opportunity_id)[-1]


def test_removing_plus_one_keeps_existing_evaluation_history(tmp_path):
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
        reaction="+1",
        event_type="reaction_removed",
        actor="U_TEST",
        payload={"reaction": "+1"},
    )

    assert service.get_opportunity(opportunity_id)["status"] == "evaluated"
    assert latest_event(service, opportunity_id)["event_type"] == "reaction_removed"


def test_removing_fire_keeps_placeholder_artifact_and_does_not_mark_ready(tmp_path):
    service = make_service(tmp_path)
    opportunity_id = seed_opportunity(service, status="artifact_requested")
    service.repo.link_slack_message_to_opportunity(
        opportunity_id=opportunity_id,
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        slack_thread_ts=None,
    )
    service.repo.create_artifact(
        opportunity_id=opportunity_id,
        artifact_type="application_bundle_placeholder",
        qa_status="stub",
    )

    service.handle_slack_reaction_event(
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        reaction="fire",
        event_type="reaction_removed",
        actor="U_TEST",
        payload={"reaction": "fire"},
    )

    assert service.get_opportunity(opportunity_id)["status"] == "artifact_requested"
    assert len(service.repo.list_artifacts(opportunity_id)) == 1
    assert latest_event(service, opportunity_id)["event_type"] == "reaction_removed"


def test_removing_rocket_does_not_rollback_application_history(tmp_path):
    service = make_service(tmp_path)
    opportunity_id = seed_opportunity(service, status="application_planned")
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
        event_type="reaction_removed",
        actor="U_TEST",
        payload={"reaction": "rocket"},
    )

    assert service.get_opportunity(opportunity_id)["status"] == "application_planned"
    assert latest_event(service, opportunity_id)["event_type"] == "reaction_removed"


def test_removing_decline_does_not_reopen(tmp_path):
    service = make_service(tmp_path)
    opportunity_id = seed_opportunity(service, status="declined_by_me")
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
        event_type="reaction_removed",
        actor="U_TEST",
        payload={"reaction": "-1"},
    )

    assert service.get_opportunity(opportunity_id)["status"] == "declined_by_me"
    assert latest_event(service, opportunity_id)["event_type"] == "reaction_removed"
