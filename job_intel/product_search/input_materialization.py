"""Fail-closed Gate B input authority materialization contracts.

This module is deliberately offline. Durable discovery authority is accepted
only through pinned Gate A corpus rows and content-addressed capture artifacts
that are re-read without following symlinks. It performs no network fetches.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from html import unescape
from html.parser import HTMLParser
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from typing import Annotated, Any, Literal, Self
from types import MappingProxyType
import unicodedata
from urllib.parse import (
    SplitResult,
    parse_qsl,
    unquote_to_bytes,
    urlencode,
    urlsplit,
    urlunsplit,
)

import idna
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
from pydantic import model_validator

SHA256 = r"^[0-9a-f]{64}$"
GATE_A_RUN_ID = "gate-a-20260816T141344Z"
PINNED_GATE_A_COMMIT = "65d60daae16093a9a7e34a11a159e2f789dd14dd"
PINNED_GATE_A_MANIFEST_SHA256 = (
    "6ecc500c291061a34c4482edb5c2a0d6c547993bea0d346ad306041dfa81df3d"
)
PINNED_GATE_B_CORPUS_SHA256 = (
    "b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69"
)
PINNED_GATE_B_CORPUS_RECORD_COUNT = 48
CANONICAL_GATE_A_ROOT = Path(
    "/home/hermes/.hermes/job_intel/experiments/gate-a/"
    "65d60daae16093a9a7e34a11a159e2f789dd14dd"
)
CANONICAL_GATE_B_CORPUS_ROOT = Path(
    "/home/hermes/.hermes/job_intel/experiments/gate-b/"
    "b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69"
)
SOURCE_AUTHORITY_POLICY_VERSION = "1.0.0"
_MAX_ARTIFACT_BYTES = 1_000_000


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
    UNVERIFIED_PUBLIC_RESULT = "unverified_public_result"


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
    REMOTIVE = "remotive"
    DUCKDUCKGO = "duckduckgo"
    COMPANY_WEBSITE = "company_website"


class ExtractionRule(str, Enum):
    HTML_ANCHOR_TEXT_V1 = "html_anchor_text_v1"


class _RootAuthorityMode(str, Enum):
    OFFICIAL_COMPANY = "official_company"
    EXTERNAL_OFFICIAL_LINK_ONLY = "external_official_link_only"


@dataclass(frozen=True, slots=True)
class _SourceFamilyAuthority:
    root_class: DiscoveryRootClass
    root_authority_mode: _RootAuthorityMode


_SOURCE_FAMILY_AUTHORITY_V1 = MappingProxyType({
    SourceFamily.GREENHOUSE: _SourceFamilyAuthority(
        DiscoveryRootClass.OFFICIAL_ATS,
        _RootAuthorityMode.EXTERNAL_OFFICIAL_LINK_ONLY,
    ),
    SourceFamily.LEVER: _SourceFamilyAuthority(
        DiscoveryRootClass.OFFICIAL_ATS,
        _RootAuthorityMode.EXTERNAL_OFFICIAL_LINK_ONLY,
    ),
    SourceFamily.ASHBY: _SourceFamilyAuthority(
        DiscoveryRootClass.OFFICIAL_ATS,
        _RootAuthorityMode.EXTERNAL_OFFICIAL_LINK_ONLY,
    ),
    SourceFamily.SMARTRECRUITERS: _SourceFamilyAuthority(
        DiscoveryRootClass.OFFICIAL_ATS,
        _RootAuthorityMode.EXTERNAL_OFFICIAL_LINK_ONLY,
    ),
    SourceFamily.TEAMTAILOR: _SourceFamilyAuthority(
        DiscoveryRootClass.OFFICIAL_ATS,
        _RootAuthorityMode.EXTERNAL_OFFICIAL_LINK_ONLY,
    ),
    SourceFamily.RECRUITEE: _SourceFamilyAuthority(
        DiscoveryRootClass.OFFICIAL_ATS,
        _RootAuthorityMode.EXTERNAL_OFFICIAL_LINK_ONLY,
    ),
    SourceFamily.PERSONIO: _SourceFamilyAuthority(
        DiscoveryRootClass.OFFICIAL_ATS,
        _RootAuthorityMode.EXTERNAL_OFFICIAL_LINK_ONLY,
    ),
    SourceFamily.LINKEDIN: _SourceFamilyAuthority(
        DiscoveryRootClass.AGGREGATOR,
        _RootAuthorityMode.EXTERNAL_OFFICIAL_LINK_ONLY,
    ),
    SourceFamily.HEADHUNTER: _SourceFamilyAuthority(
        DiscoveryRootClass.AGGREGATOR,
        _RootAuthorityMode.EXTERNAL_OFFICIAL_LINK_ONLY,
    ),
    SourceFamily.REMOTEOK: _SourceFamilyAuthority(
        DiscoveryRootClass.AGGREGATOR,
        _RootAuthorityMode.EXTERNAL_OFFICIAL_LINK_ONLY,
    ),
    SourceFamily.REMOTIVE: _SourceFamilyAuthority(
        DiscoveryRootClass.AGGREGATOR,
        _RootAuthorityMode.EXTERNAL_OFFICIAL_LINK_ONLY,
    ),
    SourceFamily.DUCKDUCKGO: _SourceFamilyAuthority(
        DiscoveryRootClass.UNVERIFIED_PUBLIC_RESULT,
        _RootAuthorityMode.EXTERNAL_OFFICIAL_LINK_ONLY,
    ),
    SourceFamily.COMPANY_WEBSITE: _SourceFamilyAuthority(
        DiscoveryRootClass.OFFICIAL_COMPANY,
        _RootAuthorityMode.OFFICIAL_COMPANY,
    ),
})
_PUBLIC_QUERY_NAMES = frozenset({
    "q",
    "query",
    "page",
    "lang",
    "locale",
    "location",
    "ref",
    "source",
    "jid",
    "job_id",
    "jobid",
    "id",
    "gh_jid",
    "hhtmfrom",
    "trk",
    "currentjobid",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
})
_TOKEN_VALUE = re.compile(
    r"(?:eyJ[A-Za-z0-9_-]{8,}\.|Bearer\s|[A-Za-z0-9_-]{32,})",
    re.I,
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?:access[\s_.-]*token|refresh[\s_.-]*token|id[\s_.-]*token|"
    r"client[\s_.-]*secret|session(?:[\s_.-]*(?:id|token))?|"
    r"auth(?:orization)?|credentials?|token(?:\[\])?|api[\s_.-]*key|"
    r"x[\s_.-]*amz[\s_.-]*(?:credential|signature|security[\s_.-]*token)|"
    r"x[\s_.-]*goog[\s_.-]*(?:credential|signature)|"
    r"aws[\s_.-]*access[\s_.-]*key[\s_.-]*id|google[\s_.-]*access[\s_.-]*id|"
    r"key[\s_.-]*pair[\s_.-]*id|policy|expires|signature|signed|sig|"
    r"secret|password|passwd|pwd)\s*[:=]",
    re.I,
)
_PROHIBITED_FRAGMENT = re.compile(
    r"(?:hermes-private://|private\s+resume|candidate\s+(?:profile|facts)|"
    r"user\s+note|authorization\s*:|bearer\s+|"
    r"(?:access[\s_.-]*token|refresh[\s_.-]*token|id[\s_.-]*token|"
    r"client[\s_.-]*secret|session[\s_.-]*(?:id|token)|token(?:\[\])?|"
    r"api[\s_.-]*key|password|passwd|pwd|secret|signature)\s*[:=])",
    re.I,
)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_COMPANY_LABELS = frozenset({
    "company",
    "company site",
    "company website",
    "official website",
    "website",
})
_CAREERS_LABELS = frozenset({
    "career site",
    "careers",
    "jobs",
    "open positions",
    "open roles",
})


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_json(value: object) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _strict_percent_decode(raw: str) -> str:
    current = raw.replace("+", " ")
    for _ in range(4):
        if re.search(r"%(?![0-9A-Fa-f]{2})", current):
            raise ValueError("malformed percent escape")
        try:
            decoded = unquote_to_bytes(current).decode("utf-8", "strict")
        except UnicodeError as exc:
            raise ValueError("percent-encoded value is not valid UTF-8") from exc
        if decoded == current or "%" not in decoded:
            return decoded
        current = decoded
    raise ValueError("nested percent encoding exceeds the closed policy")


def _normalize_query_name(raw: str) -> str:
    decoded = unicodedata.normalize("NFKC", _strict_percent_decode(raw)).casefold()
    decoded = decoded.strip()
    if "[" in decoded or "]" in decoded:
        raise ValueError("query bracket notation is prohibited")
    return re.sub(r"[\s.-]+", "_", decoded)


def _validate_query(query: str) -> None:
    for component in query.split("&") if query else ():
        if ";" in component:
            raise ValueError("alternate query separators are prohibited")
        name, separator, value = component.partition("=")
        if not name:
            raise ValueError("query parameter name is required")
        normalized_name = _normalize_query_name(name)
        decoded_value = unicodedata.normalize("NFKC", _strict_percent_decode(value))
        if normalized_name not in _PUBLIC_QUERY_NAMES:
            raise ValueError("credential-like or ungoverned query name is prohibited")
        if (
            _TOKEN_VALUE.search(decoded_value)
            or _CREDENTIAL_ASSIGNMENT.search(decoded_value)
            or "://" in decoded_value
        ):
            raise ValueError("credential-like query value is prohibited")
        if _PROHIBITED_FRAGMENT.search(decoded_value) or _EMAIL.search(decoded_value):
            raise ValueError(
                "private or personally identifying query value is prohibited"
            )
        if not separator and decoded_value:
            raise ValueError("invalid query parameter")


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
    _validate_query(parsed.query)
    if port not in (None, 443):
        raise ValueError("non-default HTTPS port is prohibited")
    if parsed.hostname.endswith(".."):
        raise ValueError("multiple trailing dots are prohibited")
    hostname = (
        parsed.hostname[:-1] if parsed.hostname.endswith(".") else parsed.hostname
    )
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("IP literal is prohibited")
    try:
        ascii_host = (
            idna
            .encode(
                hostname,
                uts46=True,
                transitional=False,
                std3_rules=True,
            )
            .decode("ascii")
            .lower()
        )
        if (
            idna.encode(idna.decode(ascii_host), uts46=False).decode("ascii")
            != ascii_host
        ):
            raise ValueError("IDNA A-label round trip failed")
    except (idna.IDNAError, UnicodeError) as exc:
        raise ValueError("hostname IDNA encoding failed") from exc
    labels = ascii_host.split(".")
    if (
        len(labels) < 2
        or len(ascii_host) > 253
        or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        )
    ):
        raise ValueError("hostname syntax is invalid")
    canonical = urlunsplit(
        SplitResult("https", ascii_host, parsed.path or "/", parsed.query, "")
    )
    return canonical, ascii_host


def _source_family_authority(
    source_family: SourceFamily,
) -> _SourceFamilyAuthority:
    if set(_SOURCE_FAMILY_AUTHORITY_V1) != set(SourceFamily):
        raise ValueError("source-family authority policy is not total")
    try:
        return _SOURCE_FAMILY_AUTHORITY_V1[source_family]
    except KeyError as exc:
        raise ValueError("source family has no governed authority policy") from exc


def _registrable_scope(domain: str) -> str:
    """Return a conservative registrable scope without a network PSL lookup."""
    labels = domain.split(".")
    if len(labels) < 2:
        raise ValueError("registrable domain scope is unavailable")
    return ".".join(labels[-2:])


def _seal_model_identity(model: BaseModel) -> None:
    current = getattr(model, "identity_sha256")
    payload = model.model_dump(mode="json", exclude={"identity_sha256"})
    expected = _sha256_json(payload)
    if current is not None and current != expected:
        raise ValueError("canonical model identity mismatch")
    object.__setattr__(model, "identity_sha256", expected)


def _identity(model: BaseModel) -> str:
    value = getattr(model, "identity_sha256", None)
    if not isinstance(value, str):
        raise ValueError("canonical model identity is unavailable")
    return value


@dataclass(frozen=True, slots=True)
class _DirectoryProof:
    device: int
    inode: int
    mode: int


@dataclass(frozen=True, slots=True)
class _ArtifactProof:
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    sha256: str
    directory_chain: tuple[_DirectoryProof, ...]


def _directory_proofs(descriptors: Sequence[int]) -> tuple[_DirectoryProof, ...]:
    return tuple(
        _DirectoryProof(
            device=file_stat.st_dev,
            inode=file_stat.st_ino,
            mode=file_stat.st_mode,
        )
        for file_stat in (os.fstat(descriptor) for descriptor in descriptors)
    )


def _open_directory_nofollow(path: Path | str) -> tuple[int, list[int]]:
    root = Path(path)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        if root.is_absolute():
            current = os.open(os.sep, directory_flags | nofollow)
            parts = root.parts[1:]
        else:
            current = os.open(".", directory_flags | nofollow)
            parts = root.parts
        descriptors.append(current)
        for part in parts:
            if part in {"", ".", ".."}:
                raise ValueError("artifact root is not a canonical contained path")
            current = os.open(part, directory_flags | nofollow, dir_fd=current)
            descriptors.append(current)
        return current, descriptors
    except (OSError, ValueError):
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def _read_contained_nofollow_with_proof(
    root: Path | str,
    reference: str,
    *,
    label: str,
    maximum_bytes: int = _MAX_ARTIFACT_BYTES,
) -> tuple[bytes, _ArtifactProof]:
    relative = Path(reference)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{label} reference is not contained")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    file_descriptor: int | None = None
    try:
        current, descriptors = _open_directory_nofollow(root)
        for part in relative.parts[:-1]:
            current = os.open(part, directory_flags | nofollow, dir_fd=current)
            descriptors.append(current)
        file_descriptor = os.open(
            relative.parts[-1],
            os.O_RDONLY | nofollow,
            dir_fd=current,
        )
        directories_before = _directory_proofs(descriptors)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} is not a regular file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(file_descriptor, min(1024 * 1024, maximum_bytes + 1))
            if not chunk:
                payload = b"".join(chunks)
                after = os.fstat(file_descriptor)
                directories_after = _directory_proofs(descriptors)
                if (
                    before.st_dev,
                    before.st_ino,
                    before.st_mode,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                ) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_mode,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise ValueError(f"{label} changed during no-follow read")
                if directories_before != directories_after:
                    raise ValueError(f"{label} path changed during no-follow read")
                return payload, _ArtifactProof(
                    device=after.st_dev,
                    inode=after.st_ino,
                    mode=after.st_mode,
                    size=after.st_size,
                    mtime_ns=after.st_mtime_ns,
                    ctime_ns=after.st_ctime_ns,
                    sha256=_sha256_bytes(payload),
                    directory_chain=directories_after,
                )
            total += len(chunk)
            if total > maximum_bytes:
                raise ValueError(f"{label} exceeds the byte limit")
            chunks.append(chunk)
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"{label} is unavailable through no-follow read") from exc
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _read_contained_nofollow(
    root: Path | str,
    reference: str,
    *,
    label: str,
    maximum_bytes: int = _MAX_ARTIFACT_BYTES,
) -> bytes:
    payload, _ = _read_contained_nofollow_with_proof(
        root,
        reference,
        label=label,
        maximum_bytes=maximum_bytes,
    )
    return payload


def _gate_a_canonical_url(raw: str) -> str:
    split = urlsplit(unescape(raw.strip()))
    hostname = (split.hostname or "").casefold()
    path = split.path.rstrip("/")
    is_linkedin_job = (
        hostname == "linkedin.com" or hostname.endswith(".linkedin.com")
    ) and path.startswith("/jobs/view/")
    is_headhunter_vacancy = (
        hostname == "hh.ru" or hostname.endswith(".hh.ru")
    ) and path.startswith("/vacancy/")
    filtered = (
        []
        if is_linkedin_job or is_headhunter_vacancy
        else [
            (key, value)
            for key, value in parse_qsl(split.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
        ]
    )
    return urlunsplit((
        split.scheme.casefold(),
        split.netloc.casefold(),
        path,
        urlencode(filtered),
        "",
    ))


class PinnedGateACorpusRow(_ClosedModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    corpus_manifest_sha256: str = Field(pattern=SHA256)
    corpus_row_index: int = Field(ge=0)
    gate_a_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    gate_a_manifest_sha256: str = Field(pattern=SHA256)
    run_id: Literal["gate-a-20260816T141344Z"]
    selection_key: str = Field(pattern=SHA256)
    canonical_identity_sha256: str = Field(pattern=SHA256)
    source_family: SourceFamily
    source_id: str = Field(min_length=1)
    query_id: str = Field(min_length=1)
    raw_content_sha256: str = Field(pattern=SHA256)
    company_label: str = Field(min_length=1, max_length=500)
    vacancy_uri: str
    identity_sha256: str | None = Field(default=None, pattern=SHA256)

    @field_validator("vacancy_uri")
    @classmethod
    def validate_vacancy_uri(cls, value: str) -> str:
        canonical, _ = _canonical_uri(value)
        if canonical != value:
            raise ValueError("pinned vacancy URI must already be canonical")
        return value

    @model_validator(mode="after")
    def validate_pinned_identity(self) -> Self:
        if self.corpus_manifest_sha256 != PINNED_GATE_B_CORPUS_SHA256:
            raise ValueError("row is not bound to the pinned Gate B corpus")
        if self.gate_a_commit != PINNED_GATE_A_COMMIT:
            raise ValueError("row is not bound to the pinned Gate A commit")
        if self.gate_a_manifest_sha256 != PINNED_GATE_A_MANIFEST_SHA256:
            raise ValueError("row is not bound to the pinned Gate A manifest")
        if _sha256_bytes(self.vacancy_uri.encode()) != (self.canonical_identity_sha256):
            raise ValueError("pinned Gate A canonical vacancy identity mismatch")
        _source_family_authority(self.source_family)
        expected_selection = _sha256_json({
            "run_id": self.run_id,
            "source_family": self.source_family.value,
            "source_id": self.source_id,
            "raw_content_sha256": self.raw_content_sha256,
        })
        if self.selection_key != expected_selection:
            raise ValueError("pinned Gate A selection identity mismatch")
        _seal_model_identity(self)
        return self


@dataclass(frozen=True, slots=True)
class _LoadedGateAuthority:
    row: PinnedGateACorpusRow
    corpus_manifest_proof: _ArtifactProof
    gate_a_manifest_proof: _ArtifactProof
    raw_artifact_proof: _ArtifactProof


def _top_level_yaml_scalar(payload: bytes, name: str) -> str:
    try:
        text = payload.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise ValueError("Gate A manifest is not valid UTF-8") from exc
    matches = re.findall(
        rf"(?m)^{re.escape(name)}:[ \t]*([^\s#]+)[ \t]*$",
        text,
    )
    if len(matches) != 1:
        raise ValueError(f"Gate A manifest {name} is unavailable")
    return matches[0]


def _load_canonical_gate_authority_once(
    *,
    selection_key: str,
) -> _LoadedGateAuthority:
    if not re.fullmatch(SHA256[1:-1], selection_key):
        raise ValueError("selection key is invalid")
    gate_a_manifest_bytes, gate_a_manifest_proof = _read_contained_nofollow_with_proof(
        CANONICAL_GATE_A_ROOT,
        "manifest.yaml",
        label="pinned Gate A manifest",
        maximum_bytes=64 * 1024,
    )
    if gate_a_manifest_proof.sha256 != PINNED_GATE_A_MANIFEST_SHA256:
        raise ValueError("pinned Gate A manifest sha256 mismatch")
    if (
        _top_level_yaml_scalar(gate_a_manifest_bytes, "schema_version") != "1.0.0"
        or _top_level_yaml_scalar(gate_a_manifest_bytes, "gate") != "gate-a"
    ):
        raise ValueError("pinned Gate A manifest identity mismatch")
    if _top_level_yaml_scalar(gate_a_manifest_bytes, "commit") != PINNED_GATE_A_COMMIT:
        raise ValueError("pinned Gate A commit mismatch")
    if _top_level_yaml_scalar(gate_a_manifest_bytes, "root") != os.fspath(
        CANONICAL_GATE_A_ROOT
    ):
        raise ValueError("pinned Gate A manifest root mismatch")
    manifest_bytes, corpus_manifest_proof = _read_contained_nofollow_with_proof(
        CANONICAL_GATE_B_CORPUS_ROOT,
        "corpus-manifest.json",
        label="corpus manifest",
        maximum_bytes=4 * 1024 * 1024,
    )
    if corpus_manifest_proof.sha256 != PINNED_GATE_B_CORPUS_SHA256:
        raise ValueError("corpus manifest sha256 mismatch")
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("corpus manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "1.0.0":
        raise ValueError("corpus manifest schema is not v1")
    gate_a_identity = manifest.get("gate_a")
    if not isinstance(gate_a_identity, dict) or (
        manifest.get("gate") != "gate-b"
        or gate_a_identity.get("run_id") != GATE_A_RUN_ID
        or gate_a_identity.get("commit") != PINNED_GATE_A_COMMIT
        or gate_a_identity.get("manifest_sha256") != PINNED_GATE_A_MANIFEST_SHA256
    ):
        raise ValueError("corpus manifest Gate A identity mismatch")
    records = manifest.get("records")
    selection = manifest.get("selection")
    if (
        not isinstance(records, list)
        or len(records) != PINNED_GATE_B_CORPUS_RECORD_COUNT
        or not isinstance(selection, dict)
        or selection.get("sample_size") != PINNED_GATE_B_CORPUS_RECORD_COUNT
    ):
        raise ValueError("corpus manifest records are invalid")
    if any(
        not isinstance(record, dict) or record.get("run_id") != GATE_A_RUN_ID
        for record in records
    ):
        raise ValueError("corpus manifest contains a mixed Gate A run")
    selection_keys = [record.get("selection_key") for record in records]
    if (
        any(
            not isinstance(key, str) or not re.fullmatch(SHA256[1:-1], key)
            for key in selection_keys
        )
        or len(set(selection_keys)) != len(selection_keys)
        or selection_keys != sorted(selection_keys)
    ):
        raise ValueError("corpus manifest is not in canonical order")
    matches = [
        (index, record)
        for index, record in enumerate(records)
        if record.get("selection_key") == selection_key
    ]
    if len(matches) != 1:
        raise ValueError("pinned Gate A row selection is not unique")
    corpus_row_index, record = matches[0]
    raw_sha256 = record.get("raw_content_sha256")
    raw_reference = record.get("raw_reference")
    if (
        not isinstance(raw_sha256, str)
        or not re.fullmatch(SHA256[1:-1], raw_sha256)
        or raw_reference != f"raw-evidence/{raw_sha256}.json"
    ):
        raise ValueError("pinned Gate A row raw artifact identity mismatch")
    raw_bytes, raw_artifact_proof = _read_contained_nofollow_with_proof(
        CANONICAL_GATE_A_ROOT,
        raw_reference,
        label="pinned Gate A raw artifact",
    )
    if raw_artifact_proof.sha256 != raw_sha256:
        raise ValueError("pinned Gate A raw artifact sha256 mismatch")
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("pinned Gate A raw artifact is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("pinned Gate A raw artifact must be an object")
    for raw_name, row_name in (
        ("source_family", "source_family"),
        ("source_id", "source_id"),
        ("query_id", "query_id"),
        ("company", "company"),
    ):
        if raw.get(raw_name) != record.get(row_name):
            raise ValueError(f"pinned Gate A row {row_name} does not match raw bytes")
    raw_uri = raw.get("url")
    if not isinstance(raw_uri, str):
        raise ValueError("pinned Gate A row has no exact vacancy URI")
    gate_a_uri = _gate_a_canonical_url(raw_uri)
    if _sha256_bytes(gate_a_uri.encode()) != record.get("canonical_identity_sha256"):
        raise ValueError("pinned Gate A row canonical vacancy identity mismatch")
    canonical_uri, _ = _canonical_uri(gate_a_uri)
    if canonical_uri != gate_a_uri:
        raise ValueError("pinned Gate A row vacancy URI is not canonical")
    return _LoadedGateAuthority(
        row=PinnedGateACorpusRow(
            corpus_manifest_sha256=PINNED_GATE_B_CORPUS_SHA256,
            corpus_row_index=corpus_row_index,
            gate_a_commit=PINNED_GATE_A_COMMIT,
            gate_a_manifest_sha256=PINNED_GATE_A_MANIFEST_SHA256,
            run_id=record.get("run_id"),
            selection_key=record.get("selection_key"),
            canonical_identity_sha256=record.get("canonical_identity_sha256"),
            source_family=record.get("source_family"),
            source_id=record.get("source_id"),
            query_id=record.get("query_id"),
            raw_content_sha256=raw_sha256,
            company_label=record.get("company"),
            vacancy_uri=canonical_uri,
        ),
        corpus_manifest_proof=corpus_manifest_proof,
        gate_a_manifest_proof=gate_a_manifest_proof,
        raw_artifact_proof=raw_artifact_proof,
    )


def _load_canonical_gate_authority(
    *,
    selection_key: str,
) -> _LoadedGateAuthority:
    first = _load_canonical_gate_authority_once(selection_key=selection_key)
    second = _load_canonical_gate_authority_once(selection_key=selection_key)
    if first != second:
        raise ValueError("canonical Gate A authority changed during validation")
    return first


def load_pinned_gate_a_row(*, selection_key: str) -> PinnedGateACorpusRow:
    """Return an audit DTO reloaded only from the exact canonical evidence chain."""
    return _load_canonical_gate_authority(selection_key=selection_key).row


class SourcePlan(_ClosedModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_policy_version: Literal["1.0.0"] = "1.0.0"
    pinned_row: PinnedGateACorpusRow
    root_class: DiscoveryRootClass
    discovery_roots: tuple[str, ...] = Field(min_length=1, max_length=1)
    max_requests: Literal[3] = 3
    max_redirects: Literal[2] = 2
    identity_sha256: str | None = Field(default=None, pattern=SHA256)

    @field_validator("discovery_roots")
    @classmethod
    def validate_root(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _canonical_uri(value[0])
        return value

    @model_validator(mode="after")
    def validate_source_authority(self) -> Self:
        if self.authority_policy_version != SOURCE_AUTHORITY_POLICY_VERSION:
            raise ValueError("source-family authority policy version mismatch")
        expected_root = self.pinned_row.vacancy_uri
        if self.discovery_roots != (expected_root,):
            raise ValueError("source root must come from the pinned Gate A row")
        policy = _source_family_authority(self.pinned_row.source_family)
        if self.root_class is not policy.root_class:
            raise ValueError("root class must be derived from the source-family policy")
        _seal_model_identity(self)
        return self


def _build_source_plan(pinned_row: PinnedGateACorpusRow) -> SourcePlan:
    policy = _source_family_authority(pinned_row.source_family)
    return SourcePlan(
        pinned_row=pinned_row,
        root_class=policy.root_class,
        discovery_roots=(pinned_row.vacancy_uri,),
    )


def build_source_plan(*, selection_key: str) -> SourcePlan:
    authority = _load_canonical_gate_authority(selection_key=selection_key)
    return _build_source_plan(authority.row)


class RequestReceipt(_ClosedModel):
    uri: str
    status: int = Field(ge=100, le=599)
    content_type: str = Field(min_length=1, max_length=200)
    content_bytes: int = Field(ge=0, le=_MAX_ARTIFACT_BYTES)
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
    def validate_redirect_semantics(self) -> Self:
        if self.capture_artifact_sha256 != self.content_sha256:
            raise ValueError("capture artifact must match response content hash")
        is_redirect = self.status in {301, 302, 303, 307, 308}
        if is_redirect != (self.redirect_to is not None):
            raise ValueError("redirect status and target must agree")
        return self


@dataclass(frozen=True)
class _ParsedOfficialLink:
    uri: str
    relation: OfficialLinkRelation
    extraction_rule: ExtractionRule
    extraction_fragment: str
    byte_start: int
    byte_end: int


def _decode_fragment_layers(fragment: str) -> str:
    decoded = unicodedata.normalize("NFKC", unescape(fragment))
    if "%" in decoded:
        decoded = _strict_percent_decode(decoded)
    return decoded


def _validate_extraction_fragment(fragment: str) -> None:
    if len(fragment.encode("utf-8")) > 1024:
        raise ValueError("extraction element exceeds the byte limit")
    decoded = _decode_fragment_layers(fragment)
    if (
        _PROHIBITED_FRAGMENT.search(decoded)
        or _CREDENTIAL_ASSIGNMENT.search(decoded)
        or _EMAIL.search(decoded)
    ):
        raise ValueError(
            "extraction element contains prohibited private or credential data"
        )


class _OfficialAnchorParser(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.line_starts = [0]
        self.line_starts.extend(match.end() for match in re.finditer("\n", source))
        self.byte_offsets = [0]
        byte_count = 0
        for character in source:
            byte_count += len(character.encode("utf-8"))
            self.byte_offsets.append(byte_count)
        self.active: dict[str, Any] | None = None
        self.links: list[_ParsedOfficialLink] = []

    def _character_offset(self) -> int:
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if self.active is not None:
            self.active["invalid"] = True
            return
        if tag != "a":
            return
        start = self._character_offset()
        start_text = self.get_starttag_text()
        hrefs = [value for name, value in attrs if name == "href" and value is not None]
        aria = [
            value for name, value in attrs if name == "aria-label" and value is not None
        ]
        self.active = {
            "start": start,
            "inner_start": start + len(start_text),
            "hrefs": hrefs,
            "aria": aria,
            "invalid": len(hrefs) != 1 or len(aria) > 1,
        }

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del tag, attrs
        if self.active is not None:
            self.active["invalid"] = True

    def handle_comment(self, data: str) -> None:
        del data
        if self.active is not None:
            self.active["invalid"] = True

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self.active is None:
            return
        active = self.active
        self.active = None
        end_start = self._character_offset()
        closing_end = self.source.find(">", end_start)
        if closing_end < 0 or active["invalid"]:
            return
        closing_end += 1
        inner = self.source[active["inner_start"] : end_start]
        if "<" in inner:
            return
        visible = " ".join(unescape(inner).split())
        aria = " ".join(unescape(active["aria"][0]).split()) if active["aria"] else ""
        label = (visible or aria).casefold()
        if label in _COMPANY_LABELS:
            relation = OfficialLinkRelation.OFFICIAL_COMPANY
        elif label in _CAREERS_LABELS:
            relation = OfficialLinkRelation.OFFICIAL_CAREERS
        else:
            return
        uri = active["hrefs"][0]
        _canonical_uri(uri)
        fragment = self.source[active["start"] : closing_end]
        _validate_extraction_fragment(fragment)
        self.links.append(
            _ParsedOfficialLink(
                uri=uri,
                relation=relation,
                extraction_rule=ExtractionRule.HTML_ANCHOR_TEXT_V1,
                extraction_fragment=fragment,
                byte_start=self.byte_offsets[active["start"]],
                byte_end=self.byte_offsets[closing_end],
            )
        )


def _parse_official_links(payload: bytes) -> tuple[_ParsedOfficialLink, ...]:
    try:
        source = payload.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise ValueError("HTML capture artifact is not valid UTF-8") from exc
    parser = _OfficialAnchorParser(source)
    parser.feed(source)
    parser.close()
    return tuple(parser.links)


class OfficialLinkReceipt(_ClosedModel):
    uri: str
    relation: OfficialLinkRelation
    extraction_rule: ExtractionRule
    source_request_uri: str
    evidence_sha256: str = Field(pattern=SHA256)
    capture_artifact_sha256: str = Field(pattern=SHA256)
    extraction_fragment: str = Field(min_length=1, max_length=1024)
    extraction_sha256: str = Field(pattern=SHA256)
    byte_start: int = Field(ge=0)
    byte_end: int = Field(gt=0)

    @field_validator("uri", "source_request_uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        _canonical_uri(value)
        return value

    @model_validator(mode="after")
    def validate_extraction(self) -> Self:
        encoded = self.extraction_fragment.encode("utf-8")
        if _sha256_bytes(encoded) != self.extraction_sha256:
            raise ValueError("extraction fragment hash mismatch")
        if self.byte_end - self.byte_start != len(encoded):
            raise ValueError("extraction byte range does not match fragment")
        parsed = _parse_official_links(encoded)
        if (
            len(parsed) != 1
            or parsed[0].byte_start != 0
            or parsed[0].byte_end != len(encoded)
            or parsed[0].uri != self.uri
            or parsed[0].relation is not self.relation
            or parsed[0].extraction_rule is not self.extraction_rule
        ):
            raise ValueError(
                "extraction fragment is not one closed parsed HTML element"
            )
        return self


class DiscoveryReceipt(_ClosedModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    root_uri: str
    requests: tuple[RequestReceipt, ...] = Field(min_length=1, max_length=3)
    explicit_official_links: tuple[OfficialLinkReceipt, ...] = ()
    identity_sha256: str | None = Field(default=None, pattern=SHA256)

    @field_validator("root_uri")
    @classmethod
    def validate_root(cls, value: str) -> str:
        _canonical_uri(value)
        return value

    @model_validator(mode="after")
    def validate_chain_and_identity(self) -> Self:
        if self.requests[0].uri != self.root_uri:
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
        link_keys: set[tuple[str, int, int]] = set()
        for link in self.explicit_official_links:
            request = requests.get((link.source_request_uri, link.evidence_sha256))
            if request is None or link.capture_artifact_sha256 != (
                request.capture_artifact_sha256
            ):
                raise ValueError("extraction proof does not match capture artifact")
            key = (link.source_request_uri, link.byte_start, link.byte_end)
            if key in link_keys:
                raise ValueError("duplicate extraction proof")
            link_keys.add(key)
        _seal_model_identity(self)
        return self


@dataclass(frozen=True, slots=True)
class _CaptureArtifactProof:
    request_uri: str
    capture_artifact_sha256: str
    artifact: _ArtifactProof


@dataclass(frozen=True, slots=True)
class _VerifiedReceiptArtifacts:
    receipt: DiscoveryReceipt
    capture_proofs: tuple[_CaptureArtifactProof, ...]


def _read_capture_artifact(
    request: RequestReceipt,
    artifacts_root: Path | str,
) -> tuple[bytes, _CaptureArtifactProof]:
    payload, artifact_proof = _read_contained_nofollow_with_proof(
        artifacts_root,
        request.capture_artifact_sha256,
        label="capture artifact",
        maximum_bytes=request.content_bytes + 1,
    )
    if len(payload) != request.content_bytes:
        raise ValueError("capture artifact byte length mismatch")
    if _sha256_bytes(payload) != request.capture_artifact_sha256:
        raise ValueError("capture artifact sha256 mismatch")
    return payload, _CaptureArtifactProof(
        request_uri=request.uri,
        capture_artifact_sha256=request.capture_artifact_sha256,
        artifact=artifact_proof,
    )


def _links_from_artifacts(
    requests: Sequence[RequestReceipt],
    artifacts_root: Path | str,
) -> tuple[tuple[OfficialLinkReceipt, ...], tuple[_CaptureArtifactProof, ...]]:
    links: list[OfficialLinkReceipt] = []
    proofs: list[_CaptureArtifactProof] = []
    for request in requests:
        payload, proof = _read_capture_artifact(request, artifacts_root)
        proofs.append(proof)
        media_type = request.content_type.partition(";")[0].strip().casefold()
        if request.status != 200 or media_type != "text/html":
            continue
        for parsed in _parse_official_links(payload):
            links.append(
                OfficialLinkReceipt(
                    uri=parsed.uri,
                    relation=parsed.relation,
                    extraction_rule=parsed.extraction_rule,
                    source_request_uri=request.uri,
                    evidence_sha256=request.content_sha256,
                    capture_artifact_sha256=request.capture_artifact_sha256,
                    extraction_fragment=parsed.extraction_fragment,
                    extraction_sha256=_sha256_bytes(
                        parsed.extraction_fragment.encode("utf-8")
                    ),
                    byte_start=parsed.byte_start,
                    byte_end=parsed.byte_end,
                )
            )
    return tuple(links), tuple(proofs)


def _verify_receipt_artifacts(
    receipt: DiscoveryReceipt,
    artifacts_root: Path | str,
) -> _VerifiedReceiptArtifacts:
    parsed_links, capture_proofs = _links_from_artifacts(
        receipt.requests,
        artifacts_root,
    )
    if parsed_links != receipt.explicit_official_links:
        raise ValueError("receipt does not match parsed artifact extraction proof")
    return _VerifiedReceiptArtifacts(
        receipt=receipt,
        capture_proofs=capture_proofs,
    )


def build_discovery_receipt(
    *,
    root_uri: str,
    requests: Sequence[RequestReceipt],
    artifacts_root: Path | str,
) -> DiscoveryReceipt:
    request_tuple = tuple(requests)
    links, _ = _links_from_artifacts(request_tuple, artifacts_root)
    return _verify_receipt_artifacts(
        DiscoveryReceipt(
            root_uri=root_uri,
            requests=request_tuple,
            explicit_official_links=links,
        ),
        artifacts_root,
    ).receipt


def load_discovery_receipt(
    payload: Mapping[str, Any] | str | bytes,
    *,
    artifacts_root: Path | str,
) -> DiscoveryReceipt:
    if isinstance(payload, Mapping):
        receipt = DiscoveryReceipt.model_validate(payload)
    elif isinstance(payload, (str, bytes)):
        receipt = DiscoveryReceipt.model_validate_json(payload)
    else:
        raise TypeError("discovery receipt must be a mapping or JSON bytes")
    return _verify_receipt_artifacts(receipt, artifacts_root).receipt


class AdmittedOfficialDomain(_ClosedModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    pinned_row_sha256: str = Field(pattern=SHA256)
    source_plan_sha256: str = Field(pattern=SHA256)
    selection_key: str = Field(pattern=SHA256)
    acquisition_source_family: SourceFamily
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
    def validate_authority(self) -> Self:
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
    def validate_plan_binding(self) -> Self:
        row = self.source_plan.pinned_row
        if (
            self.authority.pinned_row_sha256 != _identity(row)
            or self.authority.source_plan_sha256 != _identity(self.source_plan)
            or self.authority.selection_key != row.selection_key
            or self.authority.acquisition_source_family is not row.source_family
            or self.authority.company_label != row.company_label
            or self.authority.exact_root_uri != self.source_plan.discovery_roots[0]
            or self.authority.root_class is not self.source_plan.root_class
            or self.receipt_sha256 != _identity(self.discovery_receipt)
        ):
            raise ValueError("admitted authority does not match pinned source plan")
        links = [
            link
            for link in self.discovery_receipt.explicit_official_links
            if link.source_request_uri == self.authority.source_request_uri
            and link.relation is self.authority.relation
            and link.extraction_rule is self.authority.extraction_rule
            and link.capture_artifact_sha256 == self.authority.capture_artifact_sha256
            and link.evidence_sha256 == self.authority.evidence_sha256
            and link.extraction_sha256 == self.authority.extraction_sha256
            and link.byte_start == self.authority.byte_start
            and link.byte_end == self.authority.byte_end
            and link.uri == self.authority.evidence_display_uri
            and _canonical_uri(link.uri)[0] == self.authority.canonical_uri
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
    def validate_unique_reasons(self) -> Self:
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("unresolved reasons must be unique")
        return self


DiscoveryOutcome = Annotated[
    AdmittedIdentityOutcome | UnresolvedIdentityOutcome,
    Field(discriminator="status"),
]


def _unresolved(reason: MaterializationReason) -> UnresolvedIdentityOutcome:
    return UnresolvedIdentityOutcome(reasons=(reason,))


def _validate_trusted_plan(
    plan: SourcePlan,
    authority: _LoadedGateAuthority,
) -> None:
    if plan != _build_source_plan(authority.row):
        raise ValueError("source plan does not match canonical Gate A authority")


def _admit_official_domain(
    authority: _LoadedGateAuthority,
    receipt: DiscoveryReceipt,
) -> AdmittedIdentityOutcome | UnresolvedIdentityOutcome:
    pinned_row = authority.row
    plan = _build_source_plan(pinned_row)
    if receipt.root_uri != plan.discovery_roots[0]:
        return _unresolved(MaterializationReason.UNRESOLVED_COMPANY_IDENTITY)
    captured = {
        (request.uri, request.content_sha256): request for request in receipt.requests
    }
    _, root_domain = _canonical_uri(receipt.root_uri)
    source_policy = _source_family_authority(pinned_row.source_family)
    candidates: dict[str, OfficialLinkReceipt] = {}
    rejected_self_promotion = False
    for link in receipt.explicit_official_links:
        request = captured.get((link.source_request_uri, link.evidence_sha256))
        if request is None:
            continue
        canonical_uri, domain = _canonical_uri(link.uri)
        del canonical_uri
        if (
            source_policy.root_authority_mode
            is _RootAuthorityMode.EXTERNAL_OFFICIAL_LINK_ONLY
            and _registrable_scope(domain) == _registrable_scope(root_domain)
        ):
            rejected_self_promotion = True
            continue
        candidates.setdefault(domain, link)
    if not candidates:
        if rejected_self_promotion:
            return _unresolved(MaterializationReason.SOURCE_NOT_ADMISSIBLE)
        return _unresolved(MaterializationReason.UNRESOLVED_COMPANY_IDENTITY)
    if len(candidates) != 1:
        return _unresolved(MaterializationReason.AMBIGUOUS_COMPANY_IDENTITY)
    domain, link = next(iter(candidates.items()))
    canonical_uri, _ = _canonical_uri(link.uri)
    return AdmittedIdentityOutcome(
        source_plan=plan,
        discovery_receipt=receipt,
        receipt_sha256=_identity(receipt),
        authority=AdmittedOfficialDomain(
            pinned_row_sha256=_identity(pinned_row),
            source_plan_sha256=_identity(plan),
            selection_key=pinned_row.selection_key,
            acquisition_source_family=pinned_row.source_family,
            company_label=pinned_row.company_label,
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
        ),
    )


def admit_official_domain(
    receipt: DiscoveryReceipt,
    *,
    selection_key: str,
    artifacts_root: Path | str,
) -> AdmittedIdentityOutcome | UnresolvedIdentityOutcome:
    authority = _load_canonical_gate_authority(selection_key=selection_key)
    verified_receipt = _verify_receipt_artifacts(receipt, artifacts_root)
    outcome = _admit_official_domain(authority, verified_receipt.receipt)
    authority_after = _load_canonical_gate_authority(selection_key=selection_key)
    receipt_after = _verify_receipt_artifacts(receipt, artifacts_root)
    if authority_after != authority or receipt_after != verified_receipt:
        raise ValueError("admission authority changed during validation")
    return outcome


def load_discovery_outcome(
    payload: Mapping[str, Any] | str | bytes,
    *,
    selection_key: str,
    artifacts_root: Path | str,
) -> AdmittedIdentityOutcome | UnresolvedIdentityOutcome:
    adapter = TypeAdapter(DiscoveryOutcome)
    if isinstance(payload, Mapping):
        outcome = adapter.validate_python(payload)
    elif isinstance(payload, (str, bytes)):
        outcome = adapter.validate_json(payload)
    else:
        raise TypeError("discovery outcome must be a mapping or JSON bytes")
    if isinstance(outcome, UnresolvedIdentityOutcome):
        return outcome
    authority = _load_canonical_gate_authority(selection_key=selection_key)
    if outcome.source_plan.pinned_row.selection_key != selection_key:
        raise ValueError("serialized outcome selection is not canonical")
    _validate_trusted_plan(outcome.source_plan, authority)
    verified_receipt = _verify_receipt_artifacts(
        outcome.discovery_receipt,
        artifacts_root,
    )
    expected = admit_official_domain(
        verified_receipt.receipt,
        selection_key=selection_key,
        artifacts_root=artifacts_root,
    )
    if expected != outcome:
        raise ValueError(
            "serialized admitted outcome does not match verified authority"
        )
    return outcome
