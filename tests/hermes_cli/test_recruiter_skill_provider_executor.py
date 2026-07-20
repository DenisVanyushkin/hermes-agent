from __future__ import annotations

from typing import Any

import pytest

from hermes_cli.recruiter_skill_execution import (
    POSITIONING_EVIDENCE_SKILL_ID,
    VACANCY_EVALUATION_SKILL_ID,
    SkillExecutionResult,
)
from hermes_cli.recruiter_skill_provider_executor import (
    RecruiterProviderSkillExecutor,
    build_recruiter_positioning_skill_executor,
)


class _FakeProviderExecutor:
    """Mimics the real provider executors: execute(skill_input, expected_schema) -> dict."""

    provider_backed = True

    def __init__(self, payload: dict[str, Any] | None = None, *, raises: Exception | None = None) -> None:
        self.payload = payload or {}
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def execute(self, *, skill_input: dict[str, Any], expected_schema: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append({"skill_input": skill_input, "expected_schema": expected_schema})
        if self.raises is not None:
            raise self.raises
        return dict(self.payload)


def _adapter(*, evaluation_payload=None, positioning_payload=None, eval_raises=None, pos_raises=None):
    evaluation = _FakeProviderExecutor(evaluation_payload, raises=eval_raises)
    positioning = _FakeProviderExecutor(positioning_payload, raises=pos_raises)
    adapter = build_recruiter_positioning_skill_executor(
        evaluation_executor=evaluation,
        positioning_executor=positioning,
    )
    return adapter, evaluation, positioning


def test_builder_returns_provider_skill_executor() -> None:
    adapter, _, _ = _adapter()
    assert isinstance(adapter, RecruiterProviderSkillExecutor)
    assert adapter.provider_backed is True


def test_dispatch_vacancy_evaluation_uses_evaluation_executor() -> None:
    payload = {
        "vacancy_evaluation_summary": "Strong fit.",
        "fit_interpretation": "High-confidence match.",
        "evidence_gaps": ["team size"],
        "recommendation_for_next_step": "Proceed.",
        "warnings": ["w1"],
        "errors": [],
        "provenance": {"provider": "openai-codex", "model": "gpt-5.4-mini"},
    }
    adapter, evaluation, positioning = _adapter(evaluation_payload=payload)

    result = adapter.execute(
        skill_id=VACANCY_EVALUATION_SKILL_ID,
        skill_input={"foo": "bar"},
        skill_markdown_path="role-packages/recruiter/skills/vacancy-evaluation/SKILL.md",
        expected_schema=["vacancy_evaluation_summary"],
    )

    assert isinstance(result, SkillExecutionResult)
    assert result.skill_id == VACANCY_EVALUATION_SKILL_ID
    assert result.provider_called is True
    # Required fields land at the top level of .output so the runner can validate them.
    assert result.output["vacancy_evaluation_summary"] == "Strong fit."
    assert result.output["recommendation_for_next_step"] == "Proceed."
    assert result.warnings == ["w1"]
    assert result.provenance["provider"] == "openai-codex"
    # only the evaluation executor was used
    assert len(evaluation.calls) == 1
    assert positioning.calls == []


def test_dispatch_positioning_uses_positioning_executor() -> None:
    payload = {
        "positioning_summary": "Lead with B2B product leadership.",
        "evidence_map": {"leadership": ["Scaled teams."]},
        "proven_facts": ["Built orgs."],
        "derived_positioning": ["operator-executive"],
        "gaps": ["domain depth"],
        "risks_and_mitigations": ["avoid overstating"],
    }
    adapter, evaluation, positioning = _adapter(positioning_payload=payload)

    result = adapter.execute(
        skill_id=POSITIONING_EVIDENCE_SKILL_ID,
        skill_input={"foo": "bar"},
        skill_markdown_path="role-packages/recruiter/skills/positioning-and-evidence/SKILL.md",
        expected_schema=["positioning_summary"],
    )

    assert result.skill_id == POSITIONING_EVIDENCE_SKILL_ID
    assert result.provider_called is True
    assert result.output["positioning_summary"] == "Lead with B2B product leadership."
    assert result.output["evidence_map"] == {"leadership": ["Scaled teams."]}
    assert len(positioning.calls) == 1
    assert evaluation.calls == []


def test_adapter_forwards_provider_native_schema_not_runner_list() -> None:
    adapter, evaluation, _ = _adapter(evaluation_payload={"vacancy_evaluation_summary": "x"})
    adapter.execute(
        skill_id=VACANCY_EVALUATION_SKILL_ID,
        skill_input={},
        skill_markdown_path="p",
        expected_schema=["vacancy_evaluation_summary", "fit_interpretation"],
    )
    forwarded = evaluation.calls[0]["expected_schema"]
    # The provider receives its own structured schema (a dict), never the runner's list.
    assert isinstance(forwarded, dict)


def test_unknown_skill_id_returns_clean_error_result_not_raised() -> None:
    adapter, evaluation, positioning = _adapter()
    result = adapter.execute(
        skill_id="mystery-skill",
        skill_input={},
        skill_markdown_path="p",
        expected_schema=[],
    )
    assert isinstance(result, SkillExecutionResult)
    assert result.skill_id == "mystery-skill"
    assert result.provider_called is False
    assert result.output == {}
    assert any("unknown_skill_id" in err for err in result.errors)
    # neither provider was touched
    assert evaluation.calls == []
    assert positioning.calls == []


def test_provider_exception_is_captured_in_result_not_raised() -> None:
    adapter, _, _ = _adapter(pos_raises=ValueError("positioning_provider_output_invalid_json"))
    result = adapter.execute(
        skill_id=POSITIONING_EVIDENCE_SKILL_ID,
        skill_input={},
        skill_markdown_path="p",
        expected_schema=["positioning_summary"],
    )
    assert isinstance(result, SkillExecutionResult)
    assert result.output == {}
    assert any("positioning_provider_output_invalid_json" in err for err in result.errors)


def test_missing_optional_lists_default_to_empty() -> None:
    adapter, _, _ = _adapter(evaluation_payload={"vacancy_evaluation_summary": "x"})
    result = adapter.execute(
        skill_id=VACANCY_EVALUATION_SKILL_ID,
        skill_input={},
        skill_markdown_path="p",
        expected_schema=["vacancy_evaluation_summary"],
    )
    assert result.warnings == []
    assert result.errors == []
    assert isinstance(result.provenance, dict)


def test_real_builder_defers_provider_imports_until_called(monkeypatch) -> None:
    """When both executors are injected, no real provider client is constructed."""
    import hermes_cli.recruiter_skill_provider_executor as mod

    def _boom(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("real provider builder should not be called when executors injected")

    monkeypatch.setattr(mod, "build_recruiter_evaluation_provider_executor", _boom)
    monkeypatch.setattr(mod, "build_recruiter_positioning_provider_executor", _boom)
    adapter, _, _ = _adapter(evaluation_payload={"vacancy_evaluation_summary": "x"})
    assert isinstance(adapter, RecruiterProviderSkillExecutor)


# --- schema-alias reconciliation (native packet -> runner REQUIRED_POSITIONING_FIELDS) ---
from hermes_cli.recruiter_skill_execution import (  # noqa: E402
    FLOW_EVALUATE_AND_POSITION,
    REQUIRED_POSITIONING_FIELDS,
    RecruiterSkillExecutionRequest,
    RecruiterSkillExecutionStatus,
    run_recruiter_skill_execution,
)


from hermes_cli.recruiter_context import (  # noqa: E402
    RecruiterContextPacket,
    RecruiterContextStatus,
)


def _runner_context_builder(_request: Any) -> RecruiterContextPacket:
    """Known-good READY context packet that drives the runner to EXECUTION_READY."""
    return RecruiterContextPacket(
        status=RecruiterContextStatus.READY,
        request={"vacancy_id": 101},
        vacancy={
            "vacancy_id": 101,
            "vacancy_key": "vac-101",
            "source_url": "https://example.com/jobs/101",
            "title": "Head of Product",
            "company": "Acme",
            "location": "Remote",
            "source_kind": "linkedin",
            "evaluation": {"score": 92, "tier": "strong_fit", "recommendation": "apply"},
            "provenance": {"source_table": "vacancies", "source_url": "https://example.com/jobs/101"},
        },
        opportunity={"id": 501, "vacancy_id": 101, "stage": "new"},
        company_context=[
            {"company": "Acme", "summary": "Category leader", "provenance": {"source_table": "company_intelligence"}}
        ],
        application_history={"status": "found", "history": [], "artifacts": [], "feedback": []},
        machine_score={
            "status": "available",
            "score": 92,
            "tier": "strong_fit",
            "recommendation": "apply",
            "matched_signals": ["b2b_saas", "leadership"],
            "concerns": [],
            "reasons": ["strong product leadership match"],
        },
        role_package_context={
            "package_id": "hermes-recruiter",
            "package_path": "role-packages/recruiter",
            "role_id": "hermes_recruiter",
            "skills_by_id": {
                "vacancy-evaluation": {
                    "id": "vacancy-evaluation",
                    "path": "role-packages/recruiter/skills/vacancy-evaluation/SKILL.md",
                },
                "positioning-and-evidence": {
                    "id": "positioning-and-evidence",
                    "path": "role-packages/recruiter/skills/positioning-and-evidence/SKILL.md",
                },
                "document-writer": {
                    "id": "document-writer",
                    "path": "role-packages/recruiter/skills/document-writer/SKILL.md",
                },
            },
            "bundles_by_id": {
                "evaluate-vacancy": {"id": "evaluate-vacancy", "skills": ["vacancy-evaluation", "positioning-and-evidence"]}
            },
        },
        private_context={
            "status": "PRIVATE_CONTEXT_AVAILABLE",
            "dir": "/home/hermes/.hermes/private/career",
            "files": {
                "denis_vanyushkin_structured_resume_v1_1.json": {"present": True},
                "opportunity-thesis.md": {"present": True},
                "company_intelligence_architecture.md": {"present": True},
                "scoring_v3.md": {"present": True},
            },
        },
        warnings=[],
        errors=[],
        provenance={"writes_performed": False, "private_dir_checked": "/home/hermes/.hermes/private/career"},
    )


def _native_vacancy_evaluation_packet() -> dict[str, Any]:
    """Genuine ``recruiter_vacancy_evaluation_packet_v1`` shape (no runner field names)."""
    return {
        "schema_version": "recruiter_vacancy_evaluation_packet_v1",
        "skill_id": "vacancy-evaluation",
        "status": "VACANCY_EVALUATION_READY",
        "recommendation": "apply",
        "fit_assessment": "Strong fit for executive product leadership.",
        "strengths": ["Scaled B2B product orgs.", "Marketplace depth."],
        "risks": ["Company stage differs."],
        "evidence": ["Led multi-team product org."],
        "missing_information": ["Exact team size not confirmed."],
        "next_step": "Proceed to positioning synthesis.",
        "provenance": {"provider": "openai-codex"},
    }


def _native_positioning_packet() -> dict[str, Any]:
    return {
        "schema_version": "recruiter_positioning_packet_v1",
        "skill_id": "positioning-and-evidence",
        "status": "POSITIONING_READY",
        "positioning_summary": "Lead with executive B2B product leadership.",
        "target_narrative": "Operator-executive for complex platform businesses.",
        "recommended_angle": "Scale-stage operator.",
        "evidence": ["Scaled multi-team product orgs."],
        "gaps": ["domain depth"],
        "risks_and_mitigations": ["avoid overstating prior stage similarity"],
        "claims_to_use": ["Led product organizations."],
        "claims_to_avoid": [],
        "evidence_items": [{"claim_text": "Led product organizations.", "source_ref_ids": ["src-1"]}],
        "allowed_claims": [{"claim_id": "claim-1", "claim_text": "Led product organizations."}],
        "source_references": [{"source_ref_id": "src-1"}],
        "provenance": {"provider": "openai-codex"},
    }


def test_positioning_aliases_satisfy_runner_required_fields() -> None:
    adapter, _, _ = _adapter(positioning_payload=_native_positioning_packet())
    result = adapter.execute(
        skill_id=POSITIONING_EVIDENCE_SKILL_ID,
        skill_input={},
        skill_markdown_path="p",
        expected_schema=list(REQUIRED_POSITIONING_FIELDS),
    )
    for field in REQUIRED_POSITIONING_FIELDS:
        assert field in result.output, f"missing required alias: {field}"
    # deterministic derivation
    assert result.output["evidence_map"] == ["Scaled multi-team product orgs."]
    assert result.output["proven_facts"] == ["Led product organizations."]
    assert result.output["derived_positioning"] == {
        "target_narrative": "Operator-executive for complex platform businesses.",
        "recommended_angle": "Scale-stage operator.",
    }


def test_native_positioning_fields_survive_untouched() -> None:
    adapter, _, _ = _adapter(positioning_payload=_native_positioning_packet())
    result = adapter.execute(
        skill_id=POSITIONING_EVIDENCE_SKILL_ID,
        skill_input={},
        skill_markdown_path="p",
        expected_schema=list(REQUIRED_POSITIONING_FIELDS),
    )
    out = result.output
    assert out["evidence"] == ["Scaled multi-team product orgs."]
    assert out["claims_to_use"] == ["Led product organizations."]
    assert out["evidence_items"] == [{"claim_text": "Led product organizations.", "source_ref_ids": ["src-1"]}]
    assert out["allowed_claims"] == [{"claim_id": "claim-1", "claim_text": "Led product organizations."}]
    assert out["source_references"] == [{"source_ref_id": "src-1"}]
    assert out["schema_version"] == "recruiter_positioning_packet_v1"


def test_absent_optional_source_fields_yield_safe_empty_aliases() -> None:
    # Native-required fields present, but the alias SOURCE fields are absent.
    minimal = {
        "schema_version": "recruiter_positioning_packet_v1",
        "skill_id": "positioning-and-evidence",
        "status": "POSITIONING_READY",
        "positioning_summary": "Summary.",
        "gaps": [],
        "risks_and_mitigations": [],
    }
    adapter, _, _ = _adapter(positioning_payload=minimal)
    result = adapter.execute(
        skill_id=POSITIONING_EVIDENCE_SKILL_ID,
        skill_input={},
        skill_markdown_path="p",
        expected_schema=list(REQUIRED_POSITIONING_FIELDS),
    )
    assert result.output["evidence_map"] == []
    assert result.output["proven_facts"] == []
    assert result.output["derived_positioning"] == {}
    for field in REQUIRED_POSITIONING_FIELDS:
        assert field in result.output


def test_vacancy_evaluation_aliases_satisfy_runner_required_fields() -> None:
    adapter, _, _ = _adapter(evaluation_payload=_native_vacancy_evaluation_packet())
    result = adapter.execute(
        skill_id=VACANCY_EVALUATION_SKILL_ID,
        skill_input={},
        skill_markdown_path="p",
        expected_schema=["vacancy_evaluation_summary"],
    )
    for field in ("vacancy_evaluation_summary", "fit_interpretation", "evidence_gaps", "recommendation_for_next_step"):
        assert field in result.output
    assert result.output["vacancy_evaluation_summary"] == "Strong fit for executive product leadership."
    assert result.output["fit_interpretation"] == {
        "strengths": ["Scaled B2B product orgs.", "Marketplace depth."],
        "risks": ["Company stage differs."],
    }
    assert result.output["evidence_gaps"] == ["Exact team size not confirmed."]
    assert result.output["recommendation_for_next_step"] == "apply"
    # native fields survive untouched
    assert result.output["fit_assessment"] == "Strong fit for executive product leadership."
    assert result.output["recommendation"] == "apply"
    assert result.output["schema_version"] == "recruiter_vacancy_evaluation_packet_v1"


def test_vacancy_evaluation_recommendation_falls_back_to_next_step() -> None:
    packet = _native_vacancy_evaluation_packet()
    del packet["recommendation"]
    adapter, _, _ = _adapter(evaluation_payload=packet)
    result = adapter.execute(
        skill_id=VACANCY_EVALUATION_SKILL_ID,
        skill_input={},
        skill_markdown_path="p",
        expected_schema=[],
    )
    assert result.output["recommendation_for_next_step"] == "Proceed to positioning synthesis."


def test_vacancy_evaluation_absent_source_fields_yield_safe_empty_aliases() -> None:
    minimal = {
        "schema_version": "recruiter_vacancy_evaluation_packet_v1",
        "skill_id": "vacancy-evaluation",
        "status": "VACANCY_EVALUATION_READY",
    }
    adapter, _, _ = _adapter(evaluation_payload=minimal)
    result = adapter.execute(
        skill_id=VACANCY_EVALUATION_SKILL_ID,
        skill_input={},
        skill_markdown_path="p",
        expected_schema=[],
    )
    assert result.output["vacancy_evaluation_summary"] == ""
    assert result.output["fit_interpretation"] == {}
    assert result.output["evidence_gaps"] == []
    assert result.output["recommendation_for_next_step"] == ""


def test_native_packets_pass_runner_required_fields_end_to_end() -> None:
    # Full runner path: vacancy-evaluation THEN positioning, both GENUINE native packets
    # driven through the adapter. Only the adapter's aliases make this reach READY.
    evaluation = _FakeProviderExecutor(_native_vacancy_evaluation_packet())
    positioning = _FakeProviderExecutor(_native_positioning_packet())
    adapter = build_recruiter_positioning_skill_executor(
        evaluation_executor=evaluation,
        positioning_executor=positioning,
    )

    report = run_recruiter_skill_execution(
        RecruiterSkillExecutionRequest(
            vacancy_id=101,
            flow=FLOW_EVALUATE_AND_POSITION,
            allow_provider_execution=True,
        ),
        context_builder=_runner_context_builder,
        executor=adapter,
    )
    assert report.status is RecruiterSkillExecutionStatus.EXECUTION_READY
    assert report.vacancy_evaluation_result is not None
    assert report.positioning_evidence_result is not None
    # native fields preserved through the runner's normalization
    assert "fit_assessment" in report.vacancy_evaluation_result
    assert "evidence_items" in report.positioning_evidence_result
    assert "evidence_map" in report.positioning_evidence_result


class _RawNativeExecutor:
    """Bypasses the adapter's aliasing: returns the native packet as SkillExecutionResult verbatim."""

    provider_backed = True

    def __init__(self, eval_packet: dict[str, Any], pos_packet: dict[str, Any]) -> None:
        self._by_skill = {
            VACANCY_EVALUATION_SKILL_ID: eval_packet,
            POSITIONING_EVIDENCE_SKILL_ID: pos_packet,
        }

    def execute(self, *, skill_id, skill_input, skill_markdown_path, expected_schema) -> SkillExecutionResult:
        return SkillExecutionResult(
            status="SUCCESS",
            skill_id=skill_id,
            output=dict(self._by_skill[skill_id]),
            provider_called=True,
        )


def test_unaugmented_native_eval_fails_runner_gate_proving_aliases_are_load_bearing() -> None:
    # Same native packets, but WITHOUT the adapter's aliases: the runner rejects the
    # eval stage on missing required fields -> proves the aliases are what make it pass.
    raw = _RawNativeExecutor(_native_vacancy_evaluation_packet(), _native_positioning_packet())
    report = run_recruiter_skill_execution(
        RecruiterSkillExecutionRequest(
            vacancy_id=101,
            flow=FLOW_EVALUATE_AND_POSITION,
            allow_provider_execution=True,
        ),
        context_builder=_runner_context_builder,
        executor=raw,
    )
    assert report.status is RecruiterSkillExecutionStatus.SKILL_OUTPUT_INVALID
