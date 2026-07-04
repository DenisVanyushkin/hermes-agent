"""Orchestrating flow for the modular Company & Vacancy Decision Support Bundle.

Runs only the requested analysis modules, applies safety/privacy/research
gates where relevant, validates module outputs, and reduces the overall
status from requested modules only (docs/hermes_recruiter_decision_support.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .recruiter_company_research import (
    CompanyResearchQualityGateReport,
    run_company_research_quality_gate,
)
from .recruiter_decision_modules import (
    BLOCK_REASON_MISSING_REQUIRED_INPUT,
    DECISION_MODULE_IDS,
    DECISION_MODULE_REGISTRY,
    DECISION_PACKET_SCHEMA,
    DIAG_CAREER_FACT_SOURCE_UNAVAILABLE,
    DIAG_COMPANY_RESEARCH_UNAVAILABLE,
    DIAG_PRIVACY_GATE_BLOCKED,
    DIAG_REQUIRED_OUTPUT_INTERNAL_LANGUAGE_FORBIDDEN,
    DIAG_REQUIRED_OUTPUT_TOO_GENERIC,
    DIAG_VACANCY_SOURCE_UNAVAILABLE,
    INPUT_CAREER_FACT_SOURCE,
    INPUT_COMPANY_IDENTITY,
    INPUT_PUBLIC_RESEARCH,
    INPUT_ROLE_CONTEXT,
    INPUT_VACANCY_SOURCE,
    DecisionBundleStatus,
    DecisionModuleResult,
    DecisionModuleStatus,
    missing_inputs_for_module,
    parse_requested_outputs,
    reduce_overall_status,
)
from .recruiter_real_data_privacy_gate import (
    CareerSourceApproval,
    RealDataPrivacyGateRequest,
    RealDataPrivacyGateStatus,
    evaluate_real_data_application_materials_privacy_gate,
)


_ALLOWED_RECOMMENDATION_DECISIONS = {
    "strong_apply",
    "apply",
    "consider",
    "manual_review_required",
    "do_not_apply",
    "reject",
}
_ALLOWED_SUPPORT_LEVELS = {"explicit", "derived_safe", "adjacent", "weak", "unsupported"}

# Internal pipeline/process/provenance language that must never appear in
# user-facing decision-support outputs.
_FORBIDDEN_INTERNAL_LANGUAGE = (
    "positioning packet",
    "candidate facts packet",
    "provider-visible",
    "provider execution",
    "privacy gate",
    "provenance",
    "evidence packet",
    "claim ids",
    "fact ids",
    "skill_id",
    "dry-run",
    "source_ref",
)

_REQUIRED_MODULE_FIELDS: dict[str, tuple[str, ...]] = {
    "vacancy_assessment": ("company", "role", "fit", "summary"),
    "company_assessment": ("recommendation", "confidence", "summary", "sources"),
    "company_risk_register": ("risks",),
    "recommendation": ("decision", "confidence", "verdict", "next_action", "what_would_change"),
    "positioning_summary": (
        "target_role_framing",
        "positioning_angle",
        "strongest_supported_overlap",
        "recommended_narrative",
    ),
    "evidence_backed_supporting_claims": ("claims",),
    "claims_to_avoid": ("claims",),
    "questions_to_ask": (),
}

_INPUT_UNAVAILABLE_DIAGNOSTICS = {
    INPUT_VACANCY_SOURCE: DIAG_VACANCY_SOURCE_UNAVAILABLE,
    INPUT_CAREER_FACT_SOURCE: DIAG_CAREER_FACT_SOURCE_UNAVAILABLE,
    INPUT_PUBLIC_RESEARCH: DIAG_COMPANY_RESEARCH_UNAVAILABLE,
}


@dataclass(slots=True)
class DecisionModuleExecution:
    payload: dict[str, Any]
    confidence: str = "medium"
    sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class DecisionModuleExecutor(Protocol):
    def execute(
        self,
        *,
        module_id: str,
        skill_id: str,
        module_input: dict[str, Any],
    ) -> DecisionModuleExecution: ...


@dataclass(slots=True)
class DecisionSupportRequest:
    prompt: str = ""
    requested_outputs: list[str] | None = None
    vacancy_source: dict[str, Any] | None = None
    career_fact_sources: list[dict[str, Any]] = field(default_factory=list)
    company_identity: str | None = None
    company_research_claims: list[dict[str, Any]] = field(default_factory=list)
    career_facts: dict[str, Any] | None = None
    candidate_preferences: dict[str, Any] | None = None
    role_context: str | None = None
    permitted_source_types: list[str] | None = None
    output_mode: str = "draft_only"
    outbound_enabled: bool = False
    crm_writes_enabled: bool = False
    job_intel_writes_enabled: bool = False
    browser_automation_enabled: bool = False
    private_file_access_requested: bool = False
    private_file_access_approved: bool = False


@dataclass(slots=True)
class DecisionSupportReport:
    status: DecisionBundleStatus
    requested_outputs: list[str]
    preset_id: str | None
    modules: dict[str, DecisionModuleResult]
    safety: dict[str, bool]
    gates: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DECISION_PACKET_SCHEMA,
            "status": self.status.value,
            "requested_outputs": list(self.requested_outputs),
            "requested_outputs_preset": self.preset_id,
            "modules": {module_id: result.to_dict() for module_id, result in self.modules.items()},
            "safety": dict(self.safety),
            "gates": dict(self.gates),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


def run_recruiter_decision_support_flow(
    request: DecisionSupportRequest,
    *,
    module_executor: DecisionModuleExecutor | None = None,
) -> DecisionSupportReport:
    parsed = parse_requested_outputs(
        request.prompt,
        context={"requested_outputs": request.requested_outputs} if request.requested_outputs else None,
    )
    requested = list(parsed.requested)
    warnings = list(parsed.warnings)
    errors: list[str] = []

    safety = {
        "no_outbound": not request.outbound_enabled,
        "no_submission": True,
        "no_crm_writes": not request.crm_writes_enabled,
        "no_job_intel_writes": not request.job_intel_writes_enabled,
        "no_browser_automation": not request.browser_automation_enabled,
        "manual_review_required": True,
        "draft_only": request.output_mode == "draft_only",
    }

    modules: dict[str, DecisionModuleResult] = {}
    gates: dict[str, Any] = {}

    # Always-required safety rules: any enabled write/outbound capability
    # blocks the whole run, regardless of requested modules.
    safety_violations = [name for name, ok in safety.items() if not ok]
    if safety_violations:
        errors.append(f"safety violations detected: {', '.join(sorted(safety_violations))}")
        for module_id in requested:
            modules[module_id] = DecisionModuleResult(
                module_id=module_id,
                status=DecisionModuleStatus.BLOCKED,
                block_reason="SAFETY_POLICY_VIOLATION",
            )
        return DecisionSupportReport(
            status=DecisionBundleStatus.BLOCKED,
            requested_outputs=requested,
            preset_id=parsed.preset_id,
            modules=modules,
            safety=safety,
            gates=gates,
            warnings=warnings,
            errors=errors,
        )

    available_inputs, privacy_gate_report, research_gate_report = _evaluate_inputs_and_gates(
        request, requested, gates
    )

    executed: dict[str, DecisionModuleExecution] = {}
    for module_id in [m for m in DECISION_MODULE_IDS if m in requested]:
        if module_id == "manual_review_warnings":
            continue  # composed deterministically at the end
        modules[module_id] = _run_module(
            module_id,
            request=request,
            available_inputs=available_inputs,
            privacy_gate_report=privacy_gate_report,
            research_gate_report=research_gate_report,
            module_executor=module_executor,
            executed=executed,
            requested=requested,
        )

    if "manual_review_warnings" in requested:
        modules["manual_review_warnings"] = _compose_manual_review_warnings(modules, safety, warnings)

    for module_id in DECISION_MODULE_IDS:
        if module_id not in modules:
            modules[module_id] = DecisionModuleResult(
                module_id=module_id,
                status=DecisionModuleStatus.SKIPPED_NOT_REQUESTED,
            )

    status = reduce_overall_status(requested=requested, results=modules)
    return DecisionSupportReport(
        status=status,
        requested_outputs=requested,
        preset_id=parsed.preset_id,
        modules=modules,
        safety=safety,
        gates=gates,
        warnings=warnings,
        errors=errors,
    )


def _evaluate_inputs_and_gates(
    request: DecisionSupportRequest,
    requested: list[str],
    gates: dict[str, Any],
) -> tuple[set[str], Any, CompanyResearchQualityGateReport | None]:
    available: set[str] = set()

    vacancy = request.vacancy_source or {}
    vacancy_approved = bool(vacancy.get("approved")) and bool(vacancy.get("source_type"))
    if vacancy_approved:
        available.add(INPUT_VACANCY_SOURCE)

    approved_career = [
        source
        for source in request.career_fact_sources
        if source.get("approved")
        and source.get("source_kind") not in {"generated_draft", "previous_generated_material"}
    ]

    if request.company_identity or vacancy_approved:
        available.add(INPUT_COMPANY_IDENTITY)
    if request.role_context:
        available.add(INPUT_ROLE_CONTEXT)

    requested_specs = [DECISION_MODULE_REGISTRY[m] for m in requested if m in DECISION_MODULE_REGISTRY]

    privacy_gate_report = None
    if any(spec.uses_candidate_facts for spec in requested_specs):
        gate_request = RealDataPrivacyGateRequest(
            vacancy_source_type=vacancy.get("source_type"),
            vacancy_source_approved=bool(vacancy.get("approved")),
            career_sources=[CareerSourceApproval.from_dict(s) for s in request.career_fact_sources],
            permitted_source_types=(
                request.permitted_source_types
                if request.permitted_source_types is not None
                else _default_permitted_source_types(request)
            ),
            output_mode=request.output_mode,
            outbound_enabled=request.outbound_enabled,
            crm_writes_enabled=request.crm_writes_enabled,
            job_intel_writes_enabled=request.job_intel_writes_enabled,
            browser_automation_enabled=request.browser_automation_enabled,
            private_file_access_requested=request.private_file_access_requested,
            private_file_access_approved=request.private_file_access_approved,
        )
        privacy_gate_report = evaluate_real_data_application_materials_privacy_gate(gate_request)
        gates["privacy_gate"] = privacy_gate_report.to_dict()
        if privacy_gate_report.status is RealDataPrivacyGateStatus.READY and approved_career:
            available.add(INPUT_CAREER_FACT_SOURCE)
    elif approved_career:
        available.add(INPUT_CAREER_FACT_SOURCE)

    research_gate_report = None
    if any(spec.uses_company_research for spec in requested_specs):
        research_gate_report = run_company_research_quality_gate(request.company_research_claims)
        gates["company_research_quality_gate"] = research_gate_report.to_dict()
        if research_gate_report.ready:
            available.add(INPUT_PUBLIC_RESEARCH)

    return available, privacy_gate_report, research_gate_report


def _default_permitted_source_types(request: DecisionSupportRequest) -> list[str]:
    """Auto-permit only the source types the caller explicitly approved."""
    permitted: list[str] = []
    vacancy = request.vacancy_source or {}
    if vacancy.get("approved") and vacancy.get("source_type"):
        permitted.append(str(vacancy["source_type"]))
    for source in request.career_fact_sources:
        if source.get("approved") and source.get("source_type"):
            permitted.append(str(source["source_type"]))
    return sorted(set(permitted))


def _run_module(
    module_id: str,
    *,
    request: DecisionSupportRequest,
    available_inputs: set[str],
    privacy_gate_report: Any,
    research_gate_report: CompanyResearchQualityGateReport | None,
    module_executor: DecisionModuleExecutor | None,
    executed: dict[str, DecisionModuleExecution],
    requested: list[str] | None = None,
) -> DecisionModuleResult:
    spec = DECISION_MODULE_REGISTRY[module_id]

    include_career_facts = spec.uses_candidate_facts
    if spec.uses_candidate_facts and privacy_gate_report is not None:
        if privacy_gate_report.status is not RealDataPrivacyGateStatus.READY:
            if spec.degraded_allowed:
                # Run without candidate facts rather than blocking synthesis modules.
                include_career_facts = False
                return _execute_module(
                    module_id,
                    spec.skill_id,
                    request,
                    module_executor,
                    executed,
                    degraded=True,
                    extra_warnings=["career facts unavailable; ran without candidate-specific evidence"],
                    include_career_facts=False,
                )
            # Distinguish "no source at all" from "source present but not approved".
            if request.career_fact_sources or request.vacancy_source:
                has_any_career = bool(request.career_fact_sources)
                reason = (
                    DIAG_PRIVACY_GATE_BLOCKED
                    if has_any_career
                    else DIAG_CAREER_FACT_SOURCE_UNAVAILABLE
                )
            else:
                reason = DIAG_CAREER_FACT_SOURCE_UNAVAILABLE
            return DecisionModuleResult(
                module_id=module_id,
                status=DecisionModuleStatus.BLOCKED,
                block_reason=reason,
                warnings=list(privacy_gate_report.warnings),
            )

    missing = missing_inputs_for_module(module_id, available_inputs=available_inputs)
    if missing:
        if spec.degraded_allowed:
            degraded_warnings = [f"missing input for degraded run: {item}" for item in missing]
            return _execute_module(
                module_id,
                spec.skill_id,
                request,
                module_executor,
                executed,
                degraded=True,
                extra_warnings=degraded_warnings,
                include_career_facts=include_career_facts,
            )
        return DecisionModuleResult(
            module_id=module_id,
            status=DecisionModuleStatus.BLOCKED,
            block_reason=_missing_input_reason(missing, spec, research_gate_report),
        )

    if spec.uses_company_research and research_gate_report is not None and not research_gate_report.ready:
        return DecisionModuleResult(
            module_id=module_id,
            status=DecisionModuleStatus.BLOCKED,
            block_reason=research_gate_report.blocked_reason or DIAG_COMPANY_RESEARCH_UNAVAILABLE,
            warnings=list(research_gate_report.warnings),
        )

    # Only upstream modules that were requested but failed degrade this run;
    # deliberately-unrequested upstreams (e.g. quick screen without company
    # assessment) narrow the scope without lowering confidence.
    requested_set = set(requested or DECISION_MODULE_IDS)
    upstream_missing = [
        m for m in spec.upstream_modules if m in requested_set and m not in executed
    ]
    upstream_unrequested = [m for m in spec.upstream_modules if m not in requested_set]
    degraded = bool(upstream_missing) and spec.degraded_allowed
    extra_warnings = (
        [f"upstream module not available: {item}" for item in upstream_missing] if degraded else []
    )
    if upstream_unrequested:
        extra_warnings = [
            *extra_warnings,
            f"scope note: assessed without {', '.join(m.replace('_', ' ') for m in upstream_unrequested)} (not requested)",
        ]
    return _execute_module(
        module_id,
        spec.skill_id,
        request,
        module_executor,
        executed,
        degraded=degraded,
        extra_warnings=extra_warnings,
        include_career_facts=include_career_facts,
    )


def _missing_input_reason(
    missing: list[str],
    spec: Any,
    research_gate_report: CompanyResearchQualityGateReport | None,
) -> str:
    for item in missing:
        base = item.split("|")[0]
        if item in _INPUT_UNAVAILABLE_DIAGNOSTICS:
            return _INPUT_UNAVAILABLE_DIAGNOSTICS[item]
        if base in _INPUT_UNAVAILABLE_DIAGNOSTICS:
            return _INPUT_UNAVAILABLE_DIAGNOSTICS[base]
    if spec.uses_company_research and research_gate_report is not None and not research_gate_report.ready:
        return research_gate_report.blocked_reason or DIAG_COMPANY_RESEARCH_UNAVAILABLE
    return BLOCK_REASON_MISSING_REQUIRED_INPUT


def _execute_module(
    module_id: str,
    skill_id: str,
    request: DecisionSupportRequest,
    module_executor: DecisionModuleExecutor | None,
    executed: dict[str, DecisionModuleExecution],
    *,
    degraded: bool,
    extra_warnings: list[str],
    include_career_facts: bool = True,
) -> DecisionModuleResult:
    if module_executor is None:
        return DecisionModuleResult(
            module_id=module_id,
            status=DecisionModuleStatus.INCONCLUSIVE,
            block_reason="MODULE_EXECUTOR_NOT_WIRED",
            warnings=["module executor not wired; run again with provider execution enabled"],
        )

    spec = DECISION_MODULE_REGISTRY[module_id]
    module_input = {
        "module_id": module_id,
        "vacancy_source": request.vacancy_source,
        "company_identity": request.company_identity,
        "company_research_claims": request.company_research_claims,
        # Candidate facts reach a module only if it declares candidate-fact use
        # and the privacy gate approved the sources (include_career_facts).
        "career_facts": request.career_facts if (spec.uses_candidate_facts and include_career_facts) else None,
        "candidate_preferences": request.candidate_preferences,
        "role_context": request.role_context,
        "upstream_results": {name: execution.payload for name, execution in executed.items()},
        "degraded": degraded,
    }
    execution = module_executor.execute(module_id=module_id, skill_id=skill_id, module_input=module_input)
    if execution.errors:
        return DecisionModuleResult(
            module_id=module_id,
            status=DecisionModuleStatus.BLOCKED,
            block_reason="MODULE_EXECUTION_FAILED",
            errors=list(execution.errors),
            warnings=[*extra_warnings, *execution.warnings],
        )

    validation_errors, block_reason = _validate_module_output(module_id, execution.payload)
    if validation_errors:
        return DecisionModuleResult(
            module_id=module_id,
            status=DecisionModuleStatus.BLOCKED,
            block_reason=block_reason,
            errors=validation_errors,
            warnings=[*extra_warnings, *execution.warnings],
        )

    executed[module_id] = execution
    status = DecisionModuleStatus.INCONCLUSIVE if degraded else DecisionModuleStatus.READY
    confidence = "low" if degraded else execution.confidence
    return DecisionModuleResult(
        module_id=module_id,
        status=status,
        confidence=confidence,
        sources=[{"source": source} for source in execution.sources],
        warnings=[*extra_warnings, *execution.warnings],
        payload=dict(execution.payload),
    )


def _validate_module_output(module_id: str, payload: dict[str, Any]) -> tuple[list[str], str | None]:
    errors: list[str] = []
    if not payload:
        return ["module output is empty"], DIAG_REQUIRED_OUTPUT_TOO_GENERIC

    for phrase in _FORBIDDEN_INTERNAL_LANGUAGE:
        if _payload_contains_phrase(payload, phrase):
            return (
                [f"internal pipeline language detected in user-facing output: {phrase}"],
                DIAG_REQUIRED_OUTPUT_INTERNAL_LANGUAGE_FORBIDDEN,
            )

    for required in _REQUIRED_MODULE_FIELDS.get(module_id, ()):
        if not payload.get(required):
            errors.append(f"required field missing or empty: {required}")

    if module_id == "recommendation":
        decision = str(payload.get("decision") or "")
        if decision and decision not in _ALLOWED_RECOMMENDATION_DECISIONS:
            errors.append(f"recommendation decision label not allowed: {decision}")

    if module_id == "company_risk_register":
        for index, risk in enumerate(payload.get("risks") or []):
            for key in ("risk", "severity", "confidence", "evidence", "mitigation"):
                if not (risk or {}).get(key):
                    errors.append(f"risk[{index}] missing field: {key}")

    if module_id == "evidence_backed_supporting_claims":
        for index, claim in enumerate(payload.get("claims") or []):
            level = (claim or {}).get("support_level")
            if level not in _ALLOWED_SUPPORT_LEVELS:
                errors.append(f"claim[{index}] has invalid support_level: {level}")
            if not (claim or {}).get("source_reference"):
                errors.append(f"claim[{index}] missing source_reference")

    if module_id == "questions_to_ask":
        if not any(isinstance(value, list) and value for value in payload.values()):
            errors.append("questions_to_ask output has no question groups")

    if errors:
        return errors, DIAG_REQUIRED_OUTPUT_TOO_GENERIC
    return [], None


def _payload_contains_phrase(value: Any, phrase: str) -> bool:
    if isinstance(value, str):
        return phrase in value.lower()
    if isinstance(value, dict):
        return any(_payload_contains_phrase(item, phrase) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_payload_contains_phrase(item, phrase) for item in value)
    return False


def _compose_manual_review_warnings(
    modules: dict[str, DecisionModuleResult],
    safety: dict[str, bool],
    run_warnings: list[str],
) -> DecisionModuleResult:
    collected: list[str] = list(run_warnings)
    for result in modules.values():
        collected.extend(result.warnings)
        if result.status is DecisionModuleStatus.BLOCKED and result.block_reason:
            collected.append(f"{result.module_id} blocked: {result.block_reason}")
    payload = {
        "warnings": collected,
        "flags": {
            "manual_review_required": True,
            "draft_only": bool(safety.get("draft_only")),
            "no_outbound": bool(safety.get("no_outbound")),
            "no_submission": bool(safety.get("no_submission")),
        },
    }
    return DecisionModuleResult(
        module_id="manual_review_warnings",
        status=DecisionModuleStatus.READY,
        confidence="high",
        payload=payload,
        warnings=[],
    )
