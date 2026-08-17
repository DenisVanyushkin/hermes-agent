"""Fail-closed Gate B input authority materialization."""
from __future__ import annotations

from enum import Enum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MaterializationReason(str, Enum):
    UNRESOLVED_COMPANY_IDENTITY = "unresolved_company_identity"
    COMPANY_EVIDENCE_UNAVAILABLE = "company_evidence_unavailable"
    VACANCY_AUTHORITY_UNAVAILABLE = "vacancy_authority_unavailable"
    ASSESSMENT_INPUT_UNAVAILABLE = "assessment_input_unavailable"
    SOURCE_FETCH_FAILED = "source_fetch_failed"
    SOURCE_NOT_ADMISSIBLE = "source_not_admissible"


class SourcePlan(_ClosedModel):
    schema_version: str = "1.0.0"
    selection_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    company_label: str = Field(min_length=1)
    discovery_roots: tuple[str, ...]
    max_requests: int = 3
    max_redirects: int = 2


class RequestReceipt(_ClosedModel):
    uri: str
    status: int
    content_type: str
    content_bytes: int
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    redirect_to: str | None


class OfficialLinkReceipt(_ClosedModel):
    uri: str
    relation: str
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DiscoveryReceipt(_ClosedModel):
    schema_version: str = "1.0.0"
    root_uri: str
    requests: tuple[RequestReceipt, ...]
    explicit_official_links: tuple[OfficialLinkReceipt, ...]


class AdmittedOfficialDomain(_ClosedModel):
    company_label: str
    domain: str
    source_uri: str
    evidence_sha256: str


def _admissible_https(uri: str) -> str:
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("source URI must be credential-free HTTPS without fragment")
    return parsed.hostname.lower()


def build_source_plan(*, selection_key: str, company_label: str, vacancy_uri: str) -> SourcePlan:
    _admissible_https(vacancy_uri)
    return SourcePlan(
        selection_key=selection_key,
        company_label=company_label,
        discovery_roots=(vacancy_uri,),
    )


def admit_official_domain(
    company_label: str, receipt: DiscoveryReceipt
) -> AdmittedOfficialDomain | None:
    if len(receipt.requests) > 3:
        raise ValueError("discovery request cap exceeded")
    for link in receipt.explicit_official_links:
        if link.relation not in {"official_company", "official_careers"}:
            continue
        return AdmittedOfficialDomain(
            company_label=company_label,
            domain=_admissible_https(link.uri),
            source_uri=link.uri,
            evidence_sha256=link.evidence_sha256,
        )
    return None
