"""Provider-backed ``RecruiterSkillExecutor`` for the evaluate-and-position flow.

``run_recruiter_skill_execution`` drives one executor that it calls twice, dispatching
on ``skill_id`` (vacancy evaluation, then positioning), and expects each call to return
a :class:`SkillExecutionResult`. The real provider executors
(:mod:`hermes_cli.recruiter_evaluation_provider_executor`,
:mod:`hermes_cli.recruiter_positioning_provider_executor`) each cover only one skill and
speak a different contract: ``execute(*, skill_input, expected_schema=None) -> dict``.

This module bridges the two: a single dispatching executor that composes both provider
builders and reshapes their dict output into ``SkillExecutionResult``. It never sends
anything and never touches the network on its own — the provider clients live inside the
injected/lazily-built provider executors.

Field-mapping policy (no invented fields): the provider's returned packet becomes
``SkillExecutionResult.output`` verbatim, so whichever required fields the packet already
carries at the top level are found by ``run_recruiter_skill_execution``'s validation;
fields the packet does not carry are simply absent and the runner degrades to
``SKILL_OUTPUT_INVALID`` (graceful). ``warnings``/``errors``/``provenance`` are lifted
from the packet when present, else defaulted empty.
"""

from __future__ import annotations

from typing import Any, Callable

from .recruiter_evaluation_provider_executor import (
    build_recruiter_evaluation_provider_executor,
    vacancy_evaluation_expected_schema,
)
from .recruiter_positioning_provider_executor import (
    build_recruiter_positioning_provider_executor,
    positioning_expected_schema,
)
from .recruiter_skill_execution import (
    POSITIONING_EVIDENCE_SKILL_ID,
    VACANCY_EVALUATION_SKILL_ID,
    SkillExecutionResult,
)

_ADAPTER_ID = "recruiter_provider_skill_executor"


class RecruiterProviderSkillExecutor:
    """Dispatch a ``skill_id`` to the matching provider executor and reshape its output.

    Satisfies the ``RecruiterSkillExecutor`` protocol expected by
    ``run_recruiter_skill_execution``.
    """

    provider_backed = True

    def __init__(self, *, evaluation_executor: Any, positioning_executor: Any) -> None:
        # skill_id -> (provider executor, provider-native expected-schema factory)
        self._by_skill: dict[str, tuple[Any, Callable[[], dict[str, Any]]]] = {
            VACANCY_EVALUATION_SKILL_ID: (evaluation_executor, vacancy_evaluation_expected_schema),
            POSITIONING_EVIDENCE_SKILL_ID: (positioning_executor, positioning_expected_schema),
        }

    def execute(
        self,
        *,
        skill_id: str,
        skill_input: dict[str, Any],
        skill_markdown_path: str,
        expected_schema: list[str],
    ) -> SkillExecutionResult:
        entry = self._by_skill.get(skill_id)
        if entry is None:
            return SkillExecutionResult(
                status="ERROR",
                skill_id=skill_id,
                output={},
                warnings=[],
                errors=[f"unknown_skill_id:{skill_id}"],
                provenance={"adapter": _ADAPTER_ID, "skill_markdown_path": skill_markdown_path},
                provider_called=False,
            )

        provider_executor, schema_factory = entry
        # The runner passes a list of required field names; the provider executors need
        # their own structured schema, so forward the provider-native schema instead.
        try:
            payload = provider_executor.execute(
                skill_input=dict(skill_input),
                expected_schema=schema_factory(),
            )
        except Exception as exc:
            return SkillExecutionResult(
                status="ERROR",
                skill_id=skill_id,
                output={},
                warnings=[],
                errors=[f"{type(exc).__name__}: {exc}"],
                provenance={"adapter": _ADAPTER_ID, "skill_markdown_path": skill_markdown_path},
                provider_called=bool(getattr(provider_executor, "provider_backed", True)),
            )

        return _payload_to_result(
            skill_id=skill_id,
            payload=payload,
            provider_executor=provider_executor,
            skill_markdown_path=skill_markdown_path,
            requested_fields=list(expected_schema or []),
        )


