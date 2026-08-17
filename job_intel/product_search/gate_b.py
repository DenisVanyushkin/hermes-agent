"""Content-addressed Gate B preflight and owner-authorized record runner.

Preparation performs no provider work. The sole record entrypoint requires an
opaque owner authorization and durable call/spend ledger; this module contains
no Slack integration or production persistence boundary.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
import contextvars
from dataclasses import dataclass
from decimal import Decimal
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import threading
from typing import Any, Iterable, Mapping

import yaml

from job_intel.product_search.acquisition_probe import (
    _canonical_url,
    _minimum_evidence_sufficient,
)
from job_intel.product_search.evidence_synthesis import (
    AllowedEvidenceClaimV1,
    CompanyAuthorityUnavailableV2,
    EvidenceClaimStatus,
    EvidenceDimension,
    EvidenceFragmentV1,
    EvidenceSourceKind,
    EvidenceSynthesisInputV2,
    OUTPUT_SCHEMA_VERSION,
    OUTPUT_SCHEMA_VERSION_V2,
    PROVIDER_ADAPTER_VERSION_V2,
    RecordedEvidenceSynthesisProviderV2,
    TASK10_PROMPT_VERSION_V2,
    VacancyEvidenceArtifactFragmentV1,
    VacancyEvidenceArtifactV1,
    provider_output_schema_v2_sha256,
    load_evidence_synthesis_policy,
    provider_output_schema_sha256,
    run_evidence_synthesis_v2,
    synthesis_input_sha256,
    task10_prompt_sha256,
    task10_prompt_v2_sha256,
)
from job_intel.product_search.contracts import (
    AssessmentInputV2,
    AssessmentReferences,
    CompanyAuthorityStatus,
    DecisionDimensionsInput,
    DimensionEvidenceInput,
    DimensionEvidenceState,
    ImmutableArtifactRef,
)
from job_intel.vacancy_understanding.semantic.contract import load_semantic_contract
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    GovernedPricingSchedule,
    LLMObservationProvider,
    LLMProviderError,
    RecordingStore,
    StructuredCallCapability,
    build_prompt_for_version,
    _issue_structured_call_capability,
    build_live_llm_provider,
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
GATE_B_CORPUS_SHA256 = (
    "b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69"
)
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
        self.package_files: set[str] = set()
        self.denied: dict[str, int] = defaultdict(int)
        self.slack_credentials_scrubbed = 0

    def gate_a_read(self, path: Path) -> None:
        self.gate_a_files.add(str(path))

    def corpus_write(self, path: Path) -> None:
        self.corpus_files.add(str(path))

    def package_write(self, path: Path) -> None:
        self.package_files.add(str(path))

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
            "package_files_created": len(self.package_files),
            "slack_credentials_scrubbed": self.slack_credentials_scrubbed,
            "provider_attempts_denied": self.denied["provider"],
            "network_attempts_denied": self.denied["network"],
            "slack_credential_attempts_denied": self.denied["slack_credential"],
            "production_write_attempts_denied": self.denied["production_write"],
            "runtime_mutation_attempts_denied": self.denied["runtime_mutation"],
            "protected_write_attempts_denied": self.denied["protected_write"],
        }


_DRY_RUN_POLICY: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "gate_b_dry_run_policy", default=None
)
_DRY_RUN_INTERNAL_IO: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "gate_b_dry_run_internal_io", default=False
)
_AUDIT_HOOK_LOCK = threading.Lock()
_AUDIT_HOOK_INSTALLED = False
_DRY_RUN_EXECUTION_LOCK = threading.Lock()
_ACTIVE_DRY_RUN_POLICY: Any = None
_SLACK_CREDENTIAL_ENV = ("SLACK_BOT_TOKEN", "JOB_INTEL_SLACK_BOT_TOKEN")
_DRY_RUN_REPO_READ_FILES = (
    REPO_ROOT / "config/product_search/career_profile.v2.yaml",
    REPO_ROOT / "config/product_search/search_contract.v1.yaml",
    REPO_ROOT / "config/product_search/evidence_synthesis.v1.yaml",
    REPO_ROOT / "config/product_search/decision_contract.v2.yaml",
    REPO_ROOT / "config/product_search/company_evidence_contract.v1.yaml",
    REPO_ROOT
    / "job_intel/vacancy_understanding/semantic/semantic-fact-contract.yaml",
)


def _resolved_path(path: object) -> Path | None:
    if isinstance(path, int):
        return None
    try:
        candidate = Path(os.fsdecode(path))
    except (TypeError, ValueError):
        return None
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _contained_path(path: object, roots: tuple[Path, ...]) -> bool:
    candidate = _resolved_path(path)
    if candidate is None:
        return False
    for root in roots:
        resolved_root = root.resolve(strict=False)
        if candidate == resolved_root or resolved_root in candidate.parents:
            return True
    return False


class _DryRunIOPolicy:
    def __init__(
        self,
        *,
        gate_a_root: Path,
        output_root: Path,
        boundary: DryRunBoundary,
    ) -> None:
        self.read_roots = (
            gate_a_root.resolve(strict=False),
            output_root.resolve(strict=False),
        )
        self.read_files = frozenset(
            path.resolve(strict=False) for path in _DRY_RUN_REPO_READ_FILES
        )
        self.runtime_code_files: set[Path] = set()
        self.write_roots = (output_root.resolve(strict=False),)
        self.boundary = boundary

    def deny(self, category: str, event: str) -> None:
        self.boundary.denied[category] += 1
        raise GateBPreflightError(f"dry_run_io_denied:{category}:{event}")

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        if _DRY_RUN_INTERNAL_IO.get():
            return
        if event.startswith("socket."):
            self.deny("network", event)
        if event == "import" and args and str(args[0]).split(".", 1)[0] in {
            "pydantic",
        }:
            try:
                spec = importlib.util.find_spec(str(args[0]))
            except (ImportError, AttributeError, ValueError):
                spec = None
            origin = None if spec is None else spec.origin
            if origin and origin not in {"built-in", "frozen"}:
                source = Path(origin).resolve(strict=False)
                self.runtime_code_files.add(source)
                if source.suffix == ".py":
                    self.runtime_code_files.add(
                        Path(importlib.util.cache_from_source(str(source))).resolve(
                            strict=False
                        )
                    )
        if event in {"subprocess.Popen", "os.system", "pty.spawn"} or event.startswith(
            ("os.exec", "os.spawn")
        ):
            self.deny("runtime_mutation", event)
        if event == "sqlite3.connect":
            database = args[0] if args else None
            database_path = str(database).removeprefix("file:").split("?", 1)[0]
            if not _contained_path(database_path, self.read_roots):
                self.deny("production_write", event)
            return
        if event in {"os.listdir", "os.scandir"}:
            path = args[0] if args else None
            if not _contained_path(path, self.read_roots):
                self.deny("protected_read", event)
            return
        if event == "open":
            path = args[0] if args else None
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            write = bool(
                flags
                & (
                    os.O_WRONLY
                    | os.O_RDWR
                    | os.O_CREAT
                    | os.O_TRUNC
                    | os.O_APPEND
                )
            )
            if not isinstance(path, (str, bytes, os.PathLike)):
                return
            roots = self.write_roots if write else self.read_roots
            exact_read = not write and _resolved_path(path) in (
                self.read_files | self.runtime_code_files
            )
            if not exact_read and not _contained_path(path, roots):
                self.deny("production_write" if write else "protected_read", event)
            return
        if event in {
            "os.mkdir",
            "os.chmod",
            "os.remove",
            "os.rename",
            "os.link",
            "os.symlink",
            "os.truncate",
        }:
            path_args = args[:2] if event in {"os.rename", "os.link"} else args[:1]
            for path in path_args:
                if not isinstance(path, (str, bytes, os.PathLike)):
                    continue
                if not _contained_path(path, self.write_roots):
                    self.deny("production_write", event)


def _audit_hook(event: str, args: tuple[Any, ...]) -> None:
    policy = _DRY_RUN_POLICY.get() or _ACTIVE_DRY_RUN_POLICY
    if policy is not None:
        policy.audit(event, args)


def _ensure_audit_hook() -> None:
    global _AUDIT_HOOK_INSTALLED
    with _AUDIT_HOOK_LOCK:
        if not _AUDIT_HOOK_INSTALLED:
            sys.addaudithook(_audit_hook)
            _AUDIT_HOOK_INSTALLED = True


@contextmanager
def _dry_run_io_enforcement(
    *, gate_a_root: Path, output_root: Path, boundary: DryRunBoundary
) -> Iterable[None]:
    global _ACTIVE_DRY_RUN_POLICY
    _ensure_audit_hook()
    policy = _DryRunIOPolicy(
        gate_a_root=gate_a_root,
        output_root=output_root,
        boundary=boundary,
    )
    with _DRY_RUN_EXECUTION_LOCK:
        saved_credentials = {
            name: os.environ.pop(name)
            for name in _SLACK_CREDENTIAL_ENV
            if name in os.environ
        }
        boundary.slack_credentials_scrubbed = len(saved_credentials)
        token = _DRY_RUN_POLICY.set(policy)
        _ACTIVE_DRY_RUN_POLICY = policy
        try:
            yield
        finally:
            _ACTIVE_DRY_RUN_POLICY = None
            _DRY_RUN_POLICY.reset(token)
            os.environ.update(saved_credentials)


@contextmanager
def _dry_run_internal_descriptor_io() -> Iterable[None]:
    token = _DRY_RUN_INTERNAL_IO.set(True)
    try:
        yield
    finally:
        _DRY_RUN_INTERNAL_IO.reset(token)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_json(value: object) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _open_directory_nofollow(path: Path) -> int:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise GateBPreflightError("contained_nofollow:directory") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise GateBPreflightError("contained_nofollow:not_directory")
    return descriptor


def _open_child_directory(
    parent_descriptor: int, name: str, *, create: bool = False
) -> int:
    if not name or name in {".", ".."} or "/" in name:
        raise GateBPreflightError("contained_nofollow:reference")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    with _dry_run_internal_descriptor_io():
        if create:
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
            except FileExistsError:
                pass
        try:
            descriptor = os.open(name, flags, dir_fd=parent_descriptor)
        except OSError as exc:
            raise GateBPreflightError("contained_nofollow:directory") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise GateBPreflightError("contained_nofollow:not_directory")
    return descriptor


def read_contained_nofollow(root: Path, reference: str) -> bytes:
    """Read beneath an opened root without path re-resolution or symlink following."""
    if root.is_symlink():
        raise GateBPreflightError("contained_nofollow:root_symlink")
    relative = Path(reference)
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise GateBPreflightError("contained_nofollow:reference")
    descriptors: list[int] = [_open_directory_nofollow(root)]
    try:
        for part in relative.parts[:-1]:
            descriptors.append(_open_child_directory(descriptors[-1], part))
        with _dry_run_internal_descriptor_io():
            try:
                descriptor = os.open(
                    relative.parts[-1],
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptors[-1],
                )
            except OSError as exc:
                raise GateBPreflightError("contained_nofollow:open") from exc
        stat_result = os.fstat(descriptor)
        if not stat.S_ISREG(stat_result.st_mode):
            os.close(descriptor)
            raise GateBPreflightError("contained_nofollow:not_regular")
        chunks: list[bytes] = []
        try:
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        finally:
            os.close(descriptor)
    finally:
        for descriptor in reversed(descriptors):
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


def _canonical_bytes(value: object) -> bytes:
    return _canonical_json(value).encode("utf-8")


def _secure_package_write(
    *,
    package_root: Path,
    reference: str,
    payload: bytes,
    boundary: DryRunBoundary,
    write_kind: str = "package",
) -> Path:
    relative = Path(reference)
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise GateBPreflightError("package_path_invalid")
    target = package_root / relative
    descriptors = [_open_directory_nofollow(package_root.parent)]
    try:
        descriptors.append(
            _open_child_directory(descriptors[-1], package_root.name, create=True)
        )
        for part in relative.parts[:-1]:
            descriptors.append(
                _open_child_directory(descriptors[-1], part, create=True)
            )
        directory_descriptor = descriptors[-1]
        target_name = relative.parts[-1]
        with _dry_run_internal_descriptor_io():
            try:
                existing = os.open(
                    target_name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_descriptor,
                )
            except FileNotFoundError:
                existing = None
        if existing is not None:
            try:
                if not stat.S_ISREG(os.fstat(existing).st_mode):
                    raise GateBPreflightError("package_content_address_collision")
                chunks: list[bytes] = []
                while chunk := os.read(existing, 1024 * 1024):
                    chunks.append(chunk)
                if b"".join(chunks) != payload:
                    raise GateBPreflightError("package_content_address_collision")
                os.fchmod(existing, 0o600)
                return target
            finally:
                os.close(existing)

        temporary_name = f".{target_name}.{os.getpid()}.{threading.get_ident()}.tmp"
        with _dry_run_internal_descriptor_io():
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory_descriptor,
            )
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        try:
            with _dry_run_internal_descriptor_io():
                os.link(
                    temporary_name,
                    target_name,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
        except FileExistsError:
            with _dry_run_internal_descriptor_io():
                existing = os.open(
                    target_name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_descriptor,
                )
            try:
                chunks = []
                while chunk := os.read(existing, 1024 * 1024):
                    chunks.append(chunk)
                if b"".join(chunks) != payload:
                    raise GateBPreflightError("package_content_address_collision")
            finally:
                os.close(existing)
        finally:
            with _dry_run_internal_descriptor_io():
                try:
                    os.unlink(temporary_name, dir_fd=directory_descriptor)
                except FileNotFoundError:
                    pass
        if write_kind == "corpus":
            boundary.corpus_write(target)
        else:
            boundary.package_write(target)
        return target
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _secure_directory_path(path: Path) -> None:
    with _dry_run_internal_descriptor_io():
        parent_descriptor = _open_directory_nofollow(path.parent)
    try:
        descriptor = _open_child_directory(parent_descriptor, path.name, create=True)
        try:
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)


def _exact_field_spans(value: object, *, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        return ()
    result: list[str] = []
    for match in re.finditer(r"\S+(?: \S+)*", value):
        remaining = match.group(0)
        while remaining and len(result) < maximum:
            if len(remaining) <= 500:
                fragment = remaining
                remaining = ""
            else:
                boundary = remaining.rfind(" ", 0, 501)
                if boundary <= 0:
                    boundary = 500
                fragment = remaining[:boundary]
                remaining = remaining[boundary:].lstrip(" ")
            lowered = fragment.casefold()
            if not any(
                marker in lowered
                for marker in (
                    "http://",
                    "https://",
                    "bearer ",
                    "hermes-private://",
                    "private resume",
                    "user note",
                )
            ):
                result.append(fragment)
        if len(result) >= maximum:
            break
    return tuple(result)


def _vacancy_artifact(
    record: Mapping[str, Any], raw: Mapping[str, Any]
) -> VacancyEvidenceArtifactV1:
    fragments: list[VacancyEvidenceArtifactFragmentV1] = []
    limits = {"title": 1, "location": 1, "description": 6, "posted_at": 1, "salary": 1}
    for field_name, maximum in limits.items():
        for index, text in enumerate(
            _exact_field_spans(raw.get(field_name), maximum=maximum)
        ):
            fragments.append(
                VacancyEvidenceArtifactFragmentV1(
                    source_locator=f"/{field_name}#{index:03d}",
                    text=text,
                )
            )
    if not fragments:
        raise GateBPreflightError("vacancy_has_no_admissible_exact_fragment")
    return VacancyEvidenceArtifactV1(
        schema_version="1.0.0",
        artifact_id=f"gate-b-vacancy:{record['selection_key']}",
        artifact_version="1.0.0",
        redaction_state="shareable_redacted",
        fragments=tuple(fragments),
    )


def _authority_references(vacancy_ref: ImmutableArtifactRef) -> AssessmentReferences:
    profile_payload = yaml.safe_load(
        (REPO_ROOT / "config/product_search/career_profile.v2.yaml").read_text(
            encoding="utf-8"
        )
    )
    authorities = profile_payload["authorities"]
    semantic_path = (
        REPO_ROOT
        / "job_intel/vacancy_understanding/semantic/semantic-fact-contract.yaml"
    )
    return AssessmentReferences(
        profile_ref=ImmutableArtifactRef(
            artifact_id="career-profile-v2",
            version="2.0.0",
            sha256=_sha256_file(
                REPO_ROOT / "config/product_search/career_profile.v2.yaml"
            ),
        ),
        candidate_facts_ref=ImmutableArtifactRef.model_validate(
            authorities["candidate_facts_ref"]
        ),
        semantic_contract_ref=ImmutableArtifactRef(
            artifact_id="semantic-fact-contract",
            version="1.0.0",
            sha256=_sha256_file(semantic_path),
        ),
        search_contract_ref=ImmutableArtifactRef.model_validate(
            authorities["search_contract_ref"]
        ),
        policy_ref=ImmutableArtifactRef.model_validate(authorities["product_sot_ref"]),
        evidence_snapshot_ref=vacancy_ref,
    )


def _build_task10_input(
    record: Mapping[str, Any], raw: Mapping[str, Any]
) -> EvidenceSynthesisInputV2:
    artifact = _vacancy_artifact(record, raw)
    artifact_sha256 = _sha256_bytes(
        _canonical_bytes(artifact.model_dump(mode="json"))
    )
    vacancy_ref = ImmutableArtifactRef(
        artifact_id=artifact.artifact_id,
        version=artifact.artifact_version,
        sha256=artifact_sha256,
    )
    company_labels = {
        str(value).strip().casefold()
        for value in (record.get("company"), raw.get("company"))
        if isinstance(value, str) and value.strip()
    }
    prohibited_company_hashes = tuple(dict.fromkeys(
        _sha256_bytes(item.text.encode("utf-8"))
        for item in artifact.fragments
        if any(label in item.text.casefold() for label in company_labels)
    ))
    prohibited_company_hash_set = set(prohibited_company_hashes)
    fragments: list[EvidenceFragmentV1] = []
    dimension_refs: dict[EvidenceDimension, list[str]] = defaultdict(list)
    locator_dimensions = {
        "title": (EvidenceDimension.MANDATE_FIT, EvidenceDimension.EVIDENCE_CONFIDENCE),
        "location": (EvidenceDimension.FEASIBILITY, EvidenceDimension.EVIDENCE_CONFIDENCE),
        "description": (
            EvidenceDimension.MANDATE_FIT,
            EvidenceDimension.CAREER_VALUE,
            EvidenceDimension.EVIDENCE_CONFIDENCE,
        ),
        "posted_at": (EvidenceDimension.EVIDENCE_CONFIDENCE,),
        "salary": (EvidenceDimension.FEASIBILITY, EvidenceDimension.EVIDENCE_CONFIDENCE),
    }
    selection_prefix = str(record["selection_key"])[:16]
    for index, item in enumerate(artifact.fragments):
        if _sha256_bytes(item.text.encode("utf-8")) in prohibited_company_hash_set:
            continue
        field_name = item.source_locator.split("#", 1)[0].removeprefix("/")
        dimensions = locator_dimensions[field_name]
        fragment_id = f"vacancy:{selection_prefix}:{index:03d}"
        fragments.append(
            EvidenceFragmentV1(
                fragment_id=fragment_id,
                artifact_ref=vacancy_ref,
                source_kind=EvidenceSourceKind.VACANCY,
                source_locator=item.source_locator,
                permitted_dimensions=dimensions,
                text=item.text,
                text_sha256=_sha256_bytes(item.text.encode("utf-8")),
                allowed_claims=tuple(
                    AllowedEvidenceClaimV1(
                        claim_code=f"vacancy_{field_name}_{dimension.value}_explicit",
                        dimension=dimension,
                        status=EvidenceClaimStatus.EXPLICIT,
                        statement=item.text,
                    )
                    for dimension in dimensions
                ),
            )
        )
        for dimension in dimensions:
            dimension_refs[dimension].append(fragment_id)

    unknown_reasons = {
        EvidenceDimension.FEASIBILITY: "feasibility_not_stated_in_vacancy",
        EvidenceDimension.MANDATE_FIT: "mandate_not_stated_in_vacancy",
        EvidenceDimension.COMPANY_FIT: (
            "company_authority_unavailable:unresolved_company_identity"
        ),
        EvidenceDimension.TRANSFERABILITY: "candidate_profile_evidence_not_materialized",
        EvidenceDimension.CAREER_VALUE: "career_value_not_stated_in_vacancy",
        EvidenceDimension.EVIDENCE_CONFIDENCE: "evidence_confidence_not_established",
    }
    dimensions_payload: dict[str, DimensionEvidenceInput] = {}
    for dimension in EvidenceDimension:
        refs = tuple(dimension_refs.get(dimension, ()))
        force_unknown = dimension in {
            EvidenceDimension.COMPANY_FIT,
            EvidenceDimension.TRANSFERABILITY,
        }
        if refs and not force_unknown:
            dimensions_payload[dimension.value] = DimensionEvidenceInput(
                state=DimensionEvidenceState.EVIDENCE_AVAILABLE,
                evidence_refs=refs,
            )
            continue
        reason = unknown_reasons[dimension]
        fragment_id = f"unknown:{selection_prefix}:{dimension.value}"
        fragments.append(
            EvidenceFragmentV1(
                fragment_id=fragment_id,
                artifact_ref=vacancy_ref,
                source_kind=EvidenceSourceKind.ASSESSMENT_UNKNOWN,
                source_locator=reason,
                permitted_dimensions=(dimension,),
                text=reason,
                text_sha256=_sha256_bytes(reason.encode("utf-8")),
                allowed_claims=(
                    AllowedEvidenceClaimV1(
                        claim_code=f"{dimension.value}_unknown",
                        dimension=dimension,
                        status=EvidenceClaimStatus.UNKNOWN,
                        statement=reason,
                    ),
                ),
            )
        )
        dimensions_payload[dimension.value] = DimensionEvidenceInput(
            state=DimensionEvidenceState.UNKNOWN,
            unknown_reasons=(reason,),
        )
    assessment = AssessmentInputV2(
        schema_version="2.0.0",
        assessment_id=f"gate-b:{record['selection_key']}",
        references=_authority_references(vacancy_ref),
        dimensions=DecisionDimensionsInput(**dimensions_payload),
        company_authority_status=CompanyAuthorityStatus.UNAVAILABLE,
    )
    return EvidenceSynthesisInputV2(
        schema_version="2.0.0",
        assessment_input=assessment,
        company_authority=CompanyAuthorityUnavailableV2(
            status="unavailable",
            reason="unresolved_company_identity",
        ),
        vacancy_evidence_ref=vacancy_ref,
        vacancy_evidence=artifact,
        prohibited_company_claim_text_sha256s=prohibited_company_hashes,
        fragments=tuple(fragments),
    )


def _task10_v2_authority_hashes() -> dict[str, str]:
    policy = load_evidence_synthesis_policy()
    pricing = governed_pricing_schedule()
    return {
        "career_profile_sha256": _sha256_file(
            REPO_ROOT / "config/product_search/career_profile.v2.yaml"
        ),
        "candidate_facts_sha256": yaml.safe_load(
            (REPO_ROOT / "config/product_search/career_profile.v2.yaml").read_text(
                encoding="utf-8"
            )
        )["authorities"]["candidate_facts_ref"]["sha256"],
        "company_evidence_contract_sha256": _sha256_file(
            REPO_ROOT / "config/product_search/company_evidence_contract.v1.yaml"
        ),
        "evidence_synthesis_policy_sha256": _sha256_file(
            REPO_ROOT / "config/product_search/evidence_synthesis.v1.yaml"
        ),
        "input_schema_sha256": _sha256_json(
            EvidenceSynthesisInputV2.model_json_schema()
        ),
        "model_sha256": _sha256_bytes(policy.model_id.encode("utf-8")),
        "pricing_sha256": pricing.identity_sha256,
        "profile_sha256": _sha256_file(
            REPO_ROOT / "config/product_search/career_profile.v2.yaml"
        ),
        "provider_output_schema_sha256": provider_output_schema_v2_sha256(),
        "search_contract_sha256": _sha256_file(
            REPO_ROOT / "config/product_search/search_contract.v1.yaml"
        ),
        "semantic_contract_sha256": _sha256_file(
            REPO_ROOT
            / "job_intel/vacancy_understanding/semantic/semantic-fact-contract.yaml"
        ),
        "task10_prompt_sha256": task10_prompt_v2_sha256(policy),
    }


def _materialize_input_package(
    *,
    experiment_root: Path,
    gate_a_root: Path,
    selected: list[dict[str, Any]],
    boundary: DryRunBoundary,
) -> dict[str, Any]:
    package_root = experiment_root / "input-package-v2-r2"
    if package_root.is_symlink():
        raise GateBPreflightError("package_root_symlink")
    authority_hashes = _task10_v2_authority_hashes()
    manifest_records: list[dict[str, Any]] = []
    ordered_hashes: list[str] = []
    for ordinal, record in enumerate(selected):
        raw_reference = str(record["raw_reference"])
        raw_bytes = read_contained_nofollow(gate_a_root, raw_reference)
        boundary.gate_a_read(gate_a_root / raw_reference)
        if _sha256_bytes(raw_bytes) != record["raw_content_sha256"]:
            raise GateBPreflightError("gate_a_raw_content_sha256_mismatch")
        raw = json.loads(raw_bytes)
        task_input = _build_task10_input(record, raw)
        artifact_bytes = _canonical_bytes(
            task_input.vacancy_evidence.model_dump(mode="json")
        )
        artifact_sha256 = _sha256_bytes(artifact_bytes)
        artifact_reference = f"vacancy-artifacts/{artifact_sha256}.json"
        _secure_package_write(
            package_root=package_root,
            reference=artifact_reference,
            payload=artifact_bytes,
            boundary=boundary,
        )
        input_bytes = _canonical_bytes(task_input.provider_payload())
        input_sha256 = _sha256_bytes(input_bytes)
        input_reference = f"task10-inputs/{input_sha256}.json"
        _secure_package_write(
            package_root=package_root,
            reference=input_reference,
            payload=input_bytes,
            boundary=boundary,
        )
        ordered_hashes.append(input_sha256)
        manifest_records.append({
            "ordinal": ordinal,
            "selection_key": record["selection_key"],
            "run_id": record["run_id"],
            "raw_reference": raw_reference,
            "raw_content_sha256": record["raw_content_sha256"],
            "vacancy_artifact_path": artifact_reference,
            "vacancy_artifact_sha256": artifact_sha256,
            "task10_input_path": input_reference,
            "task10_input_sha256": input_sha256,
            "company_authority_sha256": _sha256_json(
                task_input.company_authority.model_dump(mode="json")
            ),
            "authority_hashes": authority_hashes,
        })
    manifest = {
        "schema_version": "2.0.0",
        "gate": "gate-b",
        "corpus_manifest_sha256": GATE_B_CORPUS_SHA256,
        "gate_a": {
            "run_id": GATE_A_RUN_ID,
            "commit": GATE_A_COMMIT,
            "manifest_sha256": GATE_A_MANIFEST_SHA256,
        },
        "company_authority": {
            "status": "unavailable",
            "reason": "unresolved_company_identity",
        },
        "authorization_constraints": {
            "provider_allowlist": ordered_hashes,
            "call_cap": EXACT_CALL_CAP,
            "per_call_maximum_usd": "0.01",
            "aggregate_maximum_usd": "0.48",
            "maximum_output_tokens": governed_pricing_schedule().max_output_tokens,
        },
        "records": manifest_records,
    }
    manifest_bytes = _canonical_bytes(manifest)
    manifest_sha256 = _sha256_bytes(manifest_bytes)
    manifest_path = _secure_package_write(
        package_root=package_root,
        reference="run-manifest.v2.json",
        payload=manifest_bytes,
        boundary=boundary,
    )
    return {
        "status": "materialized",
        "package_root": str(package_root),
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "record_count": len(manifest_records),
        "vacancy_artifact_count": len(
            {record["vacancy_artifact_sha256"] for record in manifest_records}
        ),
        "ordered_input_sha256s": ordered_hashes,
        "ordered_input_hashes_sha256": _sha256_json(ordered_hashes),
        "company_authority_status": "unavailable",
        "company_authority_reason": "unresolved_company_identity",
    }


def load_gate_b_run_manifest(
    path: Path,
    *,
    expected_sha256: str,
    expected_corpus_sha256: str,
) -> dict[str, Any]:
    if path.is_symlink():
        raise GateBPreflightError("run_manifest_symlink")
    payload_bytes = read_contained_nofollow(path.parent, path.name)
    if _sha256_bytes(payload_bytes) != expected_sha256:
        raise GateBPreflightError("input_manifest_hash_mismatch")
    try:
        payload = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateBPreflightError("input_manifest_invalid_json") from exc
    if _canonical_bytes(payload) != payload_bytes:
        raise GateBPreflightError("input_manifest_not_canonical")
    records = payload.get("records")
    constraints = payload.get("authorization_constraints")
    if (
        payload.get("schema_version") != "2.0.0"
        or payload.get("gate") != "gate-b"
        or payload.get("corpus_manifest_sha256") != expected_corpus_sha256
        or payload.get("gate_a")
        != {
            "run_id": GATE_A_RUN_ID,
            "commit": GATE_A_COMMIT,
            "manifest_sha256": GATE_A_MANIFEST_SHA256,
        }
        or not isinstance(records, list)
        or len(records) != EXACT_CALL_CAP
        or not isinstance(constraints, dict)
    ):
        raise GateBPreflightError("input_manifest_contract_mismatch")
    ordinals = [record.get("ordinal") for record in records]
    selection_keys = [record.get("selection_key") for record in records]
    input_hashes = [record.get("task10_input_sha256") for record in records]
    run_ids = [record.get("run_id") for record in records]
    if (
        ordinals != list(range(EXACT_CALL_CAP))
        or selection_keys != sorted(selection_keys)
        or len(set(selection_keys)) != EXACT_CALL_CAP
        or len(set(input_hashes)) != EXACT_CALL_CAP
        or run_ids != [GATE_A_RUN_ID] * EXACT_CALL_CAP
    ):
        raise GateBPreflightError("input_manifest_order_or_duplicate")
    for record in records:
        for field_name in ("raw_reference", "vacancy_artifact_path", "task10_input_path"):
            reference = record.get(field_name)
            if (
                not isinstance(reference, str)
                or Path(reference).is_absolute()
                or ".." in Path(reference).parts
            ):
                raise GateBPreflightError("input_manifest_mutable_path")
        if record["task10_input_path"] != (
            f"task10-inputs/{record['task10_input_sha256']}.json"
        ) or record["vacancy_artifact_path"] != (
            f"vacancy-artifacts/{record['vacancy_artifact_sha256']}.json"
        ):
            raise GateBPreflightError("input_manifest_content_address_mismatch")
    if constraints != {
        "aggregate_maximum_usd": "0.48",
        "call_cap": EXACT_CALL_CAP,
        "maximum_output_tokens": governed_pricing_schedule().max_output_tokens,
        "per_call_maximum_usd": "0.01",
        "provider_allowlist": input_hashes,
    }:
        raise GateBPreflightError("input_manifest_authorization_constraints")
    return payload


def load_gate_b_task10_input(
    *,
    package_root: Path,
    record: Mapping[str, Any],
    gate_a_root: Path,
) -> EvidenceSynthesisInputV2:
    input_bytes = read_contained_nofollow(
        package_root, str(record["task10_input_path"])
    )
    if _sha256_bytes(input_bytes) != record["task10_input_sha256"]:
        raise GateBPreflightError("task10_input_hash_mismatch")
    try:
        task_input = EvidenceSynthesisInputV2.model_validate_json(input_bytes)
    except Exception as exc:
        raise GateBPreflightError("task10_input_contract_mismatch") from exc
    artifact_bytes = read_contained_nofollow(
        package_root, str(record["vacancy_artifact_path"])
    )
    if (
        _sha256_bytes(artifact_bytes) != record["vacancy_artifact_sha256"]
        or json.loads(artifact_bytes)
        != task_input.vacancy_evidence.model_dump(mode="json")
    ):
        raise GateBPreflightError("vacancy_artifact_hash_mismatch")
    raw_bytes = read_contained_nofollow(gate_a_root, str(record["raw_reference"]))
    if _sha256_bytes(raw_bytes) != record["raw_content_sha256"]:
        raise GateBPreflightError("corpus_source_changed:input_loader")
    expected = _build_task10_input(record, json.loads(raw_bytes))
    if expected.model_dump(mode="json") != task_input.model_dump(mode="json"):
        raise GateBPreflightError("task10_input_not_exact_source_projection")
    return task_input


def _record_identity(
    corpus_sha256: str,
    *,
    input_manifest_sha256: str,
    ordered_input_hashes_sha256: str,
) -> dict[str, str]:
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
        "provider_output_schema_sha256": provider_output_schema_v2_sha256(),
        "provider_output_schema_version": OUTPUT_SCHEMA_VERSION_V2,
        "semantic_prompt_sha256": _sha256_bytes(semantic_prompt.encode("utf-8")),
        "semantic_prompt_version": policy.semantic_prompt_version,
        "task10_prompt_version": TASK10_PROMPT_VERSION_V2,
        "task10_prompt_sha256": task10_prompt_v2_sha256(policy),
        "provider_adapter_version": PROVIDER_ADAPTER_VERSION_V2,
        "input_manifest_sha256": input_manifest_sha256,
        "ordered_input_hashes_sha256": ordered_input_hashes_sha256,
        "model_id": policy.model_id,
        "model_sha256": _sha256_bytes(policy.model_id.encode("utf-8")),
        "pricing_sha256": pricing.identity_sha256,
        "pricing_version": pricing.version,
        "max_output_tokens": str(pricing.max_output_tokens),
    })
    return identity


def _build_dry_run_preflight_core(
    *,
    gate_a_root: Path,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    boundary_attempt: Any = None,
    boundary: DryRunBoundary,
) -> dict[str, Any]:
    if sample_size != EXACT_CALL_CAP:
        raise GateBPreflightError("exact_sample_size_required:48")
    gate_a_root = gate_a_root.resolve()
    output_root = GATE_B_EXPERIMENT_ROOT
    if output_root.is_symlink():
        raise GateBPreflightError("workspace_symlink")
    gate_a_before = _gate_a_snapshot(gate_a_root, boundary)
    protected_before = _protected_metadata_snapshot()
    if boundary_attempt is not None:
        try:
            boundary_attempt(boundary)
        except GateBPreflightError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise GateBPreflightError(
                "dry_run_io_denied:preopened_handle"
            ) from exc
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
    _secure_directory_path(output_root)
    _secure_package_write(
        package_root=experiment_root,
        reference=manifest_path.name,
        payload=corpus_bytes,
        boundary=boundary,
        write_kind="corpus",
    )
    input_package = _materialize_input_package(
        experiment_root=experiment_root,
        gate_a_root=gate_a_root,
        selected=selected,
        boundary=boundary,
    )
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
    identity = _record_identity(
        corpus_sha256,
        input_manifest_sha256=input_package["manifest_sha256"],
        ordered_input_hashes_sha256=input_package["ordered_input_hashes_sha256"],
    )
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
        "inputs": input_package,
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


def build_dry_run_preflight(
    *,
    gate_a_root: Path,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    boundary_attempt: Any = None,
) -> dict[str, Any]:
    """Run preparation in a child with no usable inherited I/O handles."""
    if not hasattr(os, "fork"):
        raise GateBPreflightError("dry_run_process_isolation_unavailable")
    inherited_descriptors = [
        int(name)
        for name in os.listdir("/proc/self/fd")
        if name.isdigit() and int(name) >= 3
    ]
    inherited_max = max(inherited_descriptors, default=2)
    read_descriptor, write_descriptor = os.pipe()
    process_id = os.fork()
    if process_id == 0:
        os.close(read_descriptor)
        try:
            for descriptor in range(3, inherited_max + 1):
                if descriptor != write_descriptor:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            fillers: list[int] = []
            for expected in range(3, inherited_max + 1):
                if expected == write_descriptor:
                    continue
                descriptor = os.open(os.devnull, os.O_RDONLY)
                if descriptor != expected:
                    raise GateBPreflightError("dry_run_descriptor_isolation_failed")
                fillers.append(descriptor)
            boundary = DryRunBoundary()
            with _dry_run_io_enforcement(
                gate_a_root=gate_a_root,
                output_root=GATE_B_EXPERIMENT_ROOT,
                boundary=boundary,
            ):
                result = _build_dry_run_preflight_core(
                    gate_a_root=gate_a_root,
                    sample_size=sample_size,
                    boundary_attempt=boundary_attempt,
                    boundary=boundary,
                )
            message = {"ok": True, "result": result}
        except BaseException as exc:
            message = {
                "ok": False,
                "gate_b_error": isinstance(exc, GateBPreflightError),
                "message": str(exc),
                "type": type(exc).__name__,
            }
        payload = _canonical_json(message).encode("utf-8")
        try:
            while payload:
                written = os.write(write_descriptor, payload)
                payload = payload[written:]
        finally:
            os.close(write_descriptor)
        os._exit(0)
    os.close(write_descriptor)
    chunks: list[bytes] = []
    try:
        while True:
            chunk = os.read(read_descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(read_descriptor)
    _, wait_status = os.waitpid(process_id, 0)
    if wait_status != 0:
        raise GateBPreflightError(f"dry_run_child_failed:{wait_status}")
    try:
        message = json.loads(b"".join(chunks))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GateBPreflightError("dry_run_child_protocol") from exc
    if not message.get("ok"):
        if message.get("gate_b_error"):
            raise GateBPreflightError(str(message.get("message")))
        raise GateBPreflightError(
            f"dry_run_child_error:{message.get('type')}:{message.get('message')}"
        )
    return dict(message["result"])


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
    input_manifest_sha256: str
    input_manifest_path: Path
    package_root: Path
    ordered_input_sha256s: tuple[str, ...]
    gate_a_root: Path
    record_count: int
    _owner_capability_sha256: str
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
    if approval_record.get("schema_version") != "2.0.0":
        raise GateBPreflightError("approval_schema_version")
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
        or Decimal(str(approval_record.get("max_cost_per_call_usd")))
        != DEFAULT_MAX_COST_PER_CALL_USD
        or str(approval_record.get("max_output_tokens"))
        != str(governed_pricing_schedule().max_output_tokens)
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
    inputs = preflight.get("inputs", {})
    expected_input_manifest = str(inputs.get("manifest_sha256"))
    expected_ordered_hashes = str(inputs.get("ordered_input_hashes_sha256"))
    if (
        approval_record.get("input_manifest_sha256") != expected_input_manifest
        or approval_record.get("ordered_input_hashes_sha256")
        != expected_ordered_hashes
        or identity.get("input_manifest_sha256") != expected_input_manifest
        or identity.get("ordered_input_hashes_sha256") != expected_ordered_hashes
    ):
        raise GateBPreflightError("input_identity_mismatch")
    input_manifest_path = Path(str(inputs.get("manifest_path")))
    package_root = Path(str(inputs.get("package_root")))
    if (
        package_root.parent.resolve(strict=True) != experiment_root.resolve(strict=True)
        or input_manifest_path.parent.resolve(strict=True)
        != package_root.resolve(strict=True)
    ):
        raise GateBPreflightError("input_manifest_root_mismatch")
    input_manifest = load_gate_b_run_manifest(
        input_manifest_path,
        expected_sha256=expected_input_manifest,
        expected_corpus_sha256=expected_corpus,
    )
    ordered_input_sha256s = tuple(
        record["task10_input_sha256"] for record in input_manifest["records"]
    )
    if (
        _sha256_json(ordered_input_sha256s) != expected_ordered_hashes
        or list(ordered_input_sha256s) != inputs.get("ordered_input_sha256s")
    ):
        raise GateBPreflightError("input_allowlist_mismatch")
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
        input_manifest_sha256=expected_input_manifest,
        input_manifest_path=input_manifest_path,
        package_root=package_root,
        ordered_input_sha256s=ordered_input_sha256s,
        gate_a_root=gate_a_root,
        record_count=len(corpus["records"]),
        _owner_capability_sha256=_sha256_bytes(
            owner_capability.encode("utf-8")
        ),
        _metadata_seal_key=hashlib.sha256(
            b"gate-b-record-seal\x00"
            + owner_capability.encode("utf-8")
            + identity_sha256.encode("ascii")
        ).digest(),
        _issuer=_AUTHORIZATION_ISSUER,
    )


class _LedgerConnectionLease:
    """Serialize one persistent SQLite handle without exposing close semantics."""

    def __init__(
        self, connection: sqlite3.Connection, lock: threading.RLock
    ) -> None:
        self._connection = connection
        self._lock = lock
        self._closed = False
        self._lock.acquire()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._lock.release()


class GateBBudgetLedger:
    """Transactional call/spend reservations persisted inside one Gate B run."""

    def __init__(self, path: Path, authorization: GateBRunAuthorization) -> None:
        if not isinstance(authorization, GateBRunAuthorization):
            raise GateBPreflightError("runner_authorization_required")
        self.path = path
        self.authorization = authorization
        if Path(os.path.abspath(path.parent)) != Path(
            os.path.abspath(authorization.experiment_root)
        ):
            raise GateBPreflightError("ledger_path_outside_run")
        self._root_descriptor = _open_directory_nofollow(
            authorization.experiment_root
        )
        try:
            path_stat = os.stat(
                path.name,
                dir_fd=self._root_descriptor,
                follow_symlinks=False,
            )
            if stat.S_ISLNK(path_stat.st_mode):
                self.close()
                raise GateBPreflightError("ledger_final_symlink")
        except FileNotFoundError:
            try:
                self._ledger_marker_descriptor = os.open(
                    path.name,
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=self._root_descriptor,
                )
            except OSError as exc:
                self.close()
                raise GateBPreflightError("ledger_path_unsafe") from exc
        else:
            try:
                self._ledger_marker_descriptor = os.open(
                    path.name,
                    os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=self._root_descriptor,
                )
            except OSError as exc:
                self.close()
                raise GateBPreflightError("ledger_path_unsafe") from exc
        marker_stat = os.fstat(self._ledger_marker_descriptor)
        if not stat.S_ISREG(marker_stat.st_mode):
            self.close()
            raise GateBPreflightError("ledger_path_unsafe")
        os.fchmod(self._ledger_marker_descriptor, 0o600)
        private_name = (
            ".gate-b-ledger-"
            f"{authorization.run_identity_sha256[:16]}"
        )
        try:
            try:
                os.mkdir(private_name, mode=0o700, dir_fd=self._root_descriptor)
            except FileExistsError:
                pass
            self._ledger_directory_descriptor = _open_child_directory(
                self._root_descriptor, private_name
            )
        except (OSError, GateBPreflightError) as exc:
            self.close()
            raise GateBPreflightError("ledger_path_unsafe") from exc
        ledger_directory_stat = os.fstat(self._ledger_directory_descriptor)
        if (
            not stat.S_ISDIR(ledger_directory_stat.st_mode)
            or ledger_directory_stat.st_uid != os.geteuid()
            or ledger_directory_stat.st_mode & 0o077
        ):
            self.close()
            raise GateBPreflightError("ledger_path_unsafe")
        try:
            self._database_guard_descriptor = os.open(
                "ledger.db",
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=self._ledger_directory_descriptor,
            )
        except OSError as exc:
            self.close()
            raise GateBPreflightError("ledger_path_unsafe") from exc
        database_stat = os.fstat(self._database_guard_descriptor)
        if (
            not stat.S_ISREG(database_stat.st_mode)
            or database_stat.st_uid != os.geteuid()
        ):
            self.close()
            raise GateBPreflightError("ledger_path_unsafe")
        os.fchmod(self._database_guard_descriptor, 0o600)
        self._connection_lock = threading.RLock()
        database_uri = (
            f"file:/proc/self/fd/{self._ledger_directory_descriptor}/ledger.db"
            "?mode=rwc"
        )
        self._connection = sqlite3.connect(
            database_uri,
            timeout=30,
            uri=True,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _connect(self) -> _LedgerConnectionLease:
        connection = getattr(self, "_connection", None)
        if connection is None:
            raise GateBPreflightError("ledger_closed")
        return _LedgerConnectionLease(
            connection,
            self._connection_lock,
        )

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            with self._connection_lock:
                connection.close()
                self._connection = None
        database_guard_descriptor = getattr(
            self, "_database_guard_descriptor", None
        )
        if database_guard_descriptor is not None:
            os.close(database_guard_descriptor)
            self._database_guard_descriptor = None
        ledger_directory_descriptor = getattr(
            self, "_ledger_directory_descriptor", None
        )
        if ledger_directory_descriptor is not None:
            os.close(ledger_directory_descriptor)
            self._ledger_directory_descriptor = None
        ledger_marker_descriptor = getattr(
            self, "_ledger_marker_descriptor", None
        )
        if ledger_marker_descriptor is not None:
            os.close(ledger_marker_descriptor)
            self._ledger_marker_descriptor = None
        root_descriptor = getattr(self, "_root_descriptor", None)
        if root_descriptor is not None:
            os.close(root_descriptor)
            self._root_descriptor = None

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass

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
                "status TEXT NOT NULL CHECK(status IN "
                "('reserved','charge_unknown','success','failure')));"
                "CREATE TABLE IF NOT EXISTS run_inputs ("
                "ordinal INTEGER PRIMARY KEY, input_hash TEXT UNIQUE NOT NULL);"
                "CREATE TABLE IF NOT EXISTS charge_unknown_reconciliation ("
                "input_hash TEXT PRIMARY KEY, run_identity_sha256 TEXT NOT NULL, "
                "disposition TEXT NOT NULL, cost_semantics TEXT NOT NULL, "
                "actual_microusd INTEGER NOT NULL, "
                "provider_evidence_sha256 TEXT NOT NULL, "
                "owner_capability_sha256 TEXT NOT NULL, "
                "record_metadata_sha256 TEXT NOT NULL);"
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
            existing_inputs = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT input_hash FROM run_inputs ORDER BY ordinal"
                )
            )
            if not existing_inputs:
                connection.executemany(
                    "INSERT INTO run_inputs VALUES (?, ?)",
                    enumerate(self.authorization.ordered_input_sha256s),
                )
            elif existing_inputs != self.authorization.ordered_input_sha256s:
                raise GateBPreflightError("ledger_input_allowlist_mismatch")
            connection.commit()
        finally:
            connection.close()

    def reserve(self, input_hash: str, amount: Decimal) -> str:
        amount_micros = _usd_to_micros(amount)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM run_inputs WHERE input_hash = ?", (input_hash,)
            ).fetchone() is None:
                raise GateBPreflightError("input_not_allowlisted")
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
            if row is None or row["status"] not in {"reserved", "charge_unknown"}:
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

    def mark_dispatching(self, reservation_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM call_ledger WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None or row["status"] != "reserved":
                raise GateBPreflightError("dispatch_state_invalid")
            connection.execute(
                "UPDATE call_ledger SET status = 'charge_unknown' "
                "WHERE reservation_id = ?",
                (reservation_id,),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def call_state(self, input_hash: str) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT status FROM call_ledger WHERE input_hash = ?", (input_hash,)
            ).fetchone()
            return None if row is None else str(row["status"])
        finally:
            connection.close()

    def retry_reserved_without_record(self, input_hash: str) -> bool:
        """Release only a crash-left reservation after the runner saw no record.

        The caller holds the run-manifest lock, so no second governed call can
        still be publishing the same content-addressed recording.
        """
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM call_ledger WHERE input_hash = ?", (input_hash,)
            ).fetchone()
            if row is None:
                connection.commit()
                return False
            if row["status"] == "charge_unknown":
                raise GateBPreflightError("owner_reconciliation_required:charge_unknown")
            if row["status"] != "reserved":
                raise GateBPreflightError("completed_call_without_record")
            connection.execute(
                "DELETE FROM call_ledger WHERE input_hash = ?", (input_hash,)
            )
            connection.commit()
            return True
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
                "SUM(CASE WHEN status IN ('success','failure') THEN 1 ELSE 0 END) "
                "AS completed, "
                "COALESCE(SUM(CASE WHEN status IN ('reserved','charge_unknown') "
                "THEN reserved_microusd "
                "ELSE 0 END), 0) AS outstanding, "
                "COALESCE(SUM(CASE WHEN actual_microusd IS NOT NULL "
                "THEN actual_microusd ELSE 0 END), 0) AS actual, "
                "COALESCE(SUM(CASE WHEN actual_microusd IS NULL THEN reserved_microusd "
                "ELSE actual_microusd END), 0) AS spend FROM call_ledger"
            ).fetchone()
            return {
                "calls_reserved": row["reserved"],
                "calls_completed": row["completed"] or 0,
                "outstanding_reserved_usd": _micros_to_usd(row["outstanding"]),
                "measured_actual_usd": _micros_to_usd(row["actual"]),
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
        reconciliation = self._validated_reconciliation(
            input_hash=input_hash,
            record=record,
            actual_cost=actual_cost,
        )
        actual_micros = _usd_to_micros(actual_cost)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM call_ledger WHERE input_hash = ?", (input_hash,)
            ).fetchone()
            if row is None:
                raise GateBPreflightError("recording_without_reservation")
            if actual_micros > row["reserved_microusd"]:
                raise GateBPreflightError("actual_cost_exceeds_reservation")
            expected_audit = None
            if reconciliation is not None:
                expected_audit = (
                    self.authorization.run_identity_sha256,
                    reconciliation["disposition"],
                    reconciliation["cost_semantics"],
                    actual_micros,
                    reconciliation["provider_evidence_sha256"],
                    reconciliation["owner_capability_sha256"],
                    str(record["metadata_sha256"]),
                )
            if row["status"] in {"reserved", "charge_unknown"}:
                if reconciliation is not None and row["status"] != "charge_unknown":
                    raise GateBPreflightError("reconciliation_state_invalid")
                if reconciliation is not None:
                    connection.execute(
                        "INSERT OR IGNORE INTO charge_unknown_reconciliation "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            input_hash,
                            self.authorization.run_identity_sha256,
                            reconciliation["disposition"],
                            reconciliation["cost_semantics"],
                            actual_micros,
                            reconciliation["provider_evidence_sha256"],
                            reconciliation["owner_capability_sha256"],
                            str(record["metadata_sha256"]),
                        ),
                    )
                    audit = connection.execute(
                        "SELECT * FROM charge_unknown_reconciliation "
                        "WHERE input_hash = ?",
                        (input_hash,),
                    ).fetchone()
                    if audit is None or tuple(audit)[1:] != expected_audit:
                        raise GateBPreflightError("reconciliation_audit_mismatch")
                connection.execute(
                    "UPDATE call_ledger SET actual_microusd = ?, status = ? "
                    "WHERE input_hash = ?",
                    (actual_micros, outcome, input_hash),
                )
                connection.commit()
                return
            if row["status"] != outcome or row["actual_microusd"] != actual_micros:
                raise GateBPreflightError("recording_ledger_mismatch")
            if reconciliation is not None:
                audit = connection.execute(
                    "SELECT * FROM charge_unknown_reconciliation "
                    "WHERE input_hash = ?",
                    (input_hash,),
                ).fetchone()
                if audit is None or tuple(audit)[1:] != expected_audit:
                    raise GateBPreflightError("reconciliation_audit_mismatch")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _validated_reconciliation(
        self,
        *,
        input_hash: str,
        record: Mapping[str, Any],
        actual_cost: Decimal,
    ) -> Mapping[str, Any] | None:
        reconciliation = record.get("charge_unknown_reconciliation")
        if reconciliation is None:
            return None
        if not isinstance(reconciliation, Mapping) or set(reconciliation) != {
            "schema_version",
            "run_identity_sha256",
            "input_hash",
            "disposition",
            "cost_semantics",
            "provider_evidence_sha256",
            "owner_capability_sha256",
        }:
            raise GateBPreflightError("reconciliation_record_invalid")
        if (
            reconciliation.get("schema_version") != "1.0.0"
            or reconciliation.get("run_identity_sha256")
            != self.authorization.run_identity_sha256
            or reconciliation.get("input_hash") != input_hash
            or reconciliation.get("owner_capability_sha256")
            != self.authorization._owner_capability_sha256
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(reconciliation.get("provider_evidence_sha256")),
            )
        ):
            raise GateBPreflightError("reconciliation_record_identity_invalid")
        pairs = {
            "confirmed_unbilled": "confirmed_zero",
            "confirmed_charged_measured": "measured",
            "charge_amount_unknown": "unknown_reserved_max",
        }
        disposition = str(reconciliation.get("disposition"))
        if reconciliation.get("cost_semantics") != pairs.get(disposition):
            raise GateBPreflightError("reconciliation_cost_semantics_invalid")
        if disposition == "confirmed_unbilled" and actual_cost != Decimal("0"):
            raise GateBPreflightError("reconciliation_cost_invalid")
        if (
            disposition == "confirmed_charged_measured"
            and actual_cost <= Decimal("0")
        ):
            raise GateBPreflightError("reconciliation_cost_invalid")
        if (
            disposition == "charge_amount_unknown"
            and actual_cost != governed_pricing_schedule().reservation_cost_usd
        ):
            raise GateBPreflightError("reconciliation_cost_invalid")
        return reconciliation

    def reconciliation_for(self, input_hash: str) -> dict[str, str] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM charge_unknown_reconciliation WHERE input_hash = ?",
                (input_hash,),
            ).fetchone()
            if row is None:
                return None
            return {
                "disposition": str(row["disposition"]),
                "cost_semantics": str(row["cost_semantics"]),
                "actual_cost_usd": _micros_to_usd(row["actual_microusd"]),
                "provider_evidence_sha256": str(
                    row["provider_evidence_sha256"]
                ),
                "run_identity_sha256": str(row["run_identity_sha256"]),
            }
        finally:
            connection.close()

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
            mark_dispatching=self.mark_dispatching,
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


def reconcile_gate_b_charge_unknown(
    *,
    authorization: GateBRunAuthorization,
    owner_capability: str,
    input_hash: str,
    disposition: str,
    measured_cost_usd: Decimal | None,
    reconciliation_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Governed owner action that terminally accounts for one ambiguous call.

    This path is offline: it either replays an already sealed recording or
    writes a sealed failure record with an explicit accounting disposition.
    It never constructs or enters a live provider transport.
    """
    if not isinstance(authorization, GateBRunAuthorization) or (
        authorization._issuer is not _AUTHORIZATION_ISSUER
    ):
        raise GateBPreflightError("runner_authorization_required")
    if not isinstance(owner_capability, str):
        raise GateBPreflightError("owner_capability_missing_or_mismatch")
    supplied_owner_sha256 = _sha256_bytes(owner_capability.encode("utf-8"))
    if supplied_owner_sha256 != authorization._owner_capability_sha256:
        raise GateBPreflightError("owner_capability_missing_or_mismatch")
    if input_hash not in authorization.ordered_input_sha256s:
        raise GateBPreflightError("input_not_allowlisted")
    expected_evidence = {
        "schema_version": "1.0.0",
        "run_identity_sha256": authorization.run_identity_sha256,
        "input_hash": input_hash,
        "disposition": disposition,
        "provider_evidence_sha256": reconciliation_evidence.get(
            "provider_evidence_sha256"
        ),
    }
    if (
        dict(reconciliation_evidence) != expected_evidence
        or not re.fullmatch(
            r"[0-9a-f]{64}",
            str(reconciliation_evidence.get("provider_evidence_sha256")),
        )
    ):
        raise GateBPreflightError("reconciliation_evidence_invalid")
    semantics = {
        "confirmed_unbilled": "confirmed_zero",
        "confirmed_charged_measured": "measured",
        "charge_amount_unknown": "unknown_reserved_max",
    }
    if disposition not in semantics:
        raise GateBPreflightError("reconciliation_disposition_invalid")
    reservation_cost = governed_pricing_schedule().reservation_cost_usd
    if disposition == "confirmed_unbilled":
        if measured_cost_usd is not None and (
            not isinstance(measured_cost_usd, Decimal)
            or measured_cost_usd != Decimal("0")
        ):
            raise GateBPreflightError("reconciliation_measured_cost_invalid")
        actual_cost = Decimal("0.000000")
    elif disposition == "confirmed_charged_measured":
        if not isinstance(measured_cost_usd, Decimal):
            raise GateBPreflightError("reconciliation_measured_cost_required")
        actual_cost = measured_cost_usd
        if (
            not actual_cost.is_finite()
            or actual_cost <= 0
            or actual_cost > reservation_cost
        ):
            raise GateBPreflightError("reconciliation_measured_cost_invalid")
        _usd_to_micros(actual_cost)
    else:
        if measured_cost_usd is not None:
            raise GateBPreflightError("reconciliation_measured_cost_invalid")
        actual_cost = reservation_cost

    manifest = load_gate_b_run_manifest(
        authorization.input_manifest_path,
        expected_sha256=authorization.input_manifest_sha256,
        expected_corpus_sha256=authorization.corpus_manifest_sha256,
    )
    record_manifest = next(
        (
            record
            for record in manifest["records"]
            if record["task10_input_sha256"] == input_hash
        ),
        None,
    )
    if record_manifest is None:
        raise GateBPreflightError("input_not_allowlisted")
    synthesis_input = load_gate_b_task10_input(
        package_root=authorization.package_root,
        record=record_manifest,
        gate_a_root=authorization.gate_a_root,
    )
    policy = load_evidence_synthesis_policy()
    store = RecordingStore(authorization.experiment_root / "recordings")
    semantic_provider = LLMObservationProvider(
        store=store,
        mode="replay",
        model_id=policy.model_id,
        prompt_version=policy.semantic_prompt_version,
    )
    ledger = GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )
    capability = ledger.structured_capability()
    adapter = RecordedEvidenceSynthesisProviderV2(
        semantic_provider=semantic_provider,
        policy=policy,
        pricing=governed_pricing_schedule(),
        record_capability=capability,
    )
    try:
        existing = store.load(input_hash)
    except LLMProviderError as exc:
        if exc.reason != "recording_missing":
            raise GateBPreflightError("reconciliation_recording_unsafe") from exc
    else:
        ledger.reconcile_existing_record(input_hash, existing, capability)
        recorded_reconciliation = existing.get("charge_unknown_reconciliation")
        recorded_semantics = (
            recorded_reconciliation.get("cost_semantics")
            if isinstance(recorded_reconciliation, Mapping)
            else "measured"
        )
        return {
            "status": "sealed_record_replayed",
            "cost_semantics": recorded_semantics,
            "cost_usd": str(existing["cost_usd"]),
            "input_hash": input_hash,
            "record_replayed": True,
        }
    if ledger.call_state(input_hash) != "charge_unknown":
        raise GateBPreflightError("reconciliation_state_invalid")
    reconciliation = {
        "schema_version": "1.0.0",
        "run_identity_sha256": authorization.run_identity_sha256,
        "input_hash": input_hash,
        "disposition": disposition,
        "cost_semantics": semantics[disposition],
        "provider_evidence_sha256": reconciliation_evidence[
            "provider_evidence_sha256"
        ],
        "owner_capability_sha256": supplied_owner_sha256,
    }
    terminal_record = adapter.build_charge_unknown_reconciliation_record(
        input_payload=synthesis_input.provider_payload(),
        reconciliation=reconciliation,
        cost_usd=actual_cost,
    )
    try:
        store.save_exclusive(terminal_record)
    except LLMProviderError as exc:
        if exc.reason != "recording_exists":
            raise GateBPreflightError("reconciliation_recording_write_failed") from exc
        terminal_record = store.load(input_hash)
    ledger.reconcile_existing_record(input_hash, terminal_record, capability)
    return {
        "status": "terminal_failure_recorded",
        "cost_semantics": semantics[disposition],
        "cost_usd": f"{actual_cost:.6f}",
        "input_hash": input_hash,
        "record_replayed": False,
    }


