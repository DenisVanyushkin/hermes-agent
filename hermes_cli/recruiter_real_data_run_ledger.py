from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4


LEDGER_SCHEMA_VERSION = "recruiter_real_data_run_ledger_v1"
DEFAULT_LEDGER_FILENAME = "hermes_recruiter_real_data_run_ledger.json"
POSITIONING_STAGE = "positioning-and-evidence"
APPLICATION_MATERIALS_FULL_FLOW_STAGE = "application-materials-full-flow"
TARGET_SPECIFIC_STAGE_PREFIX = "application-materials-target-specific-"


class RealDataRunAttemptStatus(str, Enum):
    ALLOWED = "REAL_DATA_RUN_ATTEMPT_ALLOWED"
    BLOCKED = "REAL_DATA_RUN_ATTEMPT_BLOCKED"


@dataclass(slots=True)
class RealDataRunAttemptDecision:
    status: RealDataRunAttemptStatus
    ready: bool
    run_id: str | None
    stage: str
    source_set_hash: str
    attempt_index: int
    blocked_reason: str | None = None
    existing_attempt: dict[str, Any] | None = None
    safe_to_retry_with_explicit_override: bool = False
    provider_execution_allowed: bool = True
    flow: str | None = None
    document_target: str | None = None
    ledger_path: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def default_real_data_run_ledger_path() -> Path:
    return Path(tempfile.gettempdir()) / DEFAULT_LEDGER_FILENAME


def stage_for_real_data_flow(flow: str, document_target: str | None = None) -> str:
    if flow == POSITIONING_STAGE:
        return POSITIONING_STAGE
    if flow == "application-materials":
        if document_target is None:
            return APPLICATION_MATERIALS_FULL_FLOW_STAGE
        return f"{TARGET_SPECIFIC_STAGE_PREFIX}{document_target}"
    raise ValueError(f"unsupported_real_data_flow:{flow}")


def check_and_record_attempt(
    *,
    flow: str,
    vacancy_source_ref: str | None,
    career_fact_source_refs: list[str],
    provider_execution_allowed: bool,
    document_target: str | None = None,
    ledger_path: str | Path | None = None,
    explicit_override: bool = False,
    report_path: str | None = None,
    attempt_started_at: str | None = None,
) -> RealDataRunAttemptDecision:
    stage = stage_for_real_data_flow(flow, document_target=document_target)
    resolved_ledger_path = Path(ledger_path) if ledger_path is not None else default_real_data_run_ledger_path()
    vacancy_hash = _safe_hash(vacancy_source_ref or "")
    career_hashes = sorted({_safe_hash(item) for item in career_fact_source_refs if item})
    source_set_hash = _safe_hash(json.dumps({"vacancy": vacancy_hash, "career": career_hashes}, sort_keys=True))
    ledger = _load_ledger(resolved_ledger_path)
    attempts = list(ledger.get("attempts") or [])
    matching_source_attempts = [item for item in attempts if item.get("source_set_hash") == source_set_hash]
    matching_stage_attempts = [item for item in matching_source_attempts if item.get("stage") == stage]

    if not explicit_override:
        if matching_stage_attempts:
            return RealDataRunAttemptDecision(
                status=RealDataRunAttemptStatus.BLOCKED,
                ready=False,
                run_id=None,
                stage=stage,
                source_set_hash=source_set_hash,
                attempt_index=len(matching_stage_attempts) + 1,
                blocked_reason="duplicate_provider_stage_attempt",
                existing_attempt=_safe_existing_attempt(matching_stage_attempts[-1]),
                safe_to_retry_with_explicit_override=True,
                provider_execution_allowed=provider_execution_allowed,
                flow=flow,
                document_target=document_target,
                ledger_path=str(resolved_ledger_path),
                provenance={"writes_performed": False, "flow": "real-data-run-ledger"},
            )
        if stage.startswith(TARGET_SPECIFIC_STAGE_PREFIX) and any(
            item.get("stage") == APPLICATION_MATERIALS_FULL_FLOW_STAGE for item in matching_source_attempts
        ):
            return RealDataRunAttemptDecision(
                status=RealDataRunAttemptStatus.BLOCKED,
                ready=False,
                run_id=None,
                stage=stage,
                source_set_hash=source_set_hash,
                attempt_index=1,
                blocked_reason="target_specific_followup_after_full_flow_requires_explicit_approval",
                existing_attempt=None,
                safe_to_retry_with_explicit_override=True,
                provider_execution_allowed=provider_execution_allowed,
                flow=flow,
                document_target=document_target,
                ledger_path=str(resolved_ledger_path),
                provenance={"writes_performed": False, "flow": "real-data-run-ledger"},
            )

    run_id = f"real-data-run-{uuid4().hex[:12]}"
    record = {
        "run_id": run_id,
        "stage": stage,
        "flow": flow,
        "document_target": document_target,
        "provider_execution_allowed": bool(provider_execution_allowed),
        "vacancy_source_ref_hash": vacancy_hash,
        "career_fact_source_hashes": career_hashes,
        "source_set_hash": source_set_hash,
        "attempt_started_at": attempt_started_at or _utc_now_iso(),
        "attempt_status": "started",
        "report_path": _safe_report_path(report_path),
        "exit_status": None,
    }
    attempts.append(record)
    ledger["attempts"] = attempts
    _write_ledger(resolved_ledger_path, ledger)
    return RealDataRunAttemptDecision(
        status=RealDataRunAttemptStatus.ALLOWED,
        ready=True,
        run_id=run_id,
        stage=stage,
        source_set_hash=source_set_hash,
        attempt_index=len(matching_stage_attempts) + 1,
        blocked_reason=None,
        existing_attempt=None,
        safe_to_retry_with_explicit_override=False,
        provider_execution_allowed=provider_execution_allowed,
        flow=flow,
        document_target=document_target,
        ledger_path=str(resolved_ledger_path),
        provenance={"writes_performed": False, "flow": "real-data-run-ledger"},
    )


