from __future__ import annotations

from job_intel.crm_repository import OpportunityRepository
from job_intel.store import JobIntelStore


def make_repo(tmp_path):
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()
    return OpportunityRepository(store)


def test_bootstrap_creates_crm_tables_and_indexes(tmp_path):
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()

    with store.connect(read_only=True) as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }

    assert "opportunities" in tables
    assert "opportunity_events" in tables
    assert "opportunity_tasks" in tables
    assert "opportunity_artifacts" in tables
    assert "opportunity_contacts" in tables
    assert "slack_message_map" in tables
    assert "idx_opportunities_company_title" in indexes
    assert "idx_opportunities_status" in indexes
    assert "idx_opportunities_ats_job" in indexes
    assert "idx_opportunities_canonical_url" in indexes
    assert "idx_opportunities_slack" in indexes
    assert "idx_events_opportunity" in indexes
    assert "idx_events_type" in indexes
    assert "idx_events_created_at" in indexes
    assert "idx_slack_message_map_unique" in indexes


def test_repository_can_create_and_find_opportunity_by_canonical_url(tmp_path):
    repo = make_repo(tmp_path)
    opportunity_id = repo.create_opportunity(
        company="Acme",
        title="Head of Product",
        location="Remote",
        source="linkedin",
        source_url="https://example.com/jobs/1",
        canonical_url="https://example.com/jobs/1",
        status="discovered",
    )

    row = repo.find_opportunity_by_canonical_url("https://example.com/jobs/1")
    assert row is not None
    assert row["id"] == opportunity_id


def test_repository_upserts_slack_message_map_uniquely(tmp_path):
    repo = make_repo(tmp_path)
    opportunity_id = repo.create_opportunity(source="linkedin", status="discovered")

    repo.link_slack_message_to_opportunity(
        opportunity_id=opportunity_id,
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        slack_thread_ts=None,
    )
    repo.link_slack_message_to_opportunity(
        opportunity_id=opportunity_id,
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        slack_thread_ts="1760000000.123",
    )

    row = repo.find_opportunity_by_slack_message("C123", "1760000000.123")
    assert row is not None
    assert row["id"] == opportunity_id
