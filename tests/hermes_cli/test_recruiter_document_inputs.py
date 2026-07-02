from __future__ import annotations

import json
from pathlib import Path

from hermes_cli.recruiter_document_inputs import (
    ALLOWED_DOCUMENT_TYPES,
    REQUIRED_POSITIONING_FIELDS,
    RecruiterDocumentInputStatus,
    build_recruiter_document_writer_input_packet,
)
from hermes_cli.recruiter_candidate_facts import build_application_materials_ready_fixture_payload
from hermes_cli.recruiter_skill_execution import RecruiterSkillExecutionReport, RecruiterSkillExecutionStatus


REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT = object()


def _execution_report(
    *,
    status: RecruiterSkillExecutionStatus = RecruiterSkillExecutionStatus.EXECUTION_READY,
    vacancy_result: dict[str, object] | None | object = _DEFAULT,
    positioning_result: dict[str, object] | None | object = _DEFAULT,
    document_writer_gate: str = "POSITIONING_AVAILABLE",
) -> RecruiterSkillExecutionReport:
    return RecruiterSkillExecutionReport(
        status=status,
        flow_id="evaluate-and-position",
        context_status="READY",
        skill_input_status="READY",
        execution_status="completed",
        provider_called=False,
        executor_called=True,
        vacancy_evaluation_result=vacancy_result
        if vacancy_result is not _DEFAULT
        else {
            "status": "SUCCESS",
            "skill_id": "vacancy-evaluation",
            "vacancy_evaluation_summary": "Strong fit for executive product role.",
            "fit_interpretation": "Match is strong on product leadership and scale.",
            "evidence_gaps": ["Exact team size not confirmed."],
            "recommendation_for_next_step": "Proceed to draft preparation.",
            "provenance": {"source": "fake-test"},
        },
        positioning_evidence_result=positioning_result
        if positioning_result is not _DEFAULT
        else {
            "status": "SUCCESS",
            "skill_id": "positioning-and-evidence",
            "positioning_summary": "Lead with platform scaling and executive product leadership.",
            "evidence_map": {"leadership": ["Scaled multi-team product org."]},
            "proven_facts": ["Led product orgs.", "Built B2B platforms."],
            "derived_positioning": ["Position as operator with platform depth."],
            "gaps": ["Need stronger direct domain match proof."],
            "risks_and_mitigations": ["Avoid overstating company-stage similarity."],
            "provenance": {"source": "fake-test"},
        },
        downstream_gates={
            "document_writer": {
                "skill_id": "document-writer",
                "status": document_writer_gate,
                "reason": (
                    "positioning packet available for downstream draft-only writer"
                    if document_writer_gate == "POSITIONING_AVAILABLE"
                    else "document-writer requires positioning-and-evidence output packet"
                ),
                "requires": ["positioning-and-evidence"],
                "references": ["role-packages/recruiter/skills/document-writer/SKILL.md"],
            }
        },
        warnings=[],
        errors=[],
        provenance={"writes_performed": False, "session_id": "fake-session"},
        forbidden_actions=[
            "send_outbound_message",
            "apply_to_job",
            "write_crm",
            "write_job_intel_db",
            "read_private_file_contents",
            "mutate_live_config",
            "restart_gateway",
        ],
        planned_flow=["vacancy-evaluation", "positioning-and-evidence"],
    )


