"""Content-addressed Gate B preflight and owner-authorized record runner.

Preparation performs no provider work. The sole record entrypoint requires an
opaque owner authorization and durable call/spend ledger; this module contains
no Slack integration or production persistence boundary.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Any, Iterable, Mapping

import yaml

from job_intel.product_search.acquisition_probe import (
    _canonical_url,
    _minimum_evidence_sufficient,
)
from job_intel.product_search.evidence_synthesis import (
    OUTPUT_SCHEMA_VERSION,
    RecordedEvidenceSynthesisProvider,
    load_evidence_synthesis_policy,
    provider_output_schema_sha256,
    run_evidence_synthesis,
    synthesis_input_sha256,
    task10_prompt_sha256,
)
from job_intel.vacancy_understanding.semantic.contract import load_semantic_contract
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    GovernedPricingSchedule,
    StructuredCallCapability,
    build_prompt_for_version,
    _issue_structured_call_capability,
)


GATE_A_RUN_ID = "gate-a-20260816T141344Z"
GATE_A_COMMIT = "65d60daae16093a9a7e34a11a159e2f789dd14dd"
GATE_A_MANIFEST_SHA256 = (
    "6ecc500c291061a34c4482edb5c2a0d6c547993bea0d346ad306041dfa81df3d"
)
DEFAULT_SAMPLE_SIZE = 48
DEFAULT_MAX_COST_PER_CALL_USD = Decimal("0.01")
EXACT_CALL_CAP = 48
EXACT_SPEND_CAP_USD = Decimal("0.48")
GATE_A_DATABASE_SHA256 = (
    "08fefb5a0fdcaee7c59b5921b1b74291471e58405fd3299e8834c5a5a6c0d8ff"
)
GATE_B_EXPERIMENT_ROOT = Path("/home/hermes/.hermes/job_intel/experiments/gate-b")
PRODUCTION_DATABASE_PATH = Path("/var/lib/job-intel/state/job_intel.sqlite3")
REPO_ROOT = Path(__file__).resolve().parents[2]


class GateBPreflightError(RuntimeError):
    """Closed, machine-readable Gate B preparation failure."""


def governed_pricing_schedule() -> GovernedPricingSchedule:
    """Exact reviewed pricing identity and conservative token bounds."""
    return GovernedPricingSchedule(
        version="openrouter-openai-gpt5-mini-2026-08-17",
        model_id="openai/gpt-5-mini",
        input_usd_per_mtok=Decimal("0.25"),
        output_usd_per_mtok=Decimal("2.00"),
        max_input_tokens=24_000,
        max_output_tokens=2_000,
    )


class DryRunBoundary:
    """Instrumented deny-by-default boundary for the no-call preflight."""

    def __init__(self) -> None:
        self.gate_a_files: set[str] = set()
        self.corpus_files: set[str] = set()
        self.denied: dict[str, int] = defaultdict(int)

    def gate_a_read(self, path: Path) -> None:
        self.gate_a_files.add(str(path))

    def corpus_write(self, path: Path) -> None:
        self.corpus_files.add(str(path))

    def _deny(self, operation: str) -> None:
        self.denied[operation] += 1
        raise GateBPreflightError(f"dry_run_forbidden:{operation}")

    def provider(self) -> None:
        self._deny("provider")

    def network(self) -> None:
        self._deny("network")

    def slack_credential(self) -> None:
        self._deny("slack_credential")

    def production_write(self) -> None:
        self._deny("production_write")

    def runtime_mutation(self) -> None:
        self._deny("runtime_mutation")

    def protected_write(self) -> None:
        self._deny("protected_write")

    def report(self) -> dict[str, int]:
        return {
            "gate_a_files_read": len(self.gate_a_files),
            "corpus_files_created": len(self.corpus_files),
            "provider_attempts_denied": self.denied["provider"],
            "network_attempts_denied": self.denied["network"],
            "slack_credential_attempts_denied": self.denied["slack_credential"],
            "production_write_attempts_denied": self.denied["production_write"],
            "runtime_mutation_attempts_denied": self.denied["runtime_mutation"],
            "protected_write_attempts_denied": self.denied["protected_write"],
        }


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_json(value: object) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def read_contained_nofollow(root: Path, reference: str) -> bytes:
    """Read a regular file beneath root without traversal or symlink following."""
    root = root.resolve(strict=True)
    relative = Path(reference)
    if relative.is_absolute() or ".." in relative.parts:
        raise GateBPreflightError("contained_nofollow:reference")
    candidate = root / relative
    try:
        resolved_parent = candidate.parent.resolve(strict=True)
    except OSError as exc:
        raise GateBPreflightError("contained_nofollow:parent") from exc
    if resolved_parent != root and root not in resolved_parent.parents:
        raise GateBPreflightError("contained_nofollow:escape")
    if candidate.is_symlink():
        raise GateBPreflightError("contained_nofollow:symlink")
    try:
        descriptor = os.open(
            candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
    except OSError as exc:
        raise GateBPreflightError("contained_nofollow:open") from exc
    try:
        stat_result = os.fstat(descriptor)
        if not stat.S_ISREG(stat_result.st_mode):
            raise GateBPreflightError("contained_nofollow:not_regular")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _gate_a_snapshot(
    gate_a_root: Path, boundary: DryRunBoundary
) -> dict[str, tuple[int, int, int, str]]:
    if gate_a_root.is_symlink():
        raise GateBPreflightError("gate_a_root_symlink")
    paths = [gate_a_root / "manifest.yaml", gate_a_root / "experiment.sqlite3"]
    raw_root = gate_a_root / "raw-evidence"
    for path in sorted(raw_root.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise GateBPreflightError("gate_a_raw_path_invalid")
        paths.append(path)
    snapshot: dict[str, tuple[int, int, int, str]] = {}
    for path in paths:
        relative = str(path.relative_to(gate_a_root))
        payload = read_contained_nofollow(gate_a_root, relative)
        boundary.gate_a_read(path)
        stat = path.stat(follow_symlinks=False)
        snapshot[str(path)] = (
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            _sha256_bytes(payload),
        )
    return snapshot


def _protected_metadata_snapshot() -> dict[str, tuple[int, int, int]]:
    paths = [
        PRODUCTION_DATABASE_PATH,
        REPO_ROOT / "config/product_search/career_profile.v2.yaml",
        REPO_ROOT / "config/product_search/search_contract.v1.yaml",
        REPO_ROOT / "config/product_search/evidence_synthesis.v1.yaml",
    ]
    result: dict[str, tuple[int, int, int]] = {}
    for path in paths:
        if path.exists():
            stat = path.stat(follow_symlinks=False)
            result[str(path)] = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    return result


def snapshot_paths(paths: Iterable[Path]) -> dict[str, tuple[int, int, int, str]]:
    snapshot: dict[str, tuple[int, int, int, str]] = {}
    for path in sorted({Path(item) for item in paths}):
        stat = path.stat()
        snapshot[str(path)] = (
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            _sha256_file(path),
        )
    return snapshot


def assert_paths_unchanged(
    before: Mapping[str, tuple[int, int, int, str]],
    after: Mapping[str, tuple[int, int, int, str]],
) -> None:
    if dict(before) != dict(after):
        changed = sorted(set(before) | set(after))
        raise GateBPreflightError("forbidden_side_effect_mutation:" + ",".join(changed))


def validate_gate_a_run_ids(
    *, evidence_run_ids: Iterable[str], probe_run_ids: Iterable[str]
) -> None:
    evidence = set(evidence_run_ids)
    runs = list(probe_run_ids)
    if evidence != {GATE_A_RUN_ID} or runs != [GATE_A_RUN_ID]:
        raise GateBPreflightError(
            f"mixed_gate_a_run_ids:evidence={sorted(evidence)},probe_runs={runs}"
        )


def _load_gate_a(
    gate_a_root: Path, boundary: DryRunBoundary
) -> tuple[list[dict[str, Any]], list[Path]]:
    manifest_path = gate_a_root / "manifest.yaml"
    database_path = gate_a_root / "experiment.sqlite3"
    manifest_bytes = read_contained_nofollow(gate_a_root, "manifest.yaml")
    boundary.gate_a_read(manifest_path)
    database_bytes = read_contained_nofollow(gate_a_root, "experiment.sqlite3")
    boundary.gate_a_read(database_path)
    if _sha256_bytes(manifest_bytes) != GATE_A_MANIFEST_SHA256:
        raise GateBPreflightError("gate_a_manifest_sha256_mismatch")
    if _sha256_bytes(database_bytes) != GATE_A_DATABASE_SHA256:
        raise GateBPreflightError("gate_a_database_sha256_mismatch")
    manifest = yaml.safe_load(manifest_bytes.decode("utf-8"))
    if manifest.get("commit") != GATE_A_COMMIT:
        raise GateBPreflightError("gate_a_commit_mismatch")
    if manifest.get("paths", {}).get("experiment.sqlite3") != str(database_path):
        raise GateBPreflightError("gate_a_database_identity_mismatch")

    connection = sqlite3.connect(f"file:{database_path}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT run_id, raw_content_sha256, query_id, source_family, "
                "source_id, raw_reference, redaction_class "
                "FROM probe_evidence ORDER BY raw_content_sha256"
            )
        ]
        run_ids = [
            str(row[0])
            for row in connection.execute(
                "SELECT run_id FROM probe_runs ORDER BY run_id"
            )
        ]
        integrity_row = connection.execute("PRAGMA integrity_check").fetchone()
        integrity = tuple(integrity_row) if integrity_row is not None else None
    finally:
        connection.close()
    if integrity != ("ok",):
        raise GateBPreflightError("gate_a_database_integrity_failure")
    validate_gate_a_run_ids(
        evidence_run_ids=(str(row["run_id"]) for row in rows),
        probe_run_ids=run_ids,
    )
    if len(rows) != 2414:
        raise GateBPreflightError(f"gate_a_raw_denominator_mismatch:{len(rows)}")

    canonical: dict[
        str, tuple[tuple[str, str, str], dict[str, Any], dict[str, Any]]
    ] = {}
    protected_paths = [manifest_path, database_path]
    for row in rows:
        if row["redaction_class"] != "vacancy_public_evidence":
            raise GateBPreflightError("gate_a_redaction_class_mismatch")
        raw_reference = str(row["raw_reference"])
        raw_path = gate_a_root / raw_reference
        protected_paths.append(raw_path)
        raw_bytes = read_contained_nofollow(gate_a_root, raw_reference)
        boundary.gate_a_read(raw_path)
        if _sha256_bytes(raw_bytes) != row["raw_content_sha256"]:
            raise GateBPreflightError("gate_a_raw_content_sha256_mismatch")
        payload = json.loads(raw_bytes)
        for name in ("source_id", "query_id", "source_family"):
            if payload.get(name) != row[name]:
                raise GateBPreflightError(f"gate_a_raw_identity_mismatch:{name}")
        identity = _canonical_url(str(payload.get("url") or ""))
        if not identity:
            identity = hashlib.sha256(
                f"{payload.get('company')}\0{payload.get('title')}".encode()
            ).hexdigest()
        candidate_key = (
            str(row["source_family"]),
            str(row["source_id"]),
            str(row["raw_content_sha256"]),
        )
        current = canonical.get(identity)
        if current is None or candidate_key < current[0]:
            canonical[identity] = (candidate_key, payload, row)
    if len(canonical) != 1814:
        raise GateBPreflightError(
            f"gate_a_canonical_denominator_mismatch:{len(canonical)}"
        )
    sufficient = [
        item for item in canonical.values() if _minimum_evidence_sufficient(item[1])
    ]
    if len(sufficient) != 1314:
        raise GateBPreflightError(
            f"gate_a_minimum_evidence_denominator_mismatch:{len(sufficient)}"
        )
    if _sha256_bytes(read_contained_nofollow(gate_a_root, "experiment.sqlite3")) != (
        GATE_A_DATABASE_SHA256
    ):
        raise GateBPreflightError("gate_a_database_changed_during_import")
    return [
        {"payload": payload, "evidence": row, "canonical_identity": identity}
        for identity, (_, payload, row) in canonical.items()
        if _minimum_evidence_sufficient(payload)
    ], protected_paths


def _cell_lanes() -> dict[str, str]:
    payload = yaml.safe_load(
        (REPO_ROOT / "config/product_search/search_contract.v1.yaml").read_text(
            encoding="utf-8"
        )
    )
    result: dict[str, str] = {}
    for lane_id, lane in payload["lanes"].items():
        for cell in lane["cells"].values():
            result[cell["cell_id"]] = lane_id
    result["ats_global_snapshot"] = "global_ats"
    return result


def _role_pattern(title: str) -> str:
    value = title.casefold()
    if "chief product" in value or "cpo" in value:
        return "chief_product"
    if "vp product" in value or "vice president" in value:
        return "vp_product"
    if "head of product" in value or "head product" in value:
        return "head_product"
    if "director" in value:
        return "director"
    if "general manager" in value or value.startswith("gm "):
        return "general_manager"
    if "product" in value:
        return "product_other"
    return "adjacent"


def _sampling_case_type(payload: Mapping[str, Any], role_pattern: str) -> str:
    title = str(payload.get("title") or "").casefold()
    description = str(payload.get("description") or "").casefold()
    hard_terms = (
        "sales",
        "marketing",
        "analyst",
        "engineer",
        "developer",
        "intern",
    )
    if any(term in title for term in hard_terms) or any(
        term in description for term in ("on-site only", "onsite only")
    ):
        return "hard_block_hypothesis"
    if role_pattern in {"chief_product", "vp_product", "head_product"}:
        return "core_hypothesis"
    if (
        not payload.get("location")
        or str(payload.get("location")).casefold() == "unknown"
        or not payload.get("company")
        or len(str(payload.get("description") or "")) < 120
    ):
        return "important_unknown"
    return "exploration_hypothesis"


def _corpus_records(
    records: list[dict[str, Any]], sample_size: int
) -> list[dict[str, Any]]:
    lanes = _cell_lanes()
    enriched: list[dict[str, Any]] = []
    for item in records:
        payload = item["payload"]
        evidence = item["evidence"]
        role_pattern = _role_pattern(str(payload.get("title") or ""))
        case_type = _sampling_case_type(payload, role_pattern)
        record = {
            "run_id": evidence["run_id"],
            "source_family": evidence["source_family"],
            "source_id": evidence["source_id"],
            "query_id": evidence["query_id"],
            "raw_content_sha256": evidence["raw_content_sha256"],
            "raw_reference": evidence["raw_reference"],
            "canonical_identity_sha256": _sha256_bytes(
                item["canonical_identity"].encode("utf-8")
            ),
            "company": payload.get("company") or "unknown",
            "cell_id": payload.get("cell_id") or "unknown",
            "lane": lanes.get(str(payload.get("cell_id") or ""), "unknown"),
            "role_pattern": role_pattern,
            "origin": "open_market",
            "sampling_case_type": case_type,
            "decision_selection_mode": None,
        }
        record["selection_key"] = _sha256_json({
            "run_id": record["run_id"],
            "source_family": record["source_family"],
            "source_id": record["source_id"],
            "raw_content_sha256": record["raw_content_sha256"],
        })
        enriched.append(record)
    enriched.sort(key=lambda item: item["selection_key"])

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()

    def add_first(group_field: str, values: Iterable[str] | None = None) -> None:
        groups = sorted(set(values or (str(item[group_field]) for item in enriched)))
        for group in groups:
            for item in enriched:
                if (
                    str(item[group_field]) == group
                    and item["selection_key"] not in selected_keys
                ):
                    selected.append(item)
                    selected_keys.add(item["selection_key"])
                    break

    add_first("sampling_case_type")
    add_first("lane")
    add_first("source_family")
    add_first("role_pattern")
    add_first("company", sorted({str(item["company"]) for item in enriched})[:12])

    strata: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in enriched:
        strata[
            (
                str(item["lane"]),
                str(item["source_family"]),
                str(item["role_pattern"]),
                str(item["sampling_case_type"]),
            )
        ].append(item)
    while len(selected) < sample_size:
        progressed = False
        for key in sorted(strata):
            for item in strata[key]:
                if item["selection_key"] not in selected_keys:
                    selected.append(item)
                    selected_keys.add(item["selection_key"])
                    progressed = True
                    break
            if len(selected) >= sample_size:
                break
        if not progressed:
            break
    if len(selected) != sample_size:
        raise GateBPreflightError(f"corpus_sample_size_unavailable:{len(selected)}")
    return sorted(selected, key=lambda item: item["selection_key"])


def _record_identity(corpus_sha256: str) -> dict[str, str]:
    policy = load_evidence_synthesis_policy()
    pricing = governed_pricing_schedule()
    semantic_prompt = build_prompt_for_version(
        policy.semantic_prompt_version, load_semantic_contract()
    )
    paths = {
        "decision_contract_sha256": REPO_ROOT
        / "config/product_search/decision_contract.v2.yaml",
        "search_contract_sha256": REPO_ROOT
        / "config/product_search/search_contract.v1.yaml",
        "profile_sha256": REPO_ROOT / "config/product_search/career_profile.v2.yaml",
        "policy_sha256": REPO_ROOT / "config/product_search/evidence_synthesis.v1.yaml",
    }
    identity = {name: _sha256_file(path) for name, path in paths.items()}
    identity.update({
        "corpus_manifest_sha256": corpus_sha256,
        "provider_output_schema_sha256": provider_output_schema_sha256(),
        "provider_output_schema_version": OUTPUT_SCHEMA_VERSION,
        "semantic_prompt_sha256": _sha256_bytes(semantic_prompt.encode("utf-8")),
        "semantic_prompt_version": policy.semantic_prompt_version,
        "task10_prompt_version": policy.prompt_version,
        "task10_prompt_sha256": task10_prompt_sha256(policy),
        "model_id": policy.model_id,
        "model_sha256": _sha256_bytes(policy.model_id.encode("utf-8")),
        "pricing_sha256": pricing.identity_sha256,
        "pricing_version": pricing.version,
        "max_output_tokens": str(pricing.max_output_tokens),
    })
    return identity


def build_dry_run_preflight(
    *,
    gate_a_root: Path,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    boundary_attempt: Any = None,
) -> dict[str, Any]:
    if sample_size <= 0:
        raise GateBPreflightError("invalid_sample_size")
    gate_a_root = gate_a_root.resolve()
    output_root = GATE_B_EXPERIMENT_ROOT
    if output_root.is_symlink():
        raise GateBPreflightError("workspace_symlink")
    boundary = DryRunBoundary()
    gate_a_before = _gate_a_snapshot(gate_a_root, boundary)
    protected_before = _protected_metadata_snapshot()
    if boundary_attempt is not None:
        boundary_attempt(boundary)
    records, protected_paths = _load_gate_a(gate_a_root, boundary)
    selected = _corpus_records(records, sample_size)
    corpus = {
        "schema_version": "1.0.0",
        "gate": "gate-b",
        "gate_a": {
            "run_id": GATE_A_RUN_ID,
            "commit": GATE_A_COMMIT,
            "manifest_sha256": GATE_A_MANIFEST_SHA256,
        },
        "selection": {
            "algorithm": "deterministic-coverage-first-stratified-round-robin-v1",
            "denominator": 1314,
            "sample_size": sample_size,
            "core_exploration_values_are_sampling_hypotheses_not_decision_outputs": True,
        },
        "records": selected,
    }
    corpus_bytes = (
        json.dumps(corpus, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    corpus_sha256 = _sha256_bytes(corpus_bytes)
    experiment_root = output_root / corpus_sha256
    manifest_path = experiment_root / "corpus-manifest.json"
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output_root, 0o700)
    if experiment_root.is_symlink():
        raise GateBPreflightError("workspace_symlink")
    experiment_root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(experiment_root, 0o700)
    if manifest_path.exists():
        if manifest_path.is_symlink() or read_contained_nofollow(
            experiment_root, manifest_path.name
        ) != corpus_bytes:
            raise GateBPreflightError("content_address_collision")
    else:
        temporary = experiment_root / f".corpus.{os.getpid()}.tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.write(descriptor, corpus_bytes)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary, manifest_path, follow_symlinks=False)
        except FileExistsError:
            if read_contained_nofollow(experiment_root, manifest_path.name) != corpus_bytes:
                raise GateBPreflightError("content_address_collision")
        finally:
            temporary.unlink(missing_ok=True)
        boundary.corpus_write(manifest_path)
    os.chmod(manifest_path, 0o600)
    gate_a_after = _gate_a_snapshot(gate_a_root, boundary)
    assert_paths_unchanged(gate_a_before, gate_a_after)
    if protected_before != _protected_metadata_snapshot():
        raise GateBPreflightError("forbidden_side_effect_mutation:protected")

    coverage = {
        "lanes": sorted({item["lane"] for item in selected}),
        "source_families": sorted({item["source_family"] for item in selected}),
        "role_patterns": sorted({item["role_pattern"] for item in selected}),
        "companies": len({item["company"] for item in selected}),
        "origins": ["open_market"],
        "strategic_watchlist_available": False,
        "sampling_case_types": sorted({
            item["sampling_case_type"] for item in selected
        }),
    }
    max_spend = DEFAULT_MAX_COST_PER_CALL_USD * sample_size
    identity = _record_identity(corpus_sha256)
    identity_sha256 = _sha256_json(identity)
    return {
        "schema_version": "1.0.0",
        "status": "ready_for_record_approval",
        "gate_a": {
            "run_id": GATE_A_RUN_ID,
            "commit": GATE_A_COMMIT,
            "manifest_sha256": GATE_A_MANIFEST_SHA256,
            "raw_observed": 2414,
            "corrected_canonical_current": 1814,
            "minimum_evidence_sufficient": 1314,
            "minimum_evidence_is_not_qualified": True,
        },
        "corpus": {
            "status": "materialized",
            "selection_denominator": 1314,
            "selected_count": sample_size,
            "manifest_path": str(manifest_path),
            "manifest_sha256": corpus_sha256,
            "coverage": coverage,
        },
        "budget": {
            "estimated_calls": sample_size,
            "max_cost_per_call_usd": f"{DEFAULT_MAX_COST_PER_CALL_USD:.2f}",
            "maximum_spend_usd": f"{max_spend:.2f}",
            "exact_call_cap": EXACT_CALL_CAP,
            "exact_spend_cap_usd": f"{EXACT_SPEND_CAP_USD:.2f}",
        },
        "record_identity": identity,
        "record_identity_sha256": identity_sha256,
        "gate_a_artifact_root": str(gate_a_root),
        "approval_capability": {"status": "not_supplied", "value": None},
        "record_authorized": False,
        "task_13_authorized": False,
        "provider": {"calls_attempted": 0, "network_enabled": False},
        "side_effect_evidence": boundary.report(),
        "side_effects": {
            "status": "observed",
            "reason": "instrumented_dry_run_boundary_and_snapshots",
            "forbidden_mutations": sum(boundary.denied.values()),
            "slack_credentials_accessed": boundary.denied["slack_credential"],
            "slack_calls": 0,
            "production_writes": boundary.denied["production_write"],
            "runtime_mutations": boundary.denied["runtime_mutation"],
            "gate_a_mutations": 0,
        },
    }


_AUTHORIZATION_ISSUER = object()


@dataclass(frozen=True)
class GateBRunAuthorization:
    run_identity_sha256: str
    exact_call_cap: int
    exact_spend_cap_usd: Decimal
    pricing_sha256: str
    corpus_manifest_sha256: str
    experiment_root: Path
    corpus_manifest_path: Path
    gate_a_root: Path
    record_count: int
    _metadata_seal_key: bytes
    _issuer: object

    def __post_init__(self) -> None:
        if self._issuer is not _AUTHORIZATION_ISSUER:
            raise GateBPreflightError("authorization_issuer_invalid")


def _locked_manifest_bytes(path: Path) -> bytes:
    if path.is_symlink():
        raise GateBPreflightError("corpus_manifest_changed:symlink")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise GateBPreflightError("corpus_manifest_changed:open") from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def authorize_record_run(
    preflight: Mapping[str, Any],
    *,
    approval_record: Mapping[str, Any],
    owner_capability: str | None,
) -> GateBRunAuthorization:
    identity = dict(preflight.get("record_identity", {}))
    identity_sha256 = _sha256_json(identity)
    if identity_sha256 != preflight.get("record_identity_sha256"):
        raise GateBPreflightError("record_identity_mismatch")
    if approval_record.get("status") != "approved":
        raise GateBPreflightError("approval_status")
    if approval_record.get("run_identity_sha256") != identity_sha256:
        raise GateBPreflightError("identity_mismatch")
    if not owner_capability or _sha256_bytes(owner_capability.encode("utf-8")) != (
        approval_record.get("capability_sha256")
    ):
        raise GateBPreflightError("capability_missing_or_mismatch")
    if (
        approval_record.get("exact_call_cap") != EXACT_CALL_CAP
        or Decimal(str(approval_record.get("exact_spend_cap_usd")))
        != EXACT_SPEND_CAP_USD
        or preflight.get("budget", {}).get("exact_call_cap") != EXACT_CALL_CAP
        or Decimal(str(preflight.get("budget", {}).get("exact_spend_cap_usd")))
        != EXACT_SPEND_CAP_USD
    ):
        raise GateBPreflightError("exact_caps_mismatch")
    if approval_record.get("pricing_sha256") != identity.get("pricing_sha256"):
        raise GateBPreflightError("pricing_identity_mismatch")
    expected_corpus = str(preflight["corpus"]["manifest_sha256"])
    if approval_record.get("corpus_manifest_sha256") != expected_corpus:
        raise GateBPreflightError("corpus_identity_mismatch")
    manifest_path = Path(str(preflight["corpus"]["manifest_path"]))
    experiment_root = manifest_path.parent
    if experiment_root.parent.resolve(strict=True) != GATE_B_EXPERIMENT_ROOT.resolve(
        strict=True
    ):
        raise GateBPreflightError("corpus_manifest_changed:root")
    manifest_bytes = _locked_manifest_bytes(manifest_path)
    if _sha256_bytes(manifest_bytes) != expected_corpus:
        raise GateBPreflightError("corpus_manifest_changed:hash")
    corpus = json.loads(manifest_bytes)
    gate_a_root = Path(str(preflight["gate_a_artifact_root"]))
    for record in corpus["records"]:
        raw = read_contained_nofollow(gate_a_root, str(record["raw_reference"]))
        if _sha256_bytes(raw) != record["raw_content_sha256"]:
            raise GateBPreflightError("corpus_source_changed")
    return GateBRunAuthorization(
        run_identity_sha256=identity_sha256,
        exact_call_cap=EXACT_CALL_CAP,
        exact_spend_cap_usd=EXACT_SPEND_CAP_USD,
        pricing_sha256=str(identity["pricing_sha256"]),
        corpus_manifest_sha256=expected_corpus,
        experiment_root=experiment_root,
        corpus_manifest_path=manifest_path,
        gate_a_root=gate_a_root,
        record_count=len(corpus["records"]),
        _metadata_seal_key=hashlib.sha256(
            b"gate-b-record-seal\x00"
            + owner_capability.encode("utf-8")
            + identity_sha256.encode("ascii")
        ).digest(),
        _issuer=_AUTHORIZATION_ISSUER,
    )


class GateBBudgetLedger:
    """Transactional call/spend reservations persisted inside one Gate B run."""

    def __init__(self, path: Path, authorization: GateBRunAuthorization) -> None:
        if not isinstance(authorization, GateBRunAuthorization):
            raise GateBPreflightError("runner_authorization_required")
        self.path = path
        self.authorization = authorization
        if path.parent.resolve(strict=True) != authorization.experiment_root.resolve(
            strict=True
        ):
            raise GateBPreflightError("ledger_path_outside_run")
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS run_budget ("
                "run_identity_sha256 TEXT PRIMARY KEY, call_cap INTEGER NOT NULL, "
                "spend_cap_microusd INTEGER NOT NULL);"
                "CREATE TABLE IF NOT EXISTS call_ledger ("
                "input_hash TEXT PRIMARY KEY, reservation_id TEXT UNIQUE NOT NULL, "
                "reserved_microusd INTEGER NOT NULL, actual_microusd INTEGER, "
                "status TEXT NOT NULL CHECK(status IN ('reserved','success','failure')));"
            )
            connection.execute(
                "INSERT OR IGNORE INTO run_budget VALUES (?, ?, ?)",
                (
                    self.authorization.run_identity_sha256,
                    self.authorization.exact_call_cap,
                    _usd_to_micros(self.authorization.exact_spend_cap_usd),
                ),
            )
            row = connection.execute("SELECT * FROM run_budget").fetchone()
            if row is None or (
                row["run_identity_sha256"] != self.authorization.run_identity_sha256
                or row["call_cap"] != self.authorization.exact_call_cap
                or row["spend_cap_microusd"]
                != _usd_to_micros(self.authorization.exact_spend_cap_usd)
            ):
                raise GateBPreflightError("ledger_identity_mismatch")
            connection.commit()
            os.chmod(self.path, 0o600)
        finally:
            connection.close()

    def reserve(self, input_hash: str, amount: Decimal) -> str:
        amount_micros = _usd_to_micros(amount)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM call_ledger WHERE input_hash = ?", (input_hash,)
            ).fetchone():
                raise GateBPreflightError("input_already_reserved")
            totals = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(CASE WHEN actual_microusd IS NULL "
                "THEN reserved_microusd ELSE actual_microusd END), 0) FROM call_ledger"
            ).fetchone()
            if totals[0] >= self.authorization.exact_call_cap:
                raise GateBPreflightError("call_cap_exhausted")
            if totals[1] + amount_micros > _usd_to_micros(
                self.authorization.exact_spend_cap_usd
            ):
                raise GateBPreflightError("spend_cap_exhausted")
            reservation_id = f"reservation:{input_hash}"
            connection.execute(
                "INSERT INTO call_ledger VALUES (?, ?, ?, NULL, 'reserved')",
                (input_hash, reservation_id, amount_micros),
            )
            connection.commit()
            return reservation_id
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reconcile(
        self, reservation_id: str, actual_cost: Decimal, outcome: str
    ) -> None:
        if outcome not in {"success", "failure"}:
            raise GateBPreflightError("ledger_outcome_invalid")
        actual_micros = _usd_to_micros(actual_cost)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM call_ledger WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None or row["status"] != "reserved":
                raise GateBPreflightError("reservation_state_invalid")
            if actual_micros > row["reserved_microusd"]:
                raise GateBPreflightError("actual_cost_exceeds_reservation")
            connection.execute(
                "UPDATE call_ledger SET actual_microusd = ?, status = ? "
                "WHERE reservation_id = ?",
                (actual_micros, outcome, reservation_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def snapshot(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT COUNT(*) AS reserved, "
                "SUM(CASE WHEN status != 'reserved' THEN 1 ELSE 0 END) AS completed, "
                "COALESCE(SUM(CASE WHEN actual_microusd IS NULL THEN reserved_microusd "
                "ELSE actual_microusd END), 0) AS spend FROM call_ledger"
            ).fetchone()
            return {
                "calls_reserved": row["reserved"],
                "calls_completed": row["completed"] or 0,
                "spend_reserved_usd": _micros_to_usd(row["spend"]),
            }
        finally:
            connection.close()

    def reconcile_existing_record(
        self,
        input_hash: str,
        record: Mapping[str, Any],
        capability: StructuredCallCapability,
    ) -> None:
        """Recover a crash after atomic record write but before ledger commit."""
        capability.verify_record(dict(record))
        if record.get("input_hash") != input_hash:
            raise GateBPreflightError("recording_input_mismatch")
        try:
            actual_cost = Decimal(str(record["cost_usd"]))
        except Exception as exc:
            raise GateBPreflightError("recording_cost_invalid") from exc
        outcome = str(record.get("status"))
        if outcome not in {"success", "failure"}:
            raise GateBPreflightError("recording_status_invalid")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM call_ledger WHERE input_hash = ?", (input_hash,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise GateBPreflightError("recording_without_reservation")
        if row["status"] == "reserved":
            self.reconcile(str(row["reservation_id"]), actual_cost, outcome)
            return
        if row["status"] != outcome or row["actual_microusd"] != _usd_to_micros(
            actual_cost
        ):
            raise GateBPreflightError("recording_ledger_mismatch")

    def structured_capability(self) -> StructuredCallCapability:
        pricing = governed_pricing_schedule()
        if pricing.identity_sha256 != self.authorization.pricing_sha256:
            raise GateBPreflightError("pricing_identity_mismatch")
        return _issue_structured_call_capability(
            run_identity_sha256=self.authorization.run_identity_sha256,
            pricing=pricing,
            exact_call_cap=self.authorization.exact_call_cap,
            exact_spend_cap_usd=self.authorization.exact_spend_cap_usd,
            metadata_seal_key=self.authorization._metadata_seal_key,
            reserve=self.reserve,
            reconcile=self.reconcile,
        )


def _usd_to_micros(value: Decimal) -> int:
    if not value.is_finite() or value < 0:
        raise GateBPreflightError("usd_value_invalid")
    scaled = value * Decimal(1_000_000)
    if scaled != scaled.to_integral_value():
        raise GateBPreflightError("usd_precision_invalid")
    return int(scaled)


def _micros_to_usd(value: int) -> str:
    return f"{Decimal(value) / Decimal(1_000_000):.6f}"


def run_gate_b_record(
    *,
    authorization: GateBRunAuthorization,
    semantic_provider: Any,
    policy: Any,
    input_loader: Any,
) -> list[Any]:
    """Sole resumable Task 12 record entrypoint after exact owner approval."""
    if not isinstance(authorization, GateBRunAuthorization):
        raise GateBPreflightError("runner_authorization_required")
    if authorization._issuer is not _AUTHORIZATION_ISSUER:
        raise GateBPreflightError("authorization_issuer_invalid")
    if authorization.record_count != EXACT_CALL_CAP:
        raise GateBPreflightError("runner_corpus_count_mismatch")
    recording_root = authorization.experiment_root / "recordings"
    if recording_root.is_symlink():
        raise GateBPreflightError("recording_root_symlink")
    if Path(semantic_provider.store.root) != recording_root:
        raise GateBPreflightError("recording_root_mismatch")
    manifest_path = authorization.corpus_manifest_path
    descriptor = os.open(
        manifest_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        manifest_bytes = b"".join(chunks)
        if _sha256_bytes(manifest_bytes) != authorization.corpus_manifest_sha256:
            raise GateBPreflightError("corpus_manifest_changed:runner")
        corpus = json.loads(manifest_bytes)
        records = corpus.get("records")
        if not isinstance(records, list) or len(records) != EXACT_CALL_CAP:
            raise GateBPreflightError("runner_corpus_count_mismatch")
        ledger = GateBBudgetLedger(
            authorization.experiment_root / "run-ledger.sqlite3", authorization
        )
        capability = ledger.structured_capability()
        adapter = RecordedEvidenceSynthesisProvider(
            semantic_provider=semantic_provider,
            policy=policy,
            pricing=governed_pricing_schedule(),
            record_capability=capability,
        )
        results: list[Any] = []
        for record in records:
            raw = read_contained_nofollow(
                authorization.gate_a_root, str(record["raw_reference"])
            )
            if _sha256_bytes(raw) != record.get("raw_content_sha256"):
                raise GateBPreflightError("corpus_source_changed:runner")
            synthesis_input = input_loader(record, raw)
            input_hash = synthesis_input_sha256(
                synthesis_input.provider_payload(), provider=adapter
            )
            recording_path = semantic_provider.store.path_for(input_hash)
            if recording_path.exists():
                existing = semantic_provider.store.load(input_hash)
                ledger.reconcile_existing_record(input_hash, existing, capability)
            results.append(
                run_evidence_synthesis(
                    synthesis_input=synthesis_input,
                    provider=adapter,
                    policy=policy,
                )
            )
        return results
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
