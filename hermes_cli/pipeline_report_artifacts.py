"""Persistence helpers for safe controlled pipeline execution report artifacts."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Mapping
import uuid

from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSession

if TYPE_CHECKING:
    from hermes_state import SessionDB


CONTROLLED_EXECUTION_REPORT_SCHEMA_VERSION = "controlled_execution_report.v1"
CONTROLLED_EXECUTION_REPORT_FILENAME = "controlled_execution_report.json"
DEFAULT_DURABLE_ROOT = Path("/home/hermes/.hermes/controlled-runs")
_SAFE_PATH_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

logger = logging.getLogger(__name__)


def persist_controlled_execution_report_artifacts(
    *,
    session: PipelineSession,
    state_snapshot: Any,
    controller_payload: Mapping[str, Any] | None,
    pipeline_execution_report_payload: Mapping[str, Any],
    router_decision: RouterDecision | None = None,
    workspace_path: str | Path | None = None,
    durable_root: str | Path | None = DEFAULT_DURABLE_ROOT,
    db: SessionDB | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace_path).expanduser() if workspace_path is not None else None
    durable_base = Path(durable_root).expanduser() if durable_root is not None else None
    workspace_report_path = workspace / CONTROLLED_EXECUTION_REPORT_FILENAME if workspace is not None else None
    durable_run_id = sanitize_report_run_id(session.pipeline_session_id)
    durable_report_path = (
        durable_base / durable_run_id / CONTROLLED_EXECUTION_REPORT_FILENAME
        if durable_base is not None
        else None
    )

    payload = build_controlled_execution_report_artifact(
        session=session,
        state_snapshot=state_snapshot,
        controller_payload=controller_payload,
        pipeline_execution_report_payload=pipeline_execution_report_payload,
        router_decision=router_decision,
        workspace_path=workspace,
        workspace_report_path=workspace_report_path,
        durable_report_path=durable_report_path,
    )
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    workspace_written = _best_effort_write_report(
        workspace_report_path,
        encoded,
        report_kind="workspace",
        run_id=session.pipeline_session_id,
    )
    durable_written = _best_effort_write_report(
        durable_report_path,
        encoded,
        report_kind="durable",
        run_id=session.pipeline_session_id,
    )

    # Best-effort DB persistence (non-fatal on failure)
    db_persisted = False
    if db is not None:
        try:
            db.persist_controlled_execution_report(
                report_run_id=durable_run_id,
                payload=payload,
                workspace_path=str(workspace_path) if workspace_path is not None else None,
                durable_report_path=str(durable_report_path) if durable_written and durable_report_path is not None else None,
                workspace_report_path=str(workspace_report_path) if workspace_written and workspace_report_path is not None else None,
            )
            db_persisted = True
        except Exception as exc:
            logger.warning(
                "controlled execution report DB persist failed: "
                "run_id=%s error_type=%s",
                session.pipeline_session_id,
                type(exc).__name__,
            )

    return {
        "run_id": session.pipeline_session_id,
        "workspace_report_path": str(workspace_report_path) if workspace_written and workspace_report_path is not None else None,
        "durable_report_path": str(durable_report_path) if durable_written and durable_report_path is not None else None,
        "workspace_basename": workspace.name if workspace is not None else None,
        "workspace_report_written": workspace_written,
        "durable_report_written": durable_written,
        "report_workspace_filename": CONTROLLED_EXECUTION_REPORT_FILENAME,
        "durable_report_available": durable_report_path is not None,
        "db_persisted": db_persisted,
    }


def sanitize_report_run_id(value: str | None) -> str:
    text = str(value or "").strip()
    if text and _SAFE_PATH_COMPONENT_RE.fullmatch(text):
        return text
    return f"controlled-run-{uuid.uuid4().hex}"


def sanitize_report_artifact_metadata(report_artifacts: Mapping[str, Any] | None) -> dict[str, Any]:
    artifacts = dict(report_artifacts or {})
    return {
        "report_artifact_written": bool(
            artifacts.get("workspace_report_written") or artifacts.get("durable_report_written")
        ),
        "report_run_id": _string(artifacts.get("run_id")),
        "report_workspace_filename": _string(artifacts.get("report_workspace_filename")) or CONTROLLED_EXECUTION_REPORT_FILENAME,
        "durable_report_available": bool(artifacts.get("durable_report_available")),
        "durable_report_written": bool(artifacts.get("durable_report_written")),
        "workspace_report_written": bool(artifacts.get("workspace_report_written")),
        "workspace_basename": _string(artifacts.get("workspace_basename")),
    }


def build_controlled_execution_report_artifact(
    *,
    session: PipelineSession,
    state_snapshot: Any,
    controller_payload: Mapping[str, Any] | None,
    pipeline_execution_report_payload: Mapping[str, Any],
    router_decision: RouterDecision | None = None,
    workspace_path: Path | None = None,
    workspace_report_path: Path | None = None,
    durable_report_path: Path | None = None,
) -> dict[str, Any]:
    controller = dict(controller_payload or {})
    report = dict(pipeline_execution_report_payload)
    usage = _mapping(report.get("usage_summary") or report.get("usage"))
    review = _mapping(report.get("review"))
    git_gate = _mapping(report.get("git_gate"))
    tests = _mapping(report.get("tests"))

    error_class = _string(controller.get("helper_error"))
    failure_reason = (
        _string(controller.get("helper_result_status"))
        or _string(controller.get("blocked_reason"))
        or _string(report.get("status"))
    )
    actual_execution_invoked = _runtime_authoritative_actual_execution_invoked(
        controller=controller,
        report=report,
        usage=usage,
    )

    return {
        "schema_version": CONTROLLED_EXECUTION_REPORT_SCHEMA_VERSION,
        "run_id": session.pipeline_session_id,
        "pipeline_session_id": session.pipeline_session_id,
        "trace_id": session.trace_id,
        "status": _string(controller.get("status")) or _string(report.get("status")) or "unknown",
        "first_failed_point": failure_reason if error_class or not actual_execution_invoked else None,
        "workspace": {
            "path": str(workspace_path) if workspace_path is not None else None,
            "basename": workspace_path.name if workspace_path is not None else _string(controller.get("workspace_basename")),
        },
        "artifacts": {
            "workspace_report_path": str(workspace_report_path) if workspace_report_path is not None else None,
            "durable_report_path": str(durable_report_path) if durable_report_path is not None else None,
        },
        "inbound": {
            "platform": session.platform,
            "channel": session.platform,
            "timestamp": session.created_at,
            "session_id": session.session_id,
            # Session key is kept in the persisted artifact for operator-side
            # correlation, but it must not be echoed through safe log payloads.
            "session_key": session.session_key,
            "chat_id": session.chat_id,
            "thread_id": session.thread_id,
            "user_id": session.user_id,
        },
        "routing": {
            "router_strategy": "llm" if router_decision is not None else None,
            "router_provider": _first_string(
                getattr(router_decision, "actual_provider", None),
                getattr(router_decision, "selected_provider", None),
            ),
            "router_model": _first_string(
                getattr(router_decision, "actual_model", None),
                getattr(router_decision, "selected_model", None),
            ),
            "router_status": _first_string(getattr(router_decision, "status", None), session.router_status),
            "selected_pipeline_id": _first_string(
                getattr(router_decision, "selected_pipeline_id", None),
                _string(report.get("routing", {}).get("selected_pipeline_id")) if isinstance(report.get("routing"), Mapping) else None,
                session.pipeline_id,
            ),
            "fallback_pipeline_id": _first_string(
                getattr(router_decision, "fallback_pipeline_id", None),
                _string(report.get("routing", {}).get("fallback_pipeline_id")) if isinstance(report.get("routing"), Mapping) else None,
            ),
            "confidence": getattr(router_decision, "confidence", session.router_confidence) if router_decision is not None else session.router_confidence,
            "routing_confidence_source": getattr(router_decision, "routing_confidence_source", None) if router_decision is not None else None,
            "reasoning_summary": getattr(router_decision, "reasoning_summary", None) if router_decision is not None else None,
            "routing_failure_reason": getattr(router_decision, "routing_failure_reason", None) if router_decision is not None else None,
            "invalid_confidence_kind": getattr(router_decision, "invalid_confidence_kind", None) if router_decision is not None else None,
            "invalid_confidence_summary": getattr(router_decision, "invalid_confidence_summary", None) if router_decision is not None else None,
            "invalid_router_contract_kind": getattr(router_decision, "invalid_router_contract_kind", None) if router_decision is not None else None,
            "invalid_router_contract_summary": getattr(router_decision, "invalid_router_contract_summary", None) if router_decision is not None else None,
            "matched_signals": list(getattr(router_decision, "matched_signals", ()) or ()) if router_decision is not None else [],
            "alternatives": [_alternative_payload(item) for item in (getattr(router_decision, "alternatives", ()) or ())] if router_decision is not None else [],
        },
        "execution": {
            "effective_pipeline_id": getattr(state_snapshot, "pipeline_id", None),
            "execution_mode": _first_string(_string(controller.get("execution_mode")), getattr(state_snapshot, "execution_mode", None)),
            "actual_execution_invoked": actual_execution_invoked,
            "controlled_manual_trigger_evidence": _controlled_manual_trigger_evidence(controller=controller, actual_execution_invoked=actual_execution_invoked),
            "subagent_runs": len(list(report.get("subagent_runs") or [])),
            "executed_subagent_count": int(usage.get("executed_subagent_count") or 0),
            "reviewer_invoked": bool(review.get("reviewer_invoked")),
            "tool_calls": int(usage.get("tool_calls") or 0),
            "api_calls": None,
            "api_calls_known": False,
            "files_changed_in_workspace": list(report.get("changed_files") or []),
            "host_repo_mutation_status": "clean" if not list(report.get("changed_files") or []) else "workspace_mutations_present",
            "commit_status": git_gate.get("status"),
            "push_status": "not_attempted",
            "final_verdict": _first_string(
                _string(report.get("completion", {}).get("final_verdict")) if isinstance(report.get("completion"), Mapping) else None,
                getattr(state_snapshot, "final_verdict", None),
            ),
        },
        "review": {
            "reviewer_invoked": bool(review.get("reviewer_invoked")),
            "reviewer_approved": review.get("reviewer_approved"),
            "status": review.get("status"),
            "blocked_reason": review.get("blocked_reason"),
        },
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "reasoning_tokens": usage.get("reasoning_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cache_hit": usage.get("cache_hit"),
            "cache_write": usage.get("cache_write"),
            "usage_known": usage.get("usage_known"),
            "providers_used": list(usage.get("providers_used") or []),
            "models_used": list(usage.get("models_used") or []),
            "token_sources": list(usage.get("token_sources") or []),
            "cache_sources": list(usage.get("cache_sources") or []),
        },
        "tests": {
            "status": tests.get("status"),
            "summary": tests.get("summary"),
            "blocked_reason": tests.get("blocked_reason"),
        },
        "error": {
            "class": error_class,
            "summary": failure_reason if error_class or failure_reason else None,
        },
        "subagent_runs": list(report.get("subagent_runs") or []),
        "pipeline_execution_report": report,
    }


def _alternative_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "pipeline_id"):
        return {
            "pipeline_id": getattr(value, "pipeline_id", None),
            "confidence": getattr(value, "confidence", None),
            "reasoning_summary": getattr(value, "reasoning_summary", None),
        }
    if isinstance(value, Mapping):
        return dict(value)
    return {"value": value}


def _controlled_manual_trigger_evidence(*, controller: Mapping[str, Any], actual_execution_invoked: bool) -> str:
    if actual_execution_invoked:
        return "controlled_manual_trigger_present"
    if _string(controller.get("blocked_reason")) == "controlled_manual_trigger_missing":
        return "controlled_manual_trigger_missing"
    return "controlled_manual_trigger_unknown"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _runtime_authoritative_actual_execution_invoked(
    *,
    controller: Mapping[str, Any],
    report: Mapping[str, Any],
    usage: Mapping[str, Any],
) -> bool:
    if bool(controller.get("actual_execution_invoked")):
        return True
    if bool(controller.get("subagent_execution_invoked")):
        return True
    if bool(controller.get("real_provider_bridge_invoked")):
        return True
    if int(usage.get("executed_subagent_count") or 0) > 0:
        return True
    if int(usage.get("subagent_run_instance_count") or 0) > 0:
        return True
    if _bridge_executor_used(report):
        return True
    if _providers_used_effective_after_bridge(report):
        return True
    return False


def _bridge_executor_used(report: Mapping[str, Any]) -> bool:
    for item in list(report.get("subagent_runs") or []):
        if not isinstance(item, Mapping):
            continue
        if _string(item.get("runtime_mode")) == "bridge_executor":
            return True
    return False


def _providers_used_effective_after_bridge(report: Mapping[str, Any]) -> bool:
    for item in list(report.get("subagent_runs") or []):
        if not isinstance(item, Mapping):
            continue
        if _string(item.get("runtime_mode")) != "bridge_executor":
            continue
        providers = item.get("providers_used_effective")
        if isinstance(providers, (list, tuple)) and any(_string(provider) for provider in providers):
            return True
    return False


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_string(*values: Any) -> str | None:
    for value in values:
        text = _string(value)
        if text is not None:
            return text
    return None


def _best_effort_write_report(path: Path | None, content: str, *, report_kind: str, run_id: str) -> bool:
    if path is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True
    except OSError as exc:
        logger.warning(
            "controlled execution report %s write failed: run_id=%s error_type=%s",
            report_kind,
            run_id,
            type(exc).__name__,
        )
        return False
