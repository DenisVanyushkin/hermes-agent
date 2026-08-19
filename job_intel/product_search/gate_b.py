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
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import threading
import time
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
RECORD_STATE_PROTOCOL_VERSION = "gate-b-record-state-r3"
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
        self.main_thread_ident = threading.get_ident()
        self.callback_active = False

    def deny(self, category: str, event: str) -> None:
        self.boundary.denied[category] += 1
        raise GateBPreflightError(f"dry_run_io_denied:{category}:{event}")

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        if _DRY_RUN_INTERNAL_IO.get():
            return
        path_events = {
            "open",
            "sqlite3.connect",
            "os.listdir",
            "os.scandir",
            "os.mkdir",
            "os.chmod",
            "os.remove",
            "os.rename",
            "os.link",
            "os.symlink",
            "os.truncate",
        }
        if event in path_events and (
            self.callback_active
            or threading.get_ident() != self.main_thread_ident
        ):
            self.deny(
                "production_write" if event != "open" else "protected_read",
                event,
            )
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
    package_root = experiment_root / "input-package-v2-r3"
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
        "record_state_protocol_version": RECORD_STATE_PROTOCOL_VERSION,
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
        policy = _DRY_RUN_POLICY.get() or _ACTIVE_DRY_RUN_POLICY
        if policy is None:
            raise GateBPreflightError("dry_run_policy_missing")
        policy.callback_active = True
        try:
            boundary_attempt(boundary)
        except GateBPreflightError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise GateBPreflightError(
                "dry_run_io_denied:preopened_handle"
            ) from exc
        finally:
            policy.callback_active = False
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
    read_descriptor, write_descriptor = os.pipe()
    process_id = os.fork()
    if process_id == 0:
        child_exit_status = 1
        try:
            os.close(read_descriptor)
            null_descriptor = os.open(os.devnull, os.O_RDONLY)
            child_descriptors = {
                int(name)
                for name in os.listdir("/proc/self/fd")
                if name.isdigit()
            }
            child_descriptors.update({0, 1, 2})
            for descriptor in sorted(child_descriptors):
                if descriptor not in {write_descriptor, null_descriptor}:
                    try:
                        os.dup2(null_descriptor, descriptor, inheritable=False)
                    except OSError as exc:
                        raise GateBPreflightError(
                            "dry_run_descriptor_isolation_failed"
                        ) from exc
            boundary = DryRunBoundary()
            with _dry_run_io_enforcement(
                gate_a_root=gate_a_root,
                output_root=GATE_B_EXPERIMENT_ROOT,
                boundary=boundary,
            ):
                try:
                    try:
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
                    try:
                        current = threading.current_thread()
                        deadline = time.monotonic() + 1.0
                        while True:
                            children = [
                                thread
                                for thread in threading.enumerate()
                                if thread is not current and thread.is_alive()
                            ]
                            if not children:
                                break
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise GateBPreflightError(
                                    "dry_run_child_thread_timeout"
                                )
                            for thread in children:
                                thread.join(remaining)
                    except BaseException as exc:
                        message = {
                            "ok": False,
                            "gate_b_error": isinstance(exc, GateBPreflightError),
                            "message": str(exc),
                            "type": type(exc).__name__,
                        }
                    payload = _canonical_json(message).encode("utf-8")
                    while payload:
                        written = os.write(write_descriptor, payload)
                        if written <= 0:
                            raise GateBPreflightError(
                                "dry_run_child_protocol_write_failed"
                            )
                        payload = payload[written:]
                    os.close(write_descriptor)
                    child_exit_status = 0
                finally:
                    # Never unwind the policy into Python exception handling.
                    # os._exit terminates any child thread that did not join.
                    os._exit(child_exit_status)
        finally:
            # Setup/enforcement-entry failures are also non-returning children.
            os._exit(child_exit_status)
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


