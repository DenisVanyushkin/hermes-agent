from __future__ import annotations

import json
from pathlib import Path

from hermes_cli.recruiter_routing import (
    APPLICATION_MATERIALS_BUNDLE_ID,
    DEFAULT_BUNDLE_ID,
    RECRUITER_ROLE_ID,
    build_recruiter_handoff_metadata,
    route_recruiter_prompt,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_russian_vacancy_prompt_selects_recruiter_evaluation_bundle() -> None:
    decision = route_recruiter_prompt(
        "Посмотри вакансию и скажи, стоит ли податься",
        context={"repo_root": REPO_ROOT},
    )

    assert decision.status.value == "selected"
    assert decision.selected_role_id == RECRUITER_ROLE_ID
    assert decision.selected_bundle == DEFAULT_BUNDLE_ID
    assert decision.provider_execution_enabled is False
    assert decision.document_provider_execution_enabled is False
    assert "recruiter-context dry-run" in decision.next_allowed_actions
    assert "recruiter-skill execute" in decision.next_allowed_actions
    assert "evaluate-vacancy" in decision.role_package_context["bundle_ids"]


def test_english_application_materials_prompt_selects_document_bundle() -> None:
    decision = route_recruiter_prompt(
        "Prepare CV, cover letter, and a LinkedIn recruiter message for this job",
        context={"repo_root": REPO_ROOT},
    )

    assert decision.status.value == "selected"
    assert decision.selected_bundle == APPLICATION_MATERIALS_BUNDLE_ID
    assert "recruiter-document execute" in decision.next_allowed_actions
    assert any("POSITIONING_REQUIRED" in warning for warning in decision.warnings)


def test_ambiguous_recruiter_prompt_defaults_to_evaluate_vacancy() -> None:
    decision = route_recruiter_prompt(
        "Помоги с карьерным решением по этой возможности",
        context={"repo_root": REPO_ROOT},
    )

    assert decision.status.value == "selected"
    assert decision.selected_bundle == DEFAULT_BUNDLE_ID
    assert any("defaulted to evaluate-vacancy" in warning for warning in decision.warnings)


def test_engineering_prompt_does_not_get_stolen_by_recruiter_router() -> None:
    decision = route_recruiter_prompt(
        "Debug hermes_cli/pipeline_router.py and fix failing pytest coverage",
        context={"repo_root": REPO_ROOT},
    )

    assert decision.status.value == "not_selected"
    assert decision.selected_role_id is None
    assert decision.selected_bundle is None


def test_generic_email_prompt_is_not_recruiter_routed() -> None:
    decision = route_recruiter_prompt(
        "Draft a short thank-you email for a dinner invitation",
        context={"repo_root": REPO_ROOT},
    )

    assert decision.status.value == "not_selected"


def test_decision_is_json_serializable() -> None:
    decision = route_recruiter_prompt(
        "follow up recruiter about this vacancy",
        context={"repo_root": REPO_ROOT},
    )

    encoded = json.dumps(decision.to_dict(), sort_keys=True)
    assert RECRUITER_ROLE_ID in encoded
    assert APPLICATION_MATERIALS_BUNDLE_ID in encoded


def test_selected_recruiter_prompt_builds_compact_role_context() -> None:
    metadata = build_recruiter_handoff_metadata(
        "Evaluate this vacancy and tell me whether I should apply: https://example.com/jobs/42",
        context={"repo_root": REPO_ROOT},
    )

    role_context = metadata["role_context"]
    assert role_context["schema_version"] == "recruiter_role_context_v1"
    assert role_context["selected_role_id"] == RECRUITER_ROLE_ID
    assert role_context["selected_bundle"] == DEFAULT_BUNDLE_ID
    assert role_context["execution_mode"] == "observe"
    assert role_context["provider_execution_enabled"] is False
    assert role_context["document_provider_execution_enabled"] is False
    assert role_context["role_package_available"] is True
    assert role_context["selected_bundle_available"] is True
    assert "vacancy-evaluation" in role_context["bundle_skill_ids"]
    assert "RecruiterPositioningPacket" in role_context["bundle_required_inputs"]
    assert "bundle_expected_outputs" in role_context


def test_non_recruiter_prompt_has_no_role_context() -> None:
    metadata = build_recruiter_handoff_metadata(
        "Debug Hermes gateway for recruiter routing",
        context={"repo_root": REPO_ROOT},
    )

    assert metadata["status"] == "not_selected"
    assert metadata["role_context"] is None


def test_routing_module_has_no_provider_or_db_imports() -> None:
    source = (REPO_ROOT / "hermes_cli" / "recruiter_routing.py").read_text(encoding="utf-8")
    forbidden = [
        "agent.auxiliary_client",
        "sqlite3",
        "import slack",
        "import telegram",
        "import gmail",
        "import linkedin",
        "import browser",
    ]
    for marker in forbidden:
        assert marker not in source
