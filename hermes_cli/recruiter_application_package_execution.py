"""Application-package flow: manifest -> positioning -> documents, draft-only.

Controller helper that orchestrates the recruiter application-materials bundle.
It never sends anything, never writes to the job-intel DB, and never touches a
provider unless the real-provider fuse is open. Every path returns a draft-only
report whose ``text`` header states that nothing was sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from hermes_cli.recruiter_career_facts import (
    CareerFactsBundle,
    career_facts_dir,
    load_career_facts,
)
from hermes_cli.recruiter_context import build_recruiter_context
from hermes_cli.recruiter_decision_execution import _real_provider_execution_allowed
from hermes_cli.recruiter_document_execution import (
    RecruiterDocumentExecutionStatus,
    run_recruiter_document_execution,
)
from hermes_cli.recruiter_routing import (
    APPLICATION_MATERIALS_BUNDLE_ID,
    RecruiterRoutingStatus,
    route_recruiter_prompt,
)
from hermes_cli.recruiter_skill_execution import (
    FLOW_EVALUATE_AND_POSITION,
    RecruiterSkillExecutionRequest,
    RecruiterSkillExecutionStatus,
    run_recruiter_skill_execution,
)

RECRUITER_APPLICATION_HELPER = "recruiter_application_package_flow"
RECRUITER_APPLICATION_PIPELINE_ID = "recruiter_decision_support_pipeline"
_DOCUMENT_TYPES = ("cv", "cover_letter", "recruiter_message")
# Public bundle keys map to the document-input builder's supported document types.
# A "cv" in this flow is analytical tailoring notes, never a fabricated full resume.
_DOCUMENT_TYPE_MAP = {
    "cv": "cv_tailoring_notes",
    "cover_letter": "cover_letter",
    "recruiter_message": "recruiter_message",
}
_DRAFT_ONLY_HEADER = "Draft-only пакет — ничего не отправлено"

_DOCUMENT_TITLES = {
    "cv": "Резюме (CV)",
    "cover_letter": "Сопроводительное письмо",
    "recruiter_message": "Сообщение рекрутеру",
}


@dataclass(slots=True)
class RecruiterApplicationExecutors:
    """Injected execution surface for the application-package helper.

    ``skill_executor`` runs the positioning-and-evidence skill; ``document_executor``
    runs the document writer/reviewer loop; ``context_builder`` resolves recruiter
    context (defaults to the real builder when not supplied). Task 4 wires the real
    provider-backed variants; tests inject fakes.
    """

    skill_executor: Any = None
    document_executor: Any = None
    context_builder: Callable[[Any], Any] | None = None


def execute_recruiter_application_package_helper(
    *,
    config: Mapping[str, Any] | None = None,
    user_message: str = "",
    executor_factory: Any = None,
    conversation_context: str | None = None,
    hermes_home: Path | str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Registered controller helper for the recruiter application-package flow."""
    # --- Gate 1: career facts must be verified; never fabricate them ---
    try:
        facts_bundle = load_career_facts(hermes_home)
    except Exception as exc:  # pragma: no cover - loader is fail-soft
        facts_bundle = CareerFactsBundle(warnings=[f"career facts loader failed: {type(exc).__name__}"])

    if not facts_bundle.available:
        return _blocked_facts_result(facts_bundle, hermes_home)

    # --- Gate 2: request must route to the application-materials bundle ---
    routing = route_recruiter_prompt(user_message)
    if (
        routing.status is not RecruiterRoutingStatus.SELECTED
        or routing.selected_bundle != APPLICATION_MATERIALS_BUNDLE_ID
    ):
        return _blocked_not_application_result(routing)

    provider_allowed = _real_provider_execution_allowed(config)

    executors = _resolve_executors(executor_factory, provider_allowed)
    executor_error: str | None = executors[1]
    executor_bundle: RecruiterApplicationExecutors | None = executors[0]

    # --- Gate 3: positioning (existing machinery) ---
    positioning_report = _run_positioning(
        executor_bundle,
        provider_allowed=provider_allowed,
    )

    # --- Gate 4: documents (POSITIONING_REQUIRED gate lives inside the builder) ---
    document_executor = executor_bundle.document_executor if executor_bundle else None
    documents: dict[str, dict[str, Any]] = {}
    for document_type in _DOCUMENT_TYPES:
        doc_report = run_recruiter_document_execution(
            positioning_report,
            _DOCUMENT_TYPE_MAP[document_type],
            allow_document_execution=provider_allowed,
            executor=document_executor,
        )
        documents[document_type] = doc_report.to_dict()

    # --- Gate 5: consolidate ---
    status = _overall_status(
        provider_allowed=provider_allowed,
        positioning_report=positioning_report,
        documents=documents,
    )
    warnings = _collect_warnings(routing, facts_bundle, positioning_report, executor_error)
    report = {
        "helper_id": RECRUITER_APPLICATION_HELPER,
        "status": status,
        "routing": {
            "selected_pipeline_id": RECRUITER_APPLICATION_PIPELINE_ID,
            "selected_role_id": routing.selected_role_id,
            "selected_bundle": routing.selected_bundle,
            "reasoning_summary": routing.reasoning_summary,
        },
        "positioning": {
            "status": positioning_report.status.value,
            "provider_called": positioning_report.provider_called,
        },
        "documents": documents,
        "warnings": warnings,
        "safety": {
            "draft_only": True,
            "no_outbound": True,
            "no_job_intel_db_write": True,
            "provider_execution_allowed": provider_allowed,
        },
    }
    text = _format_text(status, documents, warnings, executor_error)
    return {"status": status, "text": text, "report": report}


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _resolve_executors(
    executor_factory: Any,
    provider_allowed: bool,
) -> tuple[RecruiterApplicationExecutors | None, str | None]:
    """Return (executors, error). Only builds provider executors when the fuse is open."""
    if executor_factory is None:
        if not provider_allowed:
            return None, None
        try:
            from hermes_cli.recruiter_positioning_provider_executor import (  # noqa: F401
                build_recruiter_positioning_provider_executor,
            )

            # Real provider wiring is Task 4's responsibility; if the module or
            # its builder is unavailable, fail soft into a draft-only report.
            return None, None
        except Exception as exc:  # pragma: no cover - defensive
            return None, f"{type(exc).__name__}: {exc}"

    try:
        bundle = executor_factory()
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"

    if isinstance(bundle, RecruiterApplicationExecutors):
        return bundle, None
    # Tolerate any object exposing the expected attributes.
    return (
        RecruiterApplicationExecutors(
            skill_executor=getattr(bundle, "skill_executor", None),
            document_executor=getattr(bundle, "document_executor", None),
            context_builder=getattr(bundle, "context_builder", None),
        ),
        None,
    )