def _application_materials_positioning_result() -> dict[str, object]:
    fixture = build_application_materials_ready_fixture_payload()
    facts_by_id = {
        str(item["fact_id"]): item
        for item in fixture["facts"]
        if isinstance(item, dict) and item.get("fact_id")
    }
    return {
        "status": "SUCCESS",
        "skill_id": "positioning-and-evidence",
        "positioning_summary": "Lead with broad executive product leadership across payments, pricing, and telecom ecosystems.",
        "evidence_map": {
            "positioning_evidence": [str(item["safe_summary"]) for item in fixture["facts"] if item.get("safe_summary")],
            "source_references": fixture["source_references"],
        },
        "proven_facts": [str(item["claim_text"]) for item in fixture["allowed_claims"]],
        "derived_positioning": ["Broad executive operator across product strategy, commercial ownership, and monetization."],
        "gaps": ["Exact employer names and headcount should remain omitted."],
        "risks_and_mitigations": [
            "Do not claim direct bank or card-network ownership.",
            "Do not present telecom adjacency as direct telecom category leadership.",
            "Avoid broad executive seniority branding unsupported by evidence.",
        ],
        "allowed_claims": [
            {
                **dict(item),
                "evidence_item_ids": list(item.get("source_fact_ids") or []),
                "source_ref_ids": sorted(
                    {
                        str(source_ref_id)
                        for fact_id in item.get("source_fact_ids") or []
                        for source_ref_id in list(facts_by_id.get(str(fact_id), {}).get("source_ref_ids") or [])
                        if isinstance(source_ref_id, str)
                    }
                ),
            }
            for item in fixture["allowed_claims"]
        ],
        "evidence_items": [
            {
                "evidence_item_id": str(item["fact_id"]),
                "claim_text": str(item["safe_summary"]),
                "source_fact_ids": [str(item["fact_id"])],
                "source_ref_ids": [str(ref) for ref in item.get("source_ref_ids") or [] if isinstance(ref, str)],
                "support_level": str(item.get("support_level") or "explicit"),
                "category": str(item.get("category") or ""),
                "safe_summary": str(item.get("safe_summary") or ""),
            }
            for item in fixture["facts"]
        ],
        "source_references": fixture["source_references"],
        "claims_to_avoid": fixture["claims_to_avoid"],
        "unsupported_claims": fixture["unsupported_claims"],
        "support_summary": {"explicit": 4, "derived_safe": 2, "weak": 1, "unsupported": 0},
        "privacy_notes": ["sanitized fixture packet"],
        "generation_mode": "deterministic_fake",
        "source_kind": "fake_candidate_facts",
        "provenance": {"source": "fake-application-materials"},
    }


def test_ready_happy_path_builds_json_serializable_writer_input() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="cover_letter",
        audience="HR recruiter",
        purpose="First-pass tailored draft",
    )

    assert packet.status is RecruiterDocumentInputStatus.READY
    assert packet.document_writer_input is not None
    assert packet.document_writer_input["status"] == "READY"
    assert packet.document_writer_input["skill_id"] == "document-writer"
    assert packet.document_writer_input["document_type"] == "cover_letter"
    assert packet.document_writer_input["audience"] == "HR recruiter"
    assert packet.document_writer_input["purpose"] == "First-pass tailored draft"
    assert packet.document_writer_input["document_constraints"]["genre"] == "submission_ready_cover_letter"
    assert "draft" not in packet.document_writer_input
    assert "generate_final_draft" in packet.forbidden_actions
    assert "execute_document_writer" in packet.forbidden_actions
    assert packet.downstream_gates["document_writer"]["status"] == "READY_FOR_INPUT"
    json.dumps(packet.to_dict(), sort_keys=True)


def test_positioning_required_when_positioning_result_missing() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=None, document_writer_gate="POSITIONING_REQUIRED"),
        document_type="cover_letter",
    )

    assert packet.status is RecruiterDocumentInputStatus.POSITIONING_REQUIRED
    assert packet.document_writer_input is None
    assert "document-writer requires positioning-and-evidence output packet" in packet.errors


def test_positioning_required_when_document_writer_gate_not_available() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(document_writer_gate="POSITIONING_REQUIRED"),
        document_type="cover_letter",
    )

    assert packet.status is RecruiterDocumentInputStatus.POSITIONING_REQUIRED
    assert packet.document_writer_input is None
    assert any("positioning-and-evidence output packet" in item for item in packet.errors)