def _read_descriptor_bytes(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _write_descriptor_bytes(descriptor: int, payload: bytes) -> None:
    while payload:
        written = os.write(descriptor, payload)
        if written <= 0:
            raise GateBPreflightError("descriptor_write_failed")
        payload = payload[written:]


class _GateBRunAuthority:
    """One approval-bound inode for run exclusion and ledger continuity."""

    _SCHEMA_VERSION = "2.0.0"
    _CHECKPOINT_SCHEMA_VERSION = "2.0.0"
    _CHECKPOINT_DOMAIN = b"gate-b-run-checkpoint\x00"
    _ZERO_HASH = "0" * 64

    def __init__(
        self,
        *,
        experiment_root: Path,
        run_identity_sha256: str,
        input_manifest_path: Path,
        input_manifest_sha256: str,
        input_manifest: Mapping[str, Any],
        seal_key: bytes,
        restart_checkpoint: object,
    ) -> None:
        self.run_identity_sha256 = run_identity_sha256
        self.input_manifest_path = Path(os.path.abspath(input_manifest_path))
        self.input_manifest_sha256 = input_manifest_sha256
        self._seal_key = seal_key
        self._thread_lock = threading.RLock()
        self._lock_depth = 0
        self._manifest_lock_depth = 0
        self._created_authority = False
        self._expected_authority_sequence: int | None = None
        self._expected_authority_head: str | None = None
        self._expected_authority_length: int | None = None
        self._authority_sequence = -1
        self._authority_head = self._ZERO_HASH
        self._authority_length = 0
        self._authority_checkpoints: list[dict[str, Any]] = []
        self._ledger_anchor: dict[str, Any] | None = None
        self._journal_descriptor: int | None = None
        self._journal_path: Path | None = None
        self._journal_genesis: dict[str, Any] | None = None
        self._root_descriptor = _open_directory_nofollow(experiment_root)
        self._manifest_descriptor: int | None = None
        self._authority_descriptor: int | None = None
        prefix = f".gate-b-r3-run-authority-{run_identity_sha256[:16]}"
        self._authority_name = f"{prefix}.state"
        self._authority_pin_name = f"{prefix}.pin"
        try:
            self._manifest_descriptor = os.open(
                self.input_manifest_path,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
            manifest_stat = os.fstat(self._manifest_descriptor)
            if (
                not stat.S_ISREG(manifest_stat.st_mode)
                or manifest_stat.st_uid != os.geteuid()
                or manifest_stat.st_nlink != 1
            ):
                raise GateBPreflightError("input_manifest_lock_not_regular")
            manifest_bytes = _read_descriptor_bytes(self._manifest_descriptor)
            if (
                _sha256_bytes(manifest_bytes) != input_manifest_sha256
                or _canonical_bytes(input_manifest) != manifest_bytes
            ):
                raise GateBPreflightError("input_manifest_changed:authorization")
            self._open_authority_descriptor(restart_checkpoint)
            with self.exclusive():
                self._validate_restart_checkpoint_locked(restart_checkpoint)
        except BaseException:
            self.close()
            raise

    def _authority_record(self, unsigned: Mapping[str, Any]) -> dict[str, Any]:
        payload = _canonical_json(dict(unsigned)).encode("utf-8")
        return {
            **dict(unsigned),
            "record_hmac_sha256": hmac.new(
                self._seal_key, payload, hashlib.sha256
            ).hexdigest(),
        }

    def _checkpoint_unsigned_locked(self) -> dict[str, Any]:
        if self._lock_depth <= 0:
            raise GateBPreflightError("run_authority_lock_required")
        return {
            "schema_version": self._CHECKPOINT_SCHEMA_VERSION,
            "run_identity_sha256": self.run_identity_sha256,
            "input_manifest_sha256": self.input_manifest_sha256,
            "authority_sequence": self._authority_sequence,
            "authority_length": self._authority_length,
            "authority_head_sha256": self._authority_head,
            "ledger_anchor_sha256": (
                None
                if self._ledger_anchor is None
                else _sha256_json(self._ledger_anchor)
            ),
        }

    def _checkpoint_hmac(self, unsigned: Mapping[str, Any]) -> str:
        payload = self._CHECKPOINT_DOMAIN + _canonical_json(
            dict(unsigned)
        ).encode("utf-8")
        return hmac.new(self._seal_key, payload, hashlib.sha256).hexdigest()

    def _checkpoint_record_locked(self) -> dict[str, Any]:
        unsigned = self._checkpoint_unsigned_locked()
        return {
            **unsigned,
            "checkpoint_hmac_sha256": self._checkpoint_hmac(unsigned),
        }

    def _validate_restart_checkpoint_locked(self, checkpoint: object) -> None:
        if self._created_authority:
            if checkpoint is not None:
                raise GateBPreflightError("run_checkpoint_mismatch")
            return
        if checkpoint is None:
            raise GateBPreflightError("run_checkpoint_required")
        if not isinstance(checkpoint, Mapping):
            raise GateBPreflightError("run_checkpoint_mismatch")
        supplied = dict(checkpoint)
        expected_keys = set(self._checkpoint_unsigned_locked()) | {
            "checkpoint_hmac_sha256"
        }
        if set(supplied) != expected_keys:
            raise GateBPreflightError("run_checkpoint_mismatch")
        supplied_hmac = supplied.get("checkpoint_hmac_sha256")
        unsigned = {
            key: value
            for key, value in supplied.items()
            if key != "checkpoint_hmac_sha256"
        }
        try:
            calculated_hmac = self._checkpoint_hmac(unsigned)
        except (TypeError, ValueError):
            raise GateBPreflightError("run_checkpoint_mismatch") from None
        if (
            not isinstance(supplied_hmac, str)
            or not hmac.compare_digest(supplied_hmac, calculated_hmac)
            or unsigned not in self._authority_checkpoints
        ):
            raise GateBPreflightError("run_checkpoint_mismatch")

    def export_checkpoint(self) -> dict[str, Any]:
        with self.exclusive():
            return self._checkpoint_record_locked()

    def _append_raw_authority_record(self, unsigned: Mapping[str, Any]) -> None:
        descriptor = self._authority_descriptor
        if descriptor is None:
            raise GateBPreflightError("run_authority_closed")
        record = self._authority_record(unsigned)
        payload = _canonical_json(record).encode("utf-8") + b"\n"
        os.lseek(descriptor, 0, os.SEEK_END)
        _write_descriptor_bytes(descriptor, payload)
        os.fsync(descriptor)

    def _open_authority_descriptor(self, restart_checkpoint: object) -> None:
        def child_stat(name: str) -> os.stat_result | None:
            try:
                return os.stat(
                    name,
                    dir_fd=self._root_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return None

        primary_stat = child_stat(self._authority_name)
        pin_stat = child_stat(self._authority_pin_name)
        created = primary_stat is None and pin_stat is None
        self._created_authority = created
        if (primary_stat is None) != (pin_stat is None):
            raise GateBPreflightError("run_authority_split")
        if created:
            if restart_checkpoint is not None:
                raise GateBPreflightError("run_checkpoint_mismatch")
            try:
                descriptor = os.open(
                    self._authority_name,
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=self._root_descriptor,
                )
                os.link(
                    self._authority_name,
                    self._authority_pin_name,
                    src_dir_fd=self._root_descriptor,
                    dst_dir_fd=self._root_descriptor,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise GateBPreflightError("run_authority_create_failed") from exc
            self._authority_descriptor = descriptor
            authority_stat = os.fstat(descriptor)
            unsigned = {
                "schema_version": self._SCHEMA_VERSION,
                "kind": "authority_genesis",
                "sequence": 0,
                "previous_record_sha256": self._ZERO_HASH,
                "run_identity_sha256": self.run_identity_sha256,
                "input_manifest_sha256": self.input_manifest_sha256,
                "authority_device": authority_stat.st_dev,
                "authority_inode": authority_stat.st_ino,
            }
            self._append_raw_authority_record(unsigned)
            os.fsync(self._root_descriptor)
        else:
            try:
                descriptor = os.open(
                    self._authority_name,
                    os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=self._root_descriptor,
                )
                pin_descriptor = os.open(
                    self._authority_pin_name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=self._root_descriptor,
                )
            except OSError as exc:
                raise GateBPreflightError("run_authority_open_failed") from exc
            try:
                descriptor_stat = os.fstat(descriptor)
                pin_descriptor_stat = os.fstat(pin_descriptor)
                if (descriptor_stat.st_dev, descriptor_stat.st_ino) != (
                    pin_descriptor_stat.st_dev,
                    pin_descriptor_stat.st_ino,
                ):
                    raise GateBPreflightError("run_authority_split")
            finally:
                os.close(pin_descriptor)
            self._authority_descriptor = descriptor
        authority_stat = os.fstat(self._authority_descriptor)
        if (
            not stat.S_ISREG(authority_stat.st_mode)
            or authority_stat.st_uid != os.geteuid()
            or authority_stat.st_nlink != 2
            or authority_stat.st_mode & 0o077
        ):
            raise GateBPreflightError("run_authority_unsafe")

    def _load_authority_locked(self) -> None:
        descriptor = self._authority_descriptor
        if descriptor is None:
            raise GateBPreflightError("run_authority_closed")
        authority_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(authority_stat.st_mode)
            or authority_stat.st_uid != os.geteuid()
            or authority_stat.st_nlink != 2
        ):
            raise GateBPreflightError("run_authority_unsafe")
        payload = _read_descriptor_bytes(descriptor)
        if (
            self._expected_authority_length is not None
            and len(payload) < self._expected_authority_length
        ):
            raise GateBPreflightError("run_authority_rollback")
        sequence = -1
        previous_hash = self._ZERO_HASH
        offset = 0
        latest_anchor: dict[str, Any] | None = None
        checkpoints: list[dict[str, Any]] = []
        expected_seen = self._expected_authority_sequence is None
        for line in payload.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                if (
                    self._expected_authority_length is not None
                    and offset < self._expected_authority_length
                ):
                    raise GateBPreflightError("run_authority_rollback")
                os.ftruncate(descriptor, offset)
                os.fsync(descriptor)
                payload = payload[:offset]
                break
            try:
                record = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GateBPreflightError("run_authority_corrupt") from exc
            if not isinstance(record, dict):
                raise GateBPreflightError("run_authority_corrupt")
            supplied_hmac = record.get("record_hmac_sha256")
            unsigned = {
                key: value
                for key, value in record.items()
                if key != "record_hmac_sha256"
            }
            expected_hmac = self._authority_record(unsigned)[
                "record_hmac_sha256"
            ]
            record_hash = _sha256_json(record)
            if (
                record.get("schema_version") != self._SCHEMA_VERSION
                or record.get("sequence") != sequence + 1
                or record.get("previous_record_sha256") != previous_hash
                or record.get("run_identity_sha256")
                != self.run_identity_sha256
                or record.get("input_manifest_sha256")
                != self.input_manifest_sha256
                or not isinstance(supplied_hmac, str)
                or not hmac.compare_digest(supplied_hmac, expected_hmac)
                or line != _canonical_json(record).encode("utf-8") + b"\n"
            ):
                raise GateBPreflightError("run_authority_corrupt")
            if sequence == -1:
                if (
                    record.get("kind") != "authority_genesis"
                    or set(record)
                    != {
                        "schema_version",
                        "kind",
                        "sequence",
                        "previous_record_sha256",
                        "run_identity_sha256",
                        "input_manifest_sha256",
                        "authority_device",
                        "authority_inode",
                        "record_hmac_sha256",
                    }
                    or record.get("authority_device") != authority_stat.st_dev
                    or record.get("authority_inode") != authority_stat.st_ino
                ):
                    raise GateBPreflightError("run_authority_identity_mismatch")
            else:
                if record.get("kind") != "ledger_anchor" or set(record) != {
                    "schema_version",
                    "kind",
                    "sequence",
                    "previous_record_sha256",
                    "run_identity_sha256",
                    "input_manifest_sha256",
                    "ledger",
                    "record_hmac_sha256",
                }:
                    raise GateBPreflightError("run_authority_corrupt")
                if not isinstance(record["ledger"], dict):
                    raise GateBPreflightError("run_authority_corrupt")
                latest_anchor = dict(record["ledger"])
            sequence = int(record["sequence"])
            previous_hash = record_hash
            offset += len(line)
            checkpoints.append({
                "schema_version": self._CHECKPOINT_SCHEMA_VERSION,
                "run_identity_sha256": self.run_identity_sha256,
                "input_manifest_sha256": self.input_manifest_sha256,
                "authority_sequence": sequence,
                "authority_length": offset,
                "authority_head_sha256": previous_hash,
                "ledger_anchor_sha256": (
                    None
                    if latest_anchor is None
                    else _sha256_json(latest_anchor)
                ),
            })
            if sequence == self._expected_authority_sequence:
                if record_hash != self._expected_authority_head:
                    raise GateBPreflightError("run_authority_rollback")
                expected_seen = True
        if sequence < 0 or not expected_seen:
            raise GateBPreflightError("run_authority_rollback")
        self._authority_sequence = sequence
        self._authority_head = previous_hash
        self._authority_length = offset
        self._authority_checkpoints = checkpoints
        self._ledger_anchor = latest_anchor
        self._expected_authority_sequence = sequence
        self._expected_authority_head = previous_hash
        self._expected_authority_length = offset

    @contextmanager
    def exclusive(self) -> Iterable[None]:
        descriptor = self._authority_descriptor
        if descriptor is None:
            raise GateBPreflightError("run_authority_closed")
        self._thread_lock.acquire()
        outermost = self._lock_depth == 0
        entered = False
        try:
            if outermost:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                self._load_authority_locked()
            self._lock_depth += 1
            entered = True
            yield
        finally:
            if entered:
                self._lock_depth -= 1
            if outermost:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            self._thread_lock.release()

    def _verify_manifest_path_identity_locked(self, descriptor: int) -> None:
        try:
            path_stat = os.stat(self.input_manifest_path, follow_symlinks=False)
        except OSError as exc:
            raise GateBPreflightError(
                "input_manifest_lock_path_changed"
            ) from exc
        descriptor_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or (path_stat.st_dev, path_stat.st_ino)
            != (descriptor_stat.st_dev, descriptor_stat.st_ino)
        ):
            raise GateBPreflightError("input_manifest_lock_path_changed")

    @contextmanager
    def compatibility_manifest_lock(self) -> Iterable[int]:
        if self._lock_depth <= 0:
            raise GateBPreflightError("run_authority_lock_required")
        descriptor = self._manifest_descriptor
        if descriptor is None:
            raise GateBPreflightError("input_manifest_lock_closed")
        outermost = self._manifest_lock_depth == 0
        entered = False
        flocked = False
        try:
            if outermost:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                flocked = True
            manifest_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(manifest_stat.st_mode)
                or manifest_stat.st_uid != os.geteuid()
                or manifest_stat.st_nlink < 1
            ):
                raise GateBPreflightError("input_manifest_lock_not_regular")
            self._verify_manifest_path_identity_locked(descriptor)
            self._manifest_lock_depth += 1
            entered = True
            yield descriptor
        finally:
            try:
                if entered:
                    self._verify_manifest_path_identity_locked(descriptor)
            finally:
                if entered:
                    self._manifest_lock_depth -= 1
                if flocked:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)

    @contextmanager
    def locked_manifest(self) -> Iterable[dict[str, Any]]:
        with self.exclusive():
            with self.compatibility_manifest_lock() as descriptor:
                manifest_bytes = _read_descriptor_bytes(descriptor)
                if _sha256_bytes(manifest_bytes) != self.input_manifest_sha256:
                    raise GateBPreflightError("input_manifest_changed:runner")
                try:
                    manifest = json.loads(manifest_bytes)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise GateBPreflightError(
                        "input_manifest_invalid_json:runner"
                    ) from exc
                if _canonical_bytes(manifest) != manifest_bytes:
                    raise GateBPreflightError("input_manifest_not_canonical:runner")
                yield manifest

    def append_ledger_anchor(self, ledger: Mapping[str, Any]) -> None:
        if self._lock_depth <= 0:
            raise GateBPreflightError("run_authority_lock_required")
        unsigned = {
            "schema_version": self._SCHEMA_VERSION,
            "kind": "ledger_anchor",
            "sequence": self._authority_sequence + 1,
            "previous_record_sha256": self._authority_head,
            "run_identity_sha256": self.run_identity_sha256,
            "input_manifest_sha256": self.input_manifest_sha256,
            "ledger": dict(ledger),
        }
        self._append_raw_authority_record(unsigned)
        self._load_authority_locked()

    @property
    def ledger_anchor(self) -> dict[str, Any] | None:
        return None if self._ledger_anchor is None else dict(self._ledger_anchor)

    def close(self) -> None:
        for attribute in (
            "_journal_descriptor",
            "_manifest_descriptor",
            "_authority_descriptor",
            "_root_descriptor",
        ):
            descriptor = getattr(self, attribute, None)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, attribute, None)

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass


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
    _legacy_no_live_run_receipt: Mapping[str, Any] | None
    _run_authority: _GateBRunAuthority
    _issuer: object

    def __post_init__(self) -> None:
        if self._issuer is not _AUTHORIZATION_ISSUER:
            raise GateBPreflightError("authorization_issuer_invalid")


