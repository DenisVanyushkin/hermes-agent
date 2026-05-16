from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .models import Evaluation, Vacancy, VacancyResult

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    notes TEXT
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
    channel TEXT NOT NULL,
    message_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    delivery_status TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    body TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS company_intelligence (
    company TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    risk_flags_json TEXT NOT NULL,
    target_category TEXT,
    updated_at TEXT NOT NULL
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

    def bootstrap(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def list_tables(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        return [row[0] for row in rows]

    def start_run(self, mode: str, notes: str | None = None) -> int:
        self.bootstrap()
        now = datetime.utcnow().isoformat()
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO runs (mode, started_at, status, notes) VALUES (?, ?, ?, ?)",
                (mode, now, "running", notes),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str = "ok", notes: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE runs SET finished_at = ?, status = ?, notes = COALESCE(?, notes) WHERE id = ?",
                (datetime.utcnow().isoformat(), status, notes, run_id),
            )

    def upsert_vacancy(self, vacancy: Vacancy, vacancy_key: str) -> None:
        now = vacancy.scraped_at or datetime.utcnow().isoformat()
        with self.connect() as conn:
            row = conn.execute("SELECT repost_count FROM vacancies WHERE vacancy_key = ?", (vacancy_key,)).fetchone()
            if row:
                repost_count = int(row[0]) + 1
                conn.execute(
                    """
                    UPDATE vacancies
                    SET source = ?, source_id = ?, company = ?, title = ?, location = ?, url = ?, description = ?,
                        posted_at = ?, scraped_at = ?, salary = ?, company_url = ?, metadata_json = ?,
                        last_seen_at = ?, repost_count = ?
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
            else:
                conn.execute(
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
                    datetime.utcnow().isoformat(),
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
                (canonical_vacancy_key, duplicate_vacancy_key, reason, similarity, datetime.utcnow().isoformat()),
            )

    def set_memory(self, key: str, value: str, source: str = "system", confidence: float = 1.0) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO candidate_memory (key, value, source, confidence, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, source=excluded.source, confidence=excluded.confidence, updated_at=excluded.updated_at
                """,
                (key, value, source, confidence, datetime.utcnow().isoformat()),
            )

    def get_memory(self) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM candidate_memory ORDER BY key").fetchall()
        return {row[0]: row[1] for row in rows}

    def log_notification(self, run_id: int | None, channel: str, message_type: str, body: str, delivery_status: str = "sent") -> None:
        import hashlib

        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO notifications (
                    run_id, channel, message_type, content_hash, delivery_status, sent_at, body
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, channel, message_type, content_hash, delivery_status, datetime.utcnow().isoformat(), body),
            )

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
