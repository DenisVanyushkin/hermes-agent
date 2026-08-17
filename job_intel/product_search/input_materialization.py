"""Fail-closed Gate B input authority materialization contracts."""
from __future__ import annotations

from enum import Enum
import ipaddress
import re
from typing import Annotated, Literal
from urllib.parse import SplitResult, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SHA256 = r"^[0-9a-f]{64}$"


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MaterializationReason(str, Enum):
    UNRESOLVED_COMPANY_IDENTITY = "unresolved_company_identity"
    AMBIGUOUS_COMPANY_IDENTITY = "ambiguous_company_identity"
    COMPANY_EVIDENCE_UNAVAILABLE = "company_evidence_unavailable"
    VACANCY_AUTHORITY_UNAVAILABLE = "vacancy_authority_unavailable"
    ASSESSMENT_INPUT_UNAVAILABLE = "assessment_input_unavailable"
    SOURCE_FETCH_FAILED = "source_fetch_failed"
    SOURCE_NOT_ADMISSIBLE = "source_not_admissible"


class OfficialLinkRelation(str, Enum):
    OFFICIAL_COMPANY = "official_company"
    OFFICIAL_CAREERS = "official_careers"


def _canonical_uri(uri: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid HTTPS authority") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("URI must use HTTPS with a hostname")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("URI must not contain credentials or fragment")
    if port not in (None, 443):
        raise ValueError("non-default HTTPS port is prohibited")
    hostname = parsed.hostname.rstrip(".")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("IP literal is prohibited")
    try:
        ascii_host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("hostname IDNA encoding failed") from exc
    labels = ascii_host.split(".")
    if len(labels) < 2 or len(ascii_host) > 253 or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in labels
    ):
        raise ValueError("hostname syntax is invalid")
    canonical = urlunsplit(
        SplitResult("https", ascii_host, parsed.path or "/", parsed.query, "")
    )
    return canonical, ascii_host


class SourcePlan(_ClosedModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    selection_key: str = Field(pattern=SHA256)
    company_label: str = Field(min_length=1)
    discovery_roots: tuple[str] = Field(min_length=1, max_length=1)
    max_requests: Literal[3] = 3
    max_redirects: Literal[2] = 2

    @field_validator("discovery_roots")
    @classmethod
    def validate_root(cls, value: tuple[str]) -> tuple[str]:
        _canonical_uri(value[0])
        return value


class RequestReceipt(_ClosedModel):
    uri: str
    status: int = Field(ge=100, le=599)
    content_type: str = Field(min_length=1, max_length=200)
    content_bytes: int = Field(ge=0)
    content_sha256: str = Field(pattern=SHA256)
    redirect_to: str | None

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        _canonical_uri(value)
        return value

    @field_validator("redirect_to")
    @classmethod
    def validate_redirect(cls, value: str | None) -> str | None:
        if value is not None:
            _canonical_uri(value)
        return value

    @model_validator(mode="after")
    def validate_redirect_semantics(self) -> "RequestReceipt":
        is_redirect = self.status in {301, 302, 303, 307, 308}
        if is_redirect != (self.redirect_to is not None):
            raise ValueError("redirect status and target must agree")
        return self


class OfficialLinkReceipt(_ClosedModel):
    uri: str
    relation: OfficialLinkRelation
    source_request_uri: str
    evidence_sha256: str = Field(pattern=SHA256)

    @field_validator("uri", "source_request_uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        _canonical_uri(value)
        return value


class DiscoveryReceipt(_ClosedModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    root_uri: str
    requests: tuple[RequestReceipt, ...] = Field(max_length=3)
    explicit_official_links: tuple[OfficialLinkReceipt, ...]

    @field_validator("root_uri")
    @classmethod
    def validate_root(cls, value: str) -> str:
        _canonical_uri(value)
        return value

    @model_validator(mode="after")
    def validate_chain(self) -> "DiscoveryReceipt":
        if self.requests and self.requests[0].uri != self.root_uri:
            raise ValueError("first request must equal exact receipt root")
        redirects = 0
        for index, request in enumerate(self.requests):
            if request.redirect_to is not None:
                redirects += 1
                if index + 1 >= len(self.requests):
                    raise ValueError("terminal redirect is prohibited")
                if request.redirect_to != self.requests[index + 1].uri:
                    raise ValueError("redirect chain must be contiguous")
            elif index + 1 < len(self.requests):
                raise ValueError("non-redirect response cannot have a successor")
        if redirects > 2:
            raise ValueError("redirect cap exceeded")
        return self


class AdmittedOfficialDomain(_ClosedModel):
    company_label: str
    domain: str
    canonical_uri: str
    evidence_display_uri: str
    evidence_sha256: str = Field(pattern=SHA256)


class AdmittedIdentityOutcome(_ClosedModel):
    status: Literal["admitted"] = "admitted"
    authority: AdmittedOfficialDomain


class UnresolvedIdentityOutcome(_ClosedModel):
    status: Literal["unresolved"] = "unresolved"
    reasons: tuple[MaterializationReason, ...] = Field(min_length=1)


DiscoveryOutcome = Annotated[
    AdmittedIdentityOutcome | UnresolvedIdentityOutcome, Field(discriminator="status")
]


def build_source_plan(*, selection_key: str, company_label: str, vacancy_uri: str) -> SourcePlan:
    return SourcePlan(
        selection_key=selection_key,
        company_label=company_label,
        discovery_roots=(vacancy_uri,),
    )


def _unresolved(reason: MaterializationReason) -> UnresolvedIdentityOutcome:
    return UnresolvedIdentityOutcome(reasons=(reason,))


def admit_official_domain(
    plan: SourcePlan, receipt: DiscoveryReceipt
) -> AdmittedIdentityOutcome | UnresolvedIdentityOutcome:
    if receipt.root_uri != plan.discovery_roots[0]:
        return _unresolved(MaterializationReason.UNRESOLVED_COMPANY_IDENTITY)
    captured = {
        (request.uri, request.content_sha256) for request in receipt.requests
    }
    _, root_domain = _canonical_uri(receipt.root_uri)
    candidates: dict[str, OfficialLinkReceipt] = {}
    for link in receipt.explicit_official_links:
        if (link.source_request_uri, link.evidence_sha256) not in captured:
            continue
        canonical_uri, domain = _canonical_uri(link.uri)
        if domain == root_domain:
            continue
        candidates.setdefault(domain, link)
    if not candidates:
        return _unresolved(MaterializationReason.UNRESOLVED_COMPANY_IDENTITY)
    if len(candidates) != 1:
        return _unresolved(MaterializationReason.AMBIGUOUS_COMPANY_IDENTITY)
    domain, link = next(iter(candidates.items()))
    canonical_uri, _ = _canonical_uri(link.uri)
    return AdmittedIdentityOutcome(
        authority=AdmittedOfficialDomain(
            company_label=plan.company_label,
            domain=domain,
            canonical_uri=canonical_uri,
            evidence_display_uri=link.uri,
            evidence_sha256=link.evidence_sha256,
        )
    )
