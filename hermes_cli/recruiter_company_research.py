"""Company research claim schema and quality gate for the recruiter decision bundle.

Every company research claim must carry source, date/access timestamp,
confidence, and a fact-vs-inference classification. The quality gate blocks
single-source reputation conclusions and research that is too weak to support
a company assessment (see docs/hermes_recruiter_decision_support.md).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


COMPANY_RESEARCH_PACKET_SCHEMA = "recruiter_company_research_packet_v1"

ALLOWED_COMPANY_RESEARCH_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "company_website",
        "official_blog",
        "press_release",
        "funding_announcement",
        "annual_report",
        "regulatory_filing",
        "news",
        "employee_reviews",
        "public_interview",
        "podcast",
        "founder_material",
        "product_documentation",
        "developer_docs",
        "customer_case_study",
        "layoff_tracker",
        "hiring_signal",
    }
)

_ALLOWED_CONFIDENCE = frozenset({"low", "medium", "high"})
_ALLOWED_FACT_VS_INFERENCE = frozenset({"fact", "recent_public_signal", "inference", "unknown"})
_REPUTATION_CATEGORIES = frozenset({"reputation", "culture"})

BLOCKED_REASON_RESEARCH_UNAVAILABLE = "COMPANY_RESEARCH_UNAVAILABLE"
BLOCKED_REASON_RESEARCH_TOO_WEAK = "COMPANY_RESEARCH_TOO_WEAK"


class CompanyResearchQualityGateStatus(str, Enum):
    READY = "COMPANY_RESEARCH_QUALITY_GATE_READY"
    BLOCKED = "COMPANY_RESEARCH_QUALITY_GATE_BLOCKED"


@dataclass(slots=True)
class CompanyResearchClaim:
    claim: str
    category: str
    source: str
    source_type: str
    date_or_access_timestamp: str
    confidence: str
    fact_vs_inference: str
    stale: bool = False

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CompanyResearchClaim":
        return cls(
            claim=str(payload.get("claim") or ""),
            category=str(payload.get("category") or "general"),
            source=str(payload.get("source") or ""),
            source_type=str(payload.get("source_type") or ""),
            date_or_access_timestamp=str(payload.get("date_or_access_timestamp") or ""),
            confidence=str(payload.get("confidence") or ""),
            fact_vs_inference=str(payload.get("fact_vs_inference") or ""),
            stale=bool(payload.get("stale")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_company_research_claim(payload: dict[str, Any]) -> list[str]:
    """Return report-safe validation errors for a single research claim."""
    claim = CompanyResearchClaim.from_dict(payload)
    errors: list[str] = []
    if not claim.claim:
        errors.append("claim text is required")
    if not claim.source:
        errors.append("source is required for every company research claim")
    if not claim.date_or_access_timestamp:
        errors.append("date_or_access_timestamp is required for every company research claim")
    if claim.confidence not in _ALLOWED_CONFIDENCE:
        errors.append("confidence must be one of low/medium/high")
    if claim.fact_vs_inference not in _ALLOWED_FACT_VS_INFERENCE:
        errors.append("fact_vs_inference must be one of fact/recent_public_signal/inference/unknown")
    if claim.source_type not in ALLOWED_COMPANY_RESEARCH_SOURCE_TYPES:
        errors.append(f"source_type is not an approved company research source type: {claim.source_type or '<empty>'}")
    return errors


@dataclass(slots=True)
class CompanyResearchQualityGateReport:
    status: CompanyResearchQualityGateStatus
    ready: bool
    blocked_reason: str | None
    schema: str = COMPANY_RESEARCH_PACKET_SCHEMA
    claim_count: int = 0
    source_type_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def run_company_research_quality_gate(
    claims: list[dict[str, Any]],
) -> CompanyResearchQualityGateReport:
    if not claims:
        return CompanyResearchQualityGateReport(
            status=CompanyResearchQualityGateStatus.BLOCKED,
            ready=False,
            blocked_reason=BLOCKED_REASON_RESEARCH_UNAVAILABLE,
            errors=["no company research claims provided"],
        )

    parsed = [CompanyResearchClaim.from_dict(payload) for payload in claims]
    errors: list[str] = []
    for index, payload in enumerate(claims):
        for error in validate_company_research_claim(payload):
            errors.append(f"claim[{index}]: {error}")

    warnings: list[str] = []

    # Reputation/culture conclusions must never rest on a single anecdote.
    reputation_sources = {claim.source for claim in parsed if claim.category in _REPUTATION_CATEGORIES}
    if len(reputation_sources) == 1 and len(parsed) == len(
        [claim for claim in parsed if claim.category in _REPUTATION_CATEGORIES]
    ):
        errors.append(
            "reputation conclusion is based on a single source; corroborate with another source category"
        )
    elif len(reputation_sources) == 1:
        warnings.append(
            "reputation signals come from a single source; treat as single anecdote, not a repeated pattern"
        )

    stale_count = sum(1 for claim in parsed if claim.stale)
    if stale_count == len(parsed):
        errors.append("all research claims are stale; refresh before relying on the assessment")
    elif stale_count:
        warnings.append(f"{stale_count} stale claim(s) present; marked as stale in the report")

    if errors:
        return CompanyResearchQualityGateReport(
            status=CompanyResearchQualityGateStatus.BLOCKED,
            ready=False,
            blocked_reason=BLOCKED_REASON_RESEARCH_TOO_WEAK,
            claim_count=len(parsed),
            source_type_count=len({claim.source_type for claim in parsed}),
            warnings=warnings,
            errors=errors,
        )

    source_types = {claim.source_type for claim in parsed}
    if len(source_types) == 1:
        warnings.append("company research uses a single source category; add more categories when available")

    return CompanyResearchQualityGateReport(
        status=CompanyResearchQualityGateStatus.READY,
        ready=True,
        blocked_reason=None,
        claim_count=len(parsed),
        source_type_count=len(source_types),
        warnings=warnings,
    )
