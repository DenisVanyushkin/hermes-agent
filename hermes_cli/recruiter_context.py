from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from job_intel.recruiter_read_facade import RecruiterReadFacade
from job_intel.store import JobIntelStore

from .role_packages import build_repo_role_package_skill_context


_PRIVATE_CAREER_FILES = (
    "denis_vanyushkin_structured_resume_v1_1.json",
    "opportunity-thesis.md",
    "company_intelligence_architecture.md",
    "scoring_v3.md",
)
_RECRUITER_ROLE_PACKAGE_DIR = Path("role-packages") / "recruiter"
_DEFAULT_PRIVATE_CAREER_DIR = Path("~/.hermes/private/career/")


class RecruiterContextStatus(str, Enum):
    READY = "READY"
    SOURCE_REQUIRED = "SOURCE_REQUIRED"
    VACANCY_NOT_FOUND = "VACANCY_NOT_FOUND"
    OPPORTUNITY_NOT_FOUND = "OPPORTUNITY_NOT_FOUND"
    MACHINE_SCORE_UNAVAILABLE = "MACHINE_SCORE_UNAVAILABLE"
    PRIVATE_CONTEXT_MISSING = "PRIVATE_CONTEXT_MISSING"
    FACADE_ERROR = "FACADE_ERROR"
    PACKAGE_CONTEXT_ERROR = "PACKAGE_CONTEXT_ERROR"


@dataclass(slots=True)
class RecruiterContextRequest:
    vacancy_id: int | None = None
    vacancy_url: str | None = None
    opportunity_id: int | None = None
    job_intel_db_path: str | Path | None = None
    private_career_dir: str | Path | None = None
    repo_root: str | Path | None = None
    stale_after_days: int = 14

    def to_dict(self) -> dict[str, Any]:
        return {
            "vacancy_id": self.vacancy_id,
            "vacancy_url": self.vacancy_url,
            "opportunity_id": self.opportunity_id,
            "job_intel_db_path": str(self.job_intel_db_path) if self.job_intel_db_path is not None else None,
            "private_career_dir": self._private_dir_display(),
            "repo_root": str(_resolve_repo_root(self.repo_root)),
            "stale_after_days": self.stale_after_days,
        }

    def _private_dir_display(self) -> str:
        if self.private_career_dir is None:
            return str(_DEFAULT_PRIVATE_CAREER_DIR)
        return str(self.private_career_dir)


