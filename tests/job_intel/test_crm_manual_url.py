from __future__ import annotations

from job_intel import cli
from job_intel.crm_service import CRMService
from job_intel.store import JobIntelStore


def make_store(tmp_path, monkeypatch):
    db_path = tmp_path / "job_intel.sqlite3"
    monkeypatch.setattr(cli, "resolve_db_path", lambda: db_path)
    store = JobIntelStore(db_path)
    store.bootstrap()
    return store


def get_all_opportunities(service: CRMService) -> list[dict]:
    return service.repo.search_opportunities("")


def test_eval_url_creates_opportunity_with_manual_url_source(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)

    result = cli.run_manual_crm_command("/hermes-job eval https://boards.greenhouse.io/acme/jobs/12345")

    assert result["status"] == "ok"
    row = service.get_opportunity(result["opportunity_id"])
    assert row["source"] == "manual_url"


def test_manual_url_transitions_opportunity_to_evaluation_requested(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)

    result = cli.run_manual_crm_command("/hermes-job eval https://jobs.ashbyhq.com/acme/abcdef12-3456")

    assert result["status"] == "ok"
    row = service.get_opportunity(result["opportunity_id"])
    assert row["status"] == "evaluation_requested"


def test_manual_url_submitted_event_is_written(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)

    result = cli.run_manual_crm_command("/hermes-job eval https://jobs.lever.co/acme/123")

    assert result["status"] == "ok"
    event_types = [row["event_type"] for row in service.repo.list_events(result["opportunity_id"])]
    assert "manual_url_submitted" in event_types


def test_manual_url_creates_review_opportunity_task(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)

    result = cli.run_manual_crm_command("/hermes-job eval https://careers.smartrecruiters.com/Acme/job/123")

    assert result["status"] == "ok"
    tasks = service.repo.list_tasks(result["opportunity_id"])
    assert tasks[-1]["task_type"] == "review_opportunity"


def test_same_url_submitted_twice_does_not_create_duplicate_opportunity(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)

    first = cli.run_manual_crm_command("/hermes-job eval https://boards.greenhouse.io/acme/jobs/12345")
    second = cli.run_manual_crm_command("/hermes-job eval https://boards.greenhouse.io/acme/jobs/12345")

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert first["opportunity_id"] == second["opportunity_id"]
    assert len(get_all_opportunities(service)) == 1


def test_existing_opportunity_beyond_evaluation_requested_is_not_downgraded_by_repeated_url_submission(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)

    first = cli.run_manual_crm_command("/hermes-job eval https://boards.greenhouse.io/acme/jobs/12345")
    service.transition_opportunity(
        opportunity_id=first["opportunity_id"],
        new_status="applied",
        source="manual_command",
        actor="Denis",
        reason="applied_shortcut",
        payload={"command": "applied"},
    )
    second = cli.run_manual_crm_command("/hermes-job eval https://boards.greenhouse.io/acme/jobs/12345")

    assert second["status"] == "ok"
    assert service.get_opportunity(first["opportunity_id"])["status"] == "applied"


def test_ashby_url_infers_ats(tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)

    result = cli.run_manual_crm_command("/hermes-job eval https://jobs.ashbyhq.com/acme/abcdef12-3456")

    assert result["status"] == "ok"
    assert result["ats"] == "ashby"


def test_greenhouse_url_infers_ats(tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)

    result = cli.run_manual_crm_command("/hermes-job eval https://boards.greenhouse.io/acme/jobs/12345")

    assert result["status"] == "ok"
    assert result["ats"] == "greenhouse"


def test_invalid_url_returns_invalid_url_and_creates_no_opportunity(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)

    result = cli.run_manual_crm_command("/hermes-job eval not-a-url")

    assert result["status"] == "invalid_url"
    assert get_all_opportunities(service) == []


def test_extraction_unavailable_does_not_fail_ingestion(tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)

    result = cli.run_manual_crm_command("/hermes-job eval https://myworkdayjobs.com/Acme/job/12345")

    assert result["status"] == "ok"
    assert result["extraction_status"] in {"not_attempted", "failed"}


def test_manual_url_command_outside_thread_works(tmp_path, monkeypatch):
    make_store(tmp_path, monkeypatch)

    result = cli.run_manual_crm_command("/hermes-job eval https://jobs.lever.co/acme/123")

    assert result["status"] == "ok"
    assert result["opportunity_id"] is not None
