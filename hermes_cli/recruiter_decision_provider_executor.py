"""Provider-backed executor for decision-support analysis modules.

Mirrors RecruiterPositioningProviderExecutor: one auxiliary-client call per
module, strict JSON output, no outbound side effects. The flow validates the
returned payload against the module contract; this executor only guarantees
well-formed JSON plus provenance metadata.
"""

from __future__ import annotations

import json
from typing import Any

from .recruiter_decision_flow import DecisionModuleExecution
from .recruiter_decision_modules import DECISION_PACKET_SCHEMA


_MODULE_CONTRACTS: dict[str, str] = {
    "vacancy_assessment": (
        "Fields: company, role, location_and_relocation, seniority, function, domain, "
        "key_responsibilities (list), must_have_requirements (list), nice_to_have_requirements (list), "
        "risks (list), upside (list), fit (one of strong_fit/consider/near_miss/reject), summary. "
        "Assess direct fit, adjacent fit, missing evidence, overclaiming risk, location feasibility, "
        "seniority/domain/scope match. Never invent compensation, reporting line, or hiring urgency."
    ),
    "company_assessment": (
        "Fields: recommendation (worth_engaging/needs_diligence/avoid), confidence (low/medium/high), "
        "summary, dimensions (object with business_quality, financial_funding_health, strategic_momentum, "
        "reputation_culture, product_technical_credibility, role_attractiveness, compensation_upside, "
        "personal_fit), sources (list of source URLs/labels used), fact_vs_inference (object with facts, "
        "inferences, unknowns lists). Base everything on the provided research claims; mark unknowns."
    ),
    "company_risk_register": (
        "Fields: risks — a list where each item has risk, severity (low/medium/high), confidence "
        "(low/medium/high), evidence, mitigation. Categories: business, funding, reputation, culture, "
        "role-scope, manager/org, relocation, regulatory, compensation, career-narrative."
    ),
    "recommendation": (
        "Fields: decision (strong_apply/apply/consider/manual_review_required/do_not_apply/reject), "
        "confidence, verdict (one sentence), reasons_for (list), reasons_against (list), "
        "role_fit_rationale, company_quality_rationale, risk_adjusted_upside, critical_blockers (list), "
        "next_action, what_would_change. Answer: should Denis spend time on this, and what is the next "
        "best action. If inputs are incomplete, use decision manual_review_required or confidence low "
        "and list missing information."
    ),
    "positioning_summary": (
        "Fields: target_role_framing, positioning_angle, strongest_supported_overlap, "
        "adjacent_experience, caveats (list), recommended_narrative. Separate direct, adjacent, and "
        "unsupported evidence; never convert adjacent evidence into direct claims."
    ),
    "evidence_backed_supporting_claims": (
        "Fields: claims — a list where each item has claim, support_level "
        "(explicit/derived_safe/adjacent/weak/unsupported), source_reference, why_it_matters, "
        "safe_wording, where_to_use (CV summary/CV role bullet/recruiter conversation/cover letter "
        "outline/interview talking point). Only explicit and derived_safe claims may be recommended "
        "for outward-facing use."
    ),
    "claims_to_avoid": (
        "Fields: claims — a list where each item has claim and reason. Include unsupported claims, "
        "too-strong phrasings, metrics not present in the career facts, location/relocation "
        "assumptions, and domain-depth assumptions."
    ),
    "questions_to_ask": (
        "Fields: recruiter_screen (list), hiring_manager (list), product_leadership (list), "
        "team_interviews (list), compensation_relocation (list). Questions must trace back to "
        "identified risks and unknowns."
    ),
}

_COMMON_RULES = (
    "You are Hermes Recruiter producing one decision-support analysis module.\n"
    "Return only one JSON object.\n"
    "Rules: do not invent facts; use only the supplied inputs; mark unknowns as unknown; "
    "no outbound messages, no CRM or job-intel writes, no application submission.\n"
    "The output is a draft for manual review by Denis.\n"
    "Also include top-level fields: confidence (low/medium/high) and sources "
    "(list of source URLs or labels actually used).\n"
    "Do not mention internal pipeline machinery, packets, gates, providers, or provenance "
    "in any user-facing text.\n"
)


class RecruiterDecisionProviderExecutor:
    provider_backed = True

    def __init__(self, *, client: Any, model: str, provider: str | None) -> None:
        self._client = client
        self._model = model
        self._provider = provider or "auto"

    def execute(
        self,
        *,
        module_id: str,
        skill_id: str,
        module_input: dict[str, Any],
    ) -> DecisionModuleExecution:
        prompt = _build_prompt(module_id=module_id, module_input=module_input)
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": prompt}],
            temperature=0,
            timeout=120,
            extra_body=_extra_body() | _json_response_format(module_id),
        )
        raw = _response_text(response)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return DecisionModuleExecution(
                payload={},
                errors=["decision_provider_output_invalid_json"],
            )
        if not isinstance(payload, dict):
            return DecisionModuleExecution(
                payload={},
                errors=["decision_provider_output_not_object"],
            )
        # Copy, don't pop: downstream output validation checks payload fields too.
        confidence = str(payload.get("confidence", "medium") or "medium")
        sources_raw = payload.get("sources")
        sources = [str(item) for item in sources_raw] if isinstance(sources_raw, list) else []
        return DecisionModuleExecution(
            payload=payload,
            confidence=confidence,
            sources=sources,
        )


def build_recruiter_decision_provider_executor(
    *,
    provider: str | None = None,
    model: str | None = None,
) -> RecruiterDecisionProviderExecutor:
    from agent.auxiliary_client import get_text_auxiliary_client, resolve_provider_client

    if provider or model:
        client, resolved_model = resolve_provider_client(provider or "auto", model=model)
    else:
        client, resolved_model = get_text_auxiliary_client("recruiter_decision_support")
    if client is None or not resolved_model:
        raise RuntimeError("decision_provider_client_unavailable")
    return RecruiterDecisionProviderExecutor(client=client, model=resolved_model, provider=provider)


def _build_prompt(*, module_id: str, module_input: dict[str, Any]) -> str:
    contract = _MODULE_CONTRACTS.get(module_id, "Produce a specific, source-grounded analysis object.")
    return (
        f"{_COMMON_RULES}"
        f"Module: {module_id}\n"
        f"Module contract: {contract}\n"
        f"Module input JSON:\n{json.dumps(module_input, ensure_ascii=True, sort_keys=True, default=str)}"
    )


def _json_response_format(module_id: str) -> dict[str, Any]:
    return {
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": f"{DECISION_PACKET_SCHEMA}_{module_id}",
                "schema": {"type": "object", "additionalProperties": True},
                "strict": False,
            },
        }
    }


def _extra_body() -> dict[str, Any]:
    from agent.auxiliary_client import get_auxiliary_extra_body

    return get_auxiliary_extra_body() or {}


def _response_text(response: Any) -> str:
    try:
        return str(response.choices[0].message.content or "").strip()
    except Exception as exc:  # pragma: no cover
        raise ValueError("decision_provider_output_missing_content") from exc