@dataclass(slots=True)
class RecruiterContextPacket:
    status: RecruiterContextStatus
    request: dict[str, Any]
    vacancy: dict[str, Any] | None
    opportunity: dict[str, Any] | None
    company_context: list[dict[str, Any]]
    application_history: dict[str, Any]
    machine_score: dict[str, Any]
    role_package_context: dict[str, Any]
    private_context: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def build_recruiter_context(request: RecruiterContextRequest) -> RecruiterContextPacket:
    _validate_request(request)

    repo_root = _resolve_repo_root(request.repo_root)
    private_dir = _resolve_private_career_dir(request.private_career_dir)
    warnings: list[str] = []
    errors: list[str] = []
    facade_methods: list[str] = []

    try:
        role_package_context = build_repo_role_package_skill_context(
            repo_root / _RECRUITER_ROLE_PACKAGE_DIR,
            repo_root=repo_root,
        )
    except Exception:
        return RecruiterContextPacket(
            status=RecruiterContextStatus.PACKAGE_CONTEXT_ERROR,
            request=request.to_dict(),
            vacancy=None,
            opportunity=None,
            company_context=[],
            application_history={"status": "not_requested", "history": [], "artifacts": [], "feedback": []},
            machine_score={"status": RecruiterContextStatus.MACHINE_SCORE_UNAVAILABLE.value},
            role_package_context={},
            private_context=_build_private_context(private_dir, warnings=[]),
            warnings=[],
            errors=["package context unavailable"],
            provenance={
                "role_package_path": str((repo_root / _RECRUITER_ROLE_PACKAGE_DIR).resolve()),
                "private_dir_checked": str(private_dir),
                "facade_methods": [],
                "writes_performed": False,
            },
        )

    private_context = _build_private_context(private_dir, warnings)

    facade = RecruiterReadFacade(
        JobIntelStore(Path(request.job_intel_db_path) if request.job_intel_db_path is not None else None),
        stale_after_days=request.stale_after_days,
    )

    try:
        vacancy, opportunity, company_context, application_history = _load_context_from_facade(
            request,
            facade,
            facade_methods,
            warnings,
        )
    except _ContextNotFound as exc:
        return RecruiterContextPacket(
            status=exc.status,
            request=request.to_dict(),
            vacancy=exc.vacancy,
            opportunity=exc.opportunity,
            company_context=exc.company_context,
            application_history=exc.application_history,
            machine_score=_machine_score_payload(exc.vacancy),
            role_package_context=role_package_context,
            private_context=private_context,
            warnings=warnings,
            errors=[],
            provenance=_provenance(request, repo_root, private_dir, facade_methods),
        )
    except Exception:
        errors.append("facade_error: recruiter read facade failed")
        return RecruiterContextPacket(
            status=RecruiterContextStatus.FACADE_ERROR,
            request=request.to_dict(),
            vacancy=None,
            opportunity=None,
            company_context=[],
            application_history={"status": "error", "history": [], "artifacts": [], "feedback": []},
            machine_score={"status": RecruiterContextStatus.MACHINE_SCORE_UNAVAILABLE.value},
            role_package_context=role_package_context,
            private_context=private_context,
            warnings=warnings,
            errors=errors,
            provenance=_provenance(request, repo_root, private_dir, facade_methods),
        )

    return RecruiterContextPacket(
        status=RecruiterContextStatus.READY,
        request=request.to_dict(),
        vacancy=vacancy,
        opportunity=opportunity,
        company_context=company_context,
        application_history=application_history,
        machine_score=_machine_score_payload(vacancy),
        role_package_context=role_package_context,
        private_context=private_context,
        warnings=warnings,
        errors=errors,
        provenance=_provenance(request, repo_root, private_dir, facade_methods),
    )