def _run_positioning(
    executors: RecruiterApplicationExecutors | None,
    *,
    provider_allowed: bool,
):
    context_builder = None
    skill_executor = None
    if executors is not None:
        context_builder = executors.context_builder
        skill_executor = executors.skill_executor
    request = RecruiterSkillExecutionRequest(
        flow=FLOW_EVALUATE_AND_POSITION,
        allow_provider_execution=provider_allowed,
    )
    kwargs: dict[str, Any] = {"executor": skill_executor}
    kwargs["context_builder"] = context_builder or build_recruiter_context
    return run_recruiter_skill_execution(request, **kwargs)


def _overall_status(
    *,
    provider_allowed: bool,
    positioning_report,
    documents: dict[str, dict[str, Any]],
) -> str:
    if not provider_allowed:
        return "BLOCKED_EXECUTION_DISABLED"
    if positioning_report.status is not RecruiterSkillExecutionStatus.EXECUTION_READY:
        return "BLOCKED_POSITIONING_UNAVAILABLE"

    approved = RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_APPROVED.value
    changes = RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_CHANGES_REQUESTED.value
    statuses = {name: doc["status"] for name, doc in documents.items()}
    if all(value == approved for value in statuses.values()):
        return "READY"
    if any(value in (approved, changes) for value in statuses.values()):
        return "NEEDS_REVIEW"
    return "BLOCKED_DOCUMENTS_FAILED"


