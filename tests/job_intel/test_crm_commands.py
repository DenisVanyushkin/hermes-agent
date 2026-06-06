from __future__ import annotations

import json

import pytest

from job_intel import cli
from job_intel.crm_service import CRMService
from job_intel.store import JobIntelStore


def make_store(tmp_path, monkeypatch):
    db_path = tmp_path / "job_intel.sqlite3"
    monkeypatch.setattr(cli, "resolve_db_path", lambda: db_path)
    store = JobIntelStore(db_path)
    store.bootstrap()
    return store


def seed_opportunity(service: CRMService, status: str, company: str = "Acme", title: str = "Head of Product") -> int:
    return service.repo.create_opportunity(
        company=company,
        company_normalized=company.casefold(),
        title=title,
        title_normalized=title.casefold(),
        location="Remote",
        source="linkedin",
        canonical_url=f"https://example.com/jobs/{company}-{title}-{status}".replace(" ", "-"),
        status=status,
    )


def test_command_in_thread_resolves_opportunity_by_slack_mapping(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)
    opportunity_id = seed_opportunity(service, status="watchlist")
    service.repo.link_slack_message_to_opportunity(
        opportunity_id=opportunity_id,
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        slack_thread_ts=None,
    )

    result = cli.run_manual_crm_command(
        "/hermes-job status on_hold",
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
    )

    assert result["status"] == "ok"
    assert service.get_opportunity(opportunity_id)["status"] == "on_hold"


def test_command_outside_thread_with_ambiguous_matches_does_not_update(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)
    seed_opportunity(service, status="watchlist", company="Acme", title="Head of Product")
    seed_opportunity(service, status="evaluated", company="Acme", title="Head of Product")

    result = cli.run_manual_crm_command("/hermes-job applied")

    assert result["status"] == "needs_disambiguation"


def test_applied_shortcut_changes_status_and_writes_manual_event(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)
    opportunity_id = seed_opportunity(service, status="artifacts_ready", company="Acme", title="Chief Product Officer")

    result = cli.run_manual_crm_command("/hermes-job applied Chief Product Officer")

    assert result["status"] == "ok"
    assert service.get_opportunity(opportunity_id)["status"] == "applied"
    assert service.repo.list_events(opportunity_id)[-1]["event_type"] == "manual_status_changed"


def test_decline_stores_reason(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)
    opportunity_id = seed_opportunity(service, status="watchlist", company="Acme", title="VP Product")

    result = cli.run_manual_crm_command('/hermes-job decline "too early" VP Product')

    assert result["status"] == "ok"
    assert service.get_opportunity(opportunity_id)["status"] == "declined_by_me"
    payload = service.repo.list_events(opportunity_id)[-1]["payload_json"]
    assert "too early" in payload


def test_note_writes_note_event_without_status_change(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)
    opportunity_id = seed_opportunity(service, status="watchlist", company="Acme", title="Director Product")

    result = cli.run_manual_crm_command('/hermes-job note "needs more research" Director Product')

    assert result["status"] == "ok"
    assert service.get_opportunity(opportunity_id)["status"] == "watchlist"
    assert service.repo.list_events(opportunity_id)[-1]["event_type"] == "note_added"


def test_followup_5d_creates_due_task(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)
    opportunity_id = seed_opportunity(service, status="recruiter_replied", company="Acme", title="GM Product")

    result = cli.run_manual_crm_command("/hermes-job followup 5d GM Product")

    assert result["status"] == "ok"
    tasks = service.repo.list_tasks(opportunity_id)
    assert tasks[-1]["task_type"] == "follow_up_recruiter"
    assert tasks[-1]["due_at"] is not None


def test_due_returns_open_due_tasks(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)
    opportunity_id = seed_opportunity(service, status="watchlist", company="Acme", title="CPO")
    service.create_or_update_open_task(opportunity_id=opportunity_id, task_type="review_opportunity", due_at="2000-01-01T00:00:00+00:00")

    result = cli.run_manual_crm_command("/hermes-job due")

    assert result["status"] == "ok"
    assert result["items"]


def test_reopen_can_reopen_guarded_terminal_status_only_explicitly(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)
    opportunity_id = seed_opportunity(service, status="archived", company="Acme", title="SVP Product")

    result = cli.run_manual_crm_command("/hermes-job reopen SVP Product")

    assert result["status"] == "ok"
    assert service.get_opportunity(opportunity_id)["status"] == "evaluation_requested"


def test_invalid_status_is_rejected(tmp_path, monkeypatch):
    store = make_store(tmp_path, monkeypatch)
    service = CRMService.from_store(store)
    seed_opportunity(service, status="watchlist", company="Acme", title="Product Lead")

    result = cli.run_manual_crm_command("/hermes-job status definitely_not_a_status Product Lead")

    assert result["status"] == "invalid_status"


def test_main_dispatches_hermes_job_and_prints_json(tmp_path, monkeypatch, capsys):
    make_store(tmp_path, monkeypatch)

    exit_code = cli.main([
        "hermes-job",
        "/hermes-job eval https://jobs.ashbyhq.com/manual-smoke-test/cli-dispatch-test",
    ])

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert exit_code == 0
    assert out
    assert payload["status"] == "ok"


def test_main_invalid_url_returns_invalid_url_and_prints_json(tmp_path, monkeypatch, capsys):
    make_store(tmp_path, monkeypatch)

    exit_code = cli.main(["hermes-job", "/hermes-job eval not-a-url"])

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert exit_code == 0
    assert out
    assert payload["status"] == "invalid_url"


def test_main_unknown_manual_command_returns_error_and_nonzero(tmp_path, monkeypatch, capsys):
    make_store(tmp_path, monkeypatch)

    exit_code = cli.main(["hermes-job", "/hermes-job nope"])

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert exit_code == 1
    assert out
    assert payload["status"] == "invalid_command"


def test_main_missing_manual_command_is_parse_error(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["hermes-job"])

    assert exc_info.value.code == 2
    assert capsys.readouterr().err