def finalize_attempt(
    *,
    run_id: str,
    attempt_status: str,
    ledger_path: str | Path | None = None,
    report_path: str | None = None,
    exit_status: int | None = None,
) -> dict[str, Any] | None:
    resolved_ledger_path = Path(ledger_path) if ledger_path is not None else default_real_data_run_ledger_path()
    ledger = _load_ledger(resolved_ledger_path)
    attempts = list(ledger.get("attempts") or [])
    for item in attempts:
        if item.get("run_id") != run_id:
            continue
        item["attempt_status"] = str(attempt_status)
        item["report_path"] = _safe_report_path(report_path or item.get("report_path"))
        item["exit_status"] = exit_status
        _write_ledger(resolved_ledger_path, ledger)
        return _safe_existing_attempt(item)
    return None


def inspect_attempts(
    *,
    flow: str,
    vacancy_source_ref: str | None,
    career_fact_source_refs: list[str],
    document_target: str | None = None,
    ledger_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    resolved_ledger_path = Path(ledger_path) if ledger_path is not None else default_real_data_run_ledger_path()
    if not resolved_ledger_path.exists():
        return []
    stage = stage_for_real_data_flow(flow, document_target=document_target)
    vacancy_hash = _safe_hash(vacancy_source_ref or "")
    career_hashes = sorted({_safe_hash(item) for item in career_fact_source_refs if item})
    source_set_hash = _safe_hash(json.dumps({"vacancy": vacancy_hash, "career": career_hashes}, sort_keys=True))
    ledger = _load_ledger(resolved_ledger_path)
    return [
        _safe_existing_attempt(item)
        for item in ledger.get("attempts") or []
        if item.get("stage") == stage and item.get("source_set_hash") == source_set_hash
    ]


def _load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": LEDGER_SCHEMA_VERSION, "attempts": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {"schema_version": LEDGER_SCHEMA_VERSION, "attempts": []}
    payload.setdefault("schema_version", LEDGER_SCHEMA_VERSION)
    payload.setdefault("attempts", [])
    return payload


def _write_ledger(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _safe_existing_attempt(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": payload.get("run_id"),
        "stage": payload.get("stage"),
        "report_path": payload.get("report_path"),
        "attempt_status": payload.get("attempt_status"),
        "exit_status": payload.get("exit_status"),
    }


def _safe_report_path(value: str | None) -> str | None:
    if not value:
        return None
    path = Path(str(value))
    if str(path).startswith("/tmp/"):
        return str(path)
    if str(path).startswith("/private/tmp/"):
        return str(path)
    digest = sha256(str(path).encode("utf-8")).hexdigest()[:12]
    suffix = path.suffix or ".json"
    return f"/tmp/report-{digest}{suffix}"


def _safe_hash(value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
