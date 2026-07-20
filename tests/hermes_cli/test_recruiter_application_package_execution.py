from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from hermes_cli.recruiter_application_package_execution import (
    RECRUITER_APPLICATION_HELPER,
    RecruiterApplicationExecutors,
    execute_recruiter_application_package_helper,
)
from hermes_cli.recruiter_context import RecruiterContextPacket, RecruiterContextStatus
from hermes_cli.recruiter_skill_execution import (
    RecruiterSkillExecutor,
    SkillExecutionResult,
)


REPO_ROOT = Path(__file__).resolve().parents[2]

_FACTS = {"schema_version": "1.1.0", "candidate": {"full_name": "Denis Vanyushkin"}}
_PREFS = "schema_version: '1.0.0'\ntarget_roles:\n  seniority_levels: [VP, Director]\n"


# --- career-facts SoT fixture (copied from test_recruiter_career_facts.py) ---
def _write_sot(home: Path, *, corrupt: bool = False, approved: bool = True) -> None:
    base = home / "job_intel" / "career_facts"
    base.mkdir(parents=True)
    facts_path = base / "career_facts.json"
    prefs_path = base / "preferences.yaml"
    facts_path.write_text(json.dumps(_FACTS), encoding="utf-8")
    prefs_path.write_text(_PREFS, encoding="utf-8")
    facts_hash = hashlib.sha256(facts_path.read_bytes()).hexdigest()
    prefs_hash = hashlib.sha256(prefs_path.read_bytes()).hexdigest()
    if corrupt:
        facts_path.write_text(json.dumps({**_FACTS, "tampered": True}), encoding="utf-8")
    (base / "manifest.yaml").write_text(
        "schema_version: '1.0.0'\n"
        f"approved: {'true' if approved else 'false'}\n"
        "files:\n"
        "  - path: career_facts.json\n"
        "    source_kind: structured_resume\n"
        "    source_type: structured_resume\n"
        f"    sha256: {facts_hash}\n"
        "  - path: preferences.yaml\n"
        "    source_kind: candidate_preferences\n"
        "    source_type: candidate_preferences\n"
        f"    sha256: {prefs_hash}\n",
        encoding="utf-8",
    )


# --- fake context packet (copied from test_recruiter_skill_execution.py) ---
def _context_packet() -> RecruiterContextPacket:
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


# --- fake positioning skill executor (copied from test_recruiter_skill_execution.py) ---
class _FakeSkillExecutor(RecruiterSkillExecutor):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(
        self,
        *,
        skill_id: str,
        skill_input: dict[str, object],
        skill_markdown_path: str,
        expected_schema: list[str],
    ) -> SkillExecutionResult:
        self.calls.append(skill_id)
        if skill_id == "vacancy-evaluation":
            payload = {
                "vacancy_evaluation_summary": "Strong fit with clear product leadership match.",
                "fit_interpretation": "High-confidence match for executive product scope.",
                "evidence_gaps": ["Exact team size not confirmed."],
                "recommendation_for_next_step": "Proceed to positioning synthesis.",
            }
        else:
            payload = {
                "positioning_summary": "Lead with B2B product leadership and scaling evidence.",
                "evidence_map": {"leadership": ["Scaled B2B platform teams."]},
                "proven_facts": ["Built product orgs."],
                "derived_positioning": ["Position as operator-executive with marketplace depth."],
                "gaps": ["Need explicit domain depth proof for this company."],
                "risks_and_mitigations": ["Avoid overstating prior company-stage similarity."],
            }
        return SkillExecutionResult(
            status="SUCCESS",
            skill_id=skill_id,
            output=payload,
            warnings=[],
            errors=[],
            provenance={"skill_markdown_path": skill_markdown_path, "expected_schema": expected_schema},
            provider_called=False,
        )