def test_invalid_positioning_result_missing_required_field_is_blocked() -> None:
    positioning_result = {
        "status": "SUCCESS",
        "skill_id": "positioning-and-evidence",
        "positioning_summary": "Lead with platform scaling and executive product leadership.",
        "evidence_map": {"leadership": ["Scaled multi-team product org."]},
        "proven_facts": ["Led product orgs.", "Built B2B platforms."],
        "derived_positioning": ["Position as operator with platform depth."],
        "gaps": ["Need stronger direct domain match proof."],
    }

    packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=positioning_result),
        document_type="cover_letter",
    )

    assert packet.status is RecruiterDocumentInputStatus.BLOCKED_POSITIONING_RESULT_INVALID
    assert packet.document_writer_input is None
    assert "missing_positioning_fields:risks_and_mitigations" in packet.errors


def test_unsupported_document_type_is_controlled_block() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="press_release",
    )

    assert packet.status is RecruiterDocumentInputStatus.BLOCKED_UNSUPPORTED_DOCUMENT_TYPE
    assert packet.document_writer_input is None


def test_missing_vacancy_context_is_blocked() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(vacancy_result=None),
        document_type="cover_letter",
    )

    assert packet.status is RecruiterDocumentInputStatus.BLOCKED_MISSING_VACANCY_CONTEXT
    assert "vacancy_evaluation_result_missing" in packet.errors


def test_non_ready_execution_report_is_blocked() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(status=RecruiterSkillExecutionStatus.PROVIDER_EXECUTION_BLOCKED),
        document_type="cover_letter",
    )

    assert packet.status is RecruiterDocumentInputStatus.BLOCKED_INVALID_EXECUTION_REPORT
    assert "execution_report_not_ready:PROVIDER_EXECUTION_BLOCKED" in packet.errors


def test_dict_input_path_is_supported() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report().to_dict(),
        document_type="cover_letter",
        audience="HR recruiter",
        purpose="Tailored draft",
    )

    assert packet.status is RecruiterDocumentInputStatus.READY
    assert packet.document_writer_input is not None


def test_unsupported_document_type_takes_precedence_over_invalid_execution_report() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(status=RecruiterSkillExecutionStatus.PROVIDER_EXECUTION_BLOCKED),
        document_type="press_release",
    )

    assert packet.status is RecruiterDocumentInputStatus.BLOCKED_UNSUPPORTED_DOCUMENT_TYPE
    assert "unsupported_document_type:press_release" in packet.errors
    assert not any(item.startswith("execution_report_not_ready:") for item in packet.errors)


def test_ready_packet_mentions_future_draft_schema_without_generating_draft() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="linkedin_dm",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    assert writer_input["expected_future_output_schema"] == "recruiter_document_packet_v1"
    assert "draft" in writer_input["expected_future_output_fields"]
    assert "draft" not in writer_input


def test_missing_audience_and_purpose_warn_but_do_not_block() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="linkedin_dm",
    )

    assert packet.status is RecruiterDocumentInputStatus.READY
    assert "audience_missing" in packet.warnings
    assert "purpose_missing" in packet.warnings


def test_cover_letter_constraints_require_submission_ready_grounded_letter() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="cover_letter",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    constraints = packet.document_writer_input["document_constraints"]
    assert writer_input["requested_document_type"] == "cover_letter"
    assert constraints["document_type"] == "cover_letter"
    assert constraints["genre"] == "submission_ready_cover_letter"
    assert "a submission-ready letter structure" in constraints["must_include"]
    assert "disclaimer paragraph" in constraints["must_avoid"]
    assert "synthetic packet" in constraints["must_avoid"]
    assert "evidence packet" in constraints["must_avoid"]
    assert "source-backed claims" in constraints["must_avoid"]
    assert "conditional claims" in constraints["must_avoid"]
    assert "reviewer" in constraints["must_avoid"]
    assert "Hermes" in constraints["must_avoid"]
    assert "Use only facts supported by the provided evidence." in constraints["grounding_rules"]
    assert "Do not invent employers, dates, metrics, team sizes, revenue numbers, product names, or outcomes." in constraints["grounding_rules"]


