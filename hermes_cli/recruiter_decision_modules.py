"""Modular analysis registry for the recruiter Company & Vacancy Decision Support Bundle.

Implements the modular output contract from
docs/hermes_recruiter_decision_support.md: nine independently requestable
analysis modules, requested-output parsing with presets, per-module statuses,
and an overall status reducer that only considers requested modules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import re
from typing import Any, Iterable, Mapping


DECISION_BUNDLE_ID = "company-vacancy-decision-support"
DECISION_PACKET_SCHEMA = "recruiter_decision_support_packet_v1"

# Input kinds. A required input written as "a|b" means any one of the
# alternatives satisfies the requirement.
INPUT_VACANCY_SOURCE = "vacancy_source"
INPUT_CAREER_FACT_SOURCE = "career_fact_source"
INPUT_COMPANY_IDENTITY = "company_identity"
INPUT_PUBLIC_RESEARCH = "public_research"
INPUT_ROLE_CONTEXT = "role_context"

BLOCK_REASON_MISSING_REQUIRED_INPUT = "MISSING_REQUIRED_INPUT"

# Report-safe diagnostics from the SoT contract.
DIAG_VACANCY_SOURCE_UNAVAILABLE = "VACANCY_SOURCE_UNAVAILABLE"
DIAG_CAREER_FACT_SOURCE_UNAVAILABLE = "CAREER_FACT_SOURCE_UNAVAILABLE"
DIAG_COMPANY_RESEARCH_UNAVAILABLE = "COMPANY_RESEARCH_UNAVAILABLE"
DIAG_COMPANY_RESEARCH_TOO_WEAK = "COMPANY_RESEARCH_TOO_WEAK"
DIAG_PRIVACY_GATE_BLOCKED = "PRIVACY_GATE_BLOCKED"
DIAG_POSITIONING_NOT_READY = "POSITIONING_NOT_READY"
DIAG_ROLE_FIT_NOT_RECOMMENDED = "ROLE_FIT_NOT_RECOMMENDED"
DIAG_COMPANY_NOT_RECOMMENDED = "COMPANY_NOT_RECOMMENDED"
DIAG_REQUIRED_OUTPUT_TOO_GENERIC = "REQUIRED_OUTPUT_TOO_GENERIC"
DIAG_REQUIRED_OUTPUT_INTERNAL_LANGUAGE_FORBIDDEN = "REQUIRED_OUTPUT_INTERNAL_LANGUAGE_FORBIDDEN"
DIAG_REQUIRED_OUTPUT_UNSUPPORTED_CLAIMS = "REQUIRED_OUTPUT_UNSUPPORTED_CLAIMS"
DIAG_REQUIRED_OUTPUT_MISSING_SOURCE_REFERENCES = "REQUIRED_OUTPUT_MISSING_SOURCE_REFERENCES"
DIAG_REPORT_SAFE_DIAGNOSTICS_UNAVAILABLE = "REPORT_SAFE_DIAGNOSTICS_UNAVAILABLE"


class DecisionModuleStatus(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    INCONCLUSIVE = "INCONCLUSIVE"
    SKIPPED_NOT_REQUESTED = "SKIPPED_NOT_REQUESTED"


class DecisionBundleStatus(str, Enum):
    READY = "COMPANY_VACANCY_DECISION_BUNDLE_READY"
    BLOCKED = "COMPANY_VACANCY_DECISION_BUNDLE_BLOCKED"
    INCONCLUSIVE = "COMPANY_VACANCY_DECISION_BUNDLE_INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class DecisionModuleSpec:
    module_id: str
    skill_id: str
    # Each entry is either a plain input kind or an "a|b" alternative group.
    required_inputs: tuple[str, ...] = ()
    upstream_modules: tuple[str, ...] = ()
    degraded_allowed: bool = False
    uses_candidate_facts: bool = False
    uses_company_research: bool = False
    user_facing: bool = True


DECISION_MODULE_REGISTRY: dict[str, DecisionModuleSpec] = {
    spec.module_id: spec
    for spec in (
        DecisionModuleSpec(
            module_id="vacancy_assessment",
            skill_id="vacancy-evaluation",
            required_inputs=(INPUT_VACANCY_SOURCE, INPUT_CAREER_FACT_SOURCE),
            uses_candidate_facts=True,
        ),
        DecisionModuleSpec(
            module_id="company_assessment",
            skill_id="company-assessment",
            required_inputs=(
                f"{INPUT_COMPANY_IDENTITY}|{INPUT_VACANCY_SOURCE}",
                INPUT_PUBLIC_RESEARCH,
            ),
            uses_company_research=True,
        ),
        DecisionModuleSpec(
            module_id="company_risk_register",
            skill_id="company-risk-register",
            required_inputs=(
                f"{INPUT_COMPANY_IDENTITY}|{INPUT_VACANCY_SOURCE}",
                INPUT_PUBLIC_RESEARCH,
            ),
            uses_company_research=True,
        ),
        DecisionModuleSpec(
            module_id="recommendation",
            skill_id="fit-recommendation",
            upstream_modules=("vacancy_assessment", "company_assessment"),
            degraded_allowed=True,
            uses_candidate_facts=True,
        ),
        DecisionModuleSpec(
            module_id="positioning_summary",
            skill_id="positioning-and-evidence",
            required_inputs=(INPUT_VACANCY_SOURCE, INPUT_CAREER_FACT_SOURCE),
            uses_candidate_facts=True,
        ),
        DecisionModuleSpec(
            module_id="evidence_backed_supporting_claims",
            skill_id="positioning-and-evidence",
            required_inputs=(
                INPUT_CAREER_FACT_SOURCE,
                f"{INPUT_VACANCY_SOURCE}|{INPUT_ROLE_CONTEXT}",
            ),
            uses_candidate_facts=True,
        ),
        DecisionModuleSpec(
            module_id="claims_to_avoid",
            skill_id="positioning-and-evidence",
            required_inputs=(
                INPUT_CAREER_FACT_SOURCE,
                f"{INPUT_VACANCY_SOURCE}|{INPUT_ROLE_CONTEXT}",
            ),
            uses_candidate_facts=True,
        ),
        DecisionModuleSpec(
            module_id="questions_to_ask",
            skill_id="questions-to-ask",
            required_inputs=(f"{INPUT_VACANCY_SOURCE}|{INPUT_COMPANY_IDENTITY}",),
            upstream_modules=("vacancy_assessment", "company_assessment", "company_risk_register"),
            degraded_allowed=True,
        ),
        DecisionModuleSpec(
            module_id="manual_review_warnings",
            skill_id="manual-review-warnings",
            degraded_allowed=True,
        ),
    )
}

DECISION_MODULE_IDS: tuple[str, ...] = tuple(DECISION_MODULE_REGISTRY)

FULL_BUNDLE_PRESET_ID = "full_bundle"

DECISION_PRESETS: dict[str, tuple[str, ...]] = {
    FULL_BUNDLE_PRESET_ID: DECISION_MODULE_IDS,
    "quick_vacancy_screen": (
        "vacancy_assessment",
        "company_assessment",
        "recommendation",
        "manual_review_warnings",
    ),
    "company_diligence": ("company_assessment", "company_risk_register", "manual_review_warnings"),
    "role_fit_only": ("vacancy_assessment", "manual_review_warnings"),
    "positioning_only": (
        "positioning_summary",
        "evidence_backed_supporting_claims",
        "claims_to_avoid",
        "manual_review_warnings",
    ),
    "interview_prep": ("questions_to_ask", "manual_review_warnings"),
    "claims_review": ("claims_to_avoid", "manual_review_warnings"),
}


@dataclass(slots=True)
class DecisionModuleResult:
    module_id: str
    status: DecisionModuleStatus
    block_reason: str | None = None
    confidence: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    fact_vs_inference_notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    manual_review_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def missing_inputs_for_module(module_id: str, *, available_inputs: set[str]) -> list[str]:
    """Return unsatisfied required-input groups for a module.

    An "a|b" group is satisfied when any alternative is available.
    """
    spec = DECISION_MODULE_REGISTRY[module_id]
    missing: list[str] = []
    for group in spec.required_inputs:
        alternatives = group.split("|")
        if not any(alternative in available_inputs for alternative in alternatives):
            missing.extend(alternatives if len(alternatives) == 1 else [group])
    return missing


def reduce_overall_status(
    *,
    requested: Iterable[str],
    results: Mapping[str, DecisionModuleResult],
) -> DecisionBundleStatus:
    """Derive the overall bundle status from requested modules only."""
    saw_inconclusive = False
    for module_id in requested:
        result = results.get(module_id)
        if result is None or result.status is DecisionModuleStatus.BLOCKED:
            return DecisionBundleStatus.BLOCKED
        if result.status is DecisionModuleStatus.INCONCLUSIVE:
            saw_inconclusive = True
        elif result.status is DecisionModuleStatus.SKIPPED_NOT_REQUESTED:
            # A requested module must not be skipped; treat as blocked.
            return DecisionBundleStatus.BLOCKED
    return DecisionBundleStatus.INCONCLUSIVE if saw_inconclusive else DecisionBundleStatus.READY


@dataclass(slots=True)
class RequestedOutputs:
    requested: list[str]
    preset_id: str | None = None
    warnings: list[str] = field(default_factory=list)
    matched_signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Prompt-intent patterns (EN + RU transliteration-free). Order matters: more
# specific intents are checked before the generic full-bundle fallback.
_PRESET_PROMPT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "interview_prep",
        (
            r"\bquestions?\s+to\s+ask\b",
            r"\brecruiter\s+screen\b",
            r"\binterview\s+prep\b",
            r"вопрос\w*\s+(к\s+)?(рекрутеру|интервью|скрин)",
            r"подготовь\s+вопрос",
        ),
    ),
    (
        "claims_review",
        (
            r"\bclaims?\s+(should\s+i|to)\s+avoid\b",
            r"\bavoid\s+overclaim",
            r"каки[хе]\s+заявлени\w+\s+избегать",
            r"что\s+не\s+стоит\s+заявлять",
        ),
    ),
    (
        "positioning_only",
        (
            r"\bpositioning\b",
            r"позиционировани",
        ),
    ),
    (
        "company_diligence",
        (
            r"\bcompany\s+(diligence|research|assessment)\b",
            r"\btell\s+me\s+about\s+the\s+company\b",
            r"\bis\s+the\s+company\s+worth\b",
            r"\bworth\s+engaging\b",
            r"расскажи\s+про\s+компани",
            r"стоит\s+ли\s+(с\s+ней\s+)?связываться",
            r"оцени\s+компани",
            r"дью[\s-]?дилидженс",
        ),
    ),
    (
        "quick_vacancy_screen",
        (
            r"\bquick\s+(vacancy\s+)?screen\b",
            r"\bquick\s+look\b",
            r"быстр\w+\s+(скрин|оценк)",
        ),
    ),
    (
        "role_fit_only",
        (
            r"\brole\s+fit\s+only\b",
            r"только\s+фит",
        ),
    ),
)

_FULL_BUNDLE_PATTERNS: tuple[str, ...] = (
    r"\bshould\s+i\s+apply\b",
    r"\bworth\s+applying\b",
    r"\bfull\s+(bundle|analysis)\b",
    r"стоит\s+ли\s+(мне\s+)?подава?ться",
    r"стоит\s+ли\s+податься",
    r"полн\w+\s+(разбор|анализ)",
)


def parse_requested_outputs(
    prompt: str,
    *,
    context: dict[str, Any] | None = None,
) -> RequestedOutputs:
    """Resolve the requested analysis modules for a recruiter decision run.

    Explicit ``context["requested_outputs"]`` wins over prompt heuristics.
    Unknown module names are dropped report-safely with a warning.
    ``manual_review_warnings`` is always included per the SoT contract.
    """
    explicit = (context or {}).get("requested_outputs")
    if explicit is not None:
        if not isinstance(explicit, (list, tuple)) or not explicit:
            raise ValueError("requested_outputs must be a non-empty list of module ids")
        requested: list[str] = []
        warnings: list[str] = []
        for name in explicit:
            normalized = str(name).strip()
            if normalized in DECISION_MODULE_REGISTRY:
                if normalized not in requested:
                    requested.append(normalized)
            else:
                warnings.append(f"unknown requested output ignored: {normalized}")
        if not requested:
            raise ValueError("requested_outputs contained no known module ids")
        if "manual_review_warnings" not in requested:
            requested.append("manual_review_warnings")
        return RequestedOutputs(requested=requested, preset_id=None, warnings=warnings)

    lowered = " ".join(prompt.split()).casefold()

    for preset_id, patterns in _PRESET_PROMPT_PATTERNS:
        matched = [pattern for pattern in patterns if re.search(pattern, lowered, re.IGNORECASE)]
        if matched:
            return RequestedOutputs(
                requested=list(DECISION_PRESETS[preset_id]),
                preset_id=preset_id,
                matched_signals=matched,
            )

    full_matches = [pattern for pattern in _FULL_BUNDLE_PATTERNS if re.search(pattern, lowered, re.IGNORECASE)]
    if full_matches:
        return RequestedOutputs(
            requested=list(DECISION_PRESETS[FULL_BUNDLE_PRESET_ID]),
            preset_id=FULL_BUNDLE_PRESET_ID,
            matched_signals=full_matches,
        )

    # Default: cheap targeted screen rather than the heaviest workflow.
    return RequestedOutputs(
        requested=list(DECISION_PRESETS["quick_vacancy_screen"]),
        preset_id="quick_vacancy_screen",
        warnings=["no explicit analysis request detected; defaulted to quick_vacancy_screen"],
    )