def export_gate_b_run_checkpoint(
    authorization: GateBRunAuthorization,
) -> dict[str, Any]:
    """Export the exact owner-authenticated restart lower bound."""
    if not isinstance(authorization, GateBRunAuthorization) or (
        authorization._issuer is not _AUTHORIZATION_ISSUER
    ):
        raise GateBPreflightError("runner_authorization_required")
    return authorization._run_authority.export_checkpoint()


_NO_LIVE_RUN_RECEIPT_SCHEMA_VERSION = "1.0.0"
_NO_LIVE_RUN_RECEIPT_DOMAIN = b"gate-b-no-live-run-receipt\x00"


def _legacy_state_artifact_names_locked(
    authorization: GateBRunAuthorization,
    *,
    include_recordings: bool = False,
) -> tuple[str, ...]:
    authority = authorization._run_authority
    if authority._lock_depth <= 0:
        raise GateBPreflightError("run_authority_lock_required")
    try:
        names = set(os.listdir(authority._root_descriptor))
    except OSError as exc:
        raise GateBPreflightError("ledger_legacy_state_scan_failed") from exc
    artifacts = {
        name
        for name in names
        if name.startswith(".gate-b-ledger-")
        or name.startswith(".gate-b-run-authority-")
        or name
        in {
            "run-ledger.sqlite3-journal",
            "run-ledger.sqlite3-shm",
            "run-ledger.sqlite3-wal",
        }
    }
    if include_recordings and "recordings" in names:
        try:
            recording_stat = os.stat(
                "recordings",
                dir_fd=authority._root_descriptor,
                follow_symlinks=False,
            )
            if not stat.S_ISDIR(recording_stat.st_mode):
                artifacts.add("recordings")
            else:
                recording_descriptor = _open_child_directory(
                    authority._root_descriptor, "recordings"
                )
                try:
                    if os.listdir(recording_descriptor):
                        artifacts.add("recordings")
                finally:
                    os.close(recording_descriptor)
        except (OSError, GateBPreflightError):
            artifacts.add("recordings")
    return tuple(sorted(artifacts))