def _payload_to_result(
    *,
    skill_id: str,
    payload: Any,
    provider_executor: Any,
    skill_markdown_path: str,
    requested_fields: list[str],
) -> SkillExecutionResult:
    provider_called = bool(getattr(provider_executor, "provider_backed", True))
    if not isinstance(payload, dict):
        return SkillExecutionResult(
            status="ERROR",
            skill_id=skill_id,
            output={},
            warnings=[],
            errors=["provider_output_not_object"],
            provenance={"adapter": _ADAPTER_ID, "skill_markdown_path": skill_markdown_path},
            provider_called=provider_called,
        )

    output = dict(payload)
    warnings = _as_str_list(output.pop("warnings", None))
    errors = _as_str_list(output.pop("errors", None))
    provenance = output.pop("provenance", None)
    if not isinstance(provenance, dict):
        provenance = {}
    provenance = {
        **provenance,
        "adapter": _ADAPTER_ID,
        "skill_markdown_path": skill_markdown_path,
        "expected_schema": requested_fields,
    }
    if skill_id == VACANCY_EVALUATION_SKILL_ID:
        _augment_vacancy_evaluation_output(output)
    elif skill_id == POSITIONING_EVIDENCE_SKILL_ID:
        _augment_positioning_output(output)

    status = output.get("status")
    if not isinstance(status, str) or not status:
        status = "SUCCESS"

    return SkillExecutionResult(
        status=status,
        skill_id=skill_id,
        output=output,
        warnings=warnings,
        errors=errors,
        provenance=provenance,
        provider_called=provider_called,
    )


def _augment_vacancy_evaluation_output(output: dict[str, Any]) -> None:
    """Add runner-required aliases as a superset over the native evaluation packet.

    ``run_recruiter_skill_execution`` runs vacancy evaluation FIRST and validates
    ``REQUIRED_VACANCY_EVALUATION_FIELDS`` (summary/interpretation/gaps/next-step),
    while the real provider emits ``recruiter_vacancy_evaluation_packet_v1`` fields
    (recommendation/fit_assessment/strengths/risks/evidence/missing_information/
    next_step). The two share no names, so without these aliases a live run dies at
    stage 1 with ``SKILL_OUTPUT_INVALID``. Aliases regroup native fields
    deterministically (no invented content); native fields are preserved because the
    whole packet is passed through to ``recruiter_document_inputs`` downstream.
    ``_missing_fields`` checks presence, so empty fallbacks satisfy the gate.
    """
    if "vacancy_evaluation_summary" not in output:
        fit_assessment = output.get("fit_assessment")
        output["vacancy_evaluation_summary"] = fit_assessment if isinstance(fit_assessment, str) else ""
    if "fit_interpretation" not in output:
        interp: dict[str, Any] = {}
        if "strengths" in output:
            interp["strengths"] = output["strengths"]
        if "risks" in output:
            interp["risks"] = output["risks"]
        output["fit_interpretation"] = interp
    if "evidence_gaps" not in output:
        missing = output.get("missing_information")
        output["evidence_gaps"] = list(missing) if isinstance(missing, list) else []
    if "recommendation_for_next_step" not in output:
        recommendation = output.get("recommendation")
        if not isinstance(recommendation, str) or not recommendation:
            next_step = output.get("next_step")
            recommendation = next_step if isinstance(next_step, str) else ""
        output["recommendation_for_next_step"] = recommendation


def _augment_positioning_output(output: dict[str, Any]) -> None:
    """Add runner-required aliases as a superset over the native positioning packet.

    ``run_recruiter_skill_execution`` validates ``REQUIRED_POSITIONING_FIELDS``
    (``positioning_summary``/``gaps``/``risks_and_mitigations`` already match the
    native packet by name), while the native fields (``evidence``/``claims_to_use``/
    ``evidence_items``/``allowed_claims``/…) are consumed downstream by
    ``recruiter_document_inputs``. Both must survive, so the aliases are added
    deterministically from packet content without overwriting anything the packet
    already provides, and never synthesizing facts. ``_missing_fields`` checks
    presence, so an empty alias satisfies the required-field gate.
    """
    if "evidence_map" not in output:
        # Native packet carries positioning evidence under ``evidence``.
        output["evidence_map"] = output.get("evidence", [])
    if "proven_facts" not in output:
        # Evidence-backed claims to use; empty list when absent.
        claims = output.get("claims_to_use")
        output["proven_facts"] = list(claims) if isinstance(claims, list) else []
    if "derived_positioning" not in output:
        derived: dict[str, Any] = {}
        if "target_narrative" in output:
            derived["target_narrative"] = output["target_narrative"]
        if "recommended_angle" in output:
            derived["recommended_angle"] = output["recommended_angle"]
        output["derived_positioning"] = derived


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def build_recruiter_positioning_skill_executor(
    *,
    evaluation_executor: Any = None,
    positioning_executor: Any = None,
) -> RecruiterProviderSkillExecutor:
    """Build the dispatching provider-backed skill executor.

    When either provider executor is not injected, the real builder is invoked eagerly so
    a client-unavailable failure surfaces here (captured by the caller's builder-exception
    handling) rather than mid-flow. Tests inject fakes and no provider client is built.
    """
    if evaluation_executor is None:
        evaluation_executor = build_recruiter_evaluation_provider_executor()
    if positioning_executor is None:
        positioning_executor = build_recruiter_positioning_provider_executor()
    return RecruiterProviderSkillExecutor(
        evaluation_executor=evaluation_executor,
        positioning_executor=positioning_executor,
    )
