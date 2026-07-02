from __future__ import annotations

import json

import pytest

from hermes_cli.recruiter_candidate_facts import (
    SCHEMA_VERSION,
    build_application_materials_ready_fixture_payload,
    build_candidate_facts_packet,
    build_safe_source_id_hash,
    load_candidate_facts_packet,
    validate_no_unsafe_leakage,
    validate_candidate_facts_ready_for_positioning,
)


def _safe_fixture(
    *,
    approval_required: bool = False,
    provider_text: str | None = None,
    safe_summary: str = "Product and commercial leadership experience",
    source_label: str = "safe_fixture",
    source_id_hash: str | None = None,
    section_label: str = "safe_section",
    claim_text: str = "Product and commercial leadership experience in digital services.",
) -> dict[str, object]:
    return {
        "candidate_ref": "candidate-test",
        "facts": [
            {
                "fact_id": "fact-1",
                "category": "domain",
                "safe_summary": safe_summary,
                "provider_text": provider_text
                or "Candidate has product and commercial leadership experience in digital services.",
                "support_level": "explicit",
                "source_ref_ids": ["src-1"],
                "forbidden_expansions": ["Do not infer revenue ownership", "Do not infer team size"],
                "approval_required": approval_required,
                "provider_visible": True,
                "log_visible": True,
            }
        ],
        "source_references": [
            {
                "source_ref_id": "src-1",
                "source_type": "test_fixture",
                "source_label": source_label,
                "source_id_hash": source_id_hash or build_safe_source_id_hash(source_label, section_label),
                "section_label": section_label,
                "content_hash": "fixture-content-hash",
                "sensitivity": "private_sanitized",
                "provider_visible": True,
                "log_visible": True,
            }
        ],
        "allowed_claims": [
            {
                "claim_id": "claim-1",
                "claim_text": claim_text,
                "source_fact_ids": ["fact-1"],
                "support_level": "explicit",
            }
        ],
        "claims_to_avoid": ["Do not claim revenue ownership."],
        "unsupported_claims": [],
    }


def _raw(packet) -> str:
    return json.dumps(packet.to_dict(), sort_keys=True)


def test_schema_version_is_exact() -> None:
    packet = build_candidate_facts_packet(private_context_status="PRIVATE_CONTEXT_MISSING")

    assert packet.schema_version == SCHEMA_VERSION


def test_missing_private_context_returns_blocked_packet() -> None:
    packet = build_candidate_facts_packet(private_context_status="PRIVATE_CONTEXT_MISSING")

    assert packet.status == "BLOCKED_PRIVATE_CONTEXT_MISSING"
    assert packet.provider_visibility_status == "BLOCKED_PRIVATE_CONTEXT_MISSING"
    assert packet.errors == ["private_context_missing"]


def test_available_private_context_without_extractor_is_blocked_approval_required() -> None:
    packet = build_candidate_facts_packet(private_context_status="PRIVATE_CONTEXT_AVAILABLE")

    assert packet.status == "BLOCKED_APPROVAL_REQUIRED"
    assert packet.provider_visibility_status == "BLOCKED_APPROVAL_REQUIRED"
    assert packet.requires_user_approval is True
    assert packet.facts == []
    assert packet.allowed_claims == []


def test_safe_fixture_facts_produce_ready_provider_visible_packet() -> None:
    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=_safe_fixture(),
        generated_at="2026-07-01T00:00:00+00:00",
    )

    raw = _raw(packet)
    assert packet.status == "READY_PROVIDER_VISIBLE"
    assert packet.provider_visibility_status == "READY_PROVIDER_VISIBLE"
    assert packet.facts[0]["safe_summary"] == "Product and commercial leadership experience"
    assert packet.facts[0]["provider_text"].startswith("Candidate has product")
    assert "/home/" not in raw
    assert "/Users/" not in raw


def test_application_materials_ready_fixture_payload_is_rich_and_resolvable() -> None:
    fixture = build_application_materials_ready_fixture_payload()

    assert fixture["candidate_ref"] == "candidate-application-materials-fixture"
    assert len(fixture["facts"]) >= 6
    assert len(fixture["source_references"]) >= 4
    assert len(fixture["allowed_claims"]) >= 4
    assert len(fixture["claims_to_avoid"]) >= 2
    assert len(fixture["unsupported_claims"]) >= 2
    source_ref_ids = {item["source_ref_id"] for item in fixture["source_references"]}
    fact_ids = {item["fact_id"] for item in fixture["facts"]}
    assert {"domain", "achievement", "role_history", "scope"} <= {item["category"] for item in fixture["facts"]}
    assert all(item["source_ref_ids"] for item in fixture["facts"])
    assert all(set(item["source_ref_ids"]) <= source_ref_ids for item in fixture["facts"])
    assert all(set(item["source_fact_ids"]) <= fact_ids for item in fixture["allowed_claims"])


