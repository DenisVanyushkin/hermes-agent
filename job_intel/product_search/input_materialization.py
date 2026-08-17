"""Fail-closed Gate B input authority materialization contracts."""
from __future__ import annotations

from enum import Enum
import hashlib
import ipaddress
import json
import re
from typing import Annotated, Literal
from urllib.parse import SplitResult, unquote_to_bytes, urlsplit, urlunsplit

import idna
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


class DiscoveryRootClass(str, Enum):
    OFFICIAL_COMPANY = "official_company"
    OFFICIAL_ATS = "official_ats"
    AGGREGATOR = "aggregator"


class SourceFamily(str, Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    SMARTRECRUITERS = "smartrecruiters"
    TEAMTAILOR = "teamtailor"
    RECRUITEE = "recruitee"
    PERSONIO = "personio"
    LINKEDIN = "linkedin"
    HEADHUNTER = "headhunter"
    REMOTEOK = "remoteok"
    DUCKDUCKGO = "duckduckgo"
    COMPANY_WEBSITE = "company_website"


class ExtractionRule(str, Enum):
    ANCHOR_REL_OFFICIAL = "anchor_rel_official"


_SOURCE_POLICY = {
    SourceFamily.GREENHOUSE: (DiscoveryRootClass.OFFICIAL_ATS, ("greenhouse.io",)),
    SourceFamily.LEVER: (DiscoveryRootClass.OFFICIAL_ATS, ("lever.co",)),
    SourceFamily.ASHBY: (DiscoveryRootClass.OFFICIAL_ATS, ("ashbyhq.com",)),
    SourceFamily.SMARTRECRUITERS: (DiscoveryRootClass.OFFICIAL_ATS, ("smartrecruiters.com",)),
    SourceFamily.TEAMTAILOR: (DiscoveryRootClass.OFFICIAL_ATS, ("teamtailor.com",)),
    SourceFamily.RECRUITEE: (DiscoveryRootClass.OFFICIAL_ATS, ("recruitee.com",)),
    SourceFamily.PERSONIO: (DiscoveryRootClass.OFFICIAL_ATS, ("personio.com",)),
    SourceFamily.LINKEDIN: (DiscoveryRootClass.AGGREGATOR, ("linkedin.com",)),
    SourceFamily.HEADHUNTER: (DiscoveryRootClass.AGGREGATOR, ("hh.ru", "hh.kz")),
    SourceFamily.REMOTEOK: (DiscoveryRootClass.AGGREGATOR, ("remoteok.com",)),
    SourceFamily.DUCKDUCKGO: (DiscoveryRootClass.AGGREGATOR, ("duckduckgo.com",)),
}


_ATS_AGGREGATOR_HOSTS = frozenset({
    "greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com",
    "teamtailor.com", "recruitee.com", "personio.com", "linkedin.com",
    "hh.kz", "hh.ru", "remoteok.com", "duckduckgo.com",
})
_SENSITIVE_QUERY_NAMES = frozenset({
    "token", "access_token", "api_key", "auth", "authorization", "credential",
    "jwt", "password", "secret", "session", "signature", "signed", "sig",
    "x_amz_credential", "x_amz_signature", "x_amz_security_token",
    "x_goog_credential", "x_goog_signature", "google_access_id",
    "aws_access_key_id", "awsaccesskeyid", "policy", "key_pair_id", "expires",
})
_TOKEN_VALUE = re.compile(
    r"(?:eyJ[A-Za-z0-9_-]{8,}\.|Bearer\s|(?:token|api[_-]?key|signature|secret|password)=|[A-Za-z0-9_-]{32,})",
    re.I,
)


def _canonical_uri(uri: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid HTTPS authority") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("URI must use HTTPS with a hostname")
    if parsed.username is not None or parsed.password is not None or "#" in uri:
        raise ValueError("URI must not contain credentials or fragment")
    for component in parsed.query.split("&") if parsed.query else ():
        if ";" in component:
            raise ValueError("alternate query separators are prohibited")
        name, _, value = component.partition("=")
        if re.search(r"%(?![0-9A-Fa-f]{2})", name + value):
            raise ValueError("malformed percent escape in query")
        try:
            decoded_name = unquote_to_bytes(name.replace("+", " ")).decode("utf-8", "strict")
            decoded_value = unquote_to_bytes(value.replace("+", " ")).decode("utf-8", "strict")
        except UnicodeError as exc:
            raise ValueError("query is not valid UTF-8") from exc
        normalized_name = re.sub(r"[-.]", "_", decoded_name.casefold())
        if normalized_name in _SENSITIVE_QUERY_NAMES or _TOKEN_VALUE.search(decoded_value):
            raise ValueError("credential-like query data is prohibited")
    if port not in (None, 443):
        raise ValueError("non-default HTTPS port is prohibited")
    if parsed.hostname.endswith(".."):
        raise ValueError("multiple trailing dots are prohibited")
    hostname = parsed.hostname[:-1] if parsed.hostname.endswith(".") else parsed.hostname
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("IP literal is prohibited")
    try:
        ascii_host = idna.encode(
            hostname, uts46=True, transitional=False, std3_rules=True
        ).decode("ascii").lower()
        if idna.encode(idna.decode(ascii_host), uts46=False).decode("ascii") != ascii_host:
            raise ValueError("IDNA A-label round trip failed")
    except (idna.IDNAError, UnicodeError) as exc:
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
    source_family: SourceFamily
    root_class: DiscoveryRootClass
    discovery_roots: tuple[str] = Field(min_length=1, max_length=1)
    max_requests: Literal[3] = 3
    max_redirects: Literal[2] = 2

    @field_validator("discovery_roots")
    @classmethod
    def validate_root(cls, value: tuple[str]) -> tuple[str]:
        _canonical_uri(value[0])
        return value

    @model_validator(mode="after")
    def validate_source_authority(self) -> "SourcePlan":
        _, host = _canonical_uri(self.discovery_roots[0])
        if self.source_family is SourceFamily.COMPANY_WEBSITE:
            expected_class = DiscoveryRootClass.OFFICIAL_COMPANY
        else:
            expected_class, service_domains = _SOURCE_POLICY[self.source_family]
            if not any(
                host == domain or host.endswith("." + domain)
                for domain in service_domains
            ):
                raise ValueError("source family does not match governed service domain")
        if self.root_class is not expected_class:
            raise ValueError("root class must be derived from source family")
        return self


class RequestReceipt(_ClosedModel):
    uri: str
    status: int = Field(ge=100, le=599)
    content_type: str = Field(min_length=1, max_length=200)
    content_bytes: int = Field(ge=0)
    content_sha256: str = Field(pattern=SHA256)
    capture_artifact_sha256: str = Field(pattern=SHA256)
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
        if self.capture_artifact_sha256 != self.content_sha256:
            raise ValueError("capture artifact must match response content hash")
        is_redirect = self.status in {301, 302, 303, 307, 308}
        if is_redirect != (self.redirect_to is not None):
            raise ValueError("redirect status and target must agree")
        return self


class OfficialLinkReceipt(_ClosedModel):
    uri: str
    relation: OfficialLinkRelation
    extraction_rule: ExtractionRule
    source_request_uri: str
    evidence_sha256: str = Field(pattern=SHA256)
    capture_artifact_sha256: str = Field(pattern=SHA256)
    extraction_fragment: str = Field(min_length=1, max_length=4096)
    extraction_sha256: str = Field(pattern=SHA256)
    byte_start: int = Field(ge=0)
    byte_end: int = Field(gt=0)

    @field_validator("uri", "source_request_uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        _canonical_uri(value)
        return value

    @model_validator(mode="after")
    def validate_extraction(self) -> "OfficialLinkReceipt":
        if hashlib.sha256(self.extraction_fragment.encode()).hexdigest() != self.extraction_sha256:
            raise ValueError("extraction fragment hash mismatch")
        if self.uri not in self.extraction_fragment:
            raise ValueError("official link does not occur in extraction fragment")
        if self.byte_end - self.byte_start != len(self.extraction_fragment.encode()):
            raise ValueError("extraction byte range does not match fragment")
        folded = self.extraction_fragment.casefold()
        markers = (
            "hermes-private://", "private resume", "candidate profile", "bearer ",
            "access_token", "api_key", "token=", "password=", "secret=",
        )
        if any(marker in folded for marker in markers):
            raise ValueError("extraction fragment contains prohibited marker")
        if self.extraction_rule is ExtractionRule.ANCHOR_REL_OFFICIAL:
            required = (
                re.search(r"<a\b", self.extraction_fragment, re.I),
                re.search(r"\bhref=[\"']" + re.escape(self.uri) + r"[\"']", self.extraction_fragment, re.I),
                re.search(r"\brel=[\"']official[\"']", self.extraction_fragment, re.I),
                re.search(r"\bdata-relation=[\"']" + re.escape(self.relation.value) + r"[\"']", self.extraction_fragment, re.I),
            )
            if not all(required):
                raise ValueError("fragment does not satisfy official anchor rule")
        return self


class DiscoveryReceipt(_ClosedModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    root_uri: str
    requests: tuple[RequestReceipt, ...] = Field(max_length=3)
    explicit_official_links: tuple[OfficialLinkReceipt, ...]
    identity_sha256: str = Field(pattern=SHA256)

    @model_validator(mode="before")
    @classmethod
    def populate_identity(cls, value: object) -> object:
        if isinstance(value, dict):
            payload = dict(value)
            unsigned = {key: item for key, item in payload.items() if key != "identity_sha256"}
            expected = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            if "identity_sha256" in payload and payload["identity_sha256"] != expected:
                raise ValueError("discovery receipt identity mismatch")
            payload["identity_sha256"] = expected
            return payload
        return value

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
        requests = {(item.uri, item.content_sha256): item for item in self.requests}
        for link in self.explicit_official_links:
            request = requests.get((link.source_request_uri, link.evidence_sha256))
            if request is None or link.capture_artifact_sha256 != request.capture_artifact_sha256:
                raise ValueError("extraction proof does not match capture artifact")
        return self


class AdmittedOfficialDomain(_ClosedModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    selection_key: str = Field(pattern=SHA256)
    company_label: str
    exact_root_uri: str
    root_class: DiscoveryRootClass
    source_request_uri: str
    relation: OfficialLinkRelation
    extraction_rule: ExtractionRule
    capture_artifact_sha256: str = Field(pattern=SHA256)
    extraction_sha256: str = Field(pattern=SHA256)
    byte_start: int = Field(ge=0)
    byte_end: int = Field(gt=0)
    domain: str
    canonical_uri: str
    evidence_display_uri: str
    evidence_sha256: str = Field(pattern=SHA256)

    @model_validator(mode="after")
    def validate_authority(self) -> "AdmittedOfficialDomain":
        canonical, domain = _canonical_uri(self.canonical_uri)
        evidence_canonical, evidence_domain = _canonical_uri(self.evidence_display_uri)
        _canonical_uri(self.exact_root_uri)
        _canonical_uri(self.source_request_uri)
        if canonical != self.canonical_uri or domain != self.domain:
            raise ValueError("canonical authority fields disagree")
        if evidence_domain != domain or evidence_canonical != canonical:
            raise ValueError("evidence URI does not match canonical authority")
        return self


class AdmittedIdentityOutcome(_ClosedModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    status: Literal["admitted"] = "admitted"
    source_plan: SourcePlan
    discovery_receipt: DiscoveryReceipt
    receipt_sha256: str = Field(pattern=SHA256)
    authority: AdmittedOfficialDomain

    @model_validator(mode="after")
    def validate_plan_binding(self) -> "AdmittedIdentityOutcome":
        if (
            self.authority.selection_key != self.source_plan.selection_key
            or self.authority.company_label != self.source_plan.company_label
            or self.authority.exact_root_uri != self.source_plan.discovery_roots[0]
            or self.authority.root_class is not self.source_plan.root_class
            or self.receipt_sha256 != self.discovery_receipt.identity_sha256
        ):
            raise ValueError("admitted authority does not match source plan")
        links = [
            link for link in self.discovery_receipt.explicit_official_links
            if link.source_request_uri == self.authority.source_request_uri
            and link.relation is self.authority.relation
            and link.extraction_rule is self.authority.extraction_rule
            and link.capture_artifact_sha256 == self.authority.capture_artifact_sha256
            and link.evidence_sha256 == self.authority.evidence_sha256
            and link.extraction_sha256 == self.authority.extraction_sha256
            and link.byte_start == self.authority.byte_start
            and link.byte_end == self.authority.byte_end
            and _canonical_uri(link.uri)[1] == self.authority.domain
        ]
        if len(links) != 1:
            raise ValueError("admitted authority evidence binding is invalid")
        return self


class UnresolvedIdentityOutcome(_ClosedModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    status: Literal["unresolved"] = "unresolved"
    reasons: tuple[MaterializationReason, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_reasons(self) -> "UnresolvedIdentityOutcome":
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("unresolved reasons must be unique")
        return self


DiscoveryOutcome = Annotated[
    AdmittedIdentityOutcome | UnresolvedIdentityOutcome, Field(discriminator="status")
]


def build_source_plan(*, selection_key: str, company_label: str, vacancy_uri: str, source_family: SourceFamily) -> SourcePlan:
    root_class = (
        DiscoveryRootClass.OFFICIAL_COMPANY
        if source_family is SourceFamily.COMPANY_WEBSITE
        else _SOURCE_POLICY[source_family][0]
    )
    return SourcePlan(
        selection_key=selection_key,
        company_label=company_label,
        source_family=source_family,
        root_class=root_class,
        discovery_roots=(vacancy_uri,),
    )


def _unresolved(reason: MaterializationReason) -> UnresolvedIdentityOutcome:
    return UnresolvedIdentityOutcome(reasons=(reason,))


def admit_official_domain(
    plan: SourcePlan, receipt: DiscoveryReceipt
) -> AdmittedIdentityOutcome | UnresolvedIdentityOutcome:
    if receipt.root_uri != plan.discovery_roots[0]:
        return _unresolved(MaterializationReason.UNRESOLVED_COMPANY_IDENTITY)
    captured = {(request.uri, request.content_sha256): request for request in receipt.requests}
    _, root_domain = _canonical_uri(receipt.root_uri)
    candidates: dict[str, OfficialLinkReceipt] = {}
    for link in receipt.explicit_official_links:
        request = captured.get((link.source_request_uri, link.evidence_sha256))
        if request is None:
            continue
        canonical_uri, domain = _canonical_uri(link.uri)
        is_service = any(domain == item or domain.endswith("." + item) for item in _ATS_AGGREGATOR_HOSTS)
        if is_service or (
            domain == root_domain and plan.root_class is not DiscoveryRootClass.OFFICIAL_COMPANY
        ):
            continue
        candidates.setdefault(domain, link)
    if not candidates:
        return _unresolved(MaterializationReason.UNRESOLVED_COMPANY_IDENTITY)
    if len(candidates) != 1:
        return _unresolved(MaterializationReason.AMBIGUOUS_COMPANY_IDENTITY)
    domain, link = next(iter(candidates.items()))
    canonical_uri, _ = _canonical_uri(link.uri)
    return AdmittedIdentityOutcome(
        source_plan=plan,
        discovery_receipt=receipt,
        receipt_sha256=receipt.identity_sha256,
        authority=AdmittedOfficialDomain(
            selection_key=plan.selection_key,
            company_label=plan.company_label,
            exact_root_uri=plan.discovery_roots[0],
            root_class=plan.root_class,
            source_request_uri=link.source_request_uri,
            relation=link.relation,
            extraction_rule=link.extraction_rule,
            capture_artifact_sha256=link.capture_artifact_sha256,
            extraction_sha256=link.extraction_sha256,
            byte_start=link.byte_start,
            byte_end=link.byte_end,
            domain=domain,
            canonical_uri=canonical_uri,
            evidence_display_uri=link.uri,
            evidence_sha256=link.evidence_sha256,
        )
    )
