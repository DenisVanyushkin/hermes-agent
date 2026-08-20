from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from html import unescape
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict

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
SHARED_BROWSER_PROFILES = {
    "linkedin": Path("/var/lib/browser-desktop/profiles/linkedin"),
}


class ProbeSourceBlocked(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class SourceIsolation:
    mode: str
    path: Path | None


class ProbeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: str
    cell_id: str
    source_family: str
    query: str


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
            if not _inside(str(profile), root):
                raise ValueError(f"isolated environment path outside experiment root: {family}")
            continue
        if profile != SHARED_BROWSER_PROFILES[family]:
            raise ValueError(f"unapproved shared browser profile: {family}")
        backup = Path(str(settings.get("backup_path") or ""))
        if not _inside(str(backup), root) or not backup.is_dir():
            raise ValueError(f"shared profile backup is missing: {family}")

    environment = dict(ambient or {})
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
    source_states: dict[str, str]
    cell_states: dict[str, str]
    duplicates: int
    evidence: tuple[EvidencePackage, ...]
    cost: dict[str, float]
    latency_seconds: float


def expand_queries(contract: SearchContract, *, role_terms: tuple[str, ...]) -> tuple[ProbeQuery, ...]:
    expanded: list[ProbeQuery] = []
    for lane_id, lane in sorted(contract.lanes.items()):
        for cell_id, cell in sorted(lane.cells.items()):
            for family in sorted(cell.source_families):
                for role in sorted(role_terms):
                    query = f"{role} {cell.primary_geography}".strip()
                    digest = hashlib.sha256(
                        f"{lane_id}\0{cell_id}\0{family}\0{query}".encode()
                    ).hexdigest()[:20]
                    expanded.append(
                        ProbeQuery(
                            query_id=digest,
                            cell_id=cell_id,
                            source_family=family,
                            query=query,
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


def _record_source_state(states: dict[str, str], family: str, state: str) -> None:
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
    sources: Mapping[str, Callable[[str], Iterable[Any]]],
    output_dir: Path | str,
    isolation: Mapping[str, SourceIsolation],
    max_attempts: int = 2,
    environment: Mapping[str, str] | None = None,
    now: Callable[[], datetime] | None = None,
) -> ProbeResult:
    started = time.monotonic()
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
    source_states: dict[str, str] = {}
    cell_attempts: dict[str, list[str]] = {}

    for raw_query in queries:
        query = raw_query if isinstance(raw_query, ProbeQuery) else ProbeQuery.model_validate(raw_query)
        cell_attempts.setdefault(query.cell_id, []).append(query.source_family)
        source = sources.get(query.source_family)
        source_isolation = isolation.get(query.source_family)
        if source_isolation is None or source_isolation.mode not in {"cloned_profile", "exclusive_lock", "api"}:
            _record_source_state(
                source_states, query.source_family, "blocked_no_safe_isolation"
            )
            continue
        if source is None:
            _record_source_state(
                source_states, query.source_family, "blocked_missing_public_interface"
            )
            continue

        records: list[Any] | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                records = list(source(query.query))
                state = (
                    "observed_with_failures"
                    if tuple(getattr(source, "last_errors", ()) or ())
                    else "observed"
                )
                _record_source_state(source_states, query.source_family, state)
                break
            except ProbeSourceBlocked as exc:
                _record_source_state(
                    source_states, query.source_family, f"blocked_{exc.reason}"
                )
                if attempt == max_attempts:
                    records = None
            except TimeoutError:
                _record_source_state(
                    source_states,
                    query.source_family,
                    "blocked_rate_limit_or_timeout",
                )
                if attempt == max_attempts:
                    records = None
            except Exception:
                _record_source_state(
                    source_states, query.source_family, "blocked_extraction_failure"
                )
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
            raw_bytes = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str).encode()
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            relative = Path("raw-evidence") / f"{content_hash}.json"
            target = output / relative
            if not target.exists():
                target.write_bytes(raw_bytes)
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

    cell_states: dict[str, str] = {}
    for cell_id in cell_attempts:
        cell_records = [record for record in observations if record.get("cell_id") == cell_id]
        cell_blocked = all(source_states.get(family, "").startswith("blocked") for family in cell_attempts[cell_id])
        if cell_records:
            cell_states[cell_id] = "qualified_results_found"
        elif cell_blocked:
            cell_states[cell_id] = "blocked"
        else:
            cell_states[cell_id] = "searched_no_qualified_results"

    result = ProbeResult(
        run_id=run_id,
        stage_counts={
            "raw_observed": len(observations),
            "canonical_current": len(canonical_records),
            "minimum_evidence_sufficient": sufficient,
        },
        provisional_labels=dict(sorted(labels.items())),
        source_states=dict(sorted(source_states.items())),
        cell_states=dict(sorted(cell_states.items())),
        duplicates=len(observations) - len(canonical_records),
        evidence=tuple(evidence),
        cost={"provider_cost_usd": 0.0},
        latency_seconds=round(time.monotonic() - started, 6),
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


def resolve_public_sources() -> dict[str, Callable[[str], Iterable[Any]]]:
    from job_intel.sources import (
        fetch_headhunter_vacancies,
        fetch_linkedin_vacancies,
        normalize_search_hit,
        search_duckduckgo,
        search_remoteok_jobs,
        search_remotive_jobs,
    )

    sources: dict[str, Callable[[str], Iterable[Any]]] = {
        "linkedin": lambda query: fetch_linkedin_vacancies(query, max_pages=2),
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
        mode = str(dict(settings).get("mode") or "")
        path = str(dict(settings).get("path") or "")
        if mode == "api":
            if family != "headhunter":
                raise ValueError(f"API source isolation is not supported for: {family}")
            continue
        if mode not in {"cloned_profile", "exclusive_lock"}:
            raise ValueError(f"invalid source isolation mode: {family}")
        if not _inside(path, root):
            raise ValueError(f"source isolation path outside experiment root: {family}")
        shared_profile = dict(settings).get("shared_profile_path")
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
    if not dict(evidence.get("cell_states") or {}):
        raise ValueError("cell states are required")
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
        queries = expand_queries(
            contract,
            role_terms=("Chief Product Officer", "VP Product", "Head of Product", "GM Digital"),
        ) + build_snapshot_queries()
        isolation = {
            family: SourceIsolation(
                mode=str(settings.get("mode") or "blocked"),
                path=Path(settings["path"]) if settings.get("path") else None,
            )
            for family, settings in dict(manifest.get("source_isolation") or {}).items()
        }
        run_probe(
            run_id=f"gate-a-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            queries=queries,
            sources=resolve_public_sources(),
            output_dir=Path(manifest["root"]),
            isolation=isolation,
            environment=probe_environment,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