def _no_live_run_receipt_unsigned_locked(
    authorization: GateBRunAuthorization,
    marker_descriptor: int,
) -> dict[str, Any]:
    artifacts = _legacy_state_artifact_names_locked(
        authorization, include_recordings=True
    )
    if artifacts:
        raise GateBPreflightError("ledger_legacy_state_requires_owner_review")
    before = os.fstat(marker_descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
    ):
        raise GateBPreflightError("ledger_path_unsafe")
    marker_bytes = _read_descriptor_bytes(marker_descriptor)
    after = os.fstat(marker_descriptor)
    try:
        path_stat = os.stat(
            "run-ledger.sqlite3",
            dir_fd=authorization._run_authority._root_descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise GateBPreflightError("ledger_path_changed") from exc
    if (
        (before.st_dev, before.st_ino, before.st_size)
        != (after.st_dev, after.st_ino, after.st_size)
        or (after.st_dev, after.st_ino) != (path_stat.st_dev, path_stat.st_ino)
    ):
        raise GateBPreflightError("ledger_path_changed")
    if marker_bytes:
        raise GateBPreflightError("ledger_legacy_state_requires_owner_review")
    return {
        "schema_version": _NO_LIVE_RUN_RECEIPT_SCHEMA_VERSION,
        "kind": "gate_b_no_live_run_migration",
        "source_protocol_max": "r2",
        "target_protocol": RECORD_STATE_PROTOCOL_VERSION,
        "run_identity_sha256": authorization.run_identity_sha256,
        "input_manifest_sha256": authorization.input_manifest_sha256,
        "legacy_marker_name": "run-ledger.sqlite3",
        "legacy_marker_sha256": _sha256_bytes(marker_bytes),
        "legacy_marker_device": after.st_dev,
        "legacy_marker_inode": after.st_ino,
        "no_legacy_private_ledgers": True,
        "no_recordings": True,
    }


def _no_live_run_receipt_hmac(
    authorization: GateBRunAuthorization,
    unsigned: Mapping[str, Any],
) -> str:
    payload = _NO_LIVE_RUN_RECEIPT_DOMAIN + _canonical_json(
        dict(unsigned)
    ).encode("utf-8")
    return hmac.new(
        authorization._metadata_seal_key, payload, hashlib.sha256
    ).hexdigest()


def export_gate_b_no_live_run_receipt(
    authorization: GateBRunAuthorization,
) -> dict[str, Any]:
    """Seal the exact empty, never-authorized legacy marker for r3 cutover."""
    if not isinstance(authorization, GateBRunAuthorization) or (
        authorization._issuer is not _AUTHORIZATION_ISSUER
    ):
        raise GateBPreflightError("runner_authorization_required")
    authority = authorization._run_authority
    with authority.exclusive():
        try:
            descriptor = os.open(
                "run-ledger.sqlite3",
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=authority._root_descriptor,
            )
        except OSError as exc:
            raise GateBPreflightError("ledger_no_live_run_marker_missing") from exc
        try:
            unsigned = _no_live_run_receipt_unsigned_locked(
                authorization, descriptor
            )
        finally:
            os.close(descriptor)
    return {
        **unsigned,
        "receipt_hmac_sha256": _no_live_run_receipt_hmac(
            authorization, unsigned
        ),
    }


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
    if (
        identity.get("record_state_protocol_version")
        != RECORD_STATE_PROTOCOL_VERSION
    ):
        raise GateBPreflightError("record_state_protocol_version")
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
    metadata_seal_key = hashlib.sha256(
        b"gate-b-record-seal\x00"
        + owner_capability.encode("utf-8")
        + identity_sha256.encode("ascii")
    ).digest()
    run_authority = _GateBRunAuthority(
        experiment_root=experiment_root,
        run_identity_sha256=identity_sha256,
        input_manifest_path=input_manifest_path,
        input_manifest_sha256=expected_input_manifest,
        input_manifest=input_manifest,
        seal_key=metadata_seal_key,
        restart_checkpoint=approval_record.get("run_checkpoint"),
    )
    legacy_receipt = approval_record.get("legacy_no_live_run_receipt")
    if legacy_receipt is not None and not isinstance(legacy_receipt, Mapping):
        run_authority.close()
        raise GateBPreflightError("ledger_no_live_run_receipt_mismatch")
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
        _metadata_seal_key=metadata_seal_key,
        _legacy_no_live_run_receipt=(
            None if legacy_receipt is None else dict(legacy_receipt)
        ),
        _run_authority=run_authority,
        _issuer=_AUTHORIZATION_ISSUER,
    )