# --- fake document executor (copied from test_recruiter_document_execution.py) ---
class _FakeDocumentExecutor:
    def __init__(self, *, reviewer_verdict: str = "APPROVE") -> None:
        self.calls: list[str] = []
        self.reviewer_verdict = reviewer_verdict
        self.provider_backed = False

    def execute(
        self,
        *,
        skill_id: str,
        skill_input: dict[str, Any],
        expected_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(skill_id)
        if skill_id == "document-writer":
            return {
                "schema_version": "recruiter_document_packet_v1",
                "document_type": skill_input["document_type"],
                "audience": skill_input.get("audience"),
                "purpose": skill_input.get("purpose"),
                "source_positioning_packet_ref": skill_input["source_positioning_packet_ref"],
                "draft": {
                    "format": "text",
                    "content": "Draft content for user review only.",
                    "notes": ["Do not send without user approval."],
                },
                "review": {"status": "PENDING"},
                "status": "DRAFT_READY",
                "warnings": [],
                "errors": [],
                "provenance": {"expected_schema": expected_schema},
            }
        return {
            "status": "SUCCESS",
            "skill_id": "document-reviewer",
            "verdict": self.reviewer_verdict,
            "hallucination_risk": "low",
            "unsupported_claims": [],
            "genericness_assessment": "specific enough",
            "tone_seniority_assessment": "appropriate for executive audience",
            "missing_source_references": [],
            "required_changes": [] if self.reviewer_verdict == "APPROVE" else ["Tighten opening paragraph."],
            "warnings": [],
            "errors": [],
            "provenance": {"expected_schema": expected_schema},
        }


def _make_factory(*, reviewer_verdict: str = "APPROVE"):
    def factory() -> RecruiterApplicationExecutors:
        return RecruiterApplicationExecutors(
            skill_executor=_FakeSkillExecutor(),
            document_executor=_FakeDocumentExecutor(reviewer_verdict=reviewer_verdict),
            context_builder=lambda request: _context_packet(),
        )

    return factory


def test_helper_id_is_stable() -> None:
    assert RECRUITER_APPLICATION_HELPER == "recruiter_application_package_flow"


def test_blocked_when_career_facts_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))  # empty home -> no facts
    result = execute_recruiter_application_package_helper(
        user_message="подготовь пакет документов для вакансии",
    )
    assert result["status"] == "BLOCKED_FACTS_UNVERIFIED"
    assert "career_facts" in result["text"]
    assert result["report"]["safety"]["draft_only"] is True