def _load_context_from_facade(
    request: RecruiterContextRequest,
    facade: RecruiterReadFacade,
    facade_methods: list[str],
    warnings: list[str],
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    if request.vacancy_id is not None:
        facade_methods.append("get_vacancy_by_id")
        vacancy_payload = facade.get_vacancy_by_id(request.vacancy_id)
    elif request.vacancy_url is not None:
        facade_methods.append("get_vacancy_by_url")
        vacancy_payload = facade.get_vacancy_by_url(request.vacancy_url)
    else:
        facade_methods.append("get_opportunity_by_id")
        opportunity_payload = facade.get_opportunity_by_id(request.opportunity_id or 0)
        warnings.extend(opportunity_payload.get("warnings", []))
        if opportunity_payload.get("status") != "found":
            raise _ContextNotFound(
                RecruiterContextStatus.OPPORTUNITY_NOT_FOUND,
                opportunity=opportunity_payload.get("opportunity"),
            )
        opportunity = opportunity_payload.get("opportunity")
        vacancy_id = opportunity.get("vacancy_id") if isinstance(opportunity, dict) else None
        if not vacancy_id:
            raise _ContextNotFound(RecruiterContextStatus.VACANCY_NOT_FOUND, opportunity=opportunity)
        facade_methods.append("get_vacancy_by_id")
        vacancy_payload = facade.get_vacancy_by_id(vacancy_id)

    warnings.extend(vacancy_payload.get("warnings", []))
    vacancy_status = vacancy_payload.get("status")
    if vacancy_status != "found":
        raise _ContextNotFound(RecruiterContextStatus.VACANCY_NOT_FOUND)
    vacancy = vacancy_payload.get("vacancy")
    company_context = list(vacancy.get("company_context") or []) if isinstance(vacancy, dict) else []

    facade_methods.append("get_opportunity_for_vacancy")
    opportunity_payload = facade.get_opportunity_for_vacancy(vacancy["vacancy_id"])
    warnings.extend(opportunity_payload.get("warnings", []))
    opportunity = opportunity_payload.get("opportunity") if opportunity_payload.get("status") == "found" else None

    if not company_context and vacancy.get("company"):
        facade_methods.append("get_company_context")
        company_payload = facade.get_company_context(vacancy["company"])
        warnings.extend(company_payload.get("warnings", []))
        if company_payload.get("status") == "found":
            company_context = list(company_payload.get("company_context") or [])

    application_history = {"status": "not_requested", "history": [], "artifacts": [], "feedback": []}
    if opportunity and opportunity.get("id") is not None:
        facade_methods.append("get_application_history")
        application_history = facade.get_application_history(int(opportunity["id"]))
        warnings.extend(application_history.get("warnings", []))

    return vacancy, opportunity, company_context, application_history


def _machine_score_payload(vacancy: dict[str, Any] | None) -> dict[str, Any]:
    evaluation = vacancy.get("evaluation") if vacancy else None
    if not isinstance(evaluation, dict):
        return {"status": RecruiterContextStatus.MACHINE_SCORE_UNAVAILABLE.value}
    return {
        "status": "available",
        "score": evaluation.get("score"),
        "tier": evaluation.get("tier"),
        "recommendation": evaluation.get("recommendation"),
        "salary_tier": evaluation.get("salary_tier"),
        "evaluation_id": evaluation.get("evaluation_id"),
        "run_id": evaluation.get("run_id"),
        "created_at": evaluation.get("created_at"),
        "matched_signals": evaluation.get("matched_signals", []),
        "concerns": evaluation.get("concerns", []),
        "reasons": evaluation.get("reasons", []),
    }


def _build_private_context(private_dir: Path, warnings: list[str]) -> dict[str, Any]:
    files = {name: {"present": (private_dir / name).exists()} for name in _PRIVATE_CAREER_FILES}
    present_count = sum(1 for item in files.values() if item["present"])
    if present_count == len(_PRIVATE_CAREER_FILES):
        status = "PRIVATE_CONTEXT_AVAILABLE"
    elif present_count == 0:
        status = RecruiterContextStatus.PRIVATE_CONTEXT_MISSING.value
        warnings.append("private_context_missing")
    else:
        status = "PARTIAL"
        warnings.append("private_context_partial")
    return {
        "status": status,
        "dir": str(private_dir),
        "files": files,
    }


def _provenance(
    request: RecruiterContextRequest,
    repo_root: Path,
    private_dir: Path,
    facade_methods: list[str],
) -> dict[str, Any]:
    return {
        "db_path": str(Path(request.job_intel_db_path).expanduser()) if request.job_intel_db_path is not None else None,
        "facade_methods": facade_methods,
        "role_package_path": str((repo_root / _RECRUITER_ROLE_PACKAGE_DIR).resolve()),
        "private_dir_checked": str(private_dir),
        "writes_performed": False,
    }


def _validate_request(request: RecruiterContextRequest) -> None:
    provided = [
        request.vacancy_id is not None,
        request.vacancy_url is not None,
        request.opportunity_id is not None,
    ]
    if sum(provided) != 1:
        raise ValueError("exactly one of vacancy_id, vacancy_url, or opportunity_id is required")


def _resolve_private_career_dir(path: str | Path | None) -> Path:
    if path is None:
        return _DEFAULT_PRIVATE_CAREER_DIR.expanduser()
    return Path(path).expanduser()


def _resolve_repo_root(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path).resolve()
    return Path(__file__).resolve().parents[1]


@dataclass(slots=True)
class _ContextNotFound(Exception):
    status: RecruiterContextStatus
    vacancy: dict[str, Any] | None = None
    opportunity: dict[str, Any] | None = None
    company_context: list[dict[str, Any]] = field(default_factory=list)
    application_history: dict[str, Any] = field(
        default_factory=lambda: {"status": "not_requested", "history": [], "artifacts": [], "feedback": []}
    )