def test_application_materials_ready_fixture_packet_stays_privacy_safe() -> None:
    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=build_application_materials_ready_fixture_payload(),
        generated_at="2026-07-01T00:00:00+00:00",
    )

    raw = _raw(packet)
    assert packet.status == "READY_PROVIDER_VISIBLE"
    assert packet.provider_visibility_status == "READY_PROVIDER_VISIBLE"
    assert len(packet.facts) >= 6
    assert len(packet.source_references) >= 4
    assert len(packet.allowed_claims) >= 4
    assert "/home/" not in raw
    assert "/Users/" not in raw
    assert "@" not in raw


def test_unsafe_candidate_ref_blocks_packet_and_is_not_serialized() -> None:
    fixture = _safe_fixture()
    fixture["candidate_ref"] = "See /home/hermes/.hermes/private/career/resume.md for candidate-test"

    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=fixture,
    )

    raw = _raw(packet)
    assert packet.status == "BLOCKED_UNSAFE_CONTENT"
    assert packet.provider_visibility_status == "BLOCKED_UNSAFE_CONTENT"
    assert "unsafe_path_detected" in packet.errors
    assert "/home/hermes" not in raw
    assert ".hermes/private" not in raw
    assert "candidate-test" not in raw


def test_unsafe_redactions_block_packet_and_are_not_serialized() -> None:
    fixture = _safe_fixture()
    fixture["redactions"] = [
        "Leaked contact: candidate@example.com and phone +7 701 110 2626 and path /home/hermes/.hermes/private/career/notes.md"
    ]

    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=fixture,
    )

    raw = _raw(packet)
    assert packet.status == "BLOCKED_UNSAFE_CONTENT"
    assert "unsafe_contact_detected" in packet.errors
    assert "unsafe_path_detected" in packet.errors
    assert "candidate@example.com" not in raw
    assert "+7 701 110 2626" not in raw
    assert "/home/hermes" not in raw


def test_candidate_ref_and_redactions_bypass_fixture_blocks_and_redacts() -> None:
    fixture = _safe_fixture()
    fixture["candidate_ref"] = "See /home/hermes/.hermes/private/career/resume.md for candidate-test"
    fixture["redactions"] = [
        "Leaked contact: candidate@example.com and phone +7 701 110 2626 and path /home/hermes/.hermes/private/career/notes.md"
    ]

    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=fixture,
    )

    raw = _raw(packet)
    assert packet.status == "BLOCKED_UNSAFE_CONTENT"
    assert packet.facts == []
    assert packet.allowed_claims == []
    assert "/home/hermes" not in raw
    assert "candidate@example.com" not in raw
    assert "+7 701 110 2626" not in raw


def test_final_packet_privacy_scan_covers_all_serialized_fields() -> None:
    fixture = _safe_fixture()
    fixture["candidate_ref"] = "safe-candidate"
    fixture["redactions"] = ["candidate@example.com"]

    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=fixture,
    )

    assert packet.status == "BLOCKED_UNSAFE_CONTENT"


def test_load_candidate_facts_packet_accepts_ready_fixture_packet(tmp_path) -> None:
    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=_safe_fixture(),
        generated_at="2026-07-01T00:00:00+00:00",
    )
    path = tmp_path / "candidate-facts-ready.json"
    path.write_text(json.dumps(packet.to_dict()), encoding="utf-8")

    loaded = load_candidate_facts_packet(path)

    assert loaded["schema_version"] == "recruiter_candidate_facts_packet_v1"
    assert loaded["status"] == "READY_PROVIDER_VISIBLE"
    assert loaded["provider_visibility_status"] == "READY_PROVIDER_VISIBLE"


def test_validate_candidate_facts_ready_for_positioning_rejects_blocked_packet() -> None:
    packet = build_candidate_facts_packet(private_context_status="PRIVATE_CONTEXT_MISSING").to_dict()

    error = validate_candidate_facts_ready_for_positioning(packet)

    assert error == "candidate_facts_packet_not_provider_visible"


def test_validate_candidate_facts_ready_for_positioning_rejects_invalid_schema() -> None:
    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=_safe_fixture(),
    ).to_dict()
    packet["schema_version"] = "unknown"

    error = validate_candidate_facts_ready_for_positioning(packet)

    assert error == "candidate_facts_packet_schema_invalid"