def test_cover_letter_constraints_guard_against_writer_overclaims() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="cover_letter",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    constraints = writer_input["document_constraints"]
    assert (
        "Do not convert broad positioning_summary or recommended_angle language into stronger ownership, seniority, or leadership claims."
        in constraints["grounding_rules"]
    )
    assert (
        "Do not claim product leadership, strategy ownership, roadmap ownership, commercial ownership, pricing ownership, monetization ownership, or multi-team leadership unless the evidence explicitly supports it."
        in constraints["grounding_rules"]
    )
    assert (
        "If a claim appears in claims_to_avoid, unsupported_claims, or risk notes, do not use it."
        in constraints["grounding_rules"]
    )
    assert (
        "If the evidence is adjacent rather than direct, soften payments, platform, telecom, or commercially sensitive experience instead of presenting it as direct ownership."
        in constraints["grounding_rules"]
    )
    assert (
        "Prefer specific supported examples over broad leadership or seniority branding."
        in constraints["grounding_rules"]
    )
    assert (
        "If concrete metrics, outcomes, or scope are unavailable, write a modest fit statement instead of inventing scale or impact."
        in constraints["grounding_rules"]
    )
    assert "When in doubt, omit or soften the claim rather than overstate it." in constraints["grounding_rules"]
    assert "unsupported product leadership" in constraints["must_avoid"]
    assert "unsupported strategy ownership" in constraints["must_avoid"]
    assert "unsupported roadmap ownership" in constraints["must_avoid"]
    assert "unsupported commercial ownership" in constraints["must_avoid"]
    assert "unsupported pricing ownership" in constraints["must_avoid"]
    assert "unsupported monetization ownership" in constraints["must_avoid"]
    assert "unsupported multi-team leadership" in constraints["must_avoid"]
    assert "unsupported payments leadership" in constraints["must_avoid"]
    assert "unsupported telecom category leadership" in constraints["must_avoid"]
    assert "unsupported commercially sensitive product environments" in constraints["must_avoid"]


def test_recruiter_message_constraints_require_short_non_meta_message() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="recruiter_message",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    constraints = packet.document_writer_input["document_constraints"]
    assert writer_input["requested_document_type"] == "recruiter_message"
    assert constraints["document_type"] == "recruiter_message"
    assert constraints["genre"] == "concise_recruiter_message"
    assert "a concise recruiter-facing outreach or application message" in constraints["must_include"]
    assert "2-4 short sentences maximum" in constraints["must_include"]
    assert "one role-interest sentence" in constraints["must_include"]
    assert "one evidence-backed fit sentence" in constraints["must_include"]
    assert "optional soft call-to-action" in constraints["must_include"]
    assert "synthetic packet" in constraints["must_avoid"]
    assert "evidence packet" in constraints["must_avoid"]
    assert "reviewer" in constraints["must_avoid"]
    assert "Hermes" in constraints["must_avoid"]
    assert "product executive" in constraints["must_avoid"]
    assert "payments leader" in constraints["must_avoid"]
    assert "commercially sensitive environments" in constraints["must_avoid"]
    assert "strategic bets" in constraints["must_avoid"]
    assert "monetization launches" in constraints["must_avoid"]
    assert "cross-functional launches" in constraints["must_avoid"]
    assert "unsupported employer names" in constraints["must_avoid"]
    assert "Use only 1-2 of the strongest supported claims from the provided evidence." in constraints["grounding_rules"]
    assert "Keep the message to 2-4 short sentences with no cover-letter structure." in constraints["grounding_rules"]
    assert "If a claim is only adjacent rather than exact, soften it with phrases like relevant adjacent experience, could be relevant, experience that may map well to, or background across." in constraints["grounding_rules"]
    assert "Do not use broad executive-branding language or unsupported title/seniority claims." in constraints["grounding_rules"]
    assert "Do not invent payments leadership, strategic bets, monetization launches, cross-functional launches, executive-level ownership, team size, metrics, revenue, product scale, employer names, dates, or outcomes." in constraints["grounding_rules"]
    assert "Keep the message short, concrete, human, and naturally conservative." in constraints["grounding_rules"]
    assert "is 2-4 short sentences, recruiter-facing, and concrete" in constraints["review_success_criteria"]
    assert "uses only 1-2 supported claims without stacking unsupported claims" in constraints["review_success_criteria"]
    assert "softens adjacent experience rather than overstating it" in constraints["review_success_criteria"]


