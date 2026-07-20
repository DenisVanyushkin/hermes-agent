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
