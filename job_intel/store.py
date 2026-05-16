from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Evaluation, Vacancy
from .runtime import parse_iso_datetime

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    notes TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS vacancies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_key TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT NOT NULL,
    url TEXT NOT NULL,
    description TEXT NOT NULL,
    posted_at TEXT,
    scraped_at TEXT,
    salary TEXT,
    company_url TEXT,
    metadata_json TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    repost_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new'
);

CREATE TABLE IF NOT EXISTS vacancy_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_key TEXT NOT NULL,
    run_id INTEGER,
    score INTEGER NOT NULL,
    tier TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    salary_tier TEXT,
    matched_signals_json TEXT,
    concerns_json TEXT,
    reasons_json TEXT,
    raw_breakdown_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(vacancy_key) REFERENCES vacancies(vacancy_key) ON DELETE CASCADE,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS duplicate_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_vacancy_key TEXT NOT NULL,
    duplicate_vacancy_key TEXT NOT NULL,
    reason TEXT NOT NULL,
    similarity REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_memory (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    source TEXT,
    confidence REAL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS enrichment_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    answered_at TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    vacancy_id INTEGER,
    channel TEXT NOT NULL,
    message_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    delivery_status TEXT NOT NULL,
    delivery_error TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    sent_at TEXT NOT NULL,
    body TEXT NOT NULL,
    payload_json TEXT,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE SET NULL,
    FOREIGN KEY(vacancy_id) REFERENCES vacancies(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS company_intelligence (
    company TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    risk_flags_json TEXT NOT NULL,
    target_category TEXT,
    website TEXT,
    signals_json TEXT,
    career_urls_json TEXT,
    opening_count INTEGER NOT NULL DEFAULT 0,
    last_scanned_at TEXT,
    last_signal_at TEXT,
    source_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company_intelligence_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT,
    title TEXT,
    url TEXT,
    summary TEXT NOT NULL,
    details_json TEXT,
    seen_at TEXT NOT NULL
);
"""


class JobIntelStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {row[1] for row in rows}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def bootstrap(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._ensure_column(conn, "runs", "metadata_json", "TEXT")
            self._ensure_column(conn, "notifications", "vacancy_id", "INTEGER")
            self._ensure_column(conn, "notifications", "delivery_error", "TEXT")
            self._ensure_column(conn, "notifications", "delivery_attempts", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "notifications", "payload_json", "TEXT")
            self._ensure_column(conn, "company_intelligence", "website", "TEXT")
            self._ensure_column(conn, "company_intelligence", "signals_json", "TEXT")
            self._ensure_column(conn, "company_intelligence", "career_urls_json", "TEXT")
            self._ensure_column(conn, "company_intelligence", "opening_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "company_intelligence", "last_scanned_at", "TEXT")
            self._ensure_column(conn, "company_intelligence", "last_signal_at", "TEXT")
            self._ensure_column(conn, "company_intelligence", "source_json", "TEXT")

    def list_tables(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        return [row[0] for row in rows]

    def start_run(self, mode: str, notes: str | None = None, metadata: dict[str, Any] | None = None) -> int:
        self.bootstrap()
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO runs (mode, started_at, status, notes, metadata_json) VALUES (?, ?, ?, ?, ?)",
                (mode, now, "running", notes, json.dumps(metadata or {}, ensure_ascii=False)),
            )
            return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        status: str = "ok",
        notes: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT metadata_json FROM runs WHERE id = ?", (run_id,)).fetchone()
            existing_metadata = json.loads(row[0]) if row and row[0] else {}
            if metadata is not None:
                existing_metadata.update(metadata)
            conn.execute(
                "UPDATE runs SET finished_at = ?, status = ?, notes = COALESCE(?, notes), metadata_json = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), status, notes, json.dumps(existing_metadata, ensure_ascii=False), run_id),
            )

    def latest_run(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def fetch_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def upsert_vacancy(self, vacancy: Vacancy, vacancy_key: str) -> int:
        now = vacancy.scraped_at or datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            row = conn.execute("SELECT id, repost_count FROM vacancies WHERE vacancy_key = ?", (vacancy_key,)).fetchone()
            if row:
                repost_count = int(row[1]) + 1
                conn.execute(
                    """
                    UPDATE vacancies
                    SET source = ?, source_id = ?, company = ?, title = ?, location = ?, url = ?, description = ?,
                        posted_at = ?, scraped_at = ?, salary = ?, company_url = ?, metadata_json = ?,
                        last_seen_at = ?, repost_count = ?, status = 'active'
                    WHERE vacancy_key = ?
                    """,
                    (
                        vacancy.source,
                        vacancy.source_id,
                        vacancy.company,
                        vacancy.title,
                        vacancy.location,
                        vacancy.url,
                        vacancy.description,
                        vacancy.posted_at,
                        vacancy.scraped_at,
                        vacancy.salary,
                        vacancy.company_url,
                        json.dumps(vacancy.metadata, ensure_ascii=False),
                        now,
                        repost_count,
                        vacancy_key,
                    ),
                )
                return int(row[0])
            cur = conn.execute(
                """
                INSERT INTO vacancies (
                    vacancy_key, source, source_id, company, title, location, url, description,
                    posted_at, scraped_at, salary, company_url, metadata_json, first_seen_at, last_seen_at, repost_count, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'new')
                """,
                (
                    vacancy_key,
                    vacancy.source,
                    vacancy.source_id,
                    vacancy.company,
                    vacancy.title,
                    vacancy.location,
                    vacancy.url,
                    vacancy.description,
                    vacancy.posted_at,
                    vacancy.scraped_at,
                    vacancy.salary,
                    vacancy.company_url,
                    json.dumps(vacancy.metadata, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            return int(cur.lastrowid)

    def set_vacancy_status(self, vacancy_id: int, status: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE vacancies SET status = ? WHERE id = ?", (status, vacancy_id))

    def get_vacancy_by_key(self, vacancy_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM vacancies WHERE vacancy_key = ?", (vacancy_key,)).fetchone()
        return dict(row) if row else None

    def get_vacancy_by_id(self, vacancy_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM vacancies WHERE id = ?", (vacancy_id,)).fetchone()
        return dict(row) if row else None

    def save_evaluation(self, vacancy_key: str, evaluation: Evaluation, run_id: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO vacancy_evaluations (
                    vacancy_key, run_id, score, tier, recommendation, salary_tier,
                    matched_signals_json, concerns_json, reasons_json, raw_breakdown_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vacancy_key,
                    run_id,
                    evaluation.score,
                    evaluation.tier,
                    evaluation.recommendation,
                    evaluation.salary_tier,
                    json.dumps(evaluation.matched_signals, ensure_ascii=False),
                    json.dumps(evaluation.concerns, ensure_ascii=False),
                    json.dumps(evaluation.reasons, ensure_ascii=False),
                    json.dumps(evaluation.raw_breakdown, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def save_duplicate(self, canonical_vacancy_key: str, duplicate_vacancy_key: str, reason: str, similarity: float) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO duplicate_links (
                    canonical_vacancy_key, duplicate_vacancy_key, reason, similarity, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (canonical_vacancy_key, duplicate_vacancy_key, reason, similarity, datetime.now(timezone.utc).isoformat()),
            )

    def set_memory(self, key: str, value: str, source: str = "system", confidence: float = 1.0) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO candidate_memory (key, value, source, confidence, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, source=excluded.source, confidence=excluded.confidence, updated_at=excluded.updated_at
                """,
                (key, value, source, confidence, datetime.now(timezone.utc).isoformat()),
            )

    def get_memory(self) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM candidate_memory ORDER BY key").fetchall()
        return {row[0]: row[1] for row in rows}

    def create_notification(
        self,
        run_id: int | None,
        channel: str,
        message_type: str,
        body: str,
        *,
        vacancy_id: int | None = None,
        payload: dict[str, Any] | None = None,
        delivery_status: str = "pending",
        delivery_error: str | None = None,
        delivery_attempts: int = 0,
    ) -> int:
        import hashlib

        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO notifications (
                    run_id, vacancy_id, channel, message_type, content_hash, delivery_status,
                    delivery_error, delivery_attempts, sent_at, body, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    vacancy_id,
                    channel,
                    message_type,
                    content_hash,
                    delivery_status,
                    delivery_error,
                    delivery_attempts,
                    datetime.now(timezone.utc).isoformat(),
                    body,
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid)

    def mark_notification_delivery(
        self,
        notification_id: int,
        delivery_status: str,
        *,
        attempts: int | None = None,
        delivery_error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT delivery_attempts FROM notifications WHERE id = ?", (notification_id,)).fetchone()
            if not row:
                return
            current_attempts = int(row[0] or 0)
            conn.execute(
                """
                UPDATE notifications
                SET delivery_status = ?, delivery_error = ?, delivery_attempts = ?, sent_at = ?
                WHERE id = ?
                """,
                (
                    delivery_status,
                    delivery_error,
                    attempts if attempts is not None else current_attempts,
                    datetime.now(timezone.utc).isoformat(),
                    notification_id,
                ),
            )

    def latest_notification_for_vacancy(self, vacancy_id: int, delivery_status: str | None = None) -> dict[str, Any] | None:
        with self.connect() as conn:
            if delivery_status:
                row = conn.execute(
                    "SELECT * FROM notifications WHERE vacancy_id = ? AND delivery_status = ? ORDER BY id DESC LIMIT 1",
                    (vacancy_id, delivery_status),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM notifications WHERE vacancy_id = ? ORDER BY id DESC LIMIT 1",
                    (vacancy_id,),
                ).fetchone()
        return dict(row) if row else None

    def fetch_notifications(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def fetch_recent_vacancies(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM vacancies ORDER BY datetime(last_seen_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def fetch_top_evaluations(self, min_score: int = 60, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT v.*, e.score, e.tier, e.recommendation, e.salary_tier, e.matched_signals_json, e.concerns_json, e.reasons_json
                FROM vacancies v
                JOIN vacancy_evaluations e ON e.vacancy_key = v.vacancy_key
                WHERE e.score >= ?
                ORDER BY e.score DESC, datetime(v.last_seen_at) DESC
                LIMIT ?
                """,
                (min_score, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def retire_stale(self, *, days: int, archive_after_days: int | None = None) -> dict[str, int]:
        archive_after_days = archive_after_days or max(days * 2, days + 1)
        now = datetime.now(timezone.utc)
        stale_before = (now - timedelta(days=days)).isoformat()
        archive_before = (now - timedelta(days=archive_after_days)).isoformat()
        with self.connect() as conn:
            stale_candidates = conn.execute(
                """
                SELECT id FROM vacancies
                WHERE status IN ('new', 'active', 'notified')
                  AND last_seen_at < ?
                """,
                (stale_before,),
            ).fetchall()
            for row in stale_candidates:
                conn.execute("UPDATE vacancies SET status = 'stale' WHERE id = ?", (int(row[0]),))

            archived_candidates = conn.execute(
                """
                SELECT id FROM vacancies
                WHERE status = 'stale'
                  AND last_seen_at < ?
                """,
                (archive_before,),
            ).fetchall()
            for row in archived_candidates:
                conn.execute("UPDATE vacancies SET status = 'archived' WHERE id = ?", (int(row[0]),))

        return {"stale": len(stale_candidates), "archived": len(archived_candidates)}

    def source_adapter_status_from_latest_run(self) -> dict[str, Any]:
        latest = self.latest_run() or {}
        metadata = latest.get("metadata_json") or "{}"
        try:
            payload = json.loads(metadata)
        except json.JSONDecodeError:
            payload = {}
        return payload.get("source_statuses") or {}

    def upsert_company_intelligence(
        self,
        company: str,
        summary: str,
        signals: dict[str, Any],
        *,
        target_category: str | None = None,
        website: str | None = None,
        career_urls: list[str] | None = None,
        opening_count: int = 0,
        source: str | None = None,
        risk_flags: list[str] | None = None,
        last_scanned_at: str | None = None,
        last_signal_at: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO company_intelligence (
                    company, summary, risk_flags_json, target_category, website, signals_json, career_urls_json,
                    opening_count, last_scanned_at, last_signal_at, source_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company) DO UPDATE SET
                    summary=excluded.summary,
                    risk_flags_json=excluded.risk_flags_json,
                    target_category=excluded.target_category,
                    website=excluded.website,
                    signals_json=excluded.signals_json,
                    career_urls_json=excluded.career_urls_json,
                    opening_count=excluded.opening_count,
                    last_scanned_at=excluded.last_scanned_at,
                    last_signal_at=excluded.last_signal_at,
                    source_json=excluded.source_json,
                    updated_at=excluded.updated_at
                """,
                (
                    company,
                    summary,
                    json.dumps(risk_flags or [], ensure_ascii=False),
                    target_category,
                    website,
                    json.dumps(signals or {}, ensure_ascii=False),
                    json.dumps(career_urls or [], ensure_ascii=False),
                    opening_count,
                    last_scanned_at or now,
                    last_signal_at,
                    json.dumps({"source": source or "system"}, ensure_ascii=False),
                    now,
                ),
            )

    def append_company_event(
        self,
        company: str,
        event_type: str,
        *,
        source: str | None = None,
        title: str | None = None,
        url: str | None = None,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO company_intelligence_events (
                    company, event_type, source, title, url, summary, details_json, seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company,
                    event_type,
                    source,
                    title,
                    url,
                    summary,
                    json.dumps(details or {}, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def fetch_company_intelligence(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM company_intelligence ORDER BY datetime(updated_at) DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