def test_recruiter_message_writer_input_preserves_tight_constraints() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="recruiter_message",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    constraints = writer_input["document_constraints"]
    assert writer_input["requested_document_type"] == "recruiter_message"
    assert writer_input["document_type"] == "recruiter_message"
    assert constraints["document_type"] == "recruiter_message"
    assert constraints["target_audience"] == "Recruiter"
    assert constraints["tone"] == "short, concrete, human, and professional"
    assert "no generic \"I am uniquely positioned\" style" in constraints["must_avoid"]
    assert "no broad executive branding paragraph" in constraints["must_avoid"]
    assert "\u201c" not in "".join(constraints["must_avoid"])
    assert "\u201d" not in "".join(constraints["must_avoid"])


def test_recruiter_message_constraints_guard_against_blind_overclaim_imports() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="recruiter_message",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    constraints = writer_input["document_constraints"]
    assert (
        "Do not import broad positioning_summary or recommended_angle branding blindly."
        in constraints["grounding_rules"]
    )
    assert (
        "If a claim appears in claims_to_avoid, unsupported_claims, or risk notes, do not use it."
        in constraints["grounding_rules"]
    )
    assert "When in doubt, omit the claim rather than overstate it." in constraints["grounding_rules"]
    assert "fintech leader" in constraints["must_avoid"]
    assert "senior executive" in constraints["must_avoid"]
    assert "executive-level ownership" in constraints["must_avoid"]


def test_cv_tailoring_notes_constraints_preserve_analytical_language() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(),
        document_type="cv_tailoring_notes",
    )

    constraints = packet.document_writer_input["document_constraints"]
    assert constraints["document_type"] == "cv_tailoring_notes"
    assert constraints["genre"] == "analytical_cv_tailoring_notes"
    assert "supported claims to use" in constraints["must_include"]
    assert "unsupported claims to avoid" in constraints["must_include"]
    assert "It may explicitly list supported claims, unsupported claims to avoid, gaps, and evidence notes." in constraints["grounding_rules"]
    assert "Separate use claims from avoid claims when practical." in constraints["grounding_rules"]


def test_cover_letter_writer_input_uses_safe_claims_subset_for_outward_facing_draft() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=_application_materials_positioning_result()),
        document_type="cover_letter",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    safe_claims = writer_input["safe_claims_for_document"]
    assert 1 <= len(safe_claims) <= 3
    assert writer_input["claim_source_priority"]["primary"] == "safe_claims_for_document"
    assert writer_input["claim_source_priority"]["context_only"] == [
        "positioning_summary",
        "recommended_angle",
    ]
    assert all(item["source_ref_ids"] for item in safe_claims)
    assert all(item["evidence_item_ids"] for item in safe_claims)
    assert "product executive" not in json.dumps(safe_claims, sort_keys=True)
    assert "roadmap ownership" not in json.dumps(safe_claims, sort_keys=True)
    assert "commercial ownership" not in json.dumps(safe_claims, sort_keys=True)


def test_recruiter_message_writer_input_uses_smaller_safe_claim_subset() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=_application_materials_positioning_result()),
        document_type="recruiter_message",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    safe_claims = writer_input["safe_claims_for_document"]
    assert 1 <= len(safe_claims) <= 2
    assert writer_input["claim_source_priority"]["primary"] == "safe_claims_for_document"
    assert all(item["source_ref_ids"] for item in safe_claims)
    assert all(item["evidence_item_ids"] for item in safe_claims)