class GateBBudgetLedger:
    """Authority-anchored append journal for Gate B accounting."""

    _LOG_SCHEMA_VERSION = "3.0.0"
    _ANCHOR_SCHEMA_VERSION = "2.0.0"
    _ZERO_HASH = "0" * 64

    def __init__(self, path: Path, authorization: GateBRunAuthorization) -> None:
        if not isinstance(authorization, GateBRunAuthorization):
            raise GateBPreflightError("runner_authorization_required")
        self.path = path
        self.authorization = authorization
        self._run_authority = authorization._run_authority
        self._closed = False
        if Path(os.path.abspath(path.parent)) != Path(
            os.path.abspath(authorization.experiment_root)
        ):
            raise GateBPreflightError("ledger_path_outside_run")
        try:
            with self._run_authority.exclusive():
                with self._run_authority.compatibility_manifest_lock():
                    self._bind_journal_locked()
                    self._initialize()
        except BaseException:
            self._closed = True
            raise

    @property
    def _ledger_descriptor(self) -> int:
        descriptor = self._run_authority._journal_descriptor
        if self._closed or descriptor is None:
            raise GateBPreflightError("ledger_closed")
        return descriptor

    def _journal_path_stat(self) -> os.stat_result | None:
        try:
            return os.stat(
                self.path.name,
                dir_fd=self._run_authority._root_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None

    def _open_journal_descriptor(self, *, create: bool) -> int:
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(
                self.path.name,
                flags,
                0o600,
                dir_fd=self._run_authority._root_descriptor,
            )
        except OSError as exc:
            raise GateBPreflightError("ledger_path_unsafe") from exc
        file_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_uid != os.geteuid()
            or file_stat.st_nlink != 1
        ):
            os.close(descriptor)
            raise GateBPreflightError("ledger_path_unsafe")
        os.fchmod(descriptor, 0o600)
        return descriptor

    def _verify_public_journal_identity_locked(self) -> None:
        path_stat = self._journal_path_stat()
        descriptor_stat = os.fstat(self._ledger_descriptor)
        if path_stat is None or stat.S_ISLNK(path_stat.st_mode):
            raise GateBPreflightError("ledger_path_changed")
        if (path_stat.st_dev, path_stat.st_ino) != (
            descriptor_stat.st_dev,
            descriptor_stat.st_ino,
        ):
            raise GateBPreflightError("ledger_path_changed")

    def _validate_no_live_run_receipt_locked(self) -> str:
        receipt = self.authorization._legacy_no_live_run_receipt
        if receipt is None:
            raise GateBPreflightError("ledger_no_live_run_receipt_required")
        unsigned = _no_live_run_receipt_unsigned_locked(
            self.authorization, self._ledger_descriptor
        )
        supplied = dict(receipt)
        supplied_hmac = supplied.pop("receipt_hmac_sha256", None)
        expected_hmac = _no_live_run_receipt_hmac(
            self.authorization, unsigned
        )
        if (
            supplied != unsigned
            or not isinstance(supplied_hmac, str)
            or not hmac.compare_digest(supplied_hmac, expected_hmac)
        ):
            raise GateBPreflightError("ledger_no_live_run_receipt_mismatch")
        return _sha256_json(dict(receipt))

    def _bind_journal_locked(self) -> None:
        with self._run_authority.compatibility_manifest_lock():
            self._bind_journal_pinned_locked()

    def _bind_journal_pinned_locked(self) -> None:
        authority = self._run_authority
        requested_path = Path(os.path.abspath(self.path))
        if _legacy_state_artifact_names_locked(self.authorization):
            raise GateBPreflightError(
                "ledger_legacy_state_requires_owner_review"
            )
        if authority._journal_descriptor is not None:
            if authority._journal_path != requested_path:
                raise GateBPreflightError("ledger_authority_path_mismatch")
            self._verify_public_journal_identity_locked()
            self._read_state_locked()
            return

        anchor = authority.ledger_anchor
        path_stat = self._journal_path_stat()
        if path_stat is not None and stat.S_ISLNK(path_stat.st_mode):
            raise GateBPreflightError("ledger_final_symlink")
        if anchor is not None:
            if path_stat is None:
                raise GateBPreflightError("ledger_path_changed")
            descriptor = self._open_journal_descriptor(create=False)
        elif path_stat is None:
            if self.authorization._legacy_no_live_run_receipt is not None:
                raise GateBPreflightError(
                    "ledger_no_live_run_receipt_unexpected"
                )
            descriptor = self._open_journal_descriptor(create=True)
            os.fsync(authority._root_descriptor)
        else:
            descriptor = self._open_journal_descriptor(create=False)
        authority._journal_descriptor = descriptor
        authority._journal_path = requested_path
        try:
            if anchor is not None:
                self._verify_public_journal_identity_locked()
                state, _, _ = self._read_state_locked()
                self._validate_state(state)
                return
            payload = _read_descriptor_bytes(descriptor)
            if payload:
                parsed = self._parse_journal_payload(payload)
                if parsed[6]:
                    raise GateBPreflightError("ledger_journal_corrupt")
                authority._journal_genesis = parsed[4]
                authority.append_ledger_anchor(self._anchor_from_parsed(parsed))
                return
            receipt_sha256 = (
                None
                if path_stat is None
                else self._validate_no_live_run_receipt_locked()
            )
            state = self._initial_state()
            genesis = {
                "schema_version": "2.0.0",
                "source": (
                    "fresh_r3"
                    if receipt_sha256 is None
                    else "sealed_no_live_run_receipt"
                ),
                "no_live_run_receipt_sha256": receipt_sha256,
                "initial_state_sha256": _sha256_json(state),
            }
            authority._journal_genesis = genesis
            self._append_unanchored_state_locked(
                state=state,
                sequence=-1,
                previous_hash=self._ZERO_HASH,
            )
            parsed = self._parse_journal_payload(_read_descriptor_bytes(descriptor))
            authority.append_ledger_anchor(self._anchor_from_parsed(parsed))
        except BaseException:
            os.close(descriptor)
            authority._journal_descriptor = None
            authority._journal_path = None
            authority._journal_genesis = None
            raise

    def _descriptor_is_safe(self) -> bool:
        try:
            descriptor = self._ledger_descriptor
        except GateBPreflightError:
            return False
        file_stat = os.fstat(descriptor)
        return (
            stat.S_ISREG(file_stat.st_mode)
            and file_stat.st_uid == os.geteuid()
            and file_stat.st_nlink == 1
        )

    @contextmanager
    def _locked(self) -> Iterable[None]:
        if self._closed:
            raise GateBPreflightError("ledger_closed")
        with self._run_authority.exclusive():
            with self._run_authority.compatibility_manifest_lock():
                if not self._descriptor_is_safe():
                    raise GateBPreflightError("ledger_path_unsafe")
                if _legacy_state_artifact_names_locked(self.authorization):
                    raise GateBPreflightError(
                        "ledger_legacy_state_requires_owner_review"
                    )
                yield

    def _initial_state(self) -> dict[str, Any]:
        return {
            "run_identity_sha256": self.authorization.run_identity_sha256,
            "call_cap": self.authorization.exact_call_cap,
            "spend_cap_microusd": _usd_to_micros(
                self.authorization.exact_spend_cap_usd
            ),
            "ordered_inputs": list(self.authorization.ordered_input_sha256s),
            "calls": {},
            "reconciliations": {},
        }

    def _validate_state(self, state: object) -> dict[str, Any]:
        if not isinstance(state, dict) or set(state) != {
            "run_identity_sha256",
            "call_cap",
            "spend_cap_microusd",
            "ordered_inputs",
            "calls",
            "reconciliations",
        }:
            raise GateBPreflightError("ledger_state_invalid")
        if (
            state["run_identity_sha256"]
            != self.authorization.run_identity_sha256
            or state["call_cap"] != self.authorization.exact_call_cap
            or state["spend_cap_microusd"]
            != _usd_to_micros(self.authorization.exact_spend_cap_usd)
            or state["ordered_inputs"]
            != list(self.authorization.ordered_input_sha256s)
            or not isinstance(state["calls"], dict)
            or not isinstance(state["reconciliations"], dict)
        ):
            raise GateBPreflightError("ledger_identity_mismatch")
        reservation_micros = _usd_to_micros(
            governed_pricing_schedule().reservation_cost_usd
        )
        calls = state["calls"]
        allowed_inputs = set(self.authorization.ordered_input_sha256s)
        if len(calls) > self.authorization.exact_call_cap:
            raise GateBPreflightError("ledger_state_invalid")
        for input_hash, row in calls.items():
            if (
                input_hash not in allowed_inputs
                or not isinstance(row, dict)
                or set(row)
                != {
                    "reservation_id",
                    "reserved_microusd",
                    "actual_microusd",
                    "status",
                }
                or row["reservation_id"] != f"reservation:{input_hash}"
                or row["reserved_microusd"] != reservation_micros
                or row["status"]
                not in {"reserved", "charge_unknown", "success", "failure"}
            ):
                raise GateBPreflightError("ledger_state_invalid")
            actual = row["actual_microusd"]
            if row["status"] in {"reserved", "charge_unknown"}:
                if actual is not None:
                    raise GateBPreflightError("ledger_state_invalid")
            elif (
                not isinstance(actual, int)
                or isinstance(actual, bool)
                or actual < 0
                or actual > row["reserved_microusd"]
            ):
                raise GateBPreflightError("ledger_state_invalid")
        spend = sum(
            row["reserved_microusd"]
            if row["actual_microusd"] is None
            else row["actual_microusd"]
            for row in calls.values()
        )
        if spend > state["spend_cap_microusd"]:
            raise GateBPreflightError("ledger_state_invalid")
        reconciliation_pairs = {
            "confirmed_unbilled": "confirmed_zero",
            "confirmed_charged_measured": "measured",
            "charge_amount_unknown": "unknown_reserved_max",
        }
        for input_hash, row in state["reconciliations"].items():
            call = calls.get(input_hash)
            if (
                call is None
                or call["status"] != "failure"
                or not isinstance(row, dict)
                or set(row)
                != {
                    "run_identity_sha256",
                    "disposition",
                    "cost_semantics",
                    "actual_microusd",
                    "provider_evidence_sha256",
                    "owner_capability_sha256",
                    "record_metadata_sha256",
                }
                or row["run_identity_sha256"]
                != self.authorization.run_identity_sha256
                or row["cost_semantics"]
                != reconciliation_pairs.get(row["disposition"])
                or row["actual_microusd"] != call["actual_microusd"]
                or row["owner_capability_sha256"]
                != self.authorization._owner_capability_sha256
                or not re.fullmatch(
                    r"[0-9a-f]{64}", str(row["provider_evidence_sha256"])
                )
                or not re.fullmatch(
                    r"[0-9a-f]{64}", str(row["record_metadata_sha256"])
                )
            ):
                raise GateBPreflightError("ledger_state_invalid")
        return state

    def _parse_journal_payload(
        self, payload: bytes
    ) -> tuple[
        dict[str, Any],
        int,
        str,
        int,
        dict[str, Any],
        list[dict[str, Any]],
        bool,
    ]:
        if not payload:
            raise GateBPreflightError("ledger_journal_corrupt")
        offset = 0
        previous_hash = self._ZERO_HASH
        state: dict[str, Any] | None = None
        sequence = -1
        genesis: dict[str, Any] | None = None
        checkpoints: list[dict[str, Any]] = []
        incomplete = False
        for line in payload.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                incomplete = True
                break
            try:
                entry = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GateBPreflightError("ledger_journal_corrupt") from exc
            if not isinstance(entry, dict) or set(entry) != {
                "schema_version",
                "sequence",
                "previous_entry_sha256",
                "genesis",
                "state",
                "entry_sha256",
                "entry_hmac_sha256",
            }:
                raise GateBPreflightError("ledger_journal_corrupt")
            unsigned = {
                key: value
                for key, value in entry.items()
                if key not in {"entry_sha256", "entry_hmac_sha256"}
            }
            calculated_hash = _sha256_json(unsigned)
            expected_hmac = hmac.new(
                self.authorization._metadata_seal_key,
                calculated_hash.encode("ascii"),
                hashlib.sha256,
            ).hexdigest()
            if (
                entry["schema_version"] != self._LOG_SCHEMA_VERSION
                or entry["sequence"] != sequence + 1
                or entry["previous_entry_sha256"] != previous_hash
                or entry["entry_sha256"] != calculated_hash
                or not isinstance(entry["entry_hmac_sha256"], str)
                or not hmac.compare_digest(
                    entry["entry_hmac_sha256"], expected_hmac
                )
                or line != _canonical_json(entry).encode("utf-8") + b"\n"
            ):
                raise GateBPreflightError("ledger_journal_corrupt")
            if not isinstance(entry["genesis"], dict) or set(entry["genesis"]) != {
                "schema_version",
                "source",
                "no_live_run_receipt_sha256",
                "initial_state_sha256",
            }:
                raise GateBPreflightError("ledger_journal_corrupt")
            if sequence == -1:
                genesis = dict(entry["genesis"])
                if (
                    genesis["schema_version"] != "2.0.0"
                    or genesis["source"]
                    not in {"fresh_r3", "sealed_no_live_run_receipt"}
                    or (
                        genesis["source"] == "fresh_r3"
                        and genesis["no_live_run_receipt_sha256"] is not None
                    )
                    or (
                        genesis["source"] == "sealed_no_live_run_receipt"
                        and not re.fullmatch(
                            r"[0-9a-f]{64}",
                            str(genesis["no_live_run_receipt_sha256"]),
                        )
                    )
                ):
                    raise GateBPreflightError("ledger_journal_corrupt")
            elif entry["genesis"] != genesis:
                raise GateBPreflightError("ledger_journal_corrupt")
            state = self._validate_state(entry["state"])
            state_sha256 = _sha256_json(state)
            if sequence == -1 and genesis["initial_state_sha256"] != state_sha256:
                raise GateBPreflightError("ledger_journal_corrupt")
            sequence = int(entry["sequence"])
            previous_hash = calculated_hash
            offset += len(line)
            checkpoints.append({
                "journal_length": offset,
                "journal_sequence": sequence,
                "journal_head_sha256": previous_hash,
                "journal_state_sha256": state_sha256,
                "journal_genesis_sha256": _sha256_json(genesis),
            })
        if state is None or genesis is None:
            raise GateBPreflightError("ledger_journal_corrupt")
        return (
            state,
            sequence,
            previous_hash,
            offset,
            genesis,
            checkpoints,
            incomplete,
        )

    def _anchor_from_parsed(
        self,
        parsed: tuple[
            dict[str, Any],
            int,
            str,
            int,
            dict[str, Any],
            list[dict[str, Any]],
            bool,
        ],
    ) -> dict[str, Any]:
        file_stat = os.fstat(self._ledger_descriptor)
        latest = parsed[5][-1]
        return {
            "schema_version": self._ANCHOR_SCHEMA_VERSION,
            "journal_name": self.path.name,
            "journal_device": file_stat.st_dev,
            "journal_inode": file_stat.st_ino,
            **latest,
            "no_live_run_receipt_sha256": parsed[4][
                "no_live_run_receipt_sha256"
            ],
        }

    def _read_state_locked(self) -> tuple[dict[str, Any], int, str]:
        descriptor = self._ledger_descriptor
        payload = _read_descriptor_bytes(descriptor)
        anchor = self._run_authority.ledger_anchor
        if anchor is None or set(anchor) != {
            "schema_version",
            "journal_name",
            "journal_device",
            "journal_inode",
            "journal_length",
            "journal_sequence",
            "journal_head_sha256",
            "journal_state_sha256",
            "journal_genesis_sha256",
            "no_live_run_receipt_sha256",
        }:
            raise GateBPreflightError("ledger_authority_missing")
        descriptor_stat = os.fstat(descriptor)
        if (
            anchor["schema_version"] != self._ANCHOR_SCHEMA_VERSION
            or anchor["journal_name"] != self.path.name
            or (anchor["journal_device"], anchor["journal_inode"])
            != (descriptor_stat.st_dev, descriptor_stat.st_ino)
        ):
            raise GateBPreflightError("ledger_authority_identity_mismatch")
        if len(payload) < anchor["journal_length"]:
            raise GateBPreflightError("ledger_rollback_detected")
        parsed = self._parse_journal_payload(payload)
        if parsed[3] < anchor["journal_length"]:
            raise GateBPreflightError("ledger_rollback_detected")
        anchored_checkpoint = next(
            (
                checkpoint
                for checkpoint in parsed[5]
                if checkpoint["journal_length"] == anchor["journal_length"]
            ),
            None,
        )
        expected_checkpoint = {
            key: anchor[key]
            for key in (
                "journal_length",
                "journal_sequence",
                "journal_head_sha256",
                "journal_state_sha256",
                "journal_genesis_sha256",
            )
        }
        if anchored_checkpoint != expected_checkpoint:
            raise GateBPreflightError("ledger_rollback_detected")
        if parsed[6]:
            os.ftruncate(descriptor, parsed[3])
            os.fsync(descriptor)
        self._run_authority._journal_genesis = parsed[4]
        if parsed[3] > anchor["journal_length"]:
            self._run_authority.append_ledger_anchor(
                self._anchor_from_parsed(parsed)
            )
        return parsed[0], parsed[1], parsed[2]

    def _append_unanchored_state_locked(
        self, state: dict[str, Any], sequence: int, previous_hash: str
    ) -> None:
        genesis = self._run_authority._journal_genesis
        if genesis is None:
            raise GateBPreflightError("ledger_genesis_missing")
        unsigned = {
            "schema_version": self._LOG_SCHEMA_VERSION,
            "sequence": sequence + 1,
            "previous_entry_sha256": previous_hash,
            "genesis": genesis,
            "state": state,
        }
        entry_sha256 = _sha256_json(unsigned)
        entry = {
            **unsigned,
            "entry_sha256": entry_sha256,
            "entry_hmac_sha256": hmac.new(
                self.authorization._metadata_seal_key,
                entry_sha256.encode("ascii"),
                hashlib.sha256,
            ).hexdigest(),
        }
        payload = _canonical_json(entry).encode("utf-8") + b"\n"
        os.lseek(self._ledger_descriptor, 0, os.SEEK_END)
        _write_descriptor_bytes(self._ledger_descriptor, payload)
        os.fsync(self._ledger_descriptor)

    def _append_state_locked(
        self, state: dict[str, Any], sequence: int, previous_hash: str
    ) -> None:
        if not self._descriptor_is_safe():
            raise GateBPreflightError("ledger_path_unsafe")
        self._validate_state(state)
        self._append_unanchored_state_locked(state, sequence, previous_hash)
        parsed = self._parse_journal_payload(
            _read_descriptor_bytes(self._ledger_descriptor)
        )
        if parsed[6]:
            raise GateBPreflightError("ledger_journal_write_failed")
        self._run_authority.append_ledger_anchor(self._anchor_from_parsed(parsed))

    def _load_locked(self) -> tuple[dict[str, Any], int, str]:
        state, sequence, previous_hash = self._read_state_locked()
        return state, sequence, previous_hash

    def close(self) -> None:
        self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass

    def _initialize(self) -> None:
        with self._locked():
            state, _, _ = self._read_state_locked()
            self._validate_state(state)

    def reserve(self, input_hash: str, amount: Decimal) -> str:
        amount_micros = _usd_to_micros(amount)
        with self._locked():
            state, sequence, previous_hash = self._load_locked()
            if input_hash not in state["ordered_inputs"]:
                raise GateBPreflightError("input_not_allowlisted")
            calls = state["calls"]
            if input_hash in calls:
                raise GateBPreflightError("input_already_reserved")
            if len(calls) >= self.authorization.exact_call_cap:
                raise GateBPreflightError("call_cap_exhausted")
            spend = sum(
                call["reserved_microusd"]
                if call["actual_microusd"] is None
                else call["actual_microusd"]
                for call in calls.values()
            )
            if spend + amount_micros > state["spend_cap_microusd"]:
                raise GateBPreflightError("spend_cap_exhausted")
            reservation_id = f"reservation:{input_hash}"
            calls[input_hash] = {
                "reservation_id": reservation_id,
                "reserved_microusd": amount_micros,
                "actual_microusd": None,
                "status": "reserved",
            }
            self._append_state_locked(state, sequence, previous_hash)
            return reservation_id

    def reconcile(
        self, reservation_id: str, actual_cost: Decimal, outcome: str
    ) -> None:
        if outcome not in {"success", "failure"}:
            raise GateBPreflightError("ledger_outcome_invalid")
        actual_micros = _usd_to_micros(actual_cost)
        with self._locked():
            state, sequence, previous_hash = self._load_locked()
            row = next(
                (
                    call
                    for call in state["calls"].values()
                    if call["reservation_id"] == reservation_id
                ),
                None,
            )
            if row is None or row["status"] not in {"reserved", "charge_unknown"}:
                raise GateBPreflightError("reservation_state_invalid")
            if actual_micros > row["reserved_microusd"]:
                raise GateBPreflightError("actual_cost_exceeds_reservation")
            row["actual_microusd"] = actual_micros
            row["status"] = outcome
            self._append_state_locked(state, sequence, previous_hash)

    def mark_dispatching(self, reservation_id: str) -> None:
        with self._locked():
            state, sequence, previous_hash = self._load_locked()
            row = next(
                (
                    call
                    for call in state["calls"].values()
                    if call["reservation_id"] == reservation_id
                ),
                None,
            )
            if row is None or row["status"] != "reserved":
                raise GateBPreflightError("dispatch_state_invalid")
            row["status"] = "charge_unknown"
            self._append_state_locked(state, sequence, previous_hash)

    def call_state(self, input_hash: str) -> str | None:
        with self._locked():
            state, _, _ = self._load_locked()
            row = state["calls"].get(input_hash)
            return None if row is None else str(row["status"])

    def retry_reserved_without_record(self, input_hash: str) -> bool:
        """Release only a crash-left reservation after the runner saw no record.

        The caller holds the run-manifest lock, so no second governed call can
        still be publishing the same content-addressed recording.
        """
        with self._locked():
            state, sequence, previous_hash = self._load_locked()
            row = state["calls"].get(input_hash)
            if row is None:
                return False
            if row["status"] == "charge_unknown":
                raise GateBPreflightError("owner_reconciliation_required:charge_unknown")
            if row["status"] != "reserved":
                raise GateBPreflightError("completed_call_without_record")
            del state["calls"][input_hash]
            self._append_state_locked(state, sequence, previous_hash)
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._locked():
            state, _, _ = self._load_locked()
            calls = tuple(state["calls"].values())
            return {
                "calls_reserved": len(calls),
                "calls_completed": sum(
                    call["status"] in {"success", "failure"} for call in calls
                ),
                "outstanding_reserved_usd": _micros_to_usd(
                    sum(
                        call["reserved_microusd"]
                        for call in calls
                        if call["status"] in {"reserved", "charge_unknown"}
                    )
                ),
                "measured_actual_usd": _micros_to_usd(
                    sum(
                        call["actual_microusd"] or 0
                        for call in calls
                    )
                ),
                "spend_reserved_usd": _micros_to_usd(
                    sum(
                        call["reserved_microusd"]
                        if call["actual_microusd"] is None
                        else call["actual_microusd"]
                        for call in calls
                    )
                ),
            }

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
        with self._locked():
            state, sequence, previous_hash = self._load_locked()
            row = state["calls"].get(input_hash)
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
                    audit_record = {
                        "run_identity_sha256": expected_audit[0],
                        "disposition": expected_audit[1],
                        "cost_semantics": expected_audit[2],
                        "actual_microusd": expected_audit[3],
                        "provider_evidence_sha256": expected_audit[4],
                        "owner_capability_sha256": expected_audit[5],
                        "record_metadata_sha256": expected_audit[6],
                    }
                    existing_audit = state["reconciliations"].setdefault(
                        input_hash, audit_record
                    )
                    if existing_audit != audit_record:
                        raise GateBPreflightError("reconciliation_audit_mismatch")
                row["actual_microusd"] = actual_micros
                row["status"] = outcome
                self._append_state_locked(state, sequence, previous_hash)
                return
            if row["status"] != outcome or row["actual_microusd"] != actual_micros:
                raise GateBPreflightError("recording_ledger_mismatch")
            if reconciliation is not None:
                expected_audit_record = {
                    "run_identity_sha256": expected_audit[0],
                    "disposition": expected_audit[1],
                    "cost_semantics": expected_audit[2],
                    "actual_microusd": expected_audit[3],
                    "provider_evidence_sha256": expected_audit[4],
                    "owner_capability_sha256": expected_audit[5],
                    "record_metadata_sha256": expected_audit[6],
                }
                audit = state["reconciliations"].get(input_hash)
                if audit != expected_audit_record:
                    raise GateBPreflightError("reconciliation_audit_mismatch")

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
        with self._locked():
            state, _, _ = self._load_locked()
            row = state["reconciliations"].get(input_hash)
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