def _collect_warnings(routing, facts_bundle, positioning_report, executor_error) -> list[str]:
    warnings: list[str] = []
    warnings.extend(routing.warnings)
    warnings.extend(facts_bundle.warnings)
    warnings.extend(positioning_report.warnings)
    if executor_error:
        warnings.append(f"executor unavailable: {executor_error}")
    # de-dupe preserving order
    seen: set[str] = set()
    result: list[str] = []
    for item in warnings:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _blocked_facts_result(
    facts_bundle: CareerFactsBundle,
    hermes_home: Path | str | None,
) -> dict[str, Any]:
    base = career_facts_dir(hermes_home)
    missing = [
        str(base / "manifest.yaml"),
        str(base / "career_facts.json"),
        str(base / "preferences.yaml"),
    ]
    lines = [
        _DRAFT_ONLY_HEADER,
        "",
        "Не могу подготовить пакет: карьерные факты не подтверждены "
        "(манифест не одобрен, отсутствует или не прошёл проверку целостности).",
        "Факты не выдумываются. Ожидаемые файлы источника истины:",
    ]
    lines.extend(f"  - {path}" for path in missing)
    if facts_bundle.warnings:
        lines.append("")
        lines.append("Причины:")
        lines.extend(f"  - {warning}" for warning in facts_bundle.warnings)
    report = {
        "helper_id": RECRUITER_APPLICATION_HELPER,
        "status": "BLOCKED_FACTS_UNVERIFIED",
        "missing_career_facts": missing,
        "warnings": list(facts_bundle.warnings),
        "documents": {},
        "safety": {"draft_only": True, "no_outbound": True, "no_job_intel_db_write": True},
    }
    return {"status": "BLOCKED_FACTS_UNVERIFIED", "text": "\n".join(lines), "report": report}


def _blocked_not_application_result(routing) -> dict[str, Any]:
    text = "\n".join(
        [
            _DRAFT_ONLY_HEADER,
            "",
            "Запрос не относится к подготовке пакета документов "
            f"(маршрутизация: {routing.selected_bundle or 'не выбрана'}).",
            "Для оценки вакансии используйте decision-support, а не application-package.",
        ]
    )
    report = {
        "helper_id": RECRUITER_APPLICATION_HELPER,
        "status": "BLOCKED_NOT_APPLICATION_REQUEST",
        "routing": {
            "selected_bundle": routing.selected_bundle,
            "reasoning_summary": routing.reasoning_summary,
        },
        "documents": {},
        "safety": {"draft_only": True, "no_outbound": True, "no_job_intel_db_write": True},
    }
    return {"status": "BLOCKED_NOT_APPLICATION_REQUEST", "text": text, "report": report}


def _format_text(
    status: str,
    documents: dict[str, dict[str, Any]],
    warnings: list[str],
    executor_error: str | None,
) -> str:
    lines = [_DRAFT_ONLY_HEADER, ""]
    summary = {
        "READY": "Черновой пакет готов к вашей проверке (отправка запрещена).",
        "NEEDS_REVIEW": "Черновики подготовлены, но есть замечания ревьюера — см. ниже.",
        "BLOCKED_EXECUTION_DISABLED": "Провайдерное исполнение выключено — черновики не генерировались.",
        "BLOCKED_POSITIONING_UNAVAILABLE": "Не удалось построить позиционирование — документы не готовились.",
        "BLOCKED_DOCUMENTS_FAILED": "Документы не удалось подготовить — см. замечания.",
    }.get(status, status)
    lines.append(summary)
    lines.append("")
    for document_type in _DOCUMENT_TYPES:
        doc = documents.get(document_type)
        title = _DOCUMENT_TITLES.get(document_type, document_type)
        if doc is None:
            lines.append(f"- {title}: не подготовлено")
            continue
        lines.append(f"- {title}: {doc['status']}")
        review = doc.get("review_result") or {}
        required_changes = review.get("required_changes") or []
        if required_changes:
            for change in required_changes:
                lines.append(f"    • замечание ревьюера: {change}")
        for err in doc.get("errors") or []:
            lines.append(f"    • ошибка: {err}")
    if warnings:
        lines.append("")
        lines.append("Предупреждения:")
        lines.extend(f"  - {warning}" for warning in warnings)
    if executor_error:
        lines.append("")
        lines.append(f"Исполнитель недоступен: {executor_error}")
    return "\n".join(lines)