def test_outward_facing_writer_input_includes_locked_claim_sentences() -> None:
    for document_type, expected_max in (("cover_letter", 2), ("recruiter_message", 1)):
        packet = build_recruiter_document_writer_input_packet(
            _execution_report(positioning_result=_application_materials_positioning_result()),
            document_type=document_type,
        )

        writer_input = packet.document_writer_input
        assert writer_input is not None
        locked_claim_sentences = writer_input["locked_claim_sentences"]
        safe_claims = writer_input["safe_claims_for_document"]
        assert 1 <= len(locked_claim_sentences) <= expected_max
        assert len(locked_claim_sentences) <= len(safe_claims)
        for item in locked_claim_sentences:
            assert item["sentence"]
            assert item["source_ref_ids"]
            assert item["evidence_item_ids"]
            assert item["support_level"] in {"direct", "adjacent", "limited"}
            assert item["ownership_scope"] in {"direct", "collaborated", "adjacent", "exposed"}
            assert item["derived_from_safe_claim_id"]
            assert "allowed_claims" not in json.dumps(item, sort_keys=True)


def test_locked_claim_sentences_are_derived_from_safe_claims_only() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=_application_materials_positioning_result()),
        document_type="cover_letter",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    safe_claims = {
        item["claim_id"]: item
        for item in writer_input["safe_claims_for_document"]
    }
    for item in writer_input["locked_claim_sentences"]:
        safe_claim = safe_claims[item["derived_from_safe_claim_id"]]
        assert item["sentence"] == safe_claim["safe_wording"]
        assert item["source_ref_ids"] == safe_claim["source_ref_ids"]
        assert item["evidence_item_ids"] == safe_claim["evidence_item_ids"]
        assert item["support_level"] == safe_claim["support_level"]
        assert item["ownership_scope"] == safe_claim["ownership_scope"]


def test_outward_facing_writer_input_marks_safe_claims_as_only_draftable_source() -> None:
    cover_letter_packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=_application_materials_positioning_result()),
        document_type="cover_letter",
    )
    recruiter_message_packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=_application_materials_positioning_result()),
        document_type="recruiter_message",
    )

    for writer_input in (
        cover_letter_packet.document_writer_input,
        recruiter_message_packet.document_writer_input,
    ):
        assert writer_input is not None
        assert writer_input["claim_source_priority"]["primary"] == "safe_claims_for_document"
        assert writer_input["writer_guidance"]["safe_claims_for_document_primary"] is True
        assert writer_input["writer_guidance"]["safe_claims_only_draftable_source"] is True
        assert writer_input["writer_guidance"]["every_outward_facing_claim_must_anchor_to_safe_claim"] is True
        assert writer_input["writer_guidance"]["ownership_scope_must_be_respected"] is True
        assert writer_input["writer_guidance"]["unsupported_broad_claims_must_be_omitted"] is True
        assert writer_input["writer_guidance"]["locked_claim_sentences_only"] is True
        assert writer_input["writer_guidance"]["forbid_freeform_claims"] is True


def test_outward_facing_writer_guidance_forbids_freeform_claims_outside_locked_sentences() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=_application_materials_positioning_result()),
        document_type="cover_letter",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    guidance = writer_input["writer_guidance"]
    assert "You may only use claim sentences from locked_claim_sentences." in guidance["instructions"]
    assert "Do not add new experience, impact, ownership, scope, domain, metrics, employer, project, timeframe, or fit claims." in guidance["instructions"]
    assert "Opening and closing may be generic, but must not introduce claims." in guidance["instructions"]
    assert "If a claim is not in locked_claim_sentences, omit it." in guidance["instructions"]


def test_outward_facing_writer_guidance_requires_conservative_evidence_backed_structure() -> None:
    cover_letter_packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=_application_materials_positioning_result()),
        document_type="cover_letter",
    )
    recruiter_message_packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=_application_materials_positioning_result()),
        document_type="recruiter_message",
    )

    cover_writer_input = cover_letter_packet.document_writer_input
    recruiter_writer_input = recruiter_message_packet.document_writer_input
    assert cover_writer_input is not None
    assert recruiter_writer_input is not None
    assert cover_writer_input["writer_guidance"]["required_conservative_structure"] == [
        "short opening with no new claims",
        "one or two locked claim sentences only",
        "short closing with no new claims",
    ]
    assert recruiter_writer_input["writer_guidance"]["required_conservative_structure"] == [
        "max three short sentences total",
        "one interest or context sentence with no new claims",
        "exactly one locked claim sentence",
        "one soft call-to-action",
    ]
    assert "background across payments/platform services" in recruiter_writer_input["writer_guidance"]["forbidden_broad_claim_patterns"]
    assert "leadership/impact/scope claims" in recruiter_writer_input["writer_guidance"]["forbidden_broad_claim_patterns"]