@contextmanager
def _locked_gate_b_run_manifest(
    authorization: GateBRunAuthorization,
) -> Iterable[dict[str, Any]]:
    if authorization._run_authority.run_identity_sha256 != (
        authorization.run_identity_sha256
    ):
        raise GateBPreflightError("run_authority_identity_mismatch")
    with authorization._run_authority.locked_manifest() as manifest:
        yield manifest


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
    with _locked_gate_b_run_manifest(authorization) as manifest:
        return _reconcile_gate_b_charge_unknown_locked(
            authorization=authorization,
            owner_capability=owner_capability,
            input_hash=input_hash,
            disposition=disposition,
            measured_cost_usd=measured_cost_usd,
            reconciliation_evidence=reconciliation_evidence,
            manifest=manifest,
        )


def _reconcile_gate_b_charge_unknown_locked(
    *,
    authorization: GateBRunAuthorization,
    owner_capability: str,
    input_hash: str,
    disposition: str,
    measured_cost_usd: Decimal | None,
    reconciliation_evidence: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
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
    with _locked_gate_b_run_manifest(authorization) as manifest:
        semantic_provider = build_live_llm_provider(
            store_dir=recording_root,
            model_id=policy.model_id,
            prompt_version=policy.semantic_prompt_version,
        )
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
