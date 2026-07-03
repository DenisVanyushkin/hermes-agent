from __future__ import annotations

from hermes_cli.pipeline_router import (
    DEFAULT_PIPELINE_ID,
    ENGINEERING_PIPELINE_ID,
    RECRUITER_PIPELINE_ID,
    HeuristicPipelineRouter,
    _ROUTER_RESPONSE_FORMAT,
)
from hermes_cli.pipeline_specs import load_pipeline_specs


def _router() -> HeuristicPipelineRouter:
    return HeuristicPipelineRouter()


def test_registry_contains_recruiter_pipeline() -> None:
    specs = load_pipeline_specs()
    assert RECRUITER_PIPELINE_ID in specs.pipeline_specs
    entry = {item["id"]: item for item in specs.registry["registry"]}[RECRUITER_PIPELINE_ID]
    assert entry["mutation_risk"] == "none"
    assert entry["fallback_eligible"] is False
    assert entry["allowed_router_statuses"] == ["selected"]


def test_response_format_enums_include_recruiter_pipeline() -> None:
    schema = _ROUTER_RESPONSE_FORMAT["response_format"]["json_schema"]["schema"]
    assert RECRUITER_PIPELINE_ID in schema["properties"]["selected_pipeline_id"]["enum"]
    assert RECRUITER_PIPELINE_ID in schema["properties"]["alternatives"]["items"]["properties"]["pipeline_id"]["enum"]


def test_english_vacancy_prompt_routes_to_recruiter_pipeline() -> None:
    decision = _router().route(
        "Evaluate this vacancy and tell me whether I should apply: https://example.com/jobs/42",
        pipeline_session_id="sess-1",
    )
    assert decision.status == "selected"
    assert decision.selected_pipeline_id == RECRUITER_PIPELINE_ID
    assert "task_classification.domain == career" in decision.matched_signals


def test_russian_vacancy_prompt_routes_to_recruiter_pipeline() -> None:
    decision = _router().route(
        "Оцени вакансию и скажи, стоит ли податься",
        pipeline_session_id="sess-2",
    )
    assert decision.status == "selected"
    assert decision.selected_pipeline_id == RECRUITER_PIPELINE_ID


def test_company_diligence_prompt_routes_to_recruiter_pipeline() -> None:
    decision = _router().route(
        "Расскажи про компанию за этой вакансией: карьерные риски и репутация",
        pipeline_session_id="sess-3",
    )
    assert decision.status == "selected"
    assert decision.selected_pipeline_id == RECRUITER_PIPELINE_ID


def test_engineering_prompt_still_wins_over_recruiter() -> None:
    decision = _router().route(
        "Fix the failing pytest in hermes_cli/recruiter_routing.py and update tests",
        pipeline_session_id="sess-4",
    )
    assert decision.status == "selected"
    assert decision.selected_pipeline_id == ENGINEERING_PIPELINE_ID


def test_generic_prompt_still_falls_back_to_default() -> None:
    decision = _router().route(
        "Привет! Как дела?",
        pipeline_session_id="sess-5",
    )
    assert decision.status == "no_specialized_pipeline"
    assert decision.fallback_pipeline_id == DEFAULT_PIPELINE_ID


def test_candidate_hints_include_recruiter_candidate() -> None:
    hints = _router().candidate_hints("Оцени вакансию, стоит ли податься")
    assert hints["recruiter_candidate_pipeline_id"] == RECRUITER_PIPELINE_ID
    assert hints["engineering_candidate_pipeline_id"] is None
