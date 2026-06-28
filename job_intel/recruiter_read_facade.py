from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .store import JobIntelStore


READ_ONLY_SOURCE = "sqlite_read_only"
CRM_TABLES = {"opportunities", "opportunity_events", "opportunity_artifacts"}


@dataclass(slots=True)
class RecruiterReadFacade:
    store: JobIntelStore
    stale_after_days: int = 14

    def _connect_read_only(self) -> sqlite3.Connection:
        return self.store.connect(read_only=True)

    def _table_names(self) -> set[str]:
        with self._connect_read_only() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {str(row[0]) for row in rows}

    def _has_tables(self, required: set[str]) -> bool:
        return required.issubset(self._table_names())

    def _decode_json(self, value: Any, default: Any) -> Any:
        if value in (None, ""):
            return default
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    def _parse_dt(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def _stale_warning(self, *timestamps: str | None) -> list[str]:
        threshold = datetime.now(timezone.utc) - timedelta(days=self.stale_after_days)
        parsed = [self._parse_dt(value) for value in timestamps if value]
        if not parsed:
            return ["missing_recency_signal"]
        freshest = max(parsed)
        return ["stale_vacancy"] if freshest and freshest < threshold else []

    def _latest_evaluation(self, conn: sqlite3.Connection, vacancy_key: str) -> dict[str, Any] | None:
        row = conn.execute(
            """
            SELECT id, run_id, score, tier, recommendation, salary_tier,
                   matched_signals_json, concerns_json, reasons_json, raw_breakdown_json, created_at
            FROM vacancy_evaluations
            WHERE vacancy_key=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (vacancy_key,),
        ).fetchone()
        if not row:
            return None
        return {
            "evaluation_id": row[0],
            "run_id": row[1],
            "score": row[2],
            "tier": row[3],
            "recommendation": row[4],
            "salary_tier": row[5],
            "matched_signals": self._decode_json(row[6], []),
            "concerns": self._decode_json(row[7], []),
            "reasons": self._decode_json(row[8], []),
            "raw_breakdown": self._decode_json(row[9], {}),
            "created_at": row[10],
        }

    def _company_context_rows(self, conn: sqlite3.Connection, company: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        company_row = conn.execute(
            """
            SELECT company, summary, risk_flags_json, target_category, website, signals_json,
                   career_urls_json, opening_count, last_scanned_at, last_signal_at, source_json, updated_at
            FROM company_intelligence
            WHERE lower(company)=lower(?)
            LIMIT 1
            """,
            (company,),
        ).fetchone()
        if company_row:
            rows.append(
                {
                    "company": company_row[0],
                    "source_kind": "company_intelligence",
                    "title": None,
                    "source_url": company_row[4],
                    "summary": company_row[1],
                    "details": {
                        "risk_flags": self._decode_json(company_row[2], []),
                        "target_category": company_row[3],
                        "signals": self._decode_json(company_row[5], {}),
                        "career_urls": self._decode_json(company_row[6], []),
                        "opening_count": company_row[7],
                        "last_scanned_at": company_row[8],
                        "last_signal_at": company_row[9],
                        "source": self._decode_json(company_row[10], {}),
                    },
                    "updated_at": company_row[11],
                    "provenance": {
                        "source_table": "company_intelligence",
                        "source_kind": "company_intelligence",
                        "source_url": company_row[4],
                        "updated_at": company_row[11],
                    },
                }
            )
        event_rows = conn.execute(
            """
            SELECT company, event_type, source, title, url, summary, details_json, seen_at
            FROM company_intelligence_events
            WHERE lower(company)=lower(?)
            ORDER BY datetime(seen_at) DESC, id DESC
            """,
            (company,),
        ).fetchall()
        rows.extend(
            {
                "company": row[0],
                "source_kind": row[2] or row[1] or "company_intelligence_event",
                "title": row[3],
                "source_url": row[4],
                "summary": row[5],
                "details": self._decode_json(row[6], {}),
                "updated_at": row[7],
                "provenance": {
                    "source_table": "company_intelligence_events",
                    "source_kind": row[2] or row[1] or "company_intelligence_event",
                    "source_url": row[4],
                    "updated_at": row[7],
                },
            }
            for row in event_rows
        )
        return rows

    def _serialize_vacancy(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        raw = dict(row)
        evaluation = self._latest_evaluation(conn, raw["vacancy_key"])
        company_context = self._company_context_rows(conn, raw["company"])
        warnings = self._stale_warning(raw.get("last_seen_at"), raw.get("scraped_at"), raw.get("posted_at"))
        return {
            "vacancy_id": raw["id"],
            "vacancy_key": raw["vacancy_key"],
            "source_kind": raw["source"],
            "source_id": raw["source_id"],
            "company": raw["company"],
            "title": raw["title"],
            "location": raw["location"],
            "source_url": raw["url"],
            "description": raw["description"],
            "salary": raw.get("salary"),
            "company_url": raw.get("company_url"),
            "metadata": self._decode_json(raw.get("metadata_json"), {}),
            "first_seen_at": raw.get("first_seen_at"),
            "last_seen_at": raw.get("last_seen_at"),
            "posted_at": raw.get("posted_at"),
            "scraped_at": raw.get("scraped_at"),
            "status": raw.get("status"),
            "repost_count": raw.get("repost_count"),
            "evaluation": evaluation,
            "company_context": company_context,
            "warnings": warnings,
            "provenance": {
                "read_mode": READ_ONLY_SOURCE,
                "source_table": "vacancies",
                "vacancy_id": raw["id"],
                "source_kind": raw["source"],
                "source_url": raw["url"],
                "first_seen_at": raw.get("first_seen_at"),
                "last_seen_at": raw.get("last_seen_at"),
                "created_at": raw.get("first_seen_at"),
                "updated_at": raw.get("last_seen_at"),
                "run_id": evaluation["run_id"] if evaluation else None,
                "evaluation_created_at": evaluation["created_at"] if evaluation else None,
            },
        }

    def get_vacancy_by_id(self, vacancy_id: int) -> dict[str, Any]:
        with self._connect_read_only() as conn:
            row = conn.execute("SELECT * FROM vacancies WHERE id=? LIMIT 1", (vacancy_id,)).fetchone()
            if not row:
                return {"status": "not_found", "vacancy": None, "warnings": ["vacancy_not_found"]}
            vacancy = self._serialize_vacancy(conn, row)
        return {"status": "found", "vacancy": vacancy, "warnings": vacancy["warnings"]}

    def get_vacancy_by_url(self, url: str) -> dict[str, Any]:
        with self._connect_read_only() as conn:
            rows = conn.execute(
                "SELECT * FROM vacancies WHERE url=? ORDER BY datetime(last_seen_at) DESC, id DESC",
                (url,),
            ).fetchall()
            if not rows:
                return {"status": "not_found", "vacancy": None, "warnings": ["vacancy_not_found"]}
            if len(rows) > 1:
                return {
                    "status": "ambiguous",
                    "vacancy": None,
                    "warnings": ["multiple_vacancies_for_url"],
                    "matches": [self._serialize_vacancy(conn, row) for row in rows],
                }
            vacancy = self._serialize_vacancy(conn, rows[0])
        return {"status": "found", "vacancy": vacancy, "warnings": vacancy["warnings"]}

    def get_opportunity_by_id(self, opportunity_id: int) -> dict[str, Any]:
        if not self._has_tables({"opportunities"}):
            return {"status": "source_missing", "opportunity": None, "warnings": ["crm_tables_missing"]}
        with self._connect_read_only() as conn:
            row = conn.execute("SELECT * FROM opportunities WHERE id=? LIMIT 1", (opportunity_id,)).fetchone()
            if not row:
                return {"status": "not_found", "opportunity": None, "warnings": ["opportunity_not_found"]}
            payload = dict(row)
            payload["provenance"] = {
                "read_mode": READ_ONLY_SOURCE,
                "source_table": "opportunities",
                "opportunity_id": payload["id"],
                "source_url": payload.get("canonical_url") or payload.get("source_url"),
                "created_at": payload.get("created_at"),
                "updated_at": payload.get("updated_at"),
                "last_seen_at": payload.get("last_seen_at"),
            }
        return {"status": "found", "opportunity": payload, "warnings": []}

    def get_opportunity_for_vacancy(self, vacancy_id: int) -> dict[str, Any]:
        if not self._has_tables({"opportunities"}):
            return {"status": "source_missing", "opportunity": None, "warnings": ["crm_tables_missing"]}
        with self._connect_read_only() as conn:
            row = conn.execute(
                "SELECT * FROM opportunities WHERE vacancy_id=? ORDER BY datetime(updated_at) DESC, id DESC LIMIT 1",
                (vacancy_id,),
            ).fetchone()
            if not row:
                return {"status": "not_found", "opportunity": None, "warnings": ["opportunity_not_found"]}
            payload = dict(row)
            payload["provenance"] = {
                "read_mode": READ_ONLY_SOURCE,
                "source_table": "opportunities",
                "opportunity_id": payload["id"],
                "vacancy_id": vacancy_id,
                "source_url": payload.get("canonical_url") or payload.get("source_url"),
                "created_at": payload.get("created_at"),
                "updated_at": payload.get("updated_at"),
                "last_seen_at": payload.get("last_seen_at"),
            }
        return {"status": "found", "opportunity": payload, "warnings": []}

    def get_company_context(self, company: str) -> dict[str, Any]:
        with self._connect_read_only() as conn:
            rows = self._company_context_rows(conn, company)
        if not rows:
            return {"status": "not_found", "company_context": [], "warnings": ["company_context_not_found"]}
        warnings = self._stale_warning(*(row.get("updated_at") for row in rows))
        return {"status": "found", "company_context": rows, "warnings": warnings}

    def get_application_history(self, opportunity_id: int) -> dict[str, Any]:
        if not self._has_tables(CRM_TABLES):
            return {
                "status": "source_missing",
                "history": [],
                "artifacts": [],
                "feedback": [],
                "warnings": ["crm_tables_missing"],
            }
        with self._connect_read_only() as conn:
            history = [
                {
                    **dict(row),
                    "payload": self._decode_json(dict(row).get("payload_json"), {}),
                    "provenance": {
                        "read_mode": READ_ONLY_SOURCE,
                        "source_table": "opportunity_events",
                        "opportunity_id": opportunity_id,
                        "created_at": dict(row).get("created_at"),
                    },
                }
                for row in conn.execute(
                    "SELECT * FROM opportunity_events WHERE opportunity_id=? ORDER BY id",
                    (opportunity_id,),
                ).fetchall()
            ]
            artifacts = [
                {
                    **dict(row),
                    "provenance": {
                        "read_mode": READ_ONLY_SOURCE,
                        "source_table": "opportunity_artifacts",
                        "opportunity_id": opportunity_id,
                        "created_at": dict(row).get("created_at"),
                    },
                }
                for row in conn.execute(
                    "SELECT * FROM opportunity_artifacts WHERE opportunity_id=? ORDER BY id",
                    (opportunity_id,),
                ).fetchall()
            ]
            feedback = [
                {
                    **dict(row),
                    "provenance": {
                        "read_mode": READ_ONLY_SOURCE,
                        "source_table": "vacancy_feedback_state",
                        "vacancy_id": dict(row).get("vacancy_id"),
                        "updated_at": dict(row).get("updated_at"),
                    },
                }
                for row in conn.execute(
                    """
                    SELECT vfs.*
                    FROM vacancy_feedback_state vfs
                    JOIN opportunities o ON o.vacancy_id = vfs.vacancy_id
                    WHERE o.id=?
                    ORDER BY vfs.id
                    """,
                    (opportunity_id,),
                ).fetchall()
            ]
        if not history and not artifacts and not feedback:
            return {
                "status": "not_found",
                "history": [],
                "artifacts": [],
                "feedback": [],
                "warnings": ["application_history_not_found"],
            }
        return {"status": "found", "history": history, "artifacts": artifacts, "feedback": feedback, "warnings": []}

    def get_recent_relevant_vacancies(self, *, limit: int = 20, min_score: int = 60) -> dict[str, Any]:
        with self._connect_read_only() as conn:
            rows = conn.execute(
                """
                WITH latest_evaluations AS (
                    SELECT e.*
                    FROM vacancy_evaluations e
                    JOIN (
                        SELECT vacancy_key, MAX(id) AS max_id
                        FROM vacancy_evaluations
                        WHERE score >= ?
                        GROUP BY vacancy_key
                    ) latest ON latest.vacancy_key = e.vacancy_key AND latest.max_id = e.id
                )
                SELECT v.*
                FROM vacancies v
                JOIN latest_evaluations e ON e.vacancy_key = v.vacancy_key
                ORDER BY e.score DESC, datetime(v.last_seen_at) DESC
                LIMIT ?
                """,
                (min_score, limit),
            ).fetchall()
            vacancies = [self._serialize_vacancy(conn, row) for row in rows]
        if not vacancies:
            return {"status": "not_found", "vacancies": [], "warnings": ["no_relevant_vacancies_found"]}
        warnings: list[str] = []
        if any(vacancy["warnings"] for vacancy in vacancies):
            warnings.append("contains_stale_vacancies")
        return {"status": "found", "vacancies": vacancies, "warnings": warnings}
