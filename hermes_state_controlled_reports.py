"""Fork: SessionDB methods for the controlled-execution report table.

The ``controlled_execution_reports`` table survived the v0.22.5 merge in the
schema (hermes_state_common.py), and hermes_cli/pipeline_report_artifacts.py
still writes through ``db.persist_controlled_execution_report`` -- but the
methods were gone, so every pipeline report hit an AttributeError that the
caller logs as "DB persist failed" and swallows. Moved from the pre-v0.22.5
hermes_state.py; mixed into ``SessionDB``.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hermes_state")

_UPSERT_SQL = """INSERT INTO controlled_execution_reports (
       report_run_id, pipeline_session_id, trace_id,
       status, pipeline_id, execution_mode, final_verdict,
       controller_executed, report_execution_invoked,
       reviewer_invoked,
       changed_files_json, models_used_json, providers_used_json,
       tests_status, tests_summary,
       workspace_path, durable_report_path, workspace_report_path,
       report_json, created_at, updated_at
   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
   ON CONFLICT(report_run_id) DO UPDATE SET
       pipeline_session_id = excluded.pipeline_session_id,
       trace_id = excluded.trace_id,
       status = excluded.status,
       pipeline_id = excluded.pipeline_id,
       execution_mode = excluded.execution_mode,
       final_verdict = excluded.final_verdict,
       controller_executed = excluded.controller_executed,
       report_execution_invoked = excluded.report_execution_invoked,
       reviewer_invoked = excluded.reviewer_invoked,
       changed_files_json = excluded.changed_files_json,
       models_used_json = excluded.models_used_json,
       providers_used_json = excluded.providers_used_json,
       tests_status = excluded.tests_status,
       tests_summary = excluded.tests_summary,
       workspace_path = excluded.workspace_path,
       durable_report_path = excluded.durable_report_path,
       workspace_report_path = excluded.workspace_report_path,
       report_json = excluded.report_json,
       updated_at = excluded.updated_at"""


def _int_bool(value: Any) -> Optional[int]:
    if value is None:
        return None
    return 1 if value else 0


def _cer_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """A controlled_execution_reports row as a summary dict, JSON fields parsed."""
    result = dict(row)
    for field in ("changed_files_json", "models_used_json", "providers_used_json"):
        raw = result.get(field)
        if raw:
            try:
                result[field.replace("_json", "")] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                result[field.replace("_json", "")] = []
    return result


class SessionControlledReportsMixin:
    """``SessionDB`` methods for controlled execution reports (uses ``_execute_write``,
    ``_read_one`` and ``_read_all``)."""

    def persist_controlled_execution_report(
        self, *, report_run_id: str, payload: Dict[str, Any], workspace_path: Optional[str] = None,
        durable_report_path: Optional[str] = None, workspace_report_path: Optional[str] = None,
    ) -> None:
        """Upsert a controlled execution report by report_run_id (latest payload wins)."""
        now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        exec_section = payload.get("execution") or {}
        review_section = payload.get("review") or {}
        tests_section = payload.get("tests") or {}
        usage_section = payload.get("usage") or {}
        params = (
            report_run_id,
            payload.get("pipeline_session_id"),
            payload.get("trace_id"),
            payload.get("status"),
            (payload.get("routing") or {}).get("selected_pipeline_id"),
            exec_section.get("execution_mode"),
            exec_section.get("final_verdict"),
            _int_bool(exec_section.get("actual_execution_invoked")),
            _int_bool(exec_section.get("actual_execution_invoked")),
            _int_bool(review_section.get("reviewer_invoked")),
            json.dumps(exec_section.get("files_changed_in_workspace") or [], ensure_ascii=False),
            json.dumps(usage_section.get("models_used") or [], ensure_ascii=False),
            json.dumps(usage_section.get("providers_used") or [], ensure_ascii=False),
            tests_section.get("status"),
            tests_section.get("summary"),
            (payload.get("workspace") or {}).get("path") or workspace_path,
            durable_report_path,
            workspace_report_path,
            json.dumps(payload, ensure_ascii=False),
            now,
            now,
        )
        try:
            self._execute_write(lambda conn: conn.execute(_UPSERT_SQL, params))
        except Exception as exc:
            logger.warning(
                "controlled_execution_report DB persist failed: report_run_id=%s error_type=%s",
                report_run_id, type(exc).__name__,
            )

    def get_controlled_execution_report(self, report_run_id: str) -> Optional[Dict[str, Any]]:
        """A controlled execution report by report_run_id, or None."""
        try:
            row = self._read_one(
                "SELECT * FROM controlled_execution_reports WHERE report_run_id = ?", (report_run_id,))
            return _cer_row_to_dict(row) if row is not None else None
        except Exception:
            return None

    def list_controlled_execution_reports(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Recent controlled execution reports, newest first."""
        try:
            rows = self._read_all(
                "SELECT * FROM controlled_execution_reports ORDER BY created_at DESC LIMIT ?",
                (min(int(limit), 200),))
            return [_cer_row_to_dict(row) for row in rows]
        except Exception:
            return []
