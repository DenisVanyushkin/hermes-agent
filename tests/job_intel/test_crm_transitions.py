from __future__ import annotations

from job_intel.crm_repository import OpportunityRepository
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


def test_transition_blocks_automatic_overwrite_of_terminal_status(tmp_path):
    service = make_service(tmp_path)
    opportunity_id = seed_opportunity(service, status="rejected_by_company")

    changed = service.transition_opportunity(
        opportunity_id=opportunity_id,
        new_status="evaluation_requested",
        source="reaction",
        actor=None,
        reason="thumbs up reaction",
        payload={"reaction": "+1"},
    )

    row = service.get_opportunity(opportunity_id)
    assert changed is False
    assert row["status"] == "rejected_by_company"


def test_transition_allows_manual_reopen_of_terminal_status(tmp_path):
    service = make_service(tmp_path)
    opportunity_id = seed_opportunity(service, status="archived")

    changed = service.transition_opportunity(
        opportunity_id=opportunity_id,
        new_status="evaluation_requested",
        source="manual_command",
        actor="Denis",
        reason="reopen",
        payload={"command": "reopen"},
    )

    row = service.get_opportunity(opportunity_id)
    assert changed is True
    assert row["status"] == "evaluation_requested"


def test_delivery_transition_to_notified_only_allowed_from_discovered(tmp_path):
    service = make_service(tmp_path)
    opportunity_id = seed_opportunity(service, status="evaluated")

    changed = service.transition_for_delivery(opportunity_id=opportunity_id)

    row = service.get_opportunity(opportunity_id)
    assert changed is False
    assert row["status"] == "evaluated"
