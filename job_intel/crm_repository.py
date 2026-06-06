from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .store import JobIntelStore


class OpportunityRepository:
    def __init__(self, store: JobIntelStore):
        self.store = store
        self.store.bootstrap()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_opportunity(self, **fields: Any) -> int:
        now = self._now()
        payload = {
            "vacancy_id": fields.get("vacancy_id"),
            "company": fields.get("company"),
            "company_normalized": fields.get("company_normalized"),
            "title": fields.get("title"),
            "title_normalized": fields.get("title_normalized"),
            "location": fields.get("location"),
            "remote_policy": fields.get("remote_policy"),
            "source": fields["source"],
            "source_url": fields.get("source_url"),
            "canonical_url": fields.get("canonical_url"),
            "ats": fields.get("ats"),
            "ats_job_id": fields.get("ats_job_id"),
            "description": fields.get("description"),
            "description_hash": fields.get("description_hash"),
            "status": fields.get("status", "discovered"),
            "score": fields.get("score"),
            "confidence": fields.get("confidence"),
            "recommendation": fields.get("recommendation"),
            "slack_channel_id": fields.get("slack_channel_id"),
            "slack_message_ts": fields.get("slack_message_ts"),
            "slack_thread_ts": fields.get("slack_thread_ts"),
            "artifact_bundle_id": fields.get("artifact_bundle_id"),
            "next_action_id": fields.get("next_action_id"),
            "created_at": fields.get("created_at", now),
            "updated_at": fields.get("updated_at", now),
            "last_seen_at": fields.get("last_seen_at"),
        }
        columns = list(payload.keys())
        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO opportunities ({', '.join(columns)}) VALUES ({placeholders})"
        with self.store.connect() as conn:
            cursor = conn.execute(sql, [payload[column] for column in columns])
            return int(cursor.lastrowid)

    def update_opportunity(self, opportunity_id: int, **fields: Any) -> None:
        if not fields:
            return
        payload = dict(fields)
        payload.setdefault("updated_at", self._now())
        assignments = ", ".join(f"{column}=?" for column in payload)
        values = list(payload.values()) + [opportunity_id]
        with self.store.connect() as conn:
            conn.execute(
                f"UPDATE opportunities SET {assignments} WHERE id=?",
                values,
            )

    def find_opportunity_by_id(self, opportunity_id: int) -> dict[str, Any] | None:
        with self.store.connect(read_only=True) as conn:
            row = conn.execute(
                "SELECT * FROM opportunities WHERE id=? LIMIT 1",
                (opportunity_id,),
            ).fetchone()
        return dict(row) if row else None

    def find_opportunity_by_canonical_url(self, canonical_url: str) -> dict[str, Any] | None:
        with self.store.connect(read_only=True) as conn:
            row = conn.execute(
                "SELECT * FROM opportunities WHERE canonical_url=? LIMIT 1",
                (canonical_url,),
            ).fetchone()
        return dict(row) if row else None

    def find_opportunity_by_ats_job(self, ats: str, ats_job_id: str) -> dict[str, Any] | None:
        with self.store.connect(read_only=True) as conn:
            row = conn.execute(
                "SELECT * FROM opportunities WHERE ats=? AND ats_job_id=? LIMIT 1",
                (ats, ats_job_id),
            ).fetchone()
        return dict(row) if row else None

    def find_opportunity_by_slack_message(self, slack_channel_id: str, slack_message_ts: str) -> dict[str, Any] | None:
        with self.store.connect(read_only=True) as conn:
            row = conn.execute(
                """
                SELECT o.*
                FROM slack_message_map smm
                JOIN opportunities o ON o.id = smm.opportunity_id
                WHERE smm.slack_channel_id=? AND smm.slack_message_ts=?
                LIMIT 1
                """,
                (slack_channel_id, slack_message_ts),
            ).fetchone()
        return dict(row) if row else None

    def list_opportunities_by_signature(
        self,
        *,
        company_normalized: str | None,
        title_normalized: str | None,
        location: str | None,
    ) -> list[dict[str, Any]]:
        with self.store.connect(read_only=True) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM opportunities
                WHERE company_normalized IS ?
                  AND title_normalized IS ?
                  AND location IS ?
                ORDER BY id
                """,
                (company_normalized, title_normalized, location),
            ).fetchall()
        return [dict(row) for row in rows]

    def search_opportunities(self, search_text: str | None = None) -> list[dict[str, Any]]:
        query = (search_text or "").strip()
        with self.store.connect(read_only=True) as conn:
            if not query:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM opportunities
                    WHERE status NOT IN ('rejected_by_company', 'declined_by_me', 'closed', 'archived')
                    ORDER BY updated_at DESC, id DESC
                    """
                ).fetchall()
            else:
                like = f"%{query.casefold()}%"
                rows = conn.execute(
                    """
                    SELECT *
                    FROM opportunities
                    WHERE lower(coalesce(company, '')) LIKE ?
                       OR lower(coalesce(title, '')) LIKE ?
                       OR lower(coalesce(canonical_url, '')) LIKE ?
                    ORDER BY updated_at DESC, id DESC
                    """,
                    (like, like, like),
                ).fetchall()
        return [dict(row) for row in rows]

    def add_event(
        self,
        *,
        opportunity_id: int,
        event_type: str,
        event_source: str,
        actor: str | None,
        payload_json: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> int:
        now = created_at or self._now()
        with self.store.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO opportunity_events (
                    opportunity_id, event_type, event_source, actor, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    opportunity_id,
                    event_type,
                    event_source,
                    actor,
                    json.dumps(payload_json or {}, ensure_ascii=False),
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def list_events(self, opportunity_id: int) -> list[dict[str, Any]]:
        with self.store.connect(read_only=True) as conn:
            rows = conn.execute(
                "SELECT * FROM opportunity_events WHERE opportunity_id=? ORDER BY id",
                (opportunity_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def find_open_task_by_type(self, opportunity_id: int, task_type: str) -> dict[str, Any] | None:
        with self.store.connect(read_only=True) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM opportunity_tasks
                WHERE opportunity_id=? AND task_type=? AND status='open'
                ORDER BY id DESC
                LIMIT 1
                """,
                (opportunity_id, task_type),
            ).fetchone()
        return dict(row) if row else None

    def create_task(
        self,
        *,
        opportunity_id: int,
        task_type: str,
        note: str | None = None,
        due_at: str | None = None,
        owner: str = "denis",
    ) -> int:
        now = self._now()
        with self.store.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO opportunity_tasks (
                    opportunity_id, task_type, status, owner, due_at, note, created_at, completed_at
                ) VALUES (?, ?, 'open', ?, ?, ?, ?, NULL)
                """,
                (opportunity_id, task_type, owner, due_at, note, now),
            )
            return int(cursor.lastrowid)

    def update_task(self, task_id: int, *, note: str | None = None, due_at: str | None = None) -> None:
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE opportunity_tasks SET note=?, due_at=? WHERE id=?",
                (note, due_at, task_id),
            )

    def list_tasks(self, opportunity_id: int) -> list[dict[str, Any]]:
        with self.store.connect(read_only=True) as conn:
            rows = conn.execute(
                "SELECT * FROM opportunity_tasks WHERE opportunity_id=? ORDER BY id",
                (opportunity_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_due_tasks(self, now_iso: str) -> list[dict[str, Any]]:
        with self.store.connect(read_only=True) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM opportunity_tasks
                WHERE status='open' AND due_at IS NOT NULL AND due_at <= ?
                ORDER BY due_at, id
                """,
                (now_iso,),
            ).fetchall()
        return [dict(row) for row in rows]

    def find_artifact(self, opportunity_id: int, artifact_type: str) -> dict[str, Any] | None:
        with self.store.connect(read_only=True) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM opportunity_artifacts
                WHERE opportunity_id=? AND artifact_type=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (opportunity_id, artifact_type),
            ).fetchone()
        return dict(row) if row else None

    def create_artifact(
        self,
        *,
        opportunity_id: int,
        artifact_type: str,
        qa_status: str,
        content_path: str | None = None,
        content_text: str | None = None,
        summary: str | None = None,
        model: str | None = None,
        qa_notes: str | None = None,
    ) -> int:
        now = self._now()
        with self.store.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO opportunity_artifacts (
                    opportunity_id, artifact_type, version, content_path, content_text, summary,
                    model, qa_status, qa_notes, created_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    opportunity_id,
                    artifact_type,
                    content_path,
                    content_text,
                    summary,
                    model,
                    qa_status,
                    qa_notes,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def list_artifacts(self, opportunity_id: int) -> list[dict[str, Any]]:
        with self.store.connect(read_only=True) as conn:
            rows = conn.execute(
                "SELECT * FROM opportunity_artifacts WHERE opportunity_id=? ORDER BY id",
                (opportunity_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def pipeline_counts(self) -> list[dict[str, Any]]:
        with self.store.connect(read_only=True) as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM opportunities
                GROUP BY status
                ORDER BY count DESC, status
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def link_slack_message_to_opportunity(
        self,
        *,
        opportunity_id: int,
        slack_channel_id: str,
        slack_message_ts: str,
        slack_thread_ts: str | None,
    ) -> None:
        now = self._now()
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO slack_message_map (
                    opportunity_id, slack_channel_id, slack_message_ts, slack_thread_ts, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(slack_channel_id, slack_message_ts) DO UPDATE SET
                    opportunity_id=excluded.opportunity_id,
                    slack_thread_ts=excluded.slack_thread_ts
                """,
                (
                    opportunity_id,
                    slack_channel_id,
                    slack_message_ts,
                    slack_thread_ts,
                    now,
                ),
            )