def test_outward_facing_writer_input_keeps_broad_positioning_fields_context_only() -> None:
    for document_type in ("cover_letter", "recruiter_message"):
        packet = build_recruiter_document_writer_input_packet(
            _execution_report(positioning_result=_application_materials_positioning_result()),
            document_type=document_type,
        )

        writer_input = packet.document_writer_input
        assert writer_input is not None
        assert "positioning_summary" not in writer_input
        assert "recommended_angle" not in writer_input
        assert "allowed_claims" not in writer_input
        assert "proven_facts" not in writer_input
        assert "derived_positioning" not in writer_input

        if "context_only_not_for_claims" in writer_input:
            context_only = writer_input["context_only_not_for_claims"]
            assert context_only["do_not_quote_or_paraphrase_as_claims"] is True
            encoded = json.dumps(context_only, sort_keys=True).lower()
            assert "product executive" not in encoded
            assert "commercial ownership" not in encoded
            assert "monetization ownership" not in encoded

        positioning_context = writer_input["positioning_evidence_result"]
        assert "allowed_claims" not in positioning_context
        assert "proven_facts" not in positioning_context
        assert "derived_positioning" not in positioning_context
        assert "positioning_summary" not in positioning_context
        assert "recommended_angle" not in positioning_context


def test_cv_tailoring_notes_does_not_require_outward_safe_claim_subset() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=_application_materials_positioning_result()),
        document_type="cv_tailoring_notes",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    assert "safe_claims_for_document" not in writer_input
    assert "locked_claim_sentences" not in writer_input
    assert "claim_source_priority" not in writer_input
    assert writer_input["positioning_evidence_result"]["proven_facts"]
    assert writer_input["positioning_evidence_result"]["derived_positioning"]


def test_outward_facing_writer_input_keeps_minimal_draft_when_no_safe_claims_exist() -> None:
    positioning_result = _application_materials_positioning_result()
    positioning_result["allowed_claims"] = []

    for document_type in ("cover_letter", "recruiter_message"):
        packet = build_recruiter_document_writer_input_packet(
            _execution_report(positioning_result=positioning_result),
            document_type=document_type,
        )
        writer_input = packet.document_writer_input
        assert writer_input is not None
        assert writer_input["safe_claims_for_document"] == []
        assert writer_input["locked_claim_sentences"] == []
        assert writer_input["writer_guidance"]["omit_claims_when_no_locked_claims"] is True
        assert writer_input["writer_guidance"]["minimal_draft_allowed_when_no_locked_claims"] is True


def test_safe_claims_exclude_unsupported_and_broad_overclaim_language() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=_application_materials_positioning_result()),
        document_type="cover_letter",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    encoded = json.dumps(
        [
            {
                "claim": item["claim"],
                "safe_wording": item["safe_wording"],
            }
            for item in writer_input["safe_claims_for_document"]
        ],
        sort_keys=True,
    ).lower()
    assert "direct bank or card-network ownership" not in encoded
    assert "telecom category leadership" not in encoded
    assert "end-to-end p and l ownership" not in encoded
    assert "product executive" not in encoded
    assert "senior executive" not in encoded
    assert "payments leader" not in encoded
    assert "fintech leader" not in encoded
    assert "roadmap ownership" not in encoded
    assert "pricing ownership" not in encoded
    assert "monetization ownership" not in encoded
    assert "multi-team leadership" not in encoded