def test_validate_candidate_facts_ready_for_positioning_rejects_empty_facts() -> None:
    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=_safe_fixture(),
    ).to_dict()
    packet["facts"] = []

    error = validate_candidate_facts_ready_for_positioning(packet)

    assert error == "candidate_facts_packet_empty_facts"


def test_validate_candidate_facts_ready_for_positioning_rejects_missing_optional_lists() -> None:
    packet = {
        "schema_version": "recruiter_candidate_facts_packet_v1",
        "status": "READY_PROVIDER_VISIBLE",
        "provider_visibility_status": "READY_PROVIDER_VISIBLE",
        "facts": [{"fact_id": "x"}],
    }

    error = validate_candidate_facts_ready_for_positioning(packet)

    assert error == "candidate_facts_packet_invalid"


def test_load_candidate_facts_packet_invalid_json_fails_closed(tmp_path) -> None:
    path = tmp_path / "candidate-facts-invalid.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="candidate_facts_packet_json_invalid"):
        load_candidate_facts_packet(path)


def test_bare_tilde_path_in_safe_summary_blocks_packet() -> None:
    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=_safe_fixture(safe_summary="See ~/Desktop/candidate-resume-notes.txt for background"),
    )

    raw = _raw(packet)
    assert packet.status == "BLOCKED_UNSAFE_CONTENT"
    assert packet.provider_visibility_status == "BLOCKED_UNSAFE_CONTENT"
    assert "unsafe_path_detected" in packet.errors
    assert "~/Desktop" not in raw
    assert "candidate-resume-notes" not in raw


def test_home_private_path_blocks_and_is_not_serialized() -> None:
    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=_safe_fixture(safe_summary="See /home/hermes/.hermes/private/career/resume.md"),
    )

    raw = _raw(packet)
    assert packet.status == "BLOCKED_UNSAFE_CONTENT"
    assert "unsafe_path_detected" in packet.errors
    assert "/home/hermes" not in raw
    assert ".hermes/private" not in raw
    assert "private/career" not in raw


def test_email_in_provider_text_blocks_and_is_not_serialized() -> None:
    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=_safe_fixture(provider_text="Email candidate@example.com"),
    )

    raw = _raw(packet)
    assert packet.status == "BLOCKED_UNSAFE_CONTENT"
    assert "unsafe_contact_detected" in packet.errors
    assert "candidate@example.com" not in raw


def test_phone_in_provider_text_blocks_and_is_not_serialized() -> None:
    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=_safe_fixture(provider_text="Call +7 701 110 2626"),
    )

    raw = _raw(packet)
    assert packet.status == "BLOCKED_UNSAFE_CONTENT"
    assert "unsafe_contact_detected" in packet.errors
    assert "+7 701 110 2626" not in raw


def test_unsafe_source_reference_fields_are_not_serialized() -> None:
    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=_safe_fixture(
            source_label="/home/hermes/.hermes/private/career/resume.md",
            source_id_hash="/home/hermes/.hermes/private/career/resume.md",
            section_label="raw private heading",
        ),
    )

    raw = _raw(packet)
    assert packet.status == "BLOCKED_UNSAFE_CONTENT"
    assert "/home/hermes/.hermes/private/career/resume.md" not in raw
    assert "raw private heading" not in raw


def test_approval_required_blocks_provider_visibility_and_withholds_content() -> None:
    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=_safe_fixture(approval_required=True),
    )

    raw = _raw(packet)
    assert packet.status == "BLOCKED_APPROVAL_REQUIRED"
    assert packet.provider_visibility_status == "BLOCKED_APPROVAL_REQUIRED"
    assert packet.requires_user_approval is True
    assert packet.facts == []
    assert packet.allowed_claims == []
    assert "Candidate has product" not in raw


def test_unsupported_fact_cannot_become_allowed_claim_and_is_withheld() -> None:
    fixture = _safe_fixture(claim_text="Unsupported executive revenue ownership claim.")
    fixture["facts"][0]["support_level"] = "unsupported"
    fixture["allowed_claims"][0]["support_level"] = "unsupported"
    packet = build_candidate_facts_packet(
        private_context_status="PRIVATE_CONTEXT_AVAILABLE",
        fixture_payload=fixture,
    )

    raw = _raw(packet)
    assert packet.status == "BLOCKED_UNSUPPORTED_FACTS"
    assert packet.provider_visibility_status == "BLOCKED_UNSUPPORTED_FACTS"
    assert "unsupported_claims_detected" in packet.errors
    assert "Unsupported executive revenue ownership claim." not in raw


def test_recursive_privacy_validator_blocks_absolute_paths() -> None:
    assert validate_no_unsafe_leakage({"provider_text": "/home/hermes/.hermes/private/career"}) == "unsafe_path_detected"
