from __future__ import annotations

import json
from pathlib import Path

from hermes_cli.recruiter_real_data_run_ledger import (
    APPLICATION_MATERIALS_FULL_FLOW_STAGE,
    POSITIONING_STAGE,
    TARGET_SPECIFIC_STAGE_PREFIX,
    check_and_record_attempt,
    default_real_data_run_ledger_path,
    finalize_attempt,
    inspect_attempts,
)


def test_first_positioning_attempt_for_source_set_is_allowed_and_recorded(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    decision = check_and_record_attempt(
        flow="positioning-and-evidence",
        vacancy_source_ref="https://example.com/jobs/123",
        career_fact_source_refs=["career-facts-1"],
        provider_execution_allowed=True,
        ledger_path=ledger_path,
    )

    assert decision.ready is True
    assert decision.stage == POSITIONING_STAGE
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert payload["attempts"][0]["stage"] == POSITIONING_STAGE
    assert payload["attempts"][0]["attempt_status"] == "started"


def test_duplicate_positioning_attempt_is_blocked(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    check_and_record_attempt(
        flow="positioning-and-evidence",
        vacancy_source_ref="https://example.com/jobs/123",
        career_fact_source_refs=["career-facts-1"],
        provider_execution_allowed=True,
        ledger_path=ledger_path,
    )

    decision = check_and_record_attempt(
        flow="positioning-and-evidence",
        vacancy_source_ref="https://example.com/jobs/123",
        career_fact_source_refs=["career-facts-1"],
        provider_execution_allowed=True,
        ledger_path=ledger_path,
    )

    assert decision.ready is False
    assert decision.blocked_reason == "duplicate_provider_stage_attempt"
    assert decision.safe_to_retry_with_explicit_override is True


def test_application_materials_full_flow_first_attempt_is_allowed(tmp_path: Path) -> None:
    decision = check_and_record_attempt(
        flow="application-materials",
        vacancy_source_ref="vacancy-123",
        career_fact_source_refs=["career-facts-1"],
        provider_execution_allowed=True,
        ledger_path=tmp_path / "ledger.json",
    )

    assert decision.ready is True
    assert decision.stage == APPLICATION_MATERIALS_FULL_FLOW_STAGE


def test_duplicate_full_flow_attempt_is_blocked(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    check_and_record_attempt(
        flow="application-materials",
        vacancy_source_ref="vacancy-123",
        career_fact_source_refs=["career-facts-1"],
        provider_execution_allowed=True,
        ledger_path=ledger_path,
    )

    decision = check_and_record_attempt(
        flow="application-materials",
        vacancy_source_ref="vacancy-123",
        career_fact_source_refs=["career-facts-1"],
        provider_execution_allowed=True,
        ledger_path=ledger_path,
    )

    assert decision.ready is False
    assert decision.blocked_reason == "duplicate_provider_stage_attempt"


def test_target_specific_followup_after_full_flow_is_blocked_by_default(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    check_and_record_attempt(
        flow="application-materials",
        vacancy_source_ref="vacancy-123",
        career_fact_source_refs=["career-facts-1"],
        provider_execution_allowed=True,
        ledger_path=ledger_path,
    )

    decision = check_and_record_attempt(
        flow="application-materials",
        vacancy_source_ref="vacancy-123",
        career_fact_source_refs=["career-facts-1"],
        provider_execution_allowed=True,
        document_target="cover_letter_draft",
        ledger_path=ledger_path,
    )

    assert decision.ready is False
    assert decision.stage == f"{TARGET_SPECIFIC_STAGE_PREFIX}cover_letter_draft"
    assert decision.blocked_reason == "target_specific_followup_after_full_flow_requires_explicit_approval"


def test_target_specific_followup_can_be_allowed_only_with_explicit_override(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    check_and_record_attempt(
        flow="application-materials",
        vacancy_source_ref="vacancy-123",
        career_fact_source_refs=["career-facts-1"],
        provider_execution_allowed=True,
        ledger_path=ledger_path,
    )

    decision = check_and_record_attempt(
        flow="application-materials",
        vacancy_source_ref="vacancy-123",
        career_fact_source_refs=["career-facts-1"],
        provider_execution_allowed=True,
        document_target="recruiter_message_draft",
        ledger_path=ledger_path,
        explicit_override=True,
    )

    assert decision.ready is True
    assert decision.stage == f"{TARGET_SPECIFIC_STAGE_PREFIX}recruiter_message_draft"


def test_read_only_report_inspection_does_not_create_or_block_attempts(tmp_path: Path) -> None:
    ledger_path = tmp_path / "missing-ledger.json"

    attempts = inspect_attempts(
        flow="application-materials",
        vacancy_source_ref="vacancy-123",
        career_fact_source_refs=["career-facts-1"],
        ledger_path=ledger_path,
    )

    assert attempts == []
    assert not ledger_path.exists()


def test_source_set_hashing_does_not_expose_raw_inputs(tmp_path: Path) -> None:
    raw_url = "https://example.com/jobs/secret-airwallex"
    raw_source = "career-facts-private-id"
    ledger_path = tmp_path / "ledger.json"
    decision = check_and_record_attempt(
        flow="positioning-and-evidence",
        vacancy_source_ref=raw_url,
        career_fact_source_refs=[raw_source],
        provider_execution_allowed=True,
        ledger_path=ledger_path,
    )

    encoded = json.dumps(decision.to_dict(), sort_keys=True)
    ledger_encoded = ledger_path.read_text(encoding="utf-8")
    assert raw_url not in encoded
    assert raw_source not in encoded
    assert raw_url not in ledger_encoded
    assert raw_source not in ledger_encoded
    assert "sha256:" in decision.source_set_hash


def test_ledger_result_is_report_safe(tmp_path: Path) -> None:
    decision = check_and_record_attempt(
        flow="application-materials",
        vacancy_source_ref="/Users/denis/private/vacancy.txt",
        career_fact_source_refs=["/home/hermes/.hermes/private/career/resume.md"],
        provider_execution_allowed=True,
        document_target="cv_tailoring_notes",
        ledger_path=tmp_path / "ledger.json",
        report_path="/Users/denis/private/report.json",
    )

    encoded = json.dumps(decision.to_dict(), sort_keys=True)
    assert "/Users/denis" not in encoded
    assert ".hermes/private" not in encoded


def test_ledger_does_not_write_repo_db_or_crm(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    decision = check_and_record_attempt(
        flow="positioning-and-evidence",
        vacancy_source_ref="vacancy-123",
        career_fact_source_refs=["career-facts-1"],
        provider_execution_allowed=True,
        ledger_path=ledger_path,
    )
    finalized = finalize_attempt(
        run_id=str(decision.run_id),
        attempt_status="completed",
        ledger_path=ledger_path,
        report_path="/private/tmp/report.json",
        exit_status=0,
    )

    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "recruiter_real_data_run_ledger_v1"
    assert payload["attempts"][0]["report_path"] == "/private/tmp/report.json"
    assert finalized is not None
    assert default_real_data_run_ledger_path().name == "hermes_recruiter_real_data_run_ledger.json"