def test_adjacent_claims_are_softened_for_outward_facing_documents() -> None:
    cover_letter_packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=_application_materials_positioning_result()),
        document_type="cover_letter",
    )
    recruiter_message_packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=_application_materials_positioning_result()),
        document_type="recruiter_message",
    )

    cover_letter_input = cover_letter_packet.document_writer_input
    recruiter_message_input = recruiter_message_packet.document_writer_input
    assert cover_letter_input is not None
    assert recruiter_message_input is not None
    cover_letter_claims = json.dumps(
        [
            {
                "claim": item["claim"],
                "safe_wording": item["safe_wording"],
            }
            for item in cover_letter_input["safe_claims_for_document"]
        ],
        sort_keys=True,
    ).lower()
    recruiter_message_claims = json.dumps(
        [
            {
                "claim": item["claim"],
                "safe_wording": item["safe_wording"],
            }
            for item in recruiter_message_input["safe_claims_for_document"]
        ],
        sort_keys=True,
    ).lower()
    assert "adjacent" in cover_letter_claims or "commercially relevant" in cover_letter_claims
    assert "adjacent" in recruiter_message_claims or "commercially relevant" in recruiter_message_claims
    assert "direct ownership" not in cover_letter_claims
    assert "direct ownership" not in recruiter_message_claims


def test_safe_claims_expose_concrete_evidence_and_conservative_sentence_templates() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=_application_materials_positioning_result()),
        document_type="cover_letter",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    safe_claims = writer_input["safe_claims_for_document"]
    assert safe_claims
    for item in safe_claims:
        assert item["ownership_scope"] in {"direct", "collaborated", "adjacent", "exposed"}
        assert item["concrete_evidence_summary"]
        assert item["allowed_sentence_template"]
        assert item["forbidden_generalizations"]
        assert item["do_not_say"]
        lowered = item["allowed_sentence_template"].lower()
        assert "refs " in lowered
        assert "metrics" in lowered
        assert "ownership" in lowered


def test_recruiter_message_safe_claims_remain_single_sentence_ready_and_conservative() -> None:
    packet = build_recruiter_document_writer_input_packet(
        _execution_report(positioning_result=_application_materials_positioning_result()),
        document_type="recruiter_message",
    )

    writer_input = packet.document_writer_input
    assert writer_input is not None
    safe_claims = writer_input["safe_claims_for_document"]
    assert safe_claims
    for item in safe_claims:
        template = item["allowed_sentence_template"]
        assert template.startswith("One short fit sentence only:")
        assert item["ownership_scope"] != "direct" or item["support_level"] == "direct"
        encoded = json.dumps(item, sort_keys=True).lower()
        assert "multi-country launches" not in encoded
        forbidden = json.dumps(item["forbidden_generalizations"], sort_keys=True).lower()
        assert (
            "pricing and packaging iterations" in forbidden
            or "experience in payment acceptance, checkout, or recurring billing" in forbidden
            or "telecom, merchant, or ecosystem partnerships" in forbidden
        )
        assert "pricing and packaging iterations" not in item["safe_wording"].lower()
        assert "pricing and packaging iterations" not in template.lower()


def test_boundary_imports_are_safe() -> None:
    source = (REPO_ROOT / "hermes_cli" / "recruiter_document_inputs.py").read_text(encoding="utf-8")
    forbidden = [
        "import sqlite3",
        "from sqlite3",
        "import openai",
        "from openai",
        "import anthropic",
        "from anthropic",
        "import slack",
        "from slack",
        "import telegram",
        "from telegram",
        "import gmail",
        "from gmail",
        "import linkedin",
        "from linkedin",
        "import browser",
        "from browser",
        ".gateway",
        ".router",
        "read_text(",
        "read_bytes(",
        "open(",
    ]
    for needle in forbidden:
        assert needle not in source


def test_supported_document_type_constants_match_sot() -> None:
    assert ALLOWED_DOCUMENT_TYPES == [
        "cover_letter",
        "recruiter_message",
        "linkedin_dm",
        "follow_up",
        "cv_tailoring_notes",
        "application_answer",
        "executive_bio",
    ]
    assert REQUIRED_POSITIONING_FIELDS == [
        "positioning_summary",
        "evidence_map",
        "proven_facts",
        "derived_positioning",
        "gaps",
        "risks_and_mitigations",
    ]