def test_blocked_when_not_application_request(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_sot(tmp_path)  # facts present so routing is the gate under test
    result = execute_recruiter_application_package_helper(
        user_message="оцени вакансию https://hh.ru/vacancy/1",
    )
    assert result["status"] == "BLOCKED_NOT_APPLICATION_REQUEST"
    assert result["report"]["safety"]["draft_only"] is True


def test_happy_path_produces_three_draft_documents(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_sot(tmp_path)
    result = execute_recruiter_application_package_helper(
        user_message="подготовь резюме, сопроводительное письмо и сообщение рекрутеру",
        executor_factory=_make_factory(),
        config={"pipelines": {"execution": {"allow_real_provider_execution": True}}},
    )
    assert result["status"] == "READY"
    docs = result["report"]["documents"]
    assert set(docs) == {"cv", "cover_letter", "recruiter_message"}
    assert result["report"]["safety"]["draft_only"] is True
    assert "Draft-only" in result["text"]
    # machine-readable payload must be JSON serializable
    json.dumps(result["report"], sort_keys=True)


def test_disabled_provider_stays_draft_only_and_does_not_execute(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_sot(tmp_path)
    result = execute_recruiter_application_package_helper(
        user_message="подготовь резюме и сопроводительное письмо",
        executor_factory=_make_factory(),
        # no config -> provider fuse closed
    )
    assert result["status"] != "READY"
    assert result["report"]["safety"]["draft_only"] is True
    # nothing was executed against the fake document executor
    for doc in result["report"]["documents"].values():
        assert doc["provider_called"] is False


def test_review_remarks_surface_in_text(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_sot(tmp_path)
    result = execute_recruiter_application_package_helper(
        user_message="подготовь резюме, сопроводительное письмо и сообщение рекрутеру",
        executor_factory=_make_factory(reviewer_verdict="CHANGES_REQUESTED"),
        config={"pipelines": {"execution": {"allow_real_provider_execution": True}}},
    )
    assert result["status"] == "NEEDS_REVIEW"
    # remarks are surfaced, not hidden
    assert "CHANGES_REQUESTED" in result["text"] or "замечан" in result["text"].lower()
    assert result["report"]["safety"]["draft_only"] is True


# --- context builder that records the vacancy_url it was asked to resolve ---
class _RecordingContextBuilder:
    def __init__(self) -> None:
        self.seen_vacancy_url: str | None = None
        self.called = False

    def __call__(self, request: Any) -> RecruiterContextPacket:
        self.called = True
        self.seen_vacancy_url = getattr(request, "vacancy_url", None)
        return _context_packet()


def _recording_factory(recorder: _RecordingContextBuilder, *, reviewer_verdict: str = "APPROVE"):
    def factory() -> RecruiterApplicationExecutors:
        return RecruiterApplicationExecutors(
            skill_executor=_FakeSkillExecutor(),
            document_executor=_FakeDocumentExecutor(reviewer_verdict=reviewer_verdict),
            context_builder=recorder,
        )

    return factory


_PROVIDER_CONFIG = {"pipelines": {"execution": {"allow_real_provider_execution": True}}}


def test_a_vacancy_url_from_message_reaches_positioning_request(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_sot(tmp_path)
    recorder = _RecordingContextBuilder()
    result = execute_recruiter_application_package_helper(
        user_message="подготовь пакет документов для https://jobs.example.com/vacancy/42.",
        executor_factory=_recording_factory(recorder),
        config=_PROVIDER_CONFIG,
    )
    assert recorder.called is True
    assert recorder.seen_vacancy_url == "https://jobs.example.com/vacancy/42"
    assert result["report"]["safety"]["draft_only"] is True


def test_b_vacancy_url_falls_back_to_conversation_context(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_sot(tmp_path)
    recorder = _RecordingContextBuilder()
    result = execute_recruiter_application_package_helper(
        user_message="подготовь резюме и сопроводительное письмо",  # no URL here
        conversation_context="ранее в треде: https://hh.ru/vacancy/777 обсуждали",
        executor_factory=_recording_factory(recorder),
        config=_PROVIDER_CONFIG,
    )
    assert recorder.seen_vacancy_url == "https://hh.ru/vacancy/777"
    assert result["report"]["safety"]["draft_only"] is True


def test_c_no_vacancy_url_anywhere_is_graceful_block(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_sot(tmp_path)

    # Real context builder path (context_builder=None) with no URL: build_recruiter_context
    # raises ValueError, which run_recruiter_skill_execution turns into a graceful
    # INVALID_REQUEST -> BLOCKED_POSITIONING_UNAVAILABLE. No crash, no network.
    def factory() -> RecruiterApplicationExecutors:
        return RecruiterApplicationExecutors(
            skill_executor=_FakeSkillExecutor(),
            document_executor=_FakeDocumentExecutor(),
            context_builder=None,
        )

    result = execute_recruiter_application_package_helper(
        user_message="подготовь резюме, сопроводительное письмо и сообщение рекрутеру",
        executor_factory=factory,
        config=_PROVIDER_CONFIG,
    )
    assert result["status"] == "BLOCKED_POSITIONING_UNAVAILABLE"
    assert result["report"]["safety"]["draft_only"] is True


def test_d_fuse_open_builder_exception_degrades_to_draft_only_block(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_sot(tmp_path)

    import hermes_cli.recruiter_skill_provider_executor as skill_mod

    def _boom(*_a, **_k):
        raise RuntimeError("positioning_provider_client_unavailable")

    monkeypatch.setattr(skill_mod, "build_recruiter_positioning_skill_executor", _boom)

    result = execute_recruiter_application_package_helper(
        user_message="подготовь пакет документов для https://jobs.example.com/vacancy/42",
        config=_PROVIDER_CONFIG,  # fuse open, no executor_factory -> real builders attempted
    )
    assert result["status"] == "BLOCKED_EXECUTOR_UNAVAILABLE"
    assert result["report"]["safety"]["draft_only"] is True
    # the underlying builder error is disclosed, not swallowed
    assert "positioning_provider_client_unavailable" in result["text"]
    assert any("positioning_provider_client_unavailable" in w for w in result["report"]["warnings"])


def test_e_fuse_closed_never_builds_provider_executors(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_sot(tmp_path)

    import hermes_cli.recruiter_document_provider_executor as doc_mod
    import hermes_cli.recruiter_skill_provider_executor as skill_mod

    def _boom(*_a, **_k):  # pragma: no cover - must never run when fuse is closed
        raise AssertionError("provider builder called while fuse closed")

    monkeypatch.setattr(skill_mod, "build_recruiter_positioning_skill_executor", _boom)
    monkeypatch.setattr(doc_mod, "build_recruiter_document_provider_executor", _boom)

    result = execute_recruiter_application_package_helper(
        user_message="подготовь пакет документов для https://jobs.example.com/vacancy/42",
        # no config -> fuse closed, no executor_factory
    )
    assert result["status"] == "BLOCKED_EXECUTION_DISABLED"
    assert result["report"]["safety"]["draft_only"] is True
