from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Evaluation, Vacancy
from .runtime import capture_runtime_provenance, parse_iso_datetime

logger = logging.getLogger(__name__)


def _json_safe_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return str(value)


def canonical_source_key(value: str | None) -> str:
    raw = (value or "").strip().lower()
    return "".join(ch for ch in raw if ch.isalnum() or ch == "_")


def canonical_company_key(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    collapsed = re.sub(r"\s+", " ", raw.casefold())
    if collapsed == "unknown":
        return "unknown"
    return "".join(ch for ch in collapsed if ch.isalnum()) or None


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
    metadata_json TEXT,
    provenance_json TEXT
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

CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_id INTEGER,
    company TEXT,
    company_normalized TEXT,
    title TEXT,
    title_normalized TEXT,
    location TEXT,
    remote_policy TEXT,
    source TEXT NOT NULL,
    source_url TEXT,
    canonical_url TEXT,
    ats TEXT,
    ats_job_id TEXT,
    description TEXT,
    description_hash TEXT,
    status TEXT NOT NULL DEFAULT 'discovered',
    score INTEGER,
    confidence TEXT,
    recommendation TEXT,
    slack_channel_id TEXT,
    slack_message_ts TEXT,
    slack_thread_ts TEXT,
    artifact_bundle_id INTEGER,
    next_action_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_seen_at TEXT,
    FOREIGN KEY(vacancy_id) REFERENCES vacancies(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS opportunity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER,
    event_type TEXT NOT NULL,
    event_source TEXT NOT NULL,
    actor TEXT,
    payload_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS opportunity_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER NOT NULL,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    owner TEXT NOT NULL DEFAULT 'denis',
    due_at TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS opportunity_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER NOT NULL,
    artifact_type TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    content_path TEXT,
    content_text TEXT,
    summary TEXT,
    model TEXT,
    qa_status TEXT,
    qa_notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS opportunity_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER NOT NULL,
    name TEXT,
    role TEXT,
    company TEXT,
    email TEXT,
    linkedin_url TEXT,
    source TEXT,
    confidence TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS slack_message_map (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER NOT NULL,
    slack_channel_id TEXT NOT NULL,
    slack_message_ts TEXT NOT NULL,
    slack_thread_ts TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(slack_channel_id, slack_message_ts),
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE
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

CREATE TABLE IF NOT EXISTS vacancy_observability (
    run_id INTEGER NOT NULL,
    vacancy_key TEXT NOT NULL,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL DEFAULT '',
    role_bucket TEXT NOT NULL,
    geo_bucket TEXT NOT NULL,
    industry_bucket TEXT NOT NULL,
    executive_detected INTEGER NOT NULL DEFAULT 0,
    accepted INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0,
    score INTEGER NOT NULL,
    score_band TEXT NOT NULL,
    confidence REAL,
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    company TEXT,
    canonical_company_key TEXT,
    title TEXT,
    location TEXT,
    score_v1 INTEGER,
    score_v2 INTEGER,
    active_score INTEGER,
    active_scoring_version TEXT,
    recommendation TEXT,
    active_recommendation_version TEXT,
    canonical_url TEXT,
    UNIQUE(run_id, vacancy_key, url),
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS vacancy_rejection_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    vacancy_key TEXT NOT NULL,
    source TEXT NOT NULL,
    role_bucket TEXT NOT NULL,
    geo_bucket TEXT NOT NULL,
    industry_bucket TEXT NOT NULL,
    score INTEGER NOT NULL,
    score_band TEXT NOT NULL,
    confidence REAL,
    rejection_reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS vacancy_rejection_summary (
    run_id INTEGER NOT NULL,
    vacancy_key TEXT NOT NULL,
    source TEXT NOT NULL,
    score INTEGER NOT NULL,
    score_band TEXT NOT NULL,
    accepted INTEGER NOT NULL DEFAULT 0,
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    rejection_reason_count INTEGER NOT NULL DEFAULT 0,
    top_rejection_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, vacancy_key),
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
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
    notification_kind TEXT,
    card_key TEXT,
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

CREATE TABLE IF NOT EXISTS vacancy_card_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    vacancy_id INTEGER,
    vacancy_key TEXT NOT NULL,
    canonical_url TEXT,
    card_key TEXT NOT NULL,
    source TEXT,
    company TEXT,
    title TEXT,
    score INTEGER,
    recommendation TEXT,
    candidate_rank INTEGER,
    decision TEXT NOT NULL,
    suppression_reason TEXT,
    previous_sent_run_id INTEGER,
    previous_sent_at TEXT,
    previous_feedback_label TEXT,
    feedback_state_active INTEGER NOT NULL DEFAULT 0,
    next_allowed_send_at TEXT,
    score_delta_since_last_sent INTEGER,
    recommendation_changed_since_last_sent INTEGER NOT NULL DEFAULT 0,
    content_hash_changed INTEGER NOT NULL DEFAULT 0,
    description_hash_changed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, card_key),
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
    FOREIGN KEY(vacancy_id) REFERENCES vacancies(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS job_intel_performance_spans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    span_name TEXT NOT NULL,
    parent_span_name TEXT,
    source_name TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',
    found_count INTEGER,
    normalized_count INTEGER,
    accepted_count INTEGER,
    duplicate_count INTEGER,
    rejected_count INTEGER,
    new_card_keys INTEGER,
    cards_sent INTEGER,
    error_count INTEGER,
    retry_count INTEGER,
    timeout_count INTEGER,
    metadata_json TEXT,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS source_kpi_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,

    source_status TEXT,
    acquisition_mode TEXT,
    runtime_seconds REAL,
    attempts INTEGER,

    pages_fetched INTEGER,
    login_walls INTEGER,
    auth_redirects INTEGER,
    anti_bot_events INTEGER,
    extraction_failures INTEGER,

    found_count INTEGER,
    executive_detected_count INTEGER,
    scored_count INTEGER,
    accepted_count INTEGER,
    notified_count INTEGER,

    vacancies_deduped INTEGER,
    rejected_count INTEGER,

    avg_vacancy_score REAL,
    vacancy_score_p50 INTEGER,
    vacancy_score_p90 INTEGER,
    accepted_score_p50 INTEGER,

    pct_company_known REAL,
    pct_location_known REAL,
    pct_salary_known REAL,
    pct_seniority_confident REAL,

    company_score_avg REAL,
    company_score_p90 REAL,
    industry_fit_avg REAL,
    tier1_company_count INTEGER,
    tier2_company_count INTEGER,
    interview_generated_count INTEGER,

    error_class TEXT,
    error_fingerprint TEXT,
    error_message_truncated TEXT,

    UNIQUE(run_id, source),
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS registry_company_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL DEFAULT '',
    company_name TEXT NOT NULL,
    canonical_company_key TEXT,
    tier TEXT,
    ats_vendor TEXT,
    ats_slug TEXT,
    collection_url TEXT,
    validation_url TEXT,
    acquisition_enabled INTEGER NOT NULL DEFAULT 1,
    attempted INTEGER NOT NULL DEFAULT 0,
    collected INTEGER NOT NULL DEFAULT 0,
    vacancies_found INTEGER NOT NULL DEFAULT 0,
    vacancies_stored INTEGER NOT NULL DEFAULT 0,
    vacancies_scored INTEGER NOT NULL DEFAULT 0,
    source_status TEXT,
    reason TEXT,
    errors_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, source, company_name),
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
);



CREATE TABLE IF NOT EXISTS vacancy_slack_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_id INTEGER NOT NULL,
    run_id INTEGER,
    vacancy_key TEXT,
    canonical_url TEXT,
    card_key TEXT,
    notification_id INTEGER,
    slack_channel TEXT NOT NULL,
    slack_message_ts TEXT NOT NULL,
    message_type TEXT,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    score INTEGER NOT NULL,
    recommendation TEXT NOT NULL,
    score_at_send INTEGER,
    recommendation_at_send TEXT,
    url TEXT NOT NULL,
    sent_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(slack_channel, slack_message_ts),
    FOREIGN KEY(vacancy_id) REFERENCES vacancies(id) ON DELETE CASCADE,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE SET NULL,
    FOREIGN KEY(notification_id) REFERENCES notifications(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS vacancy_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_id INTEGER NOT NULL,
    run_id INTEGER,
    notification_id INTEGER,
    vacancy_key TEXT,
    canonical_url TEXT,
    card_key TEXT,
    slack_channel TEXT,
    slack_message_ts TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_timestamp TEXT NOT NULL,
    user_id TEXT NOT NULL,
    raw_event_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(vacancy_id) REFERENCES vacancies(id) ON DELETE CASCADE,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE SET NULL,
    FOREIGN KEY(notification_id) REFERENCES notifications(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS vacancy_feedback_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_id INTEGER NOT NULL,
    run_id INTEGER,
    notification_id INTEGER,
    vacancy_key TEXT,
    canonical_url TEXT,
    card_key TEXT,
    slack_channel TEXT,
    slack_message_ts TEXT NOT NULL,
    user_id TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    UNIQUE(vacancy_id, user_id, feedback_type),
    FOREIGN KEY(vacancy_id) REFERENCES vacancies(id) ON DELETE CASCADE,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE SET NULL,
    FOREIGN KEY(notification_id) REFERENCES notifications(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS production_observation_daily (
    run_id INTEGER PRIMARY KEY,
    run_started_at TEXT,
    run_finished_at TEXT,
    runtime_seconds REAL,
    total_collected INTEGER NOT NULL DEFAULT 0,
    total_unique INTEGER NOT NULL DEFAULT 0,
    duplicate_rate REAL,
    unknown_company_rate REAL,
    strong_fit_count INTEGER NOT NULL DEFAULT 0,
    needs_review_count INTEGER NOT NULL DEFAULT 0,
    near_miss_count INTEGER NOT NULL DEFAULT 0,
    login_walls INTEGER NOT NULL DEFAULT 0,
    anti_bot_events INTEGER NOT NULL DEFAULT 0,
    auth_redirects INTEGER NOT NULL DEFAULT 0,
    source_failures_json TEXT,
    source_runtimes_json TEXT,
    slowest_source TEXT,
    vacancies_sent INTEGER NOT NULL DEFAULT 0,
    vacancies_reacted INTEGER NOT NULL DEFAULT 0,
    reaction_rate REAL,
    positive_rate REAL,
    applied_rate REAL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_feedback_opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_key TEXT NOT NULL,
    run_id INTEGER,
    status TEXT NOT NULL DEFAULT 'unseen',
    updated_at TEXT NOT NULL,
    notes TEXT,
    UNIQUE(vacancy_key),
    FOREIGN KEY(vacancy_key) REFERENCES vacancies(vacancy_key) ON DELETE CASCADE,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS vacancy_scoring_shadow (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    vacancy_key TEXT NOT NULL,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL DEFAULT '',
    score_v2 INTEGER,
    recommendation_v2 TEXT,
    score_v3 INTEGER,
    recommendation_v3 TEXT,
    score_v3_nr INTEGER,
    recommendation_v3_nr TEXT,
    gates_v3_json TEXT,
    function_class_v3 TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, vacancy_key),
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
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

CREATE TABLE IF NOT EXISTS strategic_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    horizon_days INTEGER,
    probability REAL,
    rationale TEXT NOT NULL,
    evidence_json TEXT,
    source TEXT,
    observed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(company, signal_type, horizon_days, observed_at)
);

CREATE TABLE IF NOT EXISTS strategic_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    prediction_type TEXT NOT NULL,
    probability REAL NOT NULL,
    horizon_days INTEGER NOT NULL,
    rationale TEXT NOT NULL,
    evidence_json TEXT,
    source TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution_status TEXT NOT NULL DEFAULT 'open',
    outcome_text TEXT,
    observed_openings INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS feedback_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER,
    vacancy_id INTEGER,
    company TEXT,
    slack_channel_id TEXT,
    slack_message_ts TEXT,
    slack_thread_ts TEXT,
    prompt_message_ts TEXT,
    user_id TEXT,
    reaction_type TEXT,
    polarity TEXT NOT NULL DEFAULT 'negative',
    status TEXT NOT NULL DEFAULT 'awaiting_reply',
    reason_category_codes_json TEXT,
    reason_detail_codes_json TEXT,
    attribution_targets_json TEXT,
    free_text TEXT,
    classifier_version TEXT,
    classifier_confidence REAL,
    hard_blocker INTEGER,
    soft_preference INTEGER,
    applies_to_company INTEGER,
    applies_to_role INTEGER,
    applies_to_location INTEGER,
    applies_to_industry INTEGER,
    applies_to_parser_quality INTEGER,
    scoring_features_impacted_json TEXT,
    user_confirmed INTEGER,
    needs_followup INTEGER NOT NULL DEFAULT 0,
    followup_question_sent_at TEXT,
    followup_answer_received_at TEXT,
    needs_manual_review INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'slack_reaction_thread',
    raw_payload_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE SET NULL,
    FOREIGN KEY(vacancy_id) REFERENCES vacancies(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_events_opportunity ON feedback_events(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_feedback_events_message ON feedback_events(slack_channel_id, slack_message_ts, user_id);
CREATE INDEX IF NOT EXISTS idx_feedback_events_created ON feedback_events(created_at);

CREATE TABLE IF NOT EXISTS scoring_calibration_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'proposed',
    evidence_window_days INTEGER,
    evidence_json TEXT,
    proposed_changes_json TEXT,
    dry_run_result_json TEXT,
    risk_level TEXT,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    applied_at TEXT,
    rejected_at TEXT,
    rollback_ref TEXT
);

CREATE TABLE IF NOT EXISTS scoring_calibration_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT,
    event_payload_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(proposal_id) REFERENCES scoring_calibration_proposals(id) ON DELETE CASCADE
);
"""


class JobIntelStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            strategies = (
                ("ro", lambda: sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)),
                ("immutable", lambda: sqlite3.connect(f"file:{self.db_path}?mode=ro&immutable=1", uri=True)),
                ("plain", lambda: sqlite3.connect(self.db_path)),
            )
            last_error: sqlite3.OperationalError | None = None
            for strategy_name, open_conn in strategies:
                try:
                    conn = open_conn()
                    logger.debug("job-intel sqlite read-only connect strategy=%s db_path=%s", strategy_name, self.db_path)
                    break
                except sqlite3.OperationalError as exc:
                    last_error = exc
            else:
                assert last_error is not None
                raise last_error
        else:
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
            self._ensure_column(conn, "runs", "provenance_json", "TEXT")
            self._ensure_column(conn, "runs", "run_type", "TEXT NOT NULL DEFAULT 'production'")
            self._ensure_column(conn, "notifications", "vacancy_id", "INTEGER")
            self._ensure_column(conn, "notifications", "notification_kind", "TEXT")
            self._ensure_column(conn, "notifications", "card_key", "TEXT")
            self._ensure_column(conn, "notifications", "delivery_error", "TEXT")
            self._ensure_column(conn, "notifications", "delivery_attempts", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "notifications", "payload_json", "TEXT")
            self._ensure_column(conn, "vacancy_slack_messages", "vacancy_key", "TEXT")
            self._ensure_column(conn, "vacancy_slack_messages", "canonical_url", "TEXT")
            self._ensure_column(conn, "vacancy_slack_messages", "card_key", "TEXT")
            self._ensure_column(conn, "vacancy_slack_messages", "notification_id", "INTEGER")
            self._ensure_column(conn, "vacancy_slack_messages", "message_type", "TEXT")
            self._ensure_column(conn, "vacancy_slack_messages", "score_at_send", "INTEGER")
            self._ensure_column(conn, "vacancy_slack_messages", "recommendation_at_send", "TEXT")
            self._ensure_column(conn, "vacancy_slack_messages", "sent_at", "TEXT")
            self._ensure_column(conn, "vacancy_feedback", "run_id", "INTEGER")
            self._ensure_column(conn, "vacancy_feedback", "notification_id", "INTEGER")
            self._ensure_column(conn, "vacancy_feedback", "vacancy_key", "TEXT")
            self._ensure_column(conn, "vacancy_feedback", "canonical_url", "TEXT")
            self._ensure_column(conn, "vacancy_feedback", "card_key", "TEXT")
            self._ensure_column(conn, "vacancy_feedback", "slack_channel", "TEXT")
            self._ensure_column(conn, "vacancy_feedback_state", "run_id", "INTEGER")
            self._ensure_column(conn, "vacancy_feedback_state", "notification_id", "INTEGER")
            self._ensure_column(conn, "vacancy_feedback_state", "vacancy_key", "TEXT")
            self._ensure_column(conn, "vacancy_feedback_state", "canonical_url", "TEXT")
            self._ensure_column(conn, "vacancy_feedback_state", "card_key", "TEXT")
            self._ensure_column(conn, "vacancy_feedback_state", "slack_channel", "TEXT")
            self._ensure_column(conn, "company_intelligence", "website", "TEXT")
            self._ensure_column(conn, "company_intelligence", "signals_json", "TEXT")
            self._ensure_column(conn, "company_intelligence", "career_urls_json", "TEXT")
            self._ensure_column(conn, "company_intelligence", "opening_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "company_intelligence", "last_scanned_at", "TEXT")
            self._ensure_column(conn, "company_intelligence", "last_signal_at", "TEXT")
            self._ensure_column(conn, "company_intelligence", "source_json", "TEXT")
            self._ensure_column(conn, "strategic_signals", "source", "TEXT")
            self._ensure_column(conn, "strategic_signals", "evidence_json", "TEXT")
            self._ensure_column(conn, "strategic_signals", "observed_at", "TEXT")
            self._ensure_column(conn, "strategic_signals", "updated_at", "TEXT")
            self._ensure_column(conn, "strategic_predictions", "evidence_json", "TEXT")
            self._ensure_column(conn, "strategic_predictions", "source", "TEXT")
            self._ensure_column(conn, "strategic_predictions", "resolved_at", "TEXT")
            self._ensure_column(conn, "strategic_predictions", "resolution_status", "TEXT NOT NULL DEFAULT 'open'")
            self._ensure_column(conn, "strategic_predictions", "outcome_text", "TEXT")
            self._ensure_column(conn, "strategic_predictions", "observed_openings", "INTEGER NOT NULL DEFAULT 0")
            # Phase 3 observability migrations
            self._ensure_column(conn, "vacancy_observability", "company", "TEXT")
            self._ensure_column(conn, "vacancy_observability", "title", "TEXT")
            self._ensure_column(conn, "vacancy_observability", "location", "TEXT")
            self._ensure_column(conn, "vacancy_observability", "url", "TEXT")
            self._ensure_column(conn, "vacancy_observability", "score_v1", "INTEGER")
            self._ensure_column(conn, "vacancy_observability", "score_v2", "INTEGER")
            self._ensure_column(conn, "vacancy_observability", "active_score", "INTEGER")
            self._ensure_column(conn, "vacancy_observability", "recommendation", "TEXT")
            self._ensure_column(conn, "vacancy_observability", "canonical_url", "TEXT")
            self._ensure_column(conn, "vacancy_observability", "source_key", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "vacancy_observability", "canonical_company_key", "TEXT")
            self._ensure_column(conn, "vacancy_observability", "active_scoring_version", "TEXT")
            self._ensure_column(conn, "vacancy_observability", "active_recommendation_version", "TEXT")
            self._ensure_column(conn, "vacancy_rejection_events", "reason_type", "TEXT")
            self._ensure_column(conn, "vacancy_rejection_events", "severity", "TEXT")
            self._ensure_column(conn, "vacancy_rejection_summary", "recommendation", "TEXT")
            self._ensure_column(conn, "vacancy_rejection_summary", "real_blocker_count", "INTEGER")
            self._ensure_column(conn, "vacancy_rejection_summary", "unknown_count", "INTEGER")
            self._ensure_column(conn, "vacancy_rejection_summary", "warning_count", "INTEGER")
            self._ensure_column(conn, "vacancy_rejection_summary", "top_real_blocker", "TEXT")
            self._ensure_column(conn, "vacancy_rejection_summary", "top_unknown_reason", "TEXT")
            self._ensure_column(conn, "vacancy_rejection_summary", "top_warning", "TEXT")
            self._ensure_column(conn, "source_kpi_run", "enabled", "INTEGER DEFAULT 1")
            self._ensure_column(conn, "source_kpi_run", "skip_reason", "TEXT")
            self._ensure_column(conn, "registry_company_runs", "source_key", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "registry_company_runs", "canonical_company_key", "TEXT")
            self._ensure_column(conn, "production_observation_daily", "vacancies_sent", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "production_observation_daily", "vacancies_reacted", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "production_observation_daily", "reaction_rate", "REAL")
            self._ensure_column(conn, "production_observation_daily", "positive_rate", "REAL")
            self._ensure_column(conn, "production_observation_daily", "applied_rate", "REAL")
            self._ensure_column(conn, "vacancy_scoring_shadow", "score_v3_nr", "INTEGER")
            self._ensure_column(conn, "vacancy_scoring_shadow", "recommendation_v3_nr", "TEXT")
            self._ensure_column(conn, "vacancy_scoring_shadow", "source_key", "TEXT NOT NULL DEFAULT ''")
            # Phase 3.8: fix URL-dedup gap — widen unique key to (run_id, vacancy_key, url)
            self._migrate_vacancy_observability_unique_key(conn)
            self._backfill_observability_readiness_columns(conn)

    def _migrate_vacancy_observability_unique_key(self, conn: "sqlite3.Connection") -> None:
        """Idempotent migration: widen UNIQUE(run_id, vacancy_key) to
        UNIQUE(run_id, vacancy_key, url) so each distinct URL gets its own
        observability row within a run, closing the found_count gap.

        Safe to call on every bootstrap — detects the old constraint via the
        stored schema string and skips if already migrated.
        """
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='vacancy_observability'"
        ).fetchone()
        if row is None:
            return  # table not yet created; fresh install will use new SCHEMA
        existing_sql = (row[0] or "").replace(" ", "").replace("\n", "")
        if "UNIQUE(run_id,vacancy_key,url)" in existing_sql:
            return  # already migrated
        logger.info("Migrating vacancy_observability unique key to (run_id, vacancy_key, url)")
        conn.executescript("""
PRAGMA foreign_keys=OFF;
BEGIN;
ALTER TABLE vacancy_observability RENAME TO _vacancy_observability_old;
CREATE TABLE vacancy_observability (
    run_id INTEGER NOT NULL,
    vacancy_key TEXT NOT NULL,
    source TEXT NOT NULL,
    role_bucket TEXT NOT NULL,
    geo_bucket TEXT NOT NULL,
    industry_bucket TEXT NOT NULL,
    executive_detected INTEGER NOT NULL DEFAULT 0,
    accepted INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0,
    score INTEGER NOT NULL,
    score_band TEXT NOT NULL,
    confidence REAL,
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    company TEXT,
    title TEXT,
    location TEXT,
    score_v1 INTEGER,
    score_v2 INTEGER,
    active_score INTEGER,
    recommendation TEXT,
    UNIQUE(run_id, vacancy_key, url),
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
);
INSERT INTO vacancy_observability
    SELECT run_id, vacancy_key, source, role_bucket, geo_bucket, industry_bucket,
           executive_detected, accepted, notified, score, score_band, confidence,
           is_duplicate, created_at,
           COALESCE(url, '') AS url,
           company, title, location, score_v1, score_v2, active_score, recommendation
    FROM _vacancy_observability_old;
DROP TABLE _vacancy_observability_old;
COMMIT;
PRAGMA foreign_keys=ON;
""")
        logger.info("vacancy_observability migration complete")

    def _backfill_observability_readiness_columns(self, conn: "sqlite3.Connection") -> None:
        rows = conn.execute(
            """
            SELECT rowid, source, company
            FROM vacancy_observability
            WHERE source_key IS NULL OR source_key = '' OR canonical_company_key IS NULL
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE vacancy_observability SET source_key = ?, canonical_company_key = ? WHERE rowid = ?",
                (canonical_source_key(row[1]), canonical_company_key(row[2]), row[0]),
            )

        rows = conn.execute(
            """
            SELECT rowid, source, company_name
            FROM registry_company_runs
            WHERE source_key IS NULL OR source_key = '' OR canonical_company_key IS NULL
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE registry_company_runs SET source_key = ?, canonical_company_key = ? WHERE rowid = ?",
                (canonical_source_key(row[1]), canonical_company_key(row[2]), row[0]),
            )

        rows = conn.execute(
            "SELECT rowid, source FROM vacancy_scoring_shadow WHERE source_key IS NULL OR source_key = ''"
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE vacancy_scoring_shadow SET source_key = ? WHERE rowid = ?",
                (canonical_source_key(row[1]), row[0]),
            )

    def list_tables(self, *, read_only: bool = False) -> list[str]:
        with self.connect(read_only=read_only) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        return [row[0] for row in rows]

    def start_run(
        self,
        mode: str,
        notes: str | None = None,
        metadata: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> int:
        self.bootstrap()
        now = datetime.now(timezone.utc).isoformat()
        runtime_provenance = provenance or capture_runtime_provenance(db_path=self.db_path)
        run_type = os.getenv("JOB_INTEL_RUN_TYPE", "production")
        scoring_model_version = (os.getenv("SCORING_MODEL_VERSION", "v1") or "v1").strip().lower()
        merged_metadata = dict(metadata or {})
        merged_metadata.setdefault("scoring_model_version", scoring_model_version)
        merged_notes = notes
        # Also include in notes for stable SQL filtering without JSON parsing extensions.
        if merged_notes:
            if "scoring_model_version=" not in merged_notes:
                merged_notes = (merged_notes + f" scoring_model_version={scoring_model_version}").strip()
        else:
            merged_notes = f"scoring_model_version={scoring_model_version}"
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO runs (mode, started_at, status, notes, metadata_json, provenance_json, run_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    mode,
                    now,
                    "running",
                    merged_notes,
                    json.dumps(merged_metadata, ensure_ascii=False, default=_json_safe_default),
                    json.dumps(runtime_provenance, ensure_ascii=False, default=_json_safe_default),
                    run_type,
                ),
            )
            return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        status: str = "ok",
        notes: str | None = None,
        metadata: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT metadata_json, provenance_json FROM runs WHERE id = ?", (run_id,)).fetchone()
            existing_metadata = json.loads(row[0]) if row and row[0] else {}
            existing_provenance = json.loads(row[1]) if row and row[1] else {}
            if metadata is not None:
                existing_metadata.update(metadata)
            if provenance is not None:
                existing_provenance.update(provenance)
            conn.execute(
                "UPDATE runs SET finished_at = ?, status = ?, notes = COALESCE(?, notes), metadata_json = ?, provenance_json = ? WHERE id = ?",
                (
                    datetime.now(timezone.utc).isoformat(),
                    status,
                    notes,
                    json.dumps(existing_metadata, ensure_ascii=False, default=_json_safe_default),
                    json.dumps(existing_provenance, ensure_ascii=False, default=_json_safe_default),
                    run_id,
                ),
            )

    def latest_run(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ? LIMIT 1", (run_id,)).fetchone()
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

    def upsert_vacancy_observability(
        self,
        *,
        run_id: int,
        vacancy_key: str,
        source: str,
        role_bucket: str,
        geo_bucket: str,
        industry_bucket: str,
        executive_detected: bool,
        accepted: bool,
        notified: bool,
        score: int,
        score_band: str,
        confidence: float | None,
        is_duplicate: bool,
        created_at: str | None = None,
        company: str | None = None,
        title: str | None = None,
        location: str | None = None,
        url: str | None = None,
        score_v1: int | None = None,
        score_v2: int | None = None,
        active_score: int | None = None,
        recommendation: str | None = None,
        canonical_url: str | None = None,
        active_scoring_version: str | None = None,
        active_recommendation_version: str | None = None,
    ) -> None:
        source_key = canonical_source_key(source)
        company_key = canonical_company_key(company)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO vacancy_observability (
                    run_id, vacancy_key, source, source_key, role_bucket, geo_bucket, industry_bucket,
                    executive_detected, accepted, notified, score, score_band, confidence, is_duplicate, created_at,
                    company, canonical_company_key, title, location, url,
                    score_v1, score_v2, active_score, active_scoring_version, recommendation, active_recommendation_version, canonical_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, vacancy_key, url) DO UPDATE SET
                    source=excluded.source,
                    source_key=excluded.source_key,
                    role_bucket=excluded.role_bucket,
                    geo_bucket=excluded.geo_bucket,
                    industry_bucket=excluded.industry_bucket,
                    executive_detected=excluded.executive_detected,
                    accepted=excluded.accepted,
                    notified=excluded.notified,
                    score=excluded.score,
                    score_band=excluded.score_band,
                    confidence=excluded.confidence,
                    is_duplicate=excluded.is_duplicate,
                    created_at=excluded.created_at,
                    company=excluded.company,
                    canonical_company_key=excluded.canonical_company_key,
                    title=excluded.title,
                    location=excluded.location,
                    url=excluded.url,
                    score_v1=excluded.score_v1,
                    score_v2=excluded.score_v2,
                    active_score=excluded.active_score,
                    active_scoring_version=excluded.active_scoring_version,
                    recommendation=excluded.recommendation,
                    active_recommendation_version=excluded.active_recommendation_version,
                    canonical_url=excluded.canonical_url
                """,
                (
                    run_id,
                    vacancy_key,
                    source,
                    source_key,
                    role_bucket,
                    geo_bucket,
                    industry_bucket,
                    int(executive_detected),
                    int(accepted),
                    int(notified),
                    int(score),
                    score_band,
                    confidence,
                    int(is_duplicate),
                    created_at or datetime.now(timezone.utc).isoformat(),
                    company,
                    company_key,
                    title,
                    location,
                    url or '',
                    score_v1,
                    score_v2,
                    active_score if active_score is not None else score,
                    active_scoring_version,
                    recommendation,
                    active_recommendation_version,
                    canonical_url,
                ),
            )

    def upsert_vacancy_rejection_summary(
        self,
        *,
        run_id: int,
        vacancy_key: str,
        source: str,
        score: int,
        score_band: str,
        accepted: bool,
        is_duplicate: bool,
        rejection_reason_count: int,
        top_rejection_reason: str | None,
        created_at: str | None = None,
        recommendation: str | None = None,
        real_blocker_count: int = 0,
        unknown_count: int = 0,
        warning_count: int = 0,
        top_real_blocker: str | None = None,
        top_unknown_reason: str | None = None,
        top_warning: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO vacancy_rejection_summary (
                    run_id, vacancy_key, source, score, score_band, accepted, is_duplicate,
                    rejection_reason_count, top_rejection_reason, created_at,
                    recommendation, real_blocker_count, unknown_count, warning_count,
                    top_real_blocker, top_unknown_reason, top_warning
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, vacancy_key) DO UPDATE SET
                    source=excluded.source,
                    score=excluded.score,
                    score_band=excluded.score_band,
                    accepted=excluded.accepted,
                    is_duplicate=excluded.is_duplicate,
                    rejection_reason_count=excluded.rejection_reason_count,
                    top_rejection_reason=excluded.top_rejection_reason,
                    created_at=excluded.created_at,
                    recommendation=excluded.recommendation,
                    real_blocker_count=excluded.real_blocker_count,
                    unknown_count=excluded.unknown_count,
                    warning_count=excluded.warning_count,
                    top_real_blocker=excluded.top_real_blocker,
                    top_unknown_reason=excluded.top_unknown_reason,
                    top_warning=excluded.top_warning
                """,
                (
                    run_id,
                    vacancy_key,
                    source,
                    int(score),
                    score_band,
                    int(accepted),
                    int(is_duplicate),
                    int(rejection_reason_count),
                    top_rejection_reason,
                    created_at or datetime.now(timezone.utc).isoformat(),
                    recommendation,
                    int(real_blocker_count),
                    int(unknown_count),
                    int(warning_count),
                    top_real_blocker,
                    top_unknown_reason,
                    top_warning,
                ),
            )

    def insert_vacancy_rejection_events(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO vacancy_rejection_events (
                    run_id, vacancy_key, source, role_bucket, geo_bucket, industry_bucket,
                    score, score_band, confidence, rejection_reason, created_at,
                    reason_type, severity
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["run_id"],
                        row["vacancy_key"],
                        row["source"],
                        row["role_bucket"],
                        row["geo_bucket"],
                        row["industry_bucket"],
                        row["score"],
                        row["score_band"],
                        row.get("confidence"),
                        row["rejection_reason"],
                        row.get("created_at") or datetime.now(timezone.utc).isoformat(),
                        row.get("reason_type"),
                        row.get("severity"),
                    )
                    for row in rows
                ],
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
        card_key: str | None = None,
        notification_kind: str | None = None,
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
                    run_id, vacancy_id, channel, message_type, notification_kind, card_key, content_hash, delivery_status,
                    delivery_error, delivery_attempts, sent_at, body, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    vacancy_id,
                    channel,
                    message_type,
                    notification_kind or message_type,
                    card_key,
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

    def update_notification_payload(self, notification_id: int, payload: dict[str, Any] | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE notifications SET payload_json = ? WHERE id = ?",
                (json.dumps(payload or {}, ensure_ascii=False, default=_json_safe_default), notification_id),
            )

    def fetch_notifications_for_run(
        self,
        run_id: int,
        *,
        message_type: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM notifications WHERE run_id = ?"
        params: list[Any] = [run_id]
        if message_type:
            sql += " AND message_type = ?"
            params.append(message_type)
        sql += " ORDER BY id ASC"
        with self.connect(read_only=True) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def latest_notification_for_card(
        self,
        card_key: str,
        *,
        delivery_status: str | None = None,
        message_types: tuple[str, ...] | None = None,
    ) -> dict[str, Any] | None:
        if not card_key:
            return None
        with self.connect(read_only=True) as conn:
            sql = "SELECT * FROM notifications WHERE card_key = ?"
            params: list[Any] = [card_key]
            if delivery_status:
                sql += " AND delivery_status = ?"
                params.append(delivery_status)
            if message_types:
                placeholders = ",".join(["?"] * len(message_types))
                sql += f" AND message_type IN ({placeholders})"
                params.extend(message_types)
            sql += " ORDER BY id DESC LIMIT 1"
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def active_feedback_for_card(self, card_key: str) -> list[str]:
        if not card_key:
            return []
        with self.connect(read_only=True) as conn:
            rows = conn.execute(
                """
                SELECT feedback_type
                FROM vacancy_feedback_state
                WHERE card_key = ? AND active = 1
                ORDER BY feedback_type
                """,
                (card_key,),
            ).fetchall()
        return [str(row[0]) for row in rows]

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

    def record_vacancy_card_decision(
        self,
        *,
        run_id: int,
        vacancy_id: int | None,
        vacancy_key: str,
        canonical_url: str | None,
        card_key: str,
        source: str | None,
        company: str | None,
        title: str | None,
        score: int | None,
        recommendation: str | None,
        candidate_rank: int | None,
        decision: str,
        suppression_reason: str | None = None,
        previous_sent_run_id: int | None = None,
        previous_sent_at: str | None = None,
        previous_feedback_label: str | None = None,
        feedback_state_active: bool = False,
        next_allowed_send_at: str | None = None,
        score_delta_since_last_sent: int | None = None,
        recommendation_changed_since_last_sent: bool = False,
        content_hash_changed: bool = False,
        description_hash_changed: bool = False,
        created_at: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO vacancy_card_decisions (
                    run_id, vacancy_id, vacancy_key, canonical_url, card_key,
                    source, company, title, score, recommendation, candidate_rank,
                    decision, suppression_reason, previous_sent_run_id, previous_sent_at,
                    previous_feedback_label, feedback_state_active, next_allowed_send_at,
                    score_delta_since_last_sent, recommendation_changed_since_last_sent,
                    content_hash_changed, description_hash_changed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, card_key) DO UPDATE SET
                    vacancy_id=excluded.vacancy_id,
                    vacancy_key=excluded.vacancy_key,
                    canonical_url=excluded.canonical_url,
                    source=excluded.source,
                    company=excluded.company,
                    title=excluded.title,
                    score=excluded.score,
                    recommendation=excluded.recommendation,
                    candidate_rank=excluded.candidate_rank,
                    decision=excluded.decision,
                    suppression_reason=excluded.suppression_reason,
                    previous_sent_run_id=excluded.previous_sent_run_id,
                    previous_sent_at=excluded.previous_sent_at,
                    previous_feedback_label=excluded.previous_feedback_label,
                    feedback_state_active=excluded.feedback_state_active,
                    next_allowed_send_at=excluded.next_allowed_send_at,
                    score_delta_since_last_sent=excluded.score_delta_since_last_sent,
                    recommendation_changed_since_last_sent=excluded.recommendation_changed_since_last_sent,
                    content_hash_changed=excluded.content_hash_changed,
                    description_hash_changed=excluded.description_hash_changed,
                    created_at=excluded.created_at
                """,
                (
                    run_id,
                    vacancy_id,
                    vacancy_key,
                    canonical_url,
                    card_key,
                    source,
                    company,
                    title,
                    score,
                    recommendation,
                    candidate_rank,
                    decision,
                    suppression_reason,
                    previous_sent_run_id,
                    previous_sent_at,
                    previous_feedback_label,
                    int(feedback_state_active),
                    next_allowed_send_at,
                    score_delta_since_last_sent,
                    int(recommendation_changed_since_last_sent),
                    int(content_hash_changed),
                    int(description_hash_changed),
                    created_at or datetime.now(timezone.utc).isoformat(),
                ),
            )

    def card_decision_counts(self, run_id: int) -> dict[str, int]:
        with self.connect(read_only=True) as conn:
            rows = conn.execute(
                """
                SELECT decision, COUNT(*) AS n
                FROM vacancy_card_decisions
                WHERE run_id = ?
                GROUP BY decision
                """,
                (run_id,),
            ).fetchall()
        return {str(row[0]): int(row[1] or 0) for row in rows}

    def fetch_card_decisions(self, run_id: int) -> list[dict[str, Any]]:
        with self.connect(read_only=True) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM vacancy_card_decisions
                WHERE run_id = ?
                ORDER BY candidate_rank ASC, id ASC
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def fetch_source_kpi_rows(self, run_id: int) -> list[dict[str, Any]]:
        with self.connect(read_only=True) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM source_kpi_run
                WHERE run_id = ?
                ORDER BY runtime_seconds DESC, source ASC
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_performance_spans(self, run_id: int, spans: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM job_intel_performance_spans WHERE run_id = ?", (run_id,))
            if not spans:
                return
            conn.executemany(
                """
                INSERT INTO job_intel_performance_spans (
                    run_id, span_name, parent_span_name, source_name,
                    started_at, finished_at, duration_ms, status,
                    found_count, normalized_count, accepted_count, duplicate_count,
                    rejected_count, new_card_keys, cards_sent,
                    error_count, retry_count, timeout_count, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        int(row["run_id"]),
                        row["span_name"],
                        row.get("parent_span_name"),
                        row.get("source_name"),
                        row["started_at"],
                        row["finished_at"],
                        int(row["duration_ms"] or 0),
                        row.get("status") or "ok",
                        row.get("found_count"),
                        row.get("normalized_count"),
                        row.get("accepted_count"),
                        row.get("duplicate_count"),
                        row.get("rejected_count"),
                        row.get("new_card_keys"),
                        row.get("cards_sent"),
                        row.get("error_count"),
                        row.get("retry_count"),
                        row.get("timeout_count"),
                        row.get("metadata_json"),
                    )
                    for row in spans
                ],
            )

    def fetch_performance_spans(self, run_id: int) -> list[dict[str, Any]]:
        with self.connect(read_only=True) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM job_intel_performance_spans
                WHERE run_id = ?
                ORDER BY duration_ms DESC, id ASC
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_daily_run_span_durations(self, span_name: str, *, limit: int = 5, exclude_run_id: int | None = None) -> list[int]:
        sql = """
            SELECT p.duration_ms
            FROM job_intel_performance_spans p
            JOIN runs r ON r.id = p.run_id
            WHERE p.span_name = ?
              AND r.mode = 'daily'
              AND r.run_type = 'production'
        """
        params: list[Any] = [span_name]
        if exclude_run_id is not None:
            sql += " AND p.run_id <> ?"
            params.append(exclude_run_id)
        sql += " ORDER BY p.run_id DESC LIMIT ?"
        params.append(limit)
        with self.connect(read_only=True) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [int(row[0] or 0) for row in rows]

    def recent_daily_runs_with_spans(self, *, limit: int = 7) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
        with self.connect(read_only=True) as conn:
            run_rows = conn.execute(
                """
                SELECT *
                FROM runs
                WHERE mode = 'daily'
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            runs = [dict(row) for row in run_rows]
            if not runs:
                return [], {}
            run_ids = [int(row["id"]) for row in runs]
            placeholders = ",".join(["?"] * len(run_ids))
            span_rows = conn.execute(
                f"""
                SELECT *
                FROM job_intel_performance_spans
                WHERE run_id IN ({placeholders})
                ORDER BY run_id DESC, duration_ms DESC, id ASC
                """,
                run_ids,
            ).fetchall()
        spans_by_run: dict[int, list[dict[str, Any]]] = {}
        for row in span_rows:
            payload = dict(row)
            spans_by_run.setdefault(int(payload["run_id"]), []).append(payload)
        return runs, spans_by_run


    def upsert_source_kpi_run(self, run_id: int, source: str, kpi: dict[str, Any]) -> None:
        """Persist per-run/per-source KPIs. Missing keys are stored as NULL."""
        self.bootstrap()
        now = datetime.now(timezone.utc).isoformat()
        # Fixed column set to keep schema stable and queries simple.
        columns = [
            "run_id","source","created_at",
            "source_status","acquisition_mode","runtime_seconds","attempts",
            "pages_fetched","login_walls","auth_redirects","anti_bot_events","extraction_failures",
            "found_count","executive_detected_count","scored_count","accepted_count","notified_count",
            "vacancies_deduped","rejected_count",
            "avg_vacancy_score","vacancy_score_p50","vacancy_score_p90","accepted_score_p50",
            "pct_company_known","pct_location_known","pct_salary_known","pct_seniority_confident",
            "company_score_avg","company_score_p90","industry_fit_avg","tier1_company_count","tier2_company_count","interview_generated_count",
            "error_class","error_fingerprint","error_message_truncated",
            "enabled","skip_reason",
        ]
        payload: dict[str, Any] = {"run_id": run_id, "source": source, "created_at": now}
        payload.update(kpi or {})
        payload.setdefault("enabled", 1)
        values = [payload.get(c) for c in columns]
        placeholders = ",".join(["?"] * len(columns))
        col_sql = ",".join(columns)
        update_sql = ",".join([f"{c}=excluded.{c}" for c in columns if c not in {"run_id","source"}])
        sql = f"INSERT INTO source_kpi_run ({col_sql}) VALUES ({placeholders}) ON CONFLICT(run_id,source) DO UPDATE SET {update_sql}"
        with self.connect() as conn:
            conn.execute(sql, values)

    def upsert_registry_company_run(self, run_id: int, source: str, item: dict[str, Any]) -> None:
        self.bootstrap()
        now = datetime.now(timezone.utc).isoformat()
        payload = dict(item or {})
        source_key = canonical_source_key(source)
        company_key = canonical_company_key(str(payload.get("company_name") or ""))
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO registry_company_runs (
                    run_id, source, source_key, company_name, canonical_company_key, tier, ats_vendor, ats_slug, collection_url, validation_url,
                    acquisition_enabled, attempted, collected, vacancies_found, vacancies_stored, vacancies_scored,
                    source_status, reason, errors_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, source, company_name) DO UPDATE SET
                    source_key=excluded.source_key,
                    canonical_company_key=excluded.canonical_company_key,
                    tier=excluded.tier,
                    ats_vendor=excluded.ats_vendor,
                    ats_slug=excluded.ats_slug,
                    collection_url=excluded.collection_url,
                    validation_url=excluded.validation_url,
                    acquisition_enabled=excluded.acquisition_enabled,
                    attempted=excluded.attempted,
                    collected=excluded.collected,
                    vacancies_found=excluded.vacancies_found,
                    vacancies_stored=excluded.vacancies_stored,
                    vacancies_scored=excluded.vacancies_scored,
                    source_status=excluded.source_status,
                    reason=excluded.reason,
                    errors_json=excluded.errors_json,
                    created_at=excluded.created_at
                """,
                (
                    run_id,
                    source,
                    source_key,
                    str(payload.get("company_name") or ""),
                    company_key,
                    payload.get("tier"),
                    payload.get("ats_vendor"),
                    payload.get("ats_slug"),
                    payload.get("collection_url"),
                    payload.get("validation_url"),
                    int(bool(payload.get("acquisition_enabled", True))),
                    int(bool(payload.get("attempted", False))),
                    int(bool(payload.get("collected", False))),
                    int(payload.get("vacancies_found") or 0),
                    int(payload.get("vacancies_stored") or 0),
                    int(payload.get("vacancies_scored") or 0),
                    payload.get("source_status"),
                    payload.get("reason"),
                    json.dumps(payload.get("errors") or [], ensure_ascii=False),
                    now,
                ),
            )

    def upsert_user_feedback_unseen(self, vacancy_key: str, run_id: int | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_feedback_opportunities (vacancy_key, run_id, status, updated_at)
                VALUES (?, ?, 'unseen', ?)
                ON CONFLICT(vacancy_key) DO UPDATE SET
                    run_id=COALESCE(excluded.run_id, user_feedback_opportunities.run_id),
                    updated_at=CASE WHEN user_feedback_opportunities.status='unseen' THEN excluded.updated_at ELSE user_feedback_opportunities.updated_at END
                """,
                (vacancy_key, run_id, now),
            )

    def insert_vacancy_slack_message(
        self,
        *,
        vacancy_id: int,
        run_id: int | None,
        vacancy_key: str | None,
        canonical_url: str | None,
        card_key: str | None,
        notification_id: int | None,
        slack_channel: str,
        slack_message_ts: str,
        message_type: str | None,
        company: str,
        title: str,
        score: int,
        recommendation: str,
        url: str,
        sent_at: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO vacancy_slack_messages (
                    vacancy_id, run_id, vacancy_key, canonical_url, card_key, notification_id,
                    slack_channel, slack_message_ts, message_type, company, title,
                    score, recommendation, score_at_send, recommendation_at_send, url, sent_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vacancy_id,
                    run_id,
                    vacancy_key,
                    canonical_url,
                    card_key,
                    notification_id,
                    slack_channel,
                    slack_message_ts,
                    message_type,
                    company,
                    title,
                    int(score or 0),
                    recommendation,
                    int(score or 0),
                    recommendation,
                    url,
                    sent_at or datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def find_vacancy_message(self, *, slack_channel: str, slack_message_ts: str) -> dict[str, Any] | None:
        with self.connect(read_only=True) as conn:
            row = conn.execute(
                "SELECT * FROM vacancy_slack_messages WHERE slack_channel=? AND slack_message_ts=? LIMIT 1",
                (slack_channel, slack_message_ts),
            ).fetchone()
        return dict(row) if row else None

    def record_vacancy_slack_message(
        self,
        *,
        vacancy_id: int,
        run_id: int | None,
        vacancy_key: str | None,
        canonical_url: str | None,
        card_key: str | None,
        notification_id: int | None,
        slack_channel: str,
        slack_message_ts: str,
        message_type: str | None,
        company: str,
        title: str,
        score: int,
        recommendation: str,
        url: str,
        sent_at: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO vacancy_slack_messages (
                    vacancy_id, run_id, vacancy_key, canonical_url, card_key, notification_id,
                    slack_channel, slack_message_ts, message_type, company, title,
                    score, recommendation, score_at_send, recommendation_at_send, url, sent_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slack_channel, slack_message_ts) DO UPDATE SET
                    vacancy_id=excluded.vacancy_id,
                    run_id=excluded.run_id,
                    vacancy_key=excluded.vacancy_key,
                    canonical_url=excluded.canonical_url,
                    card_key=excluded.card_key,
                    notification_id=excluded.notification_id,
                    message_type=excluded.message_type,
                    company=excluded.company,
                    title=excluded.title,
                    score=excluded.score,
                    recommendation=excluded.recommendation,
                    score_at_send=excluded.score_at_send,
                    recommendation_at_send=excluded.recommendation_at_send,
                    url=excluded.url,
                    sent_at=excluded.sent_at
                """,
                (
                    vacancy_id,
                    run_id,
                    vacancy_key,
                    canonical_url,
                    card_key,
                    notification_id,
                    slack_channel,
                    slack_message_ts,
                    message_type,
                    company,
                    title,
                    score,
                    recommendation,
                    score,
                    recommendation,
                    url,
                    sent_at or now,
                    now,
                ),
            )

    def record_vacancy_feedback_event(
        self,
        *,
        vacancy_id: int,
        run_id: int | None,
        notification_id: int | None,
        vacancy_key: str | None,
        canonical_url: str | None,
        card_key: str | None,
        slack_channel: str | None,
        slack_message_ts: str,
        feedback_type: str,
        event_type: str,
        event_timestamp: str,
        user_id: str,
        raw_event_json: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        active = 1 if event_type == "reaction_added" else 0
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO vacancy_feedback (
                    vacancy_id, run_id, notification_id, vacancy_key, canonical_url, card_key, slack_channel,
                    slack_message_ts, feedback_type, event_type, event_timestamp, user_id, raw_event_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vacancy_id,
                    run_id,
                    notification_id,
                    vacancy_key,
                    canonical_url,
                    card_key,
                    slack_channel,
                    slack_message_ts,
                    feedback_type,
                    event_type,
                    event_timestamp,
                    user_id,
                    json.dumps(raw_event_json or {}, ensure_ascii=False),
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO vacancy_feedback_state (
                    vacancy_id, run_id, notification_id, vacancy_key, canonical_url, card_key, slack_channel,
                    slack_message_ts, user_id, feedback_type, active, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vacancy_id, user_id, feedback_type) DO UPDATE SET
                    run_id=excluded.run_id,
                    notification_id=excluded.notification_id,
                    vacancy_key=excluded.vacancy_key,
                    canonical_url=excluded.canonical_url,
                    card_key=excluded.card_key,
                    slack_channel=excluded.slack_channel,
                    slack_message_ts=excluded.slack_message_ts,
                    active=excluded.active,
                    updated_at=excluded.updated_at
                """,
                (
                    vacancy_id,
                    run_id,
                    notification_id,
                    vacancy_key,
                    canonical_url,
                    card_key,
                    slack_channel,
                    slack_message_ts,
                    user_id,
                    feedback_type,
                    active,
                    now,
                ),
            )

    def feedback_metrics_for_run(self, run_id: int) -> dict[str, Any]:
        with self.connect(read_only=True) as conn:
            sent_row = conn.execute(
                "SELECT COUNT(*) FROM vacancy_slack_messages WHERE run_id=?",
                (run_id,),
            ).fetchone()
            reacted_row = conn.execute(
                """
                SELECT COUNT(DISTINCT vsm.vacancy_id)
                FROM vacancy_slack_messages vsm
                JOIN vacancy_feedback_state vfs ON vfs.vacancy_id = vsm.vacancy_id
                WHERE vsm.run_id=? AND vfs.active=1
                """,
                (run_id,),
            ).fetchone()
            pos_row = conn.execute(
                """
                SELECT COUNT(*)
                FROM vacancy_feedback_state vfs
                JOIN vacancy_slack_messages vsm ON vsm.vacancy_id=vfs.vacancy_id
                WHERE vsm.run_id=? AND vfs.active=1 AND vfs.feedback_type IN ('interesting','exceptional')
                """,
                (run_id,),
            ).fetchone()
            all_active_row = conn.execute(
                """
                SELECT COUNT(*)
                FROM vacancy_feedback_state vfs
                JOIN vacancy_slack_messages vsm ON vsm.vacancy_id=vfs.vacancy_id
                WHERE vsm.run_id=? AND vfs.active=1
                """,
                (run_id,),
            ).fetchone()
            applied_row = conn.execute(
                """
                SELECT COUNT(*)
                FROM vacancy_feedback_state vfs
                JOIN vacancy_slack_messages vsm ON vsm.vacancy_id=vfs.vacancy_id
                WHERE vsm.run_id=? AND vfs.active=1 AND vfs.feedback_type='applied'
                """,
                (run_id,),
            ).fetchone()
        sent = int(sent_row[0] or 0)
        reacted = int(reacted_row[0] or 0)
        all_active = int(all_active_row[0] or 0)
        positive = int(pos_row[0] or 0)
        applied = int(applied_row[0] or 0)
        return {
            "vacancies_sent": sent,
            "vacancies_reacted": reacted,
            "reaction_rate": (float(reacted) / float(sent)) if sent else 0.0,
            "positive_rate": (float(positive) / float(all_active)) if all_active else 0.0,
            "applied_rate": (float(applied) / float(all_active)) if all_active else 0.0,
        }

    def upsert_production_observation_daily(self, run_id: int, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO production_observation_daily (
                    run_id, run_started_at, run_finished_at, runtime_seconds,
                    total_collected, total_unique, duplicate_rate, unknown_company_rate,
                    strong_fit_count, needs_review_count, near_miss_count,
                    login_walls, anti_bot_events, auth_redirects,
                    source_failures_json, source_runtimes_json, slowest_source,
                    vacancies_sent, vacancies_reacted, reaction_rate, positive_rate, applied_rate,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    run_started_at=excluded.run_started_at,
                    run_finished_at=excluded.run_finished_at,
                    runtime_seconds=excluded.runtime_seconds,
                    total_collected=excluded.total_collected,
                    total_unique=excluded.total_unique,
                    duplicate_rate=excluded.duplicate_rate,
                    unknown_company_rate=excluded.unknown_company_rate,
                    strong_fit_count=excluded.strong_fit_count,
                    needs_review_count=excluded.needs_review_count,
                    near_miss_count=excluded.near_miss_count,
                    login_walls=excluded.login_walls,
                    anti_bot_events=excluded.anti_bot_events,
                    auth_redirects=excluded.auth_redirects,
                    source_failures_json=excluded.source_failures_json,
                    source_runtimes_json=excluded.source_runtimes_json,
                    slowest_source=excluded.slowest_source,
                    vacancies_sent=excluded.vacancies_sent,
                    vacancies_reacted=excluded.vacancies_reacted,
                    reaction_rate=excluded.reaction_rate,
                    positive_rate=excluded.positive_rate,
                    applied_rate=excluded.applied_rate,
                    created_at=excluded.created_at
                """,
                (
                    run_id,
                    payload.get("run_started_at"),
                    payload.get("run_finished_at"),
                    payload.get("runtime_seconds"),
                    int(payload.get("total_collected") or 0),
                    int(payload.get("total_unique") or 0),
                    payload.get("duplicate_rate"),
                    payload.get("unknown_company_rate"),
                    int(payload.get("strong_fit_count") or 0),
                    int(payload.get("needs_review_count") or 0),
                    int(payload.get("near_miss_count") or 0),
                    int(payload.get("login_walls") or 0),
                    int(payload.get("anti_bot_events") or 0),
                    int(payload.get("auth_redirects") or 0),
                    json.dumps(payload.get("source_failures") or {}, ensure_ascii=False),
                    json.dumps(payload.get("source_runtimes") or {}, ensure_ascii=False),
                    payload.get("slowest_source"),
                    int(payload.get("vacancies_sent") or 0),
                    int(payload.get("vacancies_reacted") or 0),
                    payload.get("reaction_rate"),
                    payload.get("positive_rate"),
                    payload.get("applied_rate"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def upsert_vacancy_scoring_shadow(
        self,
        *,
        run_id: int,
        vacancy_key: str,
        source: str,
        score_v2: int | None,
        recommendation_v2: str | None,
        score_v3: int | None,
        recommendation_v3: str | None,
        score_v3_nr: int | None,
        recommendation_v3_nr: str | None,
        gates_v3: dict[str, Any] | None,
        function_class_v3: str | None,
    ) -> None:
        self.bootstrap()
        now = datetime.now(timezone.utc).isoformat()
        source_key = canonical_source_key(source)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO vacancy_scoring_shadow (
                    run_id, vacancy_key, source, source_key, score_v2, recommendation_v2,
                    score_v3, recommendation_v3, score_v3_nr, recommendation_v3_nr,
                    gates_v3_json, function_class_v3, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, vacancy_key) DO UPDATE SET
                    source=excluded.source,
                    source_key=excluded.source_key,
                    score_v2=excluded.score_v2,
                    recommendation_v2=excluded.recommendation_v2,
                    score_v3=excluded.score_v3,
                    recommendation_v3=excluded.recommendation_v3,
                    score_v3_nr=excluded.score_v3_nr,
                    recommendation_v3_nr=excluded.recommendation_v3_nr,
                    gates_v3_json=excluded.gates_v3_json,
                    function_class_v3=excluded.function_class_v3,
                    created_at=excluded.created_at
                """,
                (
                    run_id,
                    vacancy_key,
                    source,
                    source_key,
                    score_v2,
                    recommendation_v2,
                    score_v3,
                    recommendation_v3,
                    score_v3_nr,
                    recommendation_v3_nr,
                    json.dumps(gates_v3 or {}, ensure_ascii=False),
                    function_class_v3,
                    now,
                ),
            )

    def count_notified_vacancies_by_source(self, run_id: int, *, delivery_status: str = "sent") -> dict[str, int]:
        """Return count of distinct notified vacancy_ids by vacancy.source for a run."""
        sql = (
            "SELECT v.source AS source, COUNT(DISTINCT n.vacancy_id) AS c "
            "FROM notifications n "
            "JOIN vacancies v ON v.id = n.vacancy_id "
            "WHERE n.run_id = ? AND n.delivery_status = ? AND n.vacancy_id IS NOT NULL "
            "GROUP BY v.source"
        )
        with self.connect() as conn:
            rows = conn.execute(sql, (run_id, delivery_status)).fetchall()
        return {str(row["source"]): int(row["c"] or 0) for row in rows}

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
                SELECT
                    v.id AS vacancy_id,
                    v.vacancy_key,
                    v.source,
                    v.source_id,
                    v.company,
                    v.title,
                    v.location,
                    v.url,
                    v.description,
                    v.posted_at,
                    v.scraped_at,
                    v.salary,
                    v.company_url,
                    v.metadata_json,
                    v.first_seen_at,
                    v.last_seen_at,
                    v.repost_count,
                    v.status,
                    e.id AS evaluation_id,
                    e.score,
                    e.tier,
                    e.recommendation,
                    e.salary_tier,
                    e.matched_signals_json,
                    e.concerns_json,
                    e.reasons_json,
                    e.raw_breakdown_json
                FROM vacancies v
                JOIN latest_evaluations e ON e.vacancy_key = v.vacancy_key
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
            rows = conn.execute("SELECT * FROM company_intelligence ORDER BY updated_at DESC, company ASC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def fetch_company_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM company_intelligence_events ORDER BY seen_at DESC, company ASC, id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def fetch_strategic_signals(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM strategic_signals ORDER BY updated_at DESC, company ASC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def fetch_strategic_predictions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM strategic_predictions ORDER BY created_at DESC, company ASC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def record_strategic_signal(
        self,
        company: str,
        signal_type: str,
        *,
        confidence: float,
        horizon_days: int | None,
        probability: float | None,
        rationale: str,
        evidence: dict[str, Any] | None = None,
        source: str | None = None,
        observed_at: str | None = None,
    ) -> None:
        timestamp = observed_at or datetime.now(timezone.utc).date().isoformat()
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM strategic_signals WHERE company = ? AND signal_type = ? AND COALESCE(horizon_days, -1) = COALESCE(?, -1) AND observed_at = ?",
                (company, signal_type, horizon_days, timestamp),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE strategic_signals
                    SET confidence = ?, probability = ?, rationale = ?, evidence_json = ?, source = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        confidence,
                        probability,
                        rationale,
                        json.dumps(evidence or {}, ensure_ascii=False),
                        source,
                        now,
                        row[0],
                    ),
                )
                return
            conn.execute(
                """
                INSERT INTO strategic_signals (
                    company, signal_type, confidence, horizon_days, probability, rationale, evidence_json, source, observed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company,
                    signal_type,
                    confidence,
                    horizon_days,
                    probability,
                    rationale,
                    json.dumps(evidence or {}, ensure_ascii=False),
                    source,
                    timestamp,
                    now,
                ),
            )

    def record_strategic_prediction(
        self,
        company: str,
        prediction_type: str,
        *,
        probability: float,
        horizon_days: int,
        rationale: str,
        evidence: dict[str, Any] | None = None,
        source: str | None = None,
        observed_openings: int = 0,
        resolution_status: str = "open",
        outcome_text: str | None = None,
        created_at: str | None = None,
    ) -> None:
        created_at_value = created_at or datetime.now(timezone.utc).date().isoformat()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM strategic_predictions WHERE company = ? AND prediction_type = ? AND horizon_days = ? AND created_at = ?",
                (company, prediction_type, horizon_days, created_at_value),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE strategic_predictions
                    SET probability = ?, rationale = ?, evidence_json = ?, source = ?, resolution_status = ?, outcome_text = ?, observed_openings = ?
                    WHERE id = ?
                    """,
                    (
                        probability,
                        rationale,
                        json.dumps(evidence or {}, ensure_ascii=False),
                        source,
                        resolution_status,
                        outcome_text,
                        observed_openings,
                        row[0],
                    ),
                )
                return
            conn.execute(
                """
                INSERT INTO strategic_predictions (
                    company, prediction_type, probability, horizon_days, rationale, evidence_json, source,
                    created_at, resolution_status, outcome_text, observed_openings
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company,
                    prediction_type,
                    probability,
                    horizon_days,
                    rationale,
                    json.dumps(evidence or {}, ensure_ascii=False),
                    source,
                    created_at_value,
                    resolution_status,
                    outcome_text,
                    observed_openings,
                ),
            )

    # --- Negative feedback loop (feedback_events) -------------------------

    _FEEDBACK_EVENT_JSON_FIELDS = {
        "reason_category_codes_json",
        "reason_detail_codes_json",
        "attribution_targets_json",
        "scoring_features_impacted_json",
        "raw_payload_json",
    }

    _FEEDBACK_EVENT_MUTABLE_FIELDS = {
        "opportunity_id",
        "vacancy_id",
        "company",
        "slack_thread_ts",
        "prompt_message_ts",
        "reaction_type",
        "status",
        "reason_category_codes_json",
        "reason_detail_codes_json",
        "attribution_targets_json",
        "free_text",
        "classifier_version",
        "classifier_confidence",
        "hard_blocker",
        "soft_preference",
        "applies_to_company",
        "applies_to_role",
        "applies_to_location",
        "applies_to_industry",
        "applies_to_parser_quality",
        "scoring_features_impacted_json",
        "user_confirmed",
        "needs_followup",
        "followup_question_sent_at",
        "followup_answer_received_at",
        "needs_manual_review",
        "raw_payload_json",
    }

    @staticmethod
    def _feedback_json_value(field: str, value: Any) -> Any:
        if field.endswith("_json") and value is not None and not isinstance(value, str):
            return json.dumps(value, ensure_ascii=False, default=_json_safe_default)
        if isinstance(value, bool):
            return int(value)
        return value

    def create_feedback_event(
        self,
        *,
        slack_channel_id: str | None,
        slack_message_ts: str | None,
        user_id: str | None,
        reaction_type: str | None,
        opportunity_id: int | None = None,
        vacancy_id: int | None = None,
        company: str | None = None,
        polarity: str = "negative",
        status: str = "awaiting_reply",
        source: str = "slack_reaction_thread",
        **fields: Any,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        columns = [
            "opportunity_id", "vacancy_id", "company", "slack_channel_id", "slack_message_ts",
            "user_id", "reaction_type", "polarity", "status", "source", "created_at", "updated_at",
        ]
        values: list[Any] = [
            opportunity_id, vacancy_id, company, slack_channel_id, slack_message_ts,
            user_id, reaction_type, polarity, status, source, now, now,
        ]
        for field, value in fields.items():
            if field not in self._FEEDBACK_EVENT_MUTABLE_FIELDS:
                raise ValueError(f"Unsupported feedback_events field: {field}")
            columns.append(field)
            values.append(self._feedback_json_value(field, value))
        placeholders = ", ".join("?" for _ in columns)
        with self.connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO feedback_events ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )
            return int(cursor.lastrowid)

    def update_feedback_event(self, event_id: int, **fields: Any) -> None:
        if not fields:
            return
        assignments: list[str] = []
        values: list[Any] = []
        for field, value in fields.items():
            if field not in self._FEEDBACK_EVENT_MUTABLE_FIELDS:
                raise ValueError(f"Unsupported feedback_events field: {field}")
            assignments.append(f"{field}=?")
            values.append(self._feedback_json_value(field, value))
        assignments.append("updated_at=?")
        values.append(datetime.now(timezone.utc).isoformat())
        values.append(event_id)
        with self.connect() as conn:
            conn.execute(f"UPDATE feedback_events SET {', '.join(assignments)} WHERE id=?", values)

    @staticmethod
    def _feedback_event_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        for field in ("reason_category_codes_json", "reason_detail_codes_json",
                      "attribution_targets_json", "scoring_features_impacted_json"):
            raw = record.get(field)
            key = field.removesuffix("_json")
            try:
                record[key] = json.loads(raw) if raw else []
            except (TypeError, ValueError):
                record[key] = []
        return record

    def get_feedback_event(self, event_id: int) -> dict[str, Any] | None:
        with self.connect(read_only=True) as conn:
            row = conn.execute("SELECT * FROM feedback_events WHERE id=?", (event_id,)).fetchone()
        return self._feedback_event_row_to_dict(row) if row else None

    def find_feedback_event_by_message(
        self, *, slack_channel_id: str, slack_message_ts: str, user_id: str | None = None
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM feedback_events WHERE slack_channel_id=? AND slack_message_ts=?"
        params: list[Any] = [slack_channel_id, slack_message_ts]
        if user_id is not None:
            query += " AND user_id=?"
            params.append(user_id)
        query += " ORDER BY id DESC LIMIT 1"
        with self.connect(read_only=True) as conn:
            row = conn.execute(query, params).fetchone()
        return self._feedback_event_row_to_dict(row) if row else None

    def find_feedback_event_awaiting_reply(
        self, *, slack_channel_id: str, slack_thread_ts: str
    ) -> dict[str, Any] | None:
        with self.connect(read_only=True) as conn:
            row = conn.execute(
                """
                SELECT * FROM feedback_events
                WHERE slack_channel_id=? AND slack_thread_ts=?
                  AND status IN ('awaiting_reply', 'awaiting_followup')
                ORDER BY id DESC LIMIT 1
                """,
                (slack_channel_id, slack_thread_ts),
            ).fetchone()
        return self._feedback_event_row_to_dict(row) if row else None

    def fetch_feedback_events(
        self,
        *,
        opportunity_id: int | None = None,
        vacancy_id: int | None = None,
        company: str | None = None,
        days: int | None = None,
        status: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM feedback_events WHERE 1=1"
        params: list[Any] = []
        if opportunity_id is not None:
            query += " AND opportunity_id=?"
            params.append(opportunity_id)
        if vacancy_id is not None:
            query += " AND vacancy_id=?"
            params.append(vacancy_id)
        if company is not None:
            query += " AND company=?"
            params.append(company)
        if days is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            query += " AND created_at>=?"
            params.append(cutoff)
        if status is not None:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connect(read_only=True) as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._feedback_event_row_to_dict(row) for row in rows]

    # --- Scoring calibration proposals ------------------------------------

    def create_scoring_proposal(
        self,
        *,
        evidence_window_days: int,
        evidence: dict[str, Any],
        proposed_changes: list[dict[str, Any]],
        risk_level: str = "medium",
        status: str = "proposed",
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scoring_calibration_proposals (
                    status, evidence_window_days, evidence_json, proposed_changes_json, risk_level, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    status,
                    evidence_window_days,
                    json.dumps(evidence, ensure_ascii=False, default=_json_safe_default),
                    json.dumps(proposed_changes, ensure_ascii=False, default=_json_safe_default),
                    risk_level,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    @staticmethod
    def _proposal_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        for field, default in (
            ("evidence_json", {}),
            ("proposed_changes_json", []),
            ("dry_run_result_json", None),
        ):
            raw = record.get(field)
            key = field.removesuffix("_json")
            try:
                record[key] = json.loads(raw) if raw else default
            except (TypeError, ValueError):
                record[key] = default
        return record

    def get_scoring_proposal(self, proposal_id: int) -> dict[str, Any] | None:
        with self.connect(read_only=True) as conn:
            row = conn.execute(
                "SELECT * FROM scoring_calibration_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
        return self._proposal_row_to_dict(row) if row else None

    def fetch_scoring_proposals(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM scoring_calibration_proposals"
        params: list[Any] = []
        if status is not None:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connect(read_only=True) as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._proposal_row_to_dict(row) for row in rows]

    def update_scoring_proposal(self, proposal_id: int, **fields: Any) -> None:
        allowed = {
            "status", "dry_run_result_json", "risk_level",
            "approved_at", "applied_at", "rejected_at", "rollback_ref",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for field, value in fields.items():
            if field not in allowed:
                raise ValueError(f"Unsupported scoring_calibration_proposals field: {field}")
            if field.endswith("_json") and value is not None and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False, default=_json_safe_default)
            assignments.append(f"{field}=?")
            values.append(value)
        if not assignments:
            return
        values.append(proposal_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE scoring_calibration_proposals SET {', '.join(assignments)} WHERE id=?",
                values,
            )

    def add_scoring_calibration_event(
        self,
        *,
        proposal_id: int,
        event_type: str,
        actor: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scoring_calibration_events (proposal_id, event_type, actor, event_payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    event_type,
                    actor,
                    json.dumps(payload or {}, ensure_ascii=False, default=_json_safe_default),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def fetch_scoring_calibration_events(self, proposal_id: int) -> list[dict[str, Any]]:
        with self.connect(read_only=True) as conn:
            rows = conn.execute(
                "SELECT * FROM scoring_calibration_events WHERE proposal_id=? ORDER BY id ASC",
                (proposal_id,),
            ).fetchall()
        return [dict(row) for row in rows]
