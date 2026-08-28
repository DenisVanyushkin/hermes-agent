from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from html import unescape
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable, Iterable, Literal, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml

from ..browser_sourcing import EXCLUSION_REASON_CATALOG
from .search_contract import SearchContract


SLACK_CREDENTIAL_NAMES = frozenset(
    {"SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "JOB_INTEL_SLACK_WEBHOOK_URL"}
)
FORBIDDEN_PROBE_ROOTS = (
    Path("/var/lib/job-intel/state"),
    Path("/home/hermes/.hermes/job_intel"),
    Path("/home/hermes/.hermes/hermes-agent/.worktrees"),
)
GATE_A_EXPERIMENT_ROOT = Path("/home/hermes/.hermes/job_intel/experiments/gate-a")

SourceIsolationMode = Literal["cloned_profile", "exclusive_lock", "api", "blocked"]
SourceCollectionMethod = Literal["browser", "api"]
SourceState = Literal[
    "observed",
    "observed_with_failures",
    "blocked_no_safe_isolation",
    "blocked_missing_public_interface",
    "runtime_capability_blocked",
    "blocked_anti_bot",
    "blocked_rate_limit_or_timeout",
    "blocked_extraction_failure",
    "blocked_multiple_failures",
    "blocked_unsupported_geography",
]

AcquisitionOutcome = Literal[
    "not_attempted",
    "blocked",
    "degraded",
    "insufficient_breadth",
    "candidate_records_found",
    "no_candidate_records",
]
ProductObservabilityState = Literal["blocked", "not_observed"]
CreditedRecordsProvenance = Literal["attributed", "caller_supplied", "received_rows_fallback"]
LEGACY_ATTEMPT_EVIDENCE_CRITERION = "legacy_pre_b1_cell_states"


@dataclass(frozen=True)
class AcquisitionOutcomeDecision:
    acquisition_outcome: AcquisitionOutcome
    product_observability_state: ProductObservabilityState | None
    product_observability_reason: str | None


def matching_acquisition_rules(
    *,
    completed: int,
    blocked: int,
    degraded: int,
    minimum_independent_families: int,
    credited: int = 0,
) -> tuple[AcquisitionOutcome, ...]:
    rules: tuple[tuple[AcquisitionOutcome, bool], ...] = (
        (
            "not_attempted",
            completed == 0 and blocked == 0 and degraded == 0,
        ),
        (
            "blocked",
            completed == 0 and degraded == 0 and blocked > 0,
        ),
        (
            "degraded",
            completed < minimum_independent_families and degraded > 0,
        ),
        (
            "blocked",
            0 < completed < minimum_independent_families
            and degraded == 0
            and blocked > 0,
        ),
        (
            "insufficient_breadth",
            0 < completed < minimum_independent_families
            and degraded == 0
            and blocked == 0,
        ),
        (
            "candidate_records_found",
            completed >= minimum_independent_families and credited > 0,
        ),
        (
            "no_candidate_records",
            completed >= minimum_independent_families and credited == 0,
        ),
    )
    return tuple(outcome for outcome, applies in rules if applies)


def resolve_acquisition_outcome(
    *,
    completed: int,
    blocked: int,
    degraded: int,
    minimum_independent_families: int,
    credited: int = 0,
) -> AcquisitionOutcomeDecision:
    counts = (completed, blocked, degraded, credited)
    if any(value < 0 for value in counts):
        raise ValueError("acquisition counts must be non-negative")
    if minimum_independent_families < 1:
        raise ValueError("minimum independent families must be positive")
    matches = matching_acquisition_rules(
        completed=completed,
        blocked=blocked,
        degraded=degraded,
        minimum_independent_families=minimum_independent_families,
        credited=credited,
    )
    if len(matches) != 1:
        raise ValueError(
            "acquisition transition table is not total and disjoint: "
            f"{completed=}, {blocked=}, {degraded=}, {minimum_independent_families=}, {credited=}"
        )
    outcome = matches[0]
    if outcome in {"candidate_records_found", "no_candidate_records"}:
        return AcquisitionOutcomeDecision(
            acquisition_outcome=outcome,
            product_observability_state=None,
            product_observability_reason="stage_4_evidence_absent",
        )
    state: ProductObservabilityState = (
        "blocked" if outcome in {"blocked", "degraded"} else "not_observed"
    )
    return AcquisitionOutcomeDecision(
        acquisition_outcome=outcome,
        product_observability_state=state,
        product_observability_reason=None,
    )

OBSERVED_SOURCE_STATES = frozenset({"observed", "observed_with_failures"})

# These are source outcomes that mean the market was not observed. Keep this
# vocabulary explicit: cell aggregation must not infer observability from a
# string prefix that could accidentally include a new source failure.
UNOBSERVED_SOURCE_STATES = frozenset(
    {
        "blocked_no_safe_isolation",
        "blocked_missing_public_interface",
        "runtime_capability_blocked",
        "blocked_anti_bot",
        "blocked_rate_limit_or_timeout",
        "blocked_extraction_failure",
        "blocked_multiple_failures",
        "blocked_unsupported_geography",
    }
)

SHARED_BROWSER_PROFILES = {
    "linkedin": Path("/var/lib/browser-desktop/profiles/linkedin"),
}
BROWSER_PROFILE_ROOT = Path("/var/lib/browser-desktop/profiles")


class ProbeSourceBlocked(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


class RuntimeCapabilityResult(BaseModel):
    """Answer of the pre-dispatch capability seam.

    Closed on purpose (``extra="forbid"``) and with a fixed set of states: an
    unrecognised answer must not read as ready. A raw dict here would leave the
    closed vocabulary as an agreement between test doubles rather than a
    property of the contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["ready", "runtime_capability_blocked", "not_applicable"]
    error_class: str | None = None
    error_fingerprint: str | None = None
    error_message_truncated: str | None = Field(default=None, max_length=512)
    bootstrap_traffic_events: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _blocked_must_carry_a_reason(self) -> "RuntimeCapabilityResult":
        if self.state != "runtime_capability_blocked":
            return self
        missing = [
            name
            for name in ("error_class", "error_fingerprint", "error_message_truncated")
            if not (getattr(self, name) or "").strip()
        ]
        if missing:
            # A blocked result without a reason is the exact loss this gate
            # exists to prevent: runs 467 and 468 recorded the block and lost
            # why. Whitespace does not count as a reason.
            raise ValueError(f"blocked capability result lacks: {', '.join(missing)}")
        return self


class LinkedInExecutionPlan(BaseModel):
    """Versioned, reproducible observation plan for the Gate A LinkedIn path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["1.0"] = "1.0"
    page_offsets: tuple[int, ...] = (0, 25)
    max_scroll_checkpoints: int = Field(default=2, ge=1, le=20)
    settle_timeout_ms: int = Field(default=650, ge=0, le=30_000)
    saturation_checkpoints: int = Field(default=2, ge=1, le=20)
    saturation_rule: Literal["two_consecutive_checkpoint_no_new_ids"] = (
        "two_consecutive_checkpoint_no_new_ids"
    )
    results_selector: str = "div.jobs-search-results-list"

    @model_validator(mode="after")
    def _validate_offsets(self) -> "LinkedInExecutionPlan":
        if not self.page_offsets:
            raise ValueError("execution plan must contain at least one page offset")
        if any(offset < 0 for offset in self.page_offsets):
            raise ValueError("execution plan page offsets must be non-negative")
        if tuple(sorted(set(self.page_offsets))) != self.page_offsets:
            raise ValueError("execution plan page offsets must be sorted and unique")
        if not self.results_selector.strip():
            raise ValueError("execution plan results selector is required")
        return self


GeographyStatus = Literal["verified", "unverified", "unsupported", "blocked"]


class LinkedInGeographyTarget(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True
    )

    location: str | None = None
    geo_id: str | None = Field(default=None, alias="geoId")
    verified_at: str | None = None
    status: GeographyStatus
    country_codes: tuple[str, ...] = ()

    @property
    def canonical_key(self) -> str:
        return json.dumps(
            {
                "geoId": self.geo_id,
                "location": self.location,
                "status": self.status,
                "verified_at": self.verified_at,
                "country_codes": self.country_codes,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


GEOGRAPHY_MAPPING_VERSION = "1.0"
GEOGRAPHY_NORMALIZATION_RULE_VERSION = "1.0"
GEOGRAPHY_CONTAMINATION_FORMULA_VERSION = "jaccard_received_v1"
GEOGRAPHY_CONTAMINATION_THRESHOLD = 0.6


class LinkedInGeographyMapping(dict[str, LinkedInGeographyTarget]):
    """Country ownership mapping plus the versions used to interpret it."""

    def __init__(
        self,
        cells: Mapping[str, LinkedInGeographyTarget],
        *,
        version: str,
        normalization_rule_version: str,
        contamination_formula_version: str,
        contamination_threshold: float,
        city_country_codes: Mapping[str, str],
    ) -> None:
        super().__init__(cells)
        self.version = version
        self.normalization_rule_version = normalization_rule_version
        self.contamination_formula_version = contamination_formula_version
        self.contamination_threshold = contamination_threshold
        self.city_country_codes = dict(city_country_codes)


BOUNDED_PROOF_CELL_IDS = ("uk", "singapore", "kazakhstan")
BOUNDED_PROOF_SELECTION_RULE = (
    "first_alphabetical_unsupported_excluding_bounded_v1"
)
SYNTHETIC_BOUNDED_CONTROL_ID = "synthetic_c1a_unsupported"
SYNTHETIC_BOUNDED_CONTROL_LOCATION = "C1A nonexistent geography target"


def select_bounded_negative_control(
    mapping: Mapping[str, Any],
    *,
    excluded_cell_ids: Iterable[str],
    declared: Mapping[str, Any],
) -> dict[str, Any]:
    mapping_version = str(getattr(mapping, "version", GEOGRAPHY_MAPPING_VERSION))
    if str(declared.get("selection_rule") or "") != BOUNDED_PROOF_SELECTION_RULE:
        raise ValueError("bounded proof negative-control selection rule is invalid")
    if str(declared.get("mapping_version") or "") != mapping_version:
        raise ValueError("bounded proof negative-control mapping version is stale")
    excluded = {str(cell_id) for cell_id in excluded_cell_ids}
    unsupported = sorted(
        str(cell_id)
        for cell_id, target in mapping.items()
        if (
            (
                target.status
                if isinstance(target, LinkedInGeographyTarget)
                else str(target.get("status", ""))
            )
            == "unsupported"
            and str(cell_id) not in excluded
        )
    )
    expected_cell_id = unsupported[0] if unsupported else SYNTHETIC_BOUNDED_CONTROL_ID
    declared_cell_id = str(declared.get("cell_id") or "")
    if declared_cell_id != expected_cell_id:
        raise ValueError(
            "bounded proof negative control does not follow the mapping rule: "
            f"expected {expected_cell_id}, got {declared_cell_id}"
        )
    if str(declared.get("status") or "") != "unsupported":
        raise ValueError("bounded proof negative control must be unsupported")
    if not unsupported:
        if str(declared.get("location") or "") != SYNTHETIC_BOUNDED_CONTROL_LOCATION:
            raise ValueError("synthetic bounded control location is invalid")
    return dict(declared)


def _bounded_proof_configuration(
    manifest: Mapping[str, Any], mapping: Mapping[str, Any]
) -> tuple[tuple[str, ...], dict[str, Any]]:
    config = dict(manifest.get("bounded_proof") or {})
    cell_ids = tuple(str(cell_id) for cell_id in config.get("cell_ids") or ())
    if cell_ids != BOUNDED_PROOF_CELL_IDS:
        raise ValueError(
            "bounded proof must contain the predeclared cells: "
            + ", ".join(BOUNDED_PROOF_CELL_IDS)
        )
    if config.get("include_ats_snapshot") is not False:
        raise ValueError("ATS snapshot is forbidden in the bounded proof")
    control = select_bounded_negative_control(
        mapping,
        excluded_cell_ids=cell_ids,
        declared=dict(config.get("negative_control") or {}),
    )
    return cell_ids, control


def build_bounded_proof_configuration(
    mapping: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the predeclared bounded proof from the current mapping."""
    mapping_version = str(getattr(mapping, "version", GEOGRAPHY_MAPPING_VERSION))
    excluded = set(BOUNDED_PROOF_CELL_IDS)
    unsupported = sorted(
        str(cell_id)
        for cell_id, target in mapping.items()
        if (
            (
                target.status
                if isinstance(target, LinkedInGeographyTarget)
                else str(target.get("status", ""))
            )
            == "unsupported"
            and str(cell_id) not in excluded
        )
    )
    negative_control: dict[str, Any] = {
        "selection_rule": BOUNDED_PROOF_SELECTION_RULE,
        "cell_id": unsupported[0] if unsupported else SYNTHETIC_BOUNDED_CONTROL_ID,
        "status": "unsupported",
        "mapping_version": mapping_version,
    }
    if not unsupported:
        negative_control["location"] = SYNTHETIC_BOUNDED_CONTROL_LOCATION
    validated_control = select_bounded_negative_control(
        mapping,
        excluded_cell_ids=BOUNDED_PROOF_CELL_IDS,
        declared=negative_control,
    )
    return {
        "cell_ids": list(BOUNDED_PROOF_CELL_IDS),
        "include_ats_snapshot": False,
        "negative_control": validated_control,
    }


def load_linkedin_geography_mapping(
    path: Path | str | None = None,
) -> dict[str, LinkedInGeographyTarget]:
    mapping_path = Path(path) if path is not None else (
        Path(__file__).resolve().parents[2]
        / "config/product_search/linkedin_geography.v1.yaml"
    )
    document = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise ValueError("LinkedIn geography mapping must be a mapping")
    if document.get("version") != "1.0":
        raise ValueError("unsupported LinkedIn geography mapping version")
    if document.get("product_authority_id") != "PS-SOT-2026-08-10-v1":
        raise ValueError("LinkedIn geography mapping has wrong product authority")
    if document.get("search_contract_version") != "1.0.0":
        raise ValueError("LinkedIn geography mapping has wrong contract version")
    normalization_rule_version = str(document.get("normalization_rule_version") or "")
    if normalization_rule_version != GEOGRAPHY_NORMALIZATION_RULE_VERSION:
        raise ValueError("unsupported geography normalization rule version")
    contamination_formula_version = str(
        document.get("contamination_formula_version") or ""
    )
    if contamination_formula_version != GEOGRAPHY_CONTAMINATION_FORMULA_VERSION:
        raise ValueError("unsupported geography contamination formula version")
    try:
        contamination_threshold = float(document.get("contamination_threshold"))
    except (TypeError, ValueError):
        raise ValueError("invalid geography contamination threshold") from None
    if contamination_threshold != GEOGRAPHY_CONTAMINATION_THRESHOLD:
        raise ValueError("unsupported geography contamination threshold")
    city_country_codes = document.get("city_country_codes")
    if not isinstance(city_country_codes, Mapping):
        raise ValueError("geography city country mapping is required")
    normalized_city_country_codes: dict[str, str] = {}
    for city, code in city_country_codes.items():
        normalized_code = str(code).upper()
        if not str(city).strip() or not re.fullmatch(r"[A-Z]{2}", normalized_code):
            raise ValueError(f"invalid city country mapping entry: {city}")
        normalized_city_country_codes[str(city)] = normalized_code
    cells = document.get("cells")
    if not isinstance(cells, Mapping):
        raise ValueError("LinkedIn geography mapping cells are required")
    parsed = {
        str(cell_id): LinkedInGeographyTarget.model_validate(value)
        for cell_id, value in cells.items()
    }
    for cell_id, target in parsed.items():
        if target.status != "verified":
            continue
        if not (str(target.location or "").strip() or str(target.geo_id or "").strip()):
            raise ValueError(
                "verified geography target requires location or geoId: "
                f"{cell_id}"
            )
        if not str(target.verified_at or "").strip():
            raise ValueError(
                f"verified geography target requires verified_at: {cell_id}"
            )
    owners: dict[str, str] = {}
    for cell_id, target in parsed.items():
        for code in target.country_codes:
            normalized_code = str(code).upper()
            if not re.fullmatch(r"[A-Z]{2}", normalized_code):
                raise ValueError(f"invalid country code in geography mapping: {code}")
            previous = owners.get(normalized_code)
            if previous is not None:
                raise ValueError(
                    f"country code overlap in geography mapping: {normalized_code} "
                    f"belongs to {previous} and {cell_id}"
                )
            owners[normalized_code] = cell_id
    for city, code in normalized_city_country_codes.items():
        if code not in owners:
            raise ValueError(
                f"city country mapping points to unmapped country: {city} -> {code}"
            )
    return LinkedInGeographyMapping(
        parsed,
        version=str(document["version"]),
        normalization_rule_version=normalization_rule_version,
        contamination_formula_version=contamination_formula_version,
        contamination_threshold=contamination_threshold,
        city_country_codes=normalized_city_country_codes,
    )


@dataclass(frozen=True)
class SourceIsolation:
    mode: SourceIsolationMode
    path: Path | None
    # ``api`` isolation is unambiguously an API collection method. Other
    # isolation modes require this explicit classification; None is rejected
    # by the pre-dispatch gate instead of silently becoming ready.
    collection_method: SourceCollectionMethod | None = None
    cdp_url: str | None = None


def _valid_cdp_url(value: object) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and port is not None
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


class ProbeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: str
    cell_id: str
    source_family: str
    query: str
    keywords: str | None = None
    primary_geography: str | None = None
    geography_target: LinkedInGeographyTarget | None = None
    execution_plan: LinkedInExecutionPlan | None = None
    is_synthetic_control: bool = False
    minimum_independent_families: int = Field(default=1, ge=1)


def build_isolated_probe_environment(
    manifest: Mapping[str, Any], *, ambient: Mapping[str, str] | None = None
) -> dict[str, str]:
    root = Path(str(manifest.get("root") or ""))
    if not root.is_absolute():
        raise ValueError("experiment root must be absolute")
    paths = dict(manifest.get("paths") or {})
    isolation = dict(manifest.get("source_isolation") or {})
    linkedin_settings = dict(isolation.get("linkedin") or {})
    linkedin = Path(
        str(
            linkedin_settings.get("shared_profile_path")
            or linkedin_settings.get("path")
            or ""
        )
    )
    required = {
        "experiment.sqlite3": Path(str(paths.get("experiment.sqlite3") or "")),
        "browser-profile": Path(str(paths.get("browser-profile") or "")),
        "cache": Path(str(paths.get("cache") or "")),
        "logs": Path(str(paths.get("logs") or "")),
        "tmp": Path(str(paths.get("tmp") or "")),
        "python": Path(str(dict(manifest.get("python") or {}).get("executable_path") or "")),
    }
    for name, path in required.items():
        if not _inside(str(path), root):
            raise ValueError(f"isolated environment path outside experiment root: {name}")
    for family, settings, profile in (("linkedin", linkedin_settings, linkedin),):
        shared = settings.get("shared_profile_path")
        if not shared:
            mode = str(settings.get("mode") or "")
            allowed_clone = mode == "cloned_profile" and _inside(
                str(profile), BROWSER_PROFILE_ROOT
            )
            if not _inside(str(profile), root) and not allowed_clone:
                raise ValueError(f"isolated environment path outside experiment root: {family}")
            continue
        if profile != SHARED_BROWSER_PROFILES[family]:
            raise ValueError(f"unapproved shared browser profile: {family}")
        backup = Path(str(settings.get("backup_path") or ""))
        if not _inside(str(backup), root) or not backup.is_dir():
            raise ValueError(f"shared profile backup is missing: {family}")

    environment = dict(ambient or {})
    if str(linkedin_settings.get("mode") or "") == "cloned_profile":
        environment["JOB_INTEL_BROWSER_CDP_URL"] = str(
            linkedin_settings.get("cdp_url") or ""
        ).strip()
    else:
        environment.pop("JOB_INTEL_BROWSER_CDP_URL", None)
    browser_profile = required["browser-profile"]
    environment.update(
        {
            "HOME": str(root),
            "JOB_INTEL_DB_PATH": str(required["experiment.sqlite3"]),
            "JOB_INTEL_STATE_DIR": str(root),
            "JOB_INTEL_BROWSER_PROFILE_DIR": str(browser_profile),
            "JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN": str(linkedin),
            "JOB_INTEL_BROWSER_PROFILE_DIR_COMPANY_CAREER": str(
                browser_profile / "company-career"
            ),
            "JOB_INTEL_BROWSER_PYTHON": str(required["python"]),
            "JOB_INTEL_BROWSER_RUNTIME_DIR": str(root / "browser-runtime"),
            "BROWSER_DESKTOP_BASE_DIR": str(root / "browser-runtime"),
            "JOB_INTEL_BROWSER_DIAGNOSTICS_DIR": str(required["logs"]),
            "XDG_CACHE_HOME": str(required["cache"]),
            "PLAYWRIGHT_BROWSERS_PATH": str(required["cache"] / "ms-playwright"),
            "TMPDIR": str(required["tmp"]),
        }
    )
    return environment


class EvidencePackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    query_id: str
    source_family: str
    source_id: str
    raw_content_sha256: str
    raw_reference: str
    capture_version: str
    parser_version: str
    source_version: str
    captured_at: str
    redaction_class: str
    identity_hints: dict[str, str]


class ProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    stage_counts: dict[str, int]
    provisional_labels: dict[str, int]
    source_states: dict[str, SourceState]
    acquisition_outcomes: dict[str, AcquisitionOutcome]
    product_observability_state: dict[str, ProductObservabilityState | None]
    product_observability_reason: dict[str, str | None]
    credited_records_provenance: dict[str, CreditedRecordsProvenance]
    degraded_families: dict[str, tuple[str, ...]]
    blocked_families: dict[str, tuple[str, ...]]
    duplicates: int
    evidence: tuple[EvidencePackage, ...]
    cost: dict[str, float]
    latency_seconds: float
    family_attempts: tuple[dict[str, Any], ...] = ()
    cell_family_attempts: tuple[dict[str, Any], ...] = ()
    geography_summary: dict[str, Any] = Field(default_factory=dict)


def expand_queries(
    contract: SearchContract,
    *,
    role_terms: tuple[str, ...],
    geography_mapping: Mapping[str, LinkedInGeographyTarget] | None = None,
    execution_plan: LinkedInExecutionPlan | None = None,
) -> tuple[ProbeQuery, ...]:
    expanded: list[ProbeQuery] = []
    mapping = geography_mapping or load_linkedin_geography_mapping()
    for lane_id, lane in sorted(contract.lanes.items()):
        for cell_id, cell in sorted(lane.cells.items()):
            for family in sorted(cell.source_families):
                for role in sorted(role_terms):
                    query = f"{role} {cell.primary_geography}".strip()
                    target = mapping.get(cell_id) if family == "linkedin" else None
                    geography_key = (
                        target.canonical_key
                        if target is not None
                        else json.dumps(
                            {"location": cell.primary_geography, "status": "missing"},
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )
                    identity_input = (
                        f"{lane_id}\0{cell_id}\0{family}\0{role}\0{geography_key}"
                        if family == "linkedin"
                        else f"{lane_id}\0{cell_id}\0{family}\0{query}"
                    )
                    digest = hashlib.sha256(
                        identity_input.encode()
                    ).hexdigest()[:20]
                    expanded.append(
                        ProbeQuery(
                            query_id=digest,
                            cell_id=cell_id,
                            source_family=family,
                            query=query,
                            keywords=role if family == "linkedin" else None,
                            primary_geography=(
                                cell.primary_geography if family == "linkedin" else None
                            ),
                            geography_target=target,
                            execution_plan=execution_plan if family == "linkedin" else None,
                            minimum_independent_families=cell.minimum_independent_families,
                        )
                    )
    return tuple(sorted(expanded, key=lambda item: item.query_id))


def build_snapshot_queries() -> tuple[ProbeQuery, ...]:
    from .acquisition_plugins.ats_snapshot import ATS_FAMILIES

    query = (
        "Chief Product Officer OR VP Product OR Head of Product OR GM Digital "
        "OR Product Director"
    )
    return tuple(
        ProbeQuery(
            query_id=hashlib.sha256(f"ats_global_snapshot\0{family}\0{query}".encode()).hexdigest()[:20],
            cell_id="ats_global_snapshot",
            source_family=family,
            query=query,
        )
        for family in ATS_FAMILIES
    )


def validate_probe_output_path(path: Path) -> None:
    resolved = path.resolve()
    gate_root = GATE_A_EXPERIMENT_ROOT.resolve()
    if (
        resolved.parent == gate_root
        and len(resolved.name) == 40
        and all(character in "0123456789abcdef" for character in resolved.name)
    ):
        return
    for forbidden in FORBIDDEN_PROBE_ROOTS:
        forbidden_resolved = forbidden.resolve()
        if resolved == forbidden_resolved or forbidden_resolved in resolved.parents:
            raise ValueError(f"forbidden probe path: {resolved}")


def _ensure_safe_output(path: Path) -> None:
    validate_probe_output_path(path)


def _ensure_slack_blind(environment: Mapping[str, str]) -> None:
    present = sorted(name for name in SLACK_CREDENTIAL_NAMES if environment.get(name))
    if present:
        raise ValueError(f"Slack credentials are forbidden in acquisition probe: {', '.join(present)}")


def _record_source_state(
    states: dict[str, SourceState], family: str, state: SourceState
) -> None:
    previous = states.get(family)
    if previous is None or previous == state:
        states[family] = state
    elif previous.startswith("observed") or state == "observed":
        states[family] = "observed_with_failures"
    else:
        states[family] = "blocked_multiple_failures"


def _canonical_url(raw: str) -> str:
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
    return urlunsplit(
        (
            split.scheme.casefold(),
            split.netloc.casefold(),
            path,
            urlencode(filtered),
            "",
        )
    )


GeographyNormalizationSource = Literal["structured_field", "parsed_content", "unresolved"]
RemoteScope = Literal["none", "country_remote", "location_independent"]


class GeographyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_location_text: str | None
    mentioned_countries: tuple[str, ...]
    primary_country: str | None
    normalization_source: GeographyNormalizationSource
    normalization_rule_version: str
    mapping_version: str
    remote_scope: RemoteScope

    @model_validator(mode="after")
    def _validate_primary_country(self) -> "GeographyEvidence":
        if self.normalization_source == "unresolved" and self.primary_country is not None:
            raise ValueError("unresolved geography cannot have a primary country")
        if len(set(self.mentioned_countries)) != len(self.mentioned_countries):
            raise ValueError("mentioned countries must be unique")
        if self.primary_country is not None and self.primary_country not in self.mentioned_countries:
            raise ValueError("primary country must be among mentioned countries")
        if self.remote_scope == "location_independent" and (
            self.primary_country is not None or self.mentioned_countries
        ):
            raise ValueError("location-independent geography must have no country")
        return self


_COUNTRY_ALIASES: tuple[tuple[str, str], ...] = (
    ("united arab emirates", "AE"),
    ("united kingdom", "GB"),
    ("united states", "US"),
    ("south korea", "KR"),
    ("new zealand", "NZ"),
    ("australia", "AU"),
    ("austria", "AT"),
    ("bahrain", "BH"),
    ("belgium", "BE"),
    ("canada", "CA"),
    ("china", "CN"),
    ("denmark", "DK"),
    ("finland", "FI"),
    ("france", "FR"),
    ("germany", "DE"),
    ("india", "IN"),
    ("italy", "IT"),
    ("japan", "JP"),
    ("kazakhstan", "KZ"),
    ("kyrgyzstan", "KG"),
    ("kuwait", "KW"),
    ("luxembourg", "LU"),
    ("netherlands", "NL"),
    ("new zealand", "NZ"),
    ("norway", "NO"),
    ("oman", "OM"),
    ("poland", "PL"),
    ("qatar", "QA"),
    ("saudi arabia", "SA"),
    ("singapore", "SG"),
    ("south africa", "ZA"),
    ("south korea", "KR"),
    ("spain", "ES"),
    ("sweden", "SE"),
    ("switzerland", "CH"),
    ("tajikistan", "TJ"),
    ("turkmenistan", "TM"),
    ("ukraine", "UA"),
    ("united states", "US"),
    ("uzbekistan", "UZ"),
    ("vietnam", "VN"),
    ("uk", "GB"),
    ("uae", "AE"),
    ("usa", "US"),
)
_CITY_COUNTRY_ALIASES: dict[str, str] = {}


def normalize_geography_evidence(
    raw_location_text: str | None,
    *,
    mapping_version: str = GEOGRAPHY_MAPPING_VERSION,
    city_country_codes: Mapping[str, str] | None = None,
) -> GeographyEvidence:
    raw = None if raw_location_text is None else str(raw_location_text).strip()
    text = raw or ""
    if not text or text.casefold() == "unknown":
        return GeographyEvidence(
            raw_location_text=raw,
            mentioned_countries=(),
            primary_country=None,
            normalization_source="unresolved",
            normalization_rule_version=GEOGRAPHY_NORMALIZATION_RULE_VERSION,
            mapping_version=mapping_version,
            remote_scope="none",
        )

    found: dict[str, int] = {}
    folded = text.casefold()
    for alias, code in sorted(_COUNTRY_ALIASES, key=lambda item: len(item[0]), reverse=True):
        match = re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", folded)
        if match is not None:
            found.setdefault(code, match.start())
    for match in re.finditer(r"(?<![A-Za-z])([A-Z]{2})(?![A-Za-z])", text):
        found.setdefault(match.group(1), match.start())
    if not found:
        aliases = dict(_CITY_COUNTRY_ALIASES)
        aliases.update(
            {str(city).casefold(): str(code).upper() for city, code in (city_country_codes or {}).items()}
        )
        city_token = folded.split(",", 1)[0].strip()
        for city, code in aliases.items():
            if city_token == city:
                found[code] = 0

    mentioned = tuple(code for code, _ in sorted(found.items(), key=lambda item: item[1]))
    primary = mentioned[0] if len(mentioned) == 1 else None
    if len(mentioned) > 1:
        primary = None
    remote = bool(re.search(r"\bremote\b", folded))
    remote_scope: RemoteScope = (
        "country_remote" if remote and primary is not None
        else "location_independent" if remote and not mentioned
        else "none"
    )
    return GeographyEvidence(
        raw_location_text=raw,
        mentioned_countries=mentioned,
        primary_country=primary,
        normalization_source="structured_field",
        normalization_rule_version=GEOGRAPHY_NORMALIZATION_RULE_VERSION,
        mapping_version=mapping_version,
        remote_scope=remote_scope,
    )


def _mapping_metadata(mapping: Mapping[str, Any]) -> tuple[str, str, str, float]:
    return (
        str(getattr(mapping, "version", GEOGRAPHY_MAPPING_VERSION)),
        str(
            getattr(
                mapping,
                "normalization_rule_version",
                GEOGRAPHY_NORMALIZATION_RULE_VERSION,
            )
        ),
        str(
            getattr(
                mapping,
                "contamination_formula_version",
                GEOGRAPHY_CONTAMINATION_FORMULA_VERSION,
            )
        ),
        float(
            getattr(
                mapping,
                "contamination_threshold",
                GEOGRAPHY_CONTAMINATION_THRESHOLD,
            )
        ),
    )


def _mapping_city_country_codes(mapping: Mapping[str, Any]) -> Mapping[str, str]:
    return getattr(mapping, "city_country_codes", {})


def _mapping_country_codes(target: Any) -> tuple[str, ...]:
    if isinstance(target, LinkedInGeographyTarget):
        return tuple(target.country_codes)
    if isinstance(target, Mapping):
        return tuple(str(code).upper() for code in target.get("country_codes", ()))
    raise TypeError(f"unsupported geography mapping target: {type(target).__name__}")


def _validate_mapping_country_ownership(mapping: Mapping[str, Any]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for cell_id, target in mapping.items():
        for code in _mapping_country_codes(target):
            if not re.fullmatch(r"[A-Z]{2}", code):
                raise ValueError(f"invalid country code in geography mapping: {code}")
            previous = owners.get(code)
            if previous is not None:
                raise ValueError(
                    f"country code overlap in geography mapping: {code} "
                    f"belongs to {previous} and {cell_id}"
                )
            owners[code] = str(cell_id)
    return owners


def build_geography_summary(
    records: Iterable[Mapping[str, Any]],
    mapping: Mapping[str, Any],
    *,
    manifest_versions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    mapping_version, normalization_version, formula_version, threshold = _mapping_metadata(mapping)
    expected = {
        "mapping_version": mapping_version,
        "normalization_rule_version": normalization_version,
        "contamination_formula_version": formula_version,
        "contamination_threshold": threshold,
    }
    for name, value in dict(manifest_versions or {}).items():
        if name in expected and value != expected[name]:
            label = name.replace("_", " ")
            raise ValueError(f"{label} does not match geography mapping contract")
    owners = _validate_mapping_country_ownership(mapping)
    cells: dict[str, dict[str, Any]] = {
        str(cell_id): {
            "received": set(),
            "credited": set(),
            "rejected_country_mismatch": 0,
            "geography_unknown": 0,
        }
        for cell_id in mapping
    }
    evidence_by_identity: dict[str, GeographyEvidence] = {}
    evidence_by_source_id: dict[str, dict[str, Any]] = {}
    received_seen: set[tuple[str, str]] = set()
    credited_identity_owners: dict[str, str] = {}

    for raw_record in records:
        record = dict(raw_record)
        query_cell = str(record.get("cell_id") or "")
        if query_cell == "ats_global_snapshot":
            # This seeded snapshot is intentionally outside the geographic
            # denominator; it is a source-breadth control, not a lane.
            continue
        cells.setdefault(
            query_cell,
            {
                "received": set(),
                "credited": set(),
                "rejected_country_mismatch": 0,
                "geography_unknown": 0,
            },
        )
        identity = _canonical_url(str(record.get("url") or ""))
        if not identity:
            identity = hashlib.sha256(
                f"{record.get('company')}\0{record.get('title')}".encode()
            ).hexdigest()
        geography = normalize_geography_evidence(
            record.get("location"),
            mapping_version=mapping_version,
            city_country_codes=_mapping_city_country_codes(mapping),
        )
        previous = evidence_by_identity.get(identity)
        if previous is not None and previous != geography:
            raise ValueError(f"conflicting geography evidence for {identity}")
        evidence_by_identity[identity] = geography
        source_id = str(record.get("source_id") or identity)
        evidence_by_source_id[source_id] = geography.model_dump(mode="json")
        if (query_cell, identity) in received_seen:
            continue
        received_seen.add((query_cell, identity))
        cells[query_cell]["received"].add(identity)

        owner: str | None = None
        if geography.remote_scope == "location_independent":
            if "genuinely_location_independent" in cells:
                owner = "genuinely_location_independent"
        elif geography.primary_country is None:
            cells[query_cell]["geography_unknown"] += 1
        else:
            owner = owners.get(geography.primary_country)
        if owner is None:
            if geography.primary_country is not None:
                cells[query_cell]["rejected_country_mismatch"] += 1
        elif owner != query_cell:
            cells[query_cell]["rejected_country_mismatch"] += 1
        if owner is not None:
            existing_owner = credited_identity_owners.get(identity)
            if existing_owner is not None and existing_owner != owner:
                raise ValueError(f"geography identity credited to multiple cells: {identity}")
            credited_identity_owners[identity] = owner
            cells[owner]["credited"].add(identity)

    cell_ids = sorted(cells)
    pairwise: dict[str, dict[str, Any]] = {}
    for index, left in enumerate(cell_ids):
        for right in cell_ids[index + 1 :]:
            left_ids = {
                identity
                for identity in cells[left]["received"]
                if len(evidence_by_identity[identity].mentioned_countries) <= 1
            }
            right_ids = {
                identity
                for identity in cells[right]["received"]
                if len(evidence_by_identity[identity].mentioned_countries) <= 1
            }
            union = left_ids | right_ids
            jaccard = len(left_ids & right_ids) / len(union) if union else 0.0
            pairwise[f"{left}|{right}"] = {
                "jaccard": jaccard,
                "contamination_suspected": jaccard >= threshold,
            }

    return {
        "mapping_version": mapping_version,
        "normalization_rule_version": normalization_version,
        "contamination_formula_version": formula_version,
        "contamination_threshold": threshold,
        "records": dict(sorted(evidence_by_source_id.items())),
        "cells": {
            cell_id: {
                "received": sorted(cells[cell_id]["received"]),
                "credited": sorted(cells[cell_id]["credited"]),
                "rejected_country_mismatch": cells[cell_id]["rejected_country_mismatch"],
                "geography_unknown": cells[cell_id]["geography_unknown"],
            }
            for cell_id in cell_ids
        },
        "credited_identity_owners": dict(sorted(credited_identity_owners.items())),
        "pairwise": pairwise,
    }


def _as_mapping(record: Any) -> dict[str, Any]:
    if isinstance(record, Mapping):
        return dict(record)
    if hasattr(record, "__dict__"):
        return dict(record.__dict__)
    raise TypeError(f"unsupported source record: {type(record).__name__}")


def _minimum_evidence_sufficient(record: Mapping[str, Any]) -> bool:
    return all(str(record.get(field) or "").strip() for field in ("url", "title", "company", "description"))


def run_probe(
    *,
    run_id: str,
    queries: Iterable[ProbeQuery | Mapping[str, Any]],
    sources: Mapping[str, Callable[[Any], Iterable[Any]]],
    output_dir: Path | str,
    runtime_capability_checks: Mapping[str, Callable[[], Any]] | None = None,
    isolation: Mapping[str, SourceIsolation],
    minimum_independent_families_by_cell: Mapping[str, int] | None = None,
    credited_records_by_cell: Mapping[str, int] | None = None,
    geography_mapping: Mapping[str, Any] | None = None,
    max_attempts: int = 2,
    environment: Mapping[str, str] | None = None,
    now: Callable[[], datetime] | None = None,
) -> ProbeResult:
    started = time.monotonic()
    checks = dict(runtime_capability_checks or {})
    capability_by_family: dict[str, RuntimeCapabilityResult] = {}
    market_dispatch_counts: dict[str, int] = {}

    def _blocked(reason: str, detail: str, traffic: int = 0) -> RuntimeCapabilityResult:
        return RuntimeCapabilityResult(
            state="runtime_capability_blocked",
            error_class="runtime_capability",
            error_fingerprint=reason,
            error_message_truncated=detail[:512] or reason,
            bootstrap_traffic_events=traffic,
        )

    def _capability(
        family: str, source_isolation: SourceIsolation
    ) -> RuntimeCapabilityResult:
        """Answer once per family per run.

        Once per family, not once per query: the live contract issues 112
        LinkedIn queries, and a cold browser start for each of them is not a
        contract anyone would run.
        """
        cached = capability_by_family.get(family)
        if cached is not None:
            return cached

        collection_method = source_isolation.collection_method
        if collection_method is None and source_isolation.mode == "api":
            collection_method = "api"
        if source_isolation.mode == "cloned_profile" and source_isolation.cdp_url == "":
            result = _blocked(
                "missing_clone_cdp_url",
                f"cloned profile has no cdp_url for {family}",
            )
        elif (
            source_isolation.mode == "cloned_profile"
            and source_isolation.cdp_url is not None
            and not _valid_cdp_url(source_isolation.cdp_url)
        ):
            result = _blocked(
                "invalid_clone_cdp_url",
                f"cloned profile has invalid cdp_url for {family}",
            )
        elif collection_method not in {"browser", "api"}:
            result = _blocked(
                "unclassified_collection_method",
                f"no collection method for {family}",
            )
        elif collection_method == "api":
            result = RuntimeCapabilityResult(state="not_applicable")
        else:
            checker = checks.get(family)
            if checker is None:
                result = _blocked(
                    "no_capability_check", f"no capability check for {family}"
                )
            else:
                try:
                    raw = checker()
                    result = (
                        raw
                        if isinstance(raw, RuntimeCapabilityResult)
                        else RuntimeCapabilityResult.model_validate(raw)
                    )
                except Exception as exc:  # noqa: BLE001 - any failure is a refusal
                    # The preflight itself can fail. That must not abort the run and
                    # must not fall through to the extraction path, which would
                    # blame the source for a runtime problem.
                    result = _blocked(
                        "capability_check_failed", f"{type(exc).__name__}: {exc}"
                    )

        if collection_method == "browser" and result.state == "not_applicable":
            # For a browser family this answer is a contradiction, and the safe
            # reading of a contradiction is refusal.
            result = _blocked(
                "not_applicable_for_browser_family",
                f"{family} needs a browser runtime, capability reported not_applicable",
                result.bootstrap_traffic_events,
            )

        capability_by_family[family] = result
        return result

    output = Path(output_dir)
    _ensure_safe_output(output)
    _ensure_slack_blind(environment or {})
    if max_attempts < 1 or max_attempts > 3:
        raise ValueError("max_attempts must be between 1 and 3")
    clock = now or (lambda: datetime.now(timezone.utc))
    raw_dir = output / "raw-evidence"
    raw_dir.mkdir(parents=True, exist_ok=True)

    evidence: list[EvidencePackage] = []
    observations: list[dict[str, Any]] = []
    source_states: dict[str, SourceState] = {}
    cell_attempts: dict[str, list[str]] = {}
    cell_minimums: dict[str, int] = dict(minimum_independent_families_by_cell or {})
    pair_attempts: dict[tuple[str, str], dict[str, Any]] = {}

    def _ensure_pair(query: ProbeQuery) -> dict[str, Any]:
        key = (query.cell_id, query.source_family)
        record = pair_attempts.setdefault(
            key,
            {
                "cell_id": query.cell_id,
                "source_family": query.source_family,
                "timestamp": clock().isoformat(),
                "outcome": "not_attempted",
                "session_observation": (
                    "not_observed" if query.source_family == "linkedin" else "not_applicable"
                ),
                "extraction_counts": None,
                "extraction_artifact_references": [],
                "received_records": 0,
                "credited_records": None,
                "credited_records_status": "undetermined",
                "credited_records_reason": "b2_attribution_unavailable",
                "artifact_references": [],
            },
        )
        configured = cell_minimums.get(query.cell_id)
        expected = (
            query.minimum_independent_families
            if configured is None
            else configured
        )
        if (
            configured is not None
            and query.minimum_independent_families != 1
            and configured != query.minimum_independent_families
        ):
            raise ValueError(
                f"inconsistent minimum independent families for {query.cell_id}"
            )
        cell_minimums[query.cell_id] = expected
        return record

    def _set_pair_outcome(query: ProbeQuery, outcome: AcquisitionOutcome) -> None:
        record = _ensure_pair(query)
        current = record["outcome"]
        if current == "not_attempted" or current == outcome:
            record["outcome"] = outcome
        elif "degraded" in {current, outcome}:
            record["outcome"] = "degraded"
        elif "blocked" in {current, outcome}:
            record["outcome"] = "blocked"
        else:
            record["outcome"] = "degraded"

    def _capture_source_evidence(query: ProbeQuery, source: Any) -> None:
        pair = _ensure_pair(query)
        trace = getattr(source, "last_trace", None)
        health = getattr(source, "last_health", None)
        if isinstance(trace, Mapping):
            observation = trace.get("session_observation")
            if observation in {"with_session", "without_session", "not_observed"}:
                pair["session_observation"] = observation
            counts = trace.get("extraction_counts")
            if isinstance(counts, Mapping):
                pair["extraction_counts"] = dict(counts)
            pages = trace.get("pages")
            if isinstance(pages, list):
                pair["extraction_artifact_references"] = [
                    str(page["artifact_ref"])
                    for page in pages
                    if isinstance(page, Mapping) and page.get("artifact_ref")
                ]
        if (
            query.source_family == "linkedin"
            and pair["session_observation"] == "not_observed"
            and isinstance(health, Mapping)
            and health.get("session_state") == "session_ok"
        ):
            pair["session_observation"] = "with_session"

    def _record_attempt_state(query: ProbeQuery, state: SourceState) -> None:
        if not query.is_synthetic_control:
            _record_source_state(source_states, query.source_family, state)

    for raw_query in queries:
        query = raw_query if isinstance(raw_query, ProbeQuery) else ProbeQuery.model_validate(raw_query)
        cell_attempts.setdefault(query.cell_id, []).append(query.source_family)
        _ensure_pair(query)
        source = sources.get(query.source_family)
        source_isolation = isolation.get(query.source_family)
        if source_isolation is None or source_isolation.mode not in {"cloned_profile", "exclusive_lock", "api"}:
            _record_attempt_state(query, "blocked_no_safe_isolation")
            _set_pair_outcome(query, "blocked")
            continue
        if source is None:
            _record_attempt_state(query, "blocked_missing_public_interface")
            _set_pair_outcome(query, "blocked")
            continue

        if query.source_family == "linkedin" and query.primary_geography is not None:
            target = query.geography_target
            if target is None or target.status != "verified" or not (
                target.location or target.geo_id
            ):
                _record_attempt_state(query, "blocked_unsupported_geography")
                _set_pair_outcome(query, "blocked")
                continue

        capability = _capability(query.source_family, source_isolation)
        if capability.state == "runtime_capability_blocked":
            _record_attempt_state(query, "runtime_capability_blocked")
            _set_pair_outcome(query, "blocked")
            continue

        records: list[Any] | None = None
        market_dispatch_counts[query.source_family] = (
            market_dispatch_counts.get(query.source_family, 0) + 1
        )
        for attempt in range(1, max_attempts + 1):
            try:
                request: Any = query
                if query.source_family != "linkedin" or query.primary_geography is None:
                    request = query.query
                records = list(source(request))
                _capture_source_evidence(query, source)
                state = (
                    "observed_with_failures"
                    if tuple(getattr(source, "last_errors", ()) or ())
                    else "observed"
                )
                _record_attempt_state(query, state)
                _set_pair_outcome(
                    query,
                    "degraded" if state == "observed_with_failures" else "completed",
                )
                break
            except ProbeSourceBlocked as exc:
                _capture_source_evidence(query, source)
                _record_attempt_state(query, f"blocked_{exc.reason}")
                _set_pair_outcome(query, "blocked")
                if attempt == max_attempts:
                    records = None
            except TimeoutError:
                _capture_source_evidence(query, source)
                _record_attempt_state(query, "blocked_rate_limit_or_timeout")
                _set_pair_outcome(query, "blocked")
                if attempt == max_attempts:
                    records = None
            except Exception:
                _capture_source_evidence(query, source)
                _record_attempt_state(query, "blocked_extraction_failure")
                _set_pair_outcome(query, "blocked")
                if attempt == max_attempts:
                    records = None
        if records is None:
            continue

        for raw_record in records:
            record = _as_mapping(raw_record)
            canonical = _canonical_url(str(record.get("url") or ""))
            captured_at = str(record.get("captured_at") or clock().isoformat())
            normalized = dict(record)
            normalized["canonical_url"] = canonical
            normalized["query_id"] = query.query_id
            normalized["cell_id"] = query.cell_id
            normalized["source_family"] = query.source_family
            if geography_mapping is not None:
                normalized["geography_evidence"] = normalize_geography_evidence(
                    record.get("location"),
                    mapping_version=_mapping_metadata(geography_mapping)[0],
                    city_country_codes=_mapping_city_country_codes(geography_mapping),
                ).model_dump(mode="json")
            raw_bytes = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str).encode()
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            relative = Path("raw-evidence") / f"{content_hash}.json"
            target = output / relative
            if not target.exists():
                target.write_bytes(raw_bytes)
            pair_record = _ensure_pair(query)
            pair_record["received_records"] += 1
            pair_record["artifact_references"].append(relative.as_posix())
            source_id = str(record.get("source_id") or content_hash[:20])
            evidence.append(
                EvidencePackage(
                    run_id=run_id,
                    query_id=query.query_id,
                    source_family=query.source_family,
                    source_id=source_id,
                    raw_content_sha256=content_hash,
                    raw_reference=relative.as_posix(),
                    capture_version="product-search-probe-v1",
                    parser_version="existing-public-interface",
                    source_version="pinned-runtime-commit",
                    captured_at=captured_at,
                    redaction_class="vacancy_public_evidence",
                    identity_hints={
                        "canonical_url": canonical,
                        "company": str(record.get("company") or ""),
                        "title": str(record.get("title") or ""),
                    },
                )
            )
            observations.append(normalized)

    canonical_records: dict[str, dict[str, Any]] = {}
    for record in observations:
        identity = str(record.get("canonical_url") or "") or hashlib.sha256(
            f"{record.get('company')}\0{record.get('title')}".encode()
        ).hexdigest()
        canonical_records.setdefault(identity, record)

    labels: dict[str, int] = {}
    sufficient = 0
    for record in canonical_records.values():
        if record.get("known_hard_block"):
            label = "known_hard_block"
        elif _minimum_evidence_sufficient(record):
            sufficient += 1
            label = "provisionally_eligible"
        else:
            label = "unresolved_for_decision_v2"
        labels[label] = labels.get(label, 0) + 1

    geography_summary = (
        build_geography_summary(observations, geography_mapping)
        if geography_mapping is not None
        else {}
    )
    attributed_credited_by_cell = {
        cell_id: len(details["credited"])
        for cell_id, details in geography_summary.get("cells", {}).items()
    }
    credited_identities_by_pair: dict[tuple[str, str], set[str]] = {
        key: set() for key in pair_attempts
    }
    credited_counts_by_pair: dict[tuple[str, str], int] = {}
    credited_unknown_reasons_by_pair: dict[tuple[str, str], str] = {
        key: "b2_attribution_unavailable" for key in pair_attempts
    }
    if geography_mapping is not None:
        credited_identity_owners = geography_summary.get("credited_identity_owners", {})
        credited_identity_pairs: dict[str, set[tuple[str, str]]] = {}
        for observed in observations:
            identity = str(observed.get("canonical_url") or "")
            pair_key = (
                str(observed.get("cell_id") or ""),
                str(observed.get("source_family") or ""),
            )
            if identity and credited_identity_owners.get(identity) == pair_key[0]:
                credited_identity_pairs.setdefault(identity, set()).add(pair_key)
        for identity, pair_keys in credited_identity_pairs.items():
            credited_identities_by_pair[sorted(pair_keys)[0]].add(identity)
        credited_counts_by_pair = {
            key: len(credited_identities_by_pair[key]) for key in pair_attempts
        }
    else:
        pair_keys_by_cell: dict[str, list[tuple[str, str]]] = {}
        for key in pair_attempts:
            pair_keys_by_cell.setdefault(key[0], []).append(key)
        for cell_id, keys in pair_keys_by_cell.items():
            if len(keys) == 1 and cell_id in (credited_records_by_cell or {}):
                credited_counts_by_pair[keys[0]] = int(
                    (credited_records_by_cell or {})[cell_id]
                )
            elif len(keys) > 1 and cell_id in (credited_records_by_cell or {}):
                for key in keys:
                    credited_unknown_reasons_by_pair[key] = (
                        "cell_level_credit_not_attributable_to_multiple_pairs"
                    )

    acquisition_outcomes: dict[str, AcquisitionOutcome] = {}
    product_observability_state: dict[str, ProductObservabilityState | None] = {}
    product_observability_reason: dict[str, str | None] = {}
    credited_records_provenance: dict[str, CreditedRecordsProvenance] = {}
    degraded_families: dict[str, tuple[str, ...]] = {}
    blocked_families: dict[str, tuple[str, ...]] = {}
    for cell_id in cell_attempts:
        pair_records = [
            record
            for (record_cell_id, _), record in pair_attempts.items()
            if record_cell_id == cell_id
        ]
        counts = {
            outcome: sum(record["outcome"] == outcome for record in pair_records)
            for outcome in ("completed", "blocked", "degraded")
        }
        pair_record_by_key = {
            (record["cell_id"], record["source_family"]): record
            for record in pair_records
        }
        for pair_key, pair_record in pair_record_by_key.items():
            if pair_key in credited_counts_by_pair:
                pair_record["credited_records"] = credited_counts_by_pair[pair_key]
                pair_record["credited_records_status"] = "determined"
                pair_record["credited_records_reason"] = None
            else:
                pair_record["credited_records"] = None
                pair_record["credited_records_status"] = "undetermined"
                pair_record["credited_records_reason"] = (
                    credited_unknown_reasons_by_pair[pair_key]
                )
        cell_records = [
            record for record in canonical_records.values()
            if record.get("cell_id") == cell_id
        ]
        credited_by_cell = (
            attributed_credited_by_cell
            if geography_mapping is not None
            else dict(credited_records_by_cell or {})
        )
        if geography_mapping is not None:
            credited = credited_by_cell.get(cell_id, 0)
            credited_records_provenance[cell_id] = "attributed"
        elif cell_id in credited_by_cell:
            credited = credited_by_cell[cell_id]
            credited_records_provenance[cell_id] = "caller_supplied"
        else:
            credited = len(cell_records)
            credited_records_provenance[cell_id] = "received_rows_fallback"
        decision = resolve_acquisition_outcome(
            completed=counts["completed"],
            blocked=counts["blocked"],
            degraded=counts["degraded"],
            minimum_independent_families=cell_minimums[cell_id],
            credited=credited,
        )
        acquisition_outcomes[cell_id] = decision.acquisition_outcome
        product_observability_state[cell_id] = decision.product_observability_state
        product_observability_reason[cell_id] = decision.product_observability_reason
        degraded_families[cell_id] = tuple(
            sorted(
                record["source_family"]
                for record in pair_records
                if record["outcome"] == "degraded"
            )
        )
        blocked_families[cell_id] = tuple(
            sorted(
                record["source_family"]
                for record in pair_records
                if record["outcome"] == "blocked"
            )
        )

    result = ProbeResult(
        run_id=run_id,
        stage_counts={
            "raw_observed": len(observations),
            "canonical_current": len(canonical_records),
            "minimum_evidence_sufficient": sufficient,
        },
        provisional_labels=dict(sorted(labels.items())),
        source_states=dict(sorted(source_states.items())),
        acquisition_outcomes=dict(sorted(acquisition_outcomes.items())),
        product_observability_state=dict(sorted(product_observability_state.items())),
        product_observability_reason=dict(sorted(product_observability_reason.items())),
        credited_records_provenance=dict(sorted(credited_records_provenance.items())),
        degraded_families=dict(sorted(degraded_families.items())),
        blocked_families=dict(sorted(blocked_families.items())),
        duplicates=len(observations) - len(canonical_records),
        evidence=tuple(evidence),
        cost={
            "provider_cost_usd": 0.0,
            "market_query_dispatch_count": float(sum(market_dispatch_counts.values())),
        },
        latency_seconds=round(time.monotonic() - started, 6),
        family_attempts=tuple(
            {
                "source_family": family,
                "capability_state": capability.state,
                "outcome": (
                    "runtime_capability_blocked"
                    if capability.state == "runtime_capability_blocked"
                    else source_states.get(family, "not_attempted")
                ),
                "error_class": capability.error_class,
                "error_fingerprint": capability.error_fingerprint,
                "error_message_truncated": capability.error_message_truncated,
                "market_query_dispatch_count": market_dispatch_counts.get(family, 0),
                "bootstrap_traffic_events": capability.bootstrap_traffic_events,
            }
            for family, capability in sorted(capability_by_family.items())
        ),
        cell_family_attempts=tuple(
            pair_attempts[key]
            for key in sorted(pair_attempts)
        ),
        geography_summary=geography_summary,
    )
    (output / "summary.json").write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )
    with sqlite3.connect(output / "experiment.sqlite3") as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS probe_runs (
                run_id TEXT PRIMARY KEY,
                summary_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS probe_evidence (
                run_id TEXT NOT NULL,
                raw_content_sha256 TEXT NOT NULL,
                query_id TEXT NOT NULL,
                source_family TEXT NOT NULL,
                source_id TEXT NOT NULL,
                raw_reference TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                redaction_class TEXT NOT NULL,
                PRIMARY KEY (run_id, raw_content_sha256, query_id, source_id),
                FOREIGN KEY (run_id) REFERENCES probe_runs(run_id)
            );
            """
        )
        conn.execute(
            "INSERT INTO probe_runs (run_id, summary_json) VALUES (?, ?)",
            (run_id, result.model_dump_json()),
        )
        conn.executemany(
            """
            INSERT INTO probe_evidence (
                run_id, raw_content_sha256, query_id, source_family, source_id,
                raw_reference, captured_at, redaction_class
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.run_id,
                    item.raw_content_sha256,
                    item.query_id,
                    item.source_family,
                    item.source_id,
                    item.raw_reference,
                    item.captured_at,
                    item.redaction_class,
                )
                for item in evidence
            ],
        )
    return result


def resolve_runtime_capability_checks(
    isolation: Mapping[str, SourceIsolation],
) -> dict[str, Callable[[], RuntimeCapabilityResult]]:
    from job_intel import browser_worker

    checks: dict[str, Callable[[], RuntimeCapabilityResult]] = {}
    for family, settings in isolation.items():
        if settings.collection_method != "browser":
            continue
        profile = settings.path if settings.mode == "cloned_profile" else None

        def check(
            source_family: str = family,
            source_settings: SourceIsolation = settings,
            source_profile: Path | None = profile,
        ) -> RuntimeCapabilityResult:
            browser_worker._ensure_browser_desktop(
                source_family,
                cdp_url=source_settings.cdp_url,
                profile=source_profile,
            )
            return RuntimeCapabilityResult(state="ready")

        checks[family] = check
    return checks


def resolve_public_sources() -> dict[str, Callable[[Any], Iterable[Any]]]:
    from job_intel.sources import (
        fetch_headhunter_vacancies,
        fetch_linkedin_vacancies,
        normalize_search_hit,
        search_duckduckgo,
        search_remoteok_jobs,
        search_remotive_jobs,
    )

    def _publish_linkedin_metadata() -> None:
        _linkedin_source.last_trace = getattr(fetch_linkedin_vacancies, "last_trace", None)
        _linkedin_source.last_health = getattr(fetch_linkedin_vacancies, "last_health", None)

    def _linkedin_source(request: Any) -> Iterable[Any]:
        try:
            if isinstance(request, ProbeQuery):
                target = request.geography_target
                return fetch_linkedin_vacancies(
                    request.keywords or request.query,
                    location=target.location if target is not None else None,
                    geo_id=target.geo_id if target is not None else None,
                    execution_plan=(
                        request.execution_plan.model_dump(mode="json")
                        if request.execution_plan is not None
                        else None
                    ),
                    max_pages=2,
                    allow_unauthenticated=True,
                )
            return fetch_linkedin_vacancies(str(request), max_pages=2)
        finally:
            _publish_linkedin_metadata()

    sources: dict[str, Callable[[Any], Iterable[Any]]] = {
        "linkedin": _linkedin_source,
        "headhunter": lambda query: fetch_headhunter_vacancies(query, per_page=10),
        "duckduckgo": lambda query: [
            normalize_search_hit(hit) for hit in search_duckduckgo(query, max_results=10)
        ],
        "remoteok": lambda _query: search_remoteok_jobs(max_results=25),
        "remotive": lambda _query: search_remotive_jobs(max_results=25),
    }
    from .acquisition_plugins.ats_snapshot import build_ats_snapshot_sources

    registry = Path(__file__).resolve().parents[2] / "docs/company-registry-seed.yaml"
    sources.update(build_ats_snapshot_sources(registry))
    return sources


def _inside(path: str, root: Path) -> bool:
    candidate = Path(path).resolve()
    resolved_root = root.resolve()
    return candidate == resolved_root or resolved_root in candidate.parents


def validate_experiment_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("gate") != "gate-a" or manifest.get("environment_id") != "product-search-gate-a":
        raise ValueError("wrong gate or environment identity")
    exclusion_catalog = dict(manifest.get("exclusion_reason_codes") or {})
    if exclusion_catalog.get("version") != EXCLUSION_REASON_CATALOG.version:
        raise ValueError("manifest exclusion reason catalog version is not current")
    if exclusion_catalog.get("sha256") != EXCLUSION_REASON_CATALOG.sha256:
        raise ValueError("manifest exclusion reason catalog hash is not current")
    root = Path(str(manifest.get("root") or ""))
    if not root.is_absolute():
        raise ValueError("experiment root must be absolute")
    for name, value in dict(manifest.get("paths") or {}).items():
        if not _inside(str(value), root):
            raise ValueError(f"path outside experiment root: {name}")
    python = dict(manifest.get("python") or {})
    if not _inside(str(python.get("executable_path") or ""), root / "python-runtime"):
        raise ValueError("Python executable must be experiment-local")
    if not _inside(str(manifest.get("environment", {}).get("import_root") or ""), root / "runtime"):
        raise ValueError("import root must be experiment-local")
    editable = list(manifest.get("environment", {}).get("editable_installs") or [])
    if editable:
        raise ValueError("editable installs are forbidden")
    for family, settings in dict(manifest.get("source_isolation") or {}).items():
        settings_dict = dict(settings)
        mode = str(settings_dict.get("mode") or "")
        path = str(settings_dict.get("path") or "")
        collection_method = str(settings_dict.get("collection_method") or "")
        if collection_method not in {"browser", "api"}:
            raise ValueError(f"invalid source collection method: {family}")
        if mode == "api" and collection_method != "api":
            raise ValueError(f"API isolation must use API collection method: {family}")
        if mode == "api":
            if family != "headhunter":
                raise ValueError(f"API source isolation is not supported for: {family}")
            continue
        if mode not in {"cloned_profile", "exclusive_lock"}:
            raise ValueError(f"invalid source isolation mode: {family}")
        shared_profile = dict(settings).get("shared_profile_path")
        if mode == "cloned_profile":
            if shared_profile:
                raise ValueError(f"cloned profile cannot use shared profile: {family}")
            if Path(path) == SHARED_BROWSER_PROFILES.get(family):
                raise ValueError(
                    "cloned profile path must differ from the shared LinkedIn profile"
                )
            if not _inside(path, root) and not _inside(path, BROWSER_PROFILE_ROOT):
                raise ValueError(f"source isolation path outside allowed clone roots: {family}")
            cdp_url = str(settings_dict.get("cdp_url") or "").strip()
            if not cdp_url:
                raise ValueError(f"cloned profile requires cdp_url: {family}")
            if not _valid_cdp_url(cdp_url):
                raise ValueError(f"invalid cloned profile cdp_url: {family}")
        elif not _inside(path, root):
            raise ValueError(f"source isolation path outside experiment root: {family}")
        if shared_profile:
            if family not in SHARED_BROWSER_PROFILES:
                raise ValueError(f"shared browser profile forbidden for source: {family}")
            if Path(str(shared_profile)) != SHARED_BROWSER_PROFILES[family]:
                raise ValueError(f"unapproved shared browser profile: {family}")
            if not _inside(str(dict(settings).get("backup_path") or ""), root):
                raise ValueError(f"shared profile backup outside experiment root: {family}")
    for section, keys in {
        "python": ("executable_sha256", "stdlib_tree_sha256"),
        "environment": (
            "dependency_lock_sha256",
            "installed_distributions_sha256",
            "sys_path_sha256",
        ),
    }.items():
        values = dict(manifest.get(section) or {})
        for key in keys:
            if len(str(values.get(key) or "")) != 64:
                raise ValueError(f"invalid identity hash: {section}.{key}")


def classify_legacy_attempt_evidence(summary_path: Path | str) -> dict[str, Any]:
    """Refuse to infer pair outcomes from the pre-B1 summary format."""
    path = Path(summary_path)
    summary = json.loads(path.read_text(encoding="utf-8"))
    cell_states = dict(summary.get("cell_states") or {})
    if not cell_states:
        raise ValueError("legacy summary has no cell states")
    cell_outcomes = {
        cell_id: "legacy_attempt_evidence_insufficient"
        for cell_id in sorted(cell_states)
    }
    return {
        "run_id": summary.get("run_id"),
        "cell_outcomes": cell_outcomes,
        "acquisition_outcomes": {},
        "product_observability_state": {},
        "product_observability_reason": {
            cell_id: "legacy_attempt_evidence_insufficient"
            for cell_id in sorted(cell_states)
        },
        "attempt_evidence_criterion": LEGACY_ATTEMPT_EVIDENCE_CRITERION,
    }


def validate_gate_a_run_evidence(evidence: Mapping[str, Any]) -> None:
    allowed_stages = {
        "raw_observed",
        "canonical_current",
        "minimum_evidence_sufficient",
    }
    stages = set(dict(evidence.get("stage_counts") or {}))
    if stages != allowed_stages:
        raise ValueError("Gate A may contain stages 1-3 only; stage 4 is forbidden")
    allowed_labels = {
        "provisionally_eligible",
        "known_hard_block",
        "unresolved_for_decision_v2",
    }
    labels = set(dict(evidence.get("provisional_labels") or {}))
    if not labels <= allowed_labels:
        raise ValueError("unknown Gate A provisional label")
    scheduled = int(evidence.get("scheduled_attempts") or 0)
    completed = int(evidence.get("completed_attempts") or 0)
    missed = int(evidence.get("missed_attempts") or 0)
    if scheduled < 1:
        raise ValueError("scheduled attempts are required")
    if scheduled != completed + missed:
        raise ValueError("attempt accounting does not close")
    if not dict(evidence.get("family_attempts") or {}):
        raise ValueError("family attempts are required")
    if "cell_states" in evidence:
        raise ValueError("legacy cell_states are forbidden")
    if not dict(evidence.get("acquisition_outcomes") or {}):
        raise ValueError("acquisition outcomes are required")
    if evidence.get("evidence_hashes_verified") is not True:
        raise ValueError("evidence hashes must be verified")
    for name, path in dict(evidence.get("isolated_paths") or {}).items():
        if "/experiments/gate-a/" not in str(path):
            raise ValueError(f"invalid isolated path: {name}")
    for name, count in dict(evidence.get("side_effects") or {}).items():
        if int(count or 0) != 0:
            raise ValueError(f"forbidden side effect: {name}")


def _tree_sha256(root: Path, *, relative_to: Path | None = None) -> str:
    digest = hashlib.sha256()
    anchor = relative_to or root
    if not root.exists():
        return digest.hexdigest()
    paths = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
    for path in paths:
        digest.update(path.relative_to(anchor).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_experiment_manifest(
    *,
    root: Path,
    commit: str,
    python_executable: Path,
    python_version: str,
    stdlib_root: Path,
    sys_path: tuple[str, ...],
) -> dict[str, Any]:
    runtime = root / "runtime"
    python_runtime = root / "python-runtime"
    installed = python_runtime / "installed-distributions.txt"
    lock = runtime / "uv.lock"
    bounded_proof = build_bounded_proof_configuration(
        load_linkedin_geography_mapping()
    )
    paths = {
        "runtime": str(runtime),
        "experiment.sqlite3": str(root / "experiment.sqlite3"),
        "raw-evidence": str(root / "raw-evidence"),
        "logs": str(root / "logs"),
        "locks": str(root / "locks"),
        "browser-profile": str(root / "browser-profile"),
        "cache": str(root / "cache"),
        "tmp": str(root / "tmp"),
    }
    manifest: dict[str, Any] = {
        "schema_version": "1.0.0",
        "gate": "gate-a",
        "environment_id": "product-search-gate-a",
        "commit": commit,
        "root": str(root),
        "exclusion_reason_codes": {
            "version": EXCLUSION_REASON_CATALOG.version,
            "sha256": EXCLUSION_REASON_CATALOG.sha256,
        },
        "bounded_proof": bounded_proof,
        "paths": paths,
        "python": {
            "executable_path": str(python_executable),
            "executable_sha256": _tree_sha256(python_executable),
            "version": python_version,
            "implementation": "CPython",
            "stdlib_root": str(stdlib_root),
            "stdlib_tree_sha256": _tree_sha256(stdlib_root),
        },
        "environment": {
            "dependency_lock_sha256": _tree_sha256(lock),
            "installed_distributions_sha256": _tree_sha256(installed),
            "import_root": str(runtime),
            "sys_path": list(sys_path),
            "sys_path_sha256": hashlib.sha256("\n".join(sys_path).encode()).hexdigest(),
            "editable_installs": [],
        },
        "runtime_sha256": _tree_sha256(runtime),
        "config_sha256": _tree_sha256(runtime / "config/product_search", relative_to=runtime),
        "source_sha256": _tree_sha256(runtime / "job_intel/product_search", relative_to=runtime),
        "unit_sha256": _tree_sha256(runtime / "deploy/systemd/experiments", relative_to=runtime),
    }
    validate_experiment_manifest(manifest)
    return manifest


def relocate_experiment_manifest(
    manifest: Mapping[str, Any], *, new_root: Path
) -> dict[str, Any]:
    validate_experiment_manifest(manifest)
    old_root = str(manifest["root"])
    if not new_root.is_absolute():
        raise ValueError("relocated experiment root must be absolute")

    def relocate(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: relocate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [relocate(item) for item in value]
        if isinstance(value, str) and (value == old_root or value.startswith(f"{old_root}/")):
            return f"{new_root}{value[len(old_root):]}"
        return value

    relocated = relocate(dict(manifest))
    relocated_sys_path = tuple(relocated["environment"].get("sys_path") or ())
    if not relocated_sys_path:
        raise ValueError("manifest sys.path is required for relocation")
    relocated["environment"]["sys_path_sha256"] = hashlib.sha256(
        "\n".join(relocated_sys_path).encode()
    ).hexdigest()
    relocated["source_isolation"] = {
        "ashby": {
            "mode": "exclusive_lock",
            "path": str(new_root / "locks/ashby.lock"),
        },
        "duckduckgo": {
            "mode": "exclusive_lock",
            "path": str(new_root / "locks/duckduckgo.lock"),
        },
        "headhunter": {"mode": "api"},
        "greenhouse": {
            "mode": "exclusive_lock",
            "path": str(new_root / "locks/greenhouse.lock"),
        },
        "lever": {
            "mode": "exclusive_lock",
            "path": str(new_root / "locks/lever.lock"),
        },
        "linkedin": {
            "backup_path": str(new_root / "browser-profile-backup/linkedin"),
            "mode": "exclusive_lock",
            "path": str(new_root / "locks/linkedin-profile.lock"),
            "shared_profile_path": str(SHARED_BROWSER_PROFILES["linkedin"]),
        },
        "personio": {
            "mode": "exclusive_lock",
            "path": str(new_root / "locks/personio.lock"),
        },
        "recruitee": {
            "mode": "exclusive_lock",
            "path": str(new_root / "locks/recruitee.lock"),
        },
        "remoteok": {
            "mode": "exclusive_lock",
            "path": str(new_root / "locks/remoteok.lock"),
        },
        "remotive": {
            "mode": "exclusive_lock",
            "path": str(new_root / "locks/remotive.lock"),
        },
        "smartrecruiters": {
            "mode": "exclusive_lock",
            "path": str(new_root / "locks/smartrecruiters.lock"),
        },
        "teamtailor": {
            "mode": "exclusive_lock",
            "path": str(new_root / "locks/teamtailor.lock"),
        },
    }
    original_isolation = dict(manifest.get("source_isolation") or {})
    for family, original_settings in original_isolation.items():
        if not isinstance(original_settings, Mapping) or "collection_method" not in original_settings:
            continue
        if family in relocated["source_isolation"]:
            relocated["source_isolation"][family]["collection_method"] = original_settings[
                "collection_method"
            ]
    # A legacy manifest without source_isolation is accepted on input for
    # relocation compatibility. Its generated legacy isolation map must not be
    # enriched with collection_method, and is intentionally not revalidated as
    # a newly classified manifest. Classified manifests remain fail-closed.
    if original_isolation:
        validate_experiment_manifest(relocated)
    return relocated


def verify_experiment_runtime(
    manifest: Mapping[str, Any],
    *,
    python_executable: Path | None = None,
    python_version: str | None = None,
    stdlib_root: Path | None = None,
    sys_path: tuple[str, ...] | None = None,
) -> None:
    import platform
    import sys
    import sysconfig

    validate_experiment_manifest(manifest)
    expected = build_experiment_manifest(
        root=Path(str(manifest["root"])),
        commit=str(manifest["commit"]),
        python_executable=python_executable or Path(sys.executable),
        python_version=python_version or platform.python_version(),
        stdlib_root=stdlib_root or Path(sysconfig.get_paths()["stdlib"]),
        sys_path=sys_path or tuple(sys.path),
    )
    identity_fields = (
        "commit",
        "root",
        "paths",
        "python",
        "environment",
        "runtime_sha256",
        "config_sha256",
        "source_sha256",
        "unit_sha256",
    )
    drifted = [name for name in identity_fields if manifest.get(name) != expected.get(name)]
    if drifted:
        raise ValueError(f"experiment runtime drift: {', '.join(drifted)}")


def main() -> int:
    import argparse
    import yaml

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-manifest")
    validate.add_argument("path", type=Path)
    write = subparsers.add_parser("write-manifest")
    write.add_argument("root", type=Path)
    write.add_argument("commit")
    relocate = subparsers.add_parser("relocate-manifest")
    relocate.add_argument("source", type=Path)
    relocate.add_argument("new_root", type=Path)
    relocate.add_argument("destination", type=Path)
    run = subparsers.add_parser("run-manifest")
    run.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "validate-manifest":
        manifest = yaml.safe_load(args.path.read_text(encoding="utf-8"))
        verify_experiment_runtime(manifest)
    elif args.command == "write-manifest":
        import platform
        import sys
        import sysconfig

        manifest = build_experiment_manifest(
            root=args.root,
            commit=args.commit,
            python_executable=Path(sys.executable),
            python_version=platform.python_version(),
            stdlib_root=Path(sysconfig.get_paths()["stdlib"]),
            sys_path=tuple(sys.path),
        )
        (args.root / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8"
        )
    elif args.command == "relocate-manifest":
        manifest = yaml.safe_load(args.source.read_text(encoding="utf-8"))
        relocated = relocate_experiment_manifest(manifest, new_root=args.new_root)
        args.destination.write_text(
            yaml.safe_dump(relocated, sort_keys=True), encoding="utf-8"
        )
    elif args.command == "run-manifest":
        manifest = yaml.safe_load(args.path.read_text(encoding="utf-8"))
        verify_experiment_runtime(manifest)
        runtime = Path(manifest["environment"]["import_root"])
        probe_environment = build_isolated_probe_environment(
            manifest, ambient=os.environ
        )
        os.environ.update(probe_environment)
        contract = __import__(
            "job_intel.product_search.search_contract", fromlist=["load_search_contract"]
        ).load_search_contract(runtime / "config/product_search/search_contract.v1.yaml")
        mapping = load_linkedin_geography_mapping(
            runtime / "config/product_search/linkedin_geography.v1.yaml"
        )
        bounded_cell_ids, negative_control = _bounded_proof_configuration(
            manifest, mapping
        )
        queries = expand_queries(
            contract,
            role_terms=("Chief Product Officer", "VP Product", "Head of Product", "GM Digital"),
            geography_mapping=mapping,
        )
        queries = tuple(query for query in queries if query.cell_id in bounded_cell_ids)
        if negative_control["cell_id"] not in mapping:
            control_cell_id = str(negative_control["cell_id"])
            control_location = str(negative_control["location"])
            synthetic_query = ProbeQuery(
                query_id=hashlib.sha256(
                    f"c1a-negative-control\0{control_cell_id}\0{control_location}".encode()
                ).hexdigest()[:20],
                cell_id=str(negative_control["cell_id"]),
                source_family="linkedin",
                query=str(negative_control["location"]),
                keywords="C1A negative control",
                primary_geography=str(negative_control["location"]),
                geography_target=LinkedInGeographyTarget(
                    location=str(negative_control["location"]),
                    status="unsupported",
                    country_codes=(),
                ),
                is_synthetic_control=True,
                minimum_independent_families=1,
            )
            queries = queries + (synthetic_query,)
        isolation = {
            family: SourceIsolation(
                mode=str(settings.get("mode") or "blocked"),
                path=Path(settings["path"]) if settings.get("path") else None,
                collection_method=(
                    str(settings["collection_method"])
                    if settings.get("collection_method")
                    else None
                ),
                cdp_url=(
                    str(settings["cdp_url"])
                    if settings.get("cdp_url")
                    else None
                ),
            )
            for family, settings in dict(manifest.get("source_isolation") or {}).items()
        }
        run_probe(
            run_id=f"gate-a-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            queries=queries,
            sources=resolve_public_sources(),
            output_dir=Path(manifest["root"]),
            isolation=isolation,
            runtime_capability_checks=resolve_runtime_capability_checks(isolation),
            geography_mapping=mapping,
            environment=probe_environment,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