def run_gate_b_record(
    *,
    authorization: GateBRunAuthorization,
) -> list[Any]:
    """Sole resumable record entrypoint; provider and inputs are runner-owned."""
    if not isinstance(authorization, GateBRunAuthorization):
        raise GateBPreflightError("runner_authorization_required")
    if authorization._issuer is not _AUTHORIZATION_ISSUER:
        raise GateBPreflightError("authorization_issuer_invalid")
    if authorization.record_count != EXACT_CALL_CAP:
        raise GateBPreflightError("runner_corpus_count_mismatch")
    recording_root = authorization.experiment_root / "recordings"
    if recording_root.is_symlink():
        raise GateBPreflightError("recording_root_symlink")
    policy = load_evidence_synthesis_policy()
    semantic_provider = build_live_llm_provider(
        store_dir=recording_root,
        model_id=policy.model_id,
        prompt_version=policy.semantic_prompt_version,
    )
    input_manifest_path = authorization.input_manifest_path
    descriptor = os.open(
        input_manifest_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
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
        if _sha256_bytes(manifest_bytes) != authorization.input_manifest_sha256:
            raise GateBPreflightError("input_manifest_changed:runner")
        manifest = json.loads(manifest_bytes)
        if _canonical_bytes(manifest) != manifest_bytes:
            raise GateBPreflightError("input_manifest_not_canonical:runner")
        records = manifest.get("records")
        if not isinstance(records, list) or len(records) != EXACT_CALL_CAP:
            raise GateBPreflightError("runner_corpus_count_mismatch")
        if tuple(record.get("task10_input_sha256") for record in records) != (
            authorization.ordered_input_sha256s
        ):
            raise GateBPreflightError("runner_input_allowlist_mismatch")
        ledger = GateBBudgetLedger(
            authorization.experiment_root / "run-ledger.sqlite3", authorization
        )
        capability = ledger.structured_capability()
        adapter = RecordedEvidenceSynthesisProviderV2(
            semantic_provider=semantic_provider,
            policy=policy,
            pricing=governed_pricing_schedule(),
            record_capability=capability,
        )
        results: list[Any] = []
        for record in records:
            synthesis_input = load_gate_b_task10_input(
                package_root=authorization.package_root,
                record=record,
                gate_a_root=authorization.gate_a_root,
            )
            input_hash = synthesis_input_sha256(
                synthesis_input.provider_payload(), provider=adapter
            )
            if input_hash != record.get("task10_input_sha256"):
                raise GateBPreflightError("runner_task10_input_hash_mismatch")
            try:
                existing = semantic_provider.store.load(input_hash)
            except LLMProviderError as exc:
                if exc.reason != "recording_missing":
                    raise GateBPreflightError("recording_load_failed") from exc
                ledger.retry_reserved_without_record(input_hash)
            else:
                ledger.reconcile_existing_record(input_hash, existing, capability)
            results.append(
                run_evidence_synthesis_v2(
                    synthesis_input=synthesis_input,
                    provider=adapter,
                    policy=policy,
                )
            )
        return results
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
