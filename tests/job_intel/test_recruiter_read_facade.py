from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path

import pytest

from job_intel.store import JobIntelStore


REPO_ROOT = Path(__file__).resolve().parents[2]


def _seed_store(tmp_path: Path) -> JobIntelStore:
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()
    with store.connect() as conn:
        conn.executescript(
            """
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
                confidence REAL,
                recommendation TEXT,
                slack_channel_id TEXT,
                slack_message_ts TEXT,
                slack_thread_ts TEXT,
                artifact_bundle_id TEXT,
                next_action_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_seen_at TEXT
            );
            CREATE TABLE IF NOT EXISTS opportunity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_source TEXT NOT NULL,
                actor TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
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
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS slack_message_map (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id INTEGER NOT NULL,
                slack_channel_id TEXT NOT NULL,
                slack_message_ts TEXT NOT NULL,
                slack_thread_ts TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(slack_channel_id, slack_message_ts)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO vacancies (
                id, vacancy_key, source, source_id, company, title, location, url, description,
                posted_at, scraped_at, salary, company_url, metadata_json, first_seen_at, last_seen_at, repost_count, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                101,
                "linkedin:example:1",
                "linkedin",
                "example-1",
                "Acme",
                "VP Product",
                "Remote",
                "https://example.com/jobs/1",
                "Lead product strategy",
                "2026-06-20T00:00:00+00:00",
                "2026-06-27T00:00:00+00:00",
                "$200k-$250k",
                "https://example.com",
                json.dumps({"seniority": "executive", "skills": ["product", "ai"]}),
                "2026-06-20T00:00:00+00:00",
                "2026-06-27T00:00:00+00:00",
                0,
                "active",
            ),
        )
        conn.execute(
            """
            INSERT INTO runs (id, mode, started_at, finished_at, status, notes, metadata_json, provenance_json, run_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                55,
                "daily",
                "2026-06-27T00:00:00+00:00",
                "2026-06-27T00:05:00+00:00",
                "success",
                None,
                json.dumps({"source_statuses": {"linkedin": {"status": "ok"}}}),
                json.dumps({"db_path": str(store.db_path)}),
                "production",
            ),
        )
        conn.execute(
            """
            INSERT INTO vacancy_evaluations (
                vacancy_key, run_id, score, tier, recommendation, salary_tier,
                matched_signals_json, concerns_json, reasons_json, raw_breakdown_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "linkedin:example:1",
                55,
                91,
                "strong_fit",
                "strong_fit",
                "top",
                json.dumps(["product_leadership"]),
                json.dumps(["location_unknown"]),
                json.dumps(["strong_product_fit"]),
                json.dumps({"product": 5}),
                "2026-06-27T00:04:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO company_intelligence (
                company, summary, risk_flags_json, target_category, website, signals_json,
                career_urls_json, opening_count, last_scanned_at, last_signal_at, source_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Acme",
                "Executive hiring signal",
                json.dumps([]),
                "tier1",
                "https://example.com/company",
                json.dumps({"signals": ["hiring_activity"]}),
                json.dumps(["https://example.com/careers"]),
                1,
                "2026-06-27T00:02:00+00:00",
                "2026-06-27T00:03:00+00:00",
                json.dumps({"source": "target-company"}),
                "2026-06-27T00:03:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO company_intelligence_events (
                company, event_type, source, title, url, summary, details_json, seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Acme",
                "hiring_signal",
                "target-company",
                "Acme hiring",
                "https://example.com/company",
                "Hiring event",
                json.dumps({"tier": "tier1"}),
                "2026-06-27T00:03:30+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO opportunities (
                id, vacancy_id, company, company_normalized, title, title_normalized, location,
                remote_policy, source, source_url, canonical_url, status, score, confidence,
                recommendation, created_at, updated_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                501,
                101,
                "Acme",
                "acme",
                "VP Product",
                "vpproduct",
                "Remote",
                "remote",
                "linkedin",
                "https://example.com/jobs/1",
                "https://example.com/jobs/1",
                "applied",
                91,
                0.82,
                "strong_fit",
                "2026-06-27T00:10:00+00:00",
                "2026-06-27T00:20:00+00:00",
                "2026-06-27T00:20:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO opportunity_events (
                opportunity_id, event_type, event_source, actor, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                501,
                "application_submitted",
                "manual",
                "denis",
                json.dumps({"channel": "linkedin"}),
                "2026-06-27T00:30:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO opportunity_artifacts (
                opportunity_id, artifact_type, version, content_path, content_text, summary,
                model, qa_status, qa_notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                501,
                "cover_letter",
                1,
                None,
                "Draft cover letter",
                "Strong positioning draft",
                "gpt-5.4",
                "approved",
                None,
                "2026-06-27T00:35:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO vacancy_feedback_state (
                vacancy_id, run_id, notification_id, vacancy_key, canonical_url, card_key,
                slack_channel, slack_message_ts, user_id, feedback_type, active, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                101,
                55,
                None,
                "linkedin:example:1",
                "https://example.com/jobs/1",
                "card-1",
                "C123",
                "1760000000.123",
                "U123",
                "interesting",
                1,
                "2026-06-27T00:40:00+00:00",
            ),
        )
    return store


@pytest.fixture()
def facade(tmp_path: Path):
    module = importlib.import_module("job_intel.recruiter_read_facade")
    store = _seed_store(tmp_path)
    return module.RecruiterReadFacade(store=store, stale_after_days=0)


def test_read_connection_is_opened_in_read_only_mode(facade) -> None:
    with facade._connect_read_only() as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM vacancies")


def test_get_vacancy_by_id_returns_json_serializable_found_payload(facade) -> None:
    result = facade.get_vacancy_by_id(101)

    assert result["status"] == "found"
    assert result["vacancy"]["vacancy_id"] == 101
    assert result["vacancy"]["source_url"] == "https://example.com/jobs/1"
    assert result["vacancy"]["provenance"]["source_table"] == "vacancies"
    assert result["vacancy"]["evaluation"]["run_id"] == 55
    assert result["warnings"] == ["stale_vacancy"]
    json.dumps(result)


def test_get_vacancy_by_url_returns_not_found_without_exception(facade) -> None:
    result = facade.get_vacancy_by_url("https://example.com/jobs/missing")

    assert result["status"] == "not_found"
    assert result["vacancy"] is None
    assert result["warnings"] == ["vacancy_not_found"]


def test_get_vacancy_by_url_resolves_duplicates_deterministically(tmp_path: Path) -> None:
    module = importlib.import_module("job_intel.recruiter_read_facade")
    store = _seed_store(tmp_path)
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO vacancies (
                id, vacancy_key, source, source_id, company, title, location, url, description,
                posted_at, scraped_at, salary, company_url, metadata_json, first_seen_at, last_seen_at, repost_count, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                202,
                "linkedin:example:2",
                "linkedin",
                "example-2",
                "Acme",
                "VP Product",
                "Remote",
                "https://example.com/jobs/1",
                "Older duplicate posting",
                "2026-06-18T00:00:00+00:00",
                "2026-06-18T00:00:00+00:00",
                "$180k-$220k",
                "https://example.com",
                json.dumps({"seniority": "executive"}),
                "2026-06-18T00:00:00+00:00",
                "2026-06-18T00:00:00+00:00",
                0,
                "rejected",
            ),
        )
        conn.execute(
            """
            INSERT INTO vacancy_evaluations (
                vacancy_key, run_id, score, tier, recommendation, salary_tier,
                matched_signals_json, concerns_json, reasons_json, raw_breakdown_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "linkedin:example:2",
                55,
                72,
                "potential_fit",
                "potential_fit",
                "mid",
                json.dumps(["product_leadership"]),
                json.dumps([]),
                json.dumps(["older_duplicate"]),
                json.dumps({"product": 4}),
                "2026-06-18T00:04:00+00:00",
            ),
        )
    facade = module.RecruiterReadFacade(store=store, stale_after_days=365)

    result = facade.get_vacancy_by_url("https://example.com/jobs/1")

    assert result["status"] == "found"
    assert result["vacancy"]["vacancy_id"] == 101
    assert "multiple_vacancies_for_url_resolved" in result["warnings"]
    assert result["provenance"]["duplicate_count"] == 2
    assert result["provenance"]["selected_vacancy_id"] == 101
    assert result["provenance"]["candidate_vacancy_ids"] == [101, 202]
    assert result["provenance"]["selection_policy"] == [
        "prefer_opportunity_link",
        "prefer_machine_score",
        "prefer_freshness",
        "tie_break_highest_vacancy_id",
    ]
    assert result["vacancy"]["provenance"]["duplicate_url_resolution"]["duplicate_count"] == 2


def test_get_opportunity_for_vacancy_returns_source_missing_when_crm_tables_absent(tmp_path: Path) -> None:
    module = importlib.import_module("job_intel.recruiter_read_facade")
    store = JobIntelStore(tmp_path / "minimal.sqlite3")
    store.bootstrap()
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO vacancies (
                id, vacancy_key, source, source_id, company, title, location, url, description,
                metadata_json, first_seen_at, last_seen_at, repost_count, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "k1",
                "linkedin",
                "s1",
                "Acme",
                "VP Product",
                "Remote",
                "https://example.com/jobs/1",
                "Desc",
                json.dumps({}),
                "2026-06-27T00:00:00+00:00",
                "2026-06-27T00:00:00+00:00",
                0,
                "active",
            ),
        )
    facade = module.RecruiterReadFacade(store=store)

    result = facade.get_opportunity_for_vacancy(1)

    assert result["status"] == "source_missing"
    assert result["opportunity"] is None
    assert result["warnings"] == ["crm_tables_missing"]


def test_get_application_history_returns_found_payload(facade) -> None:
    result = facade.get_application_history(501)

    assert result["status"] == "found"
    assert result["history"][0]["event_type"] == "application_submitted"
    assert result["artifacts"][0]["artifact_type"] == "cover_letter"
    assert result["feedback"][0]["feedback_type"] == "interesting"
    json.dumps(result)


def test_recent_relevant_vacancies_respects_limit_and_serializes(facade) -> None:
    result = facade.get_recent_relevant_vacancies(limit=1, min_score=80)

    assert result["status"] == "found"
    assert len(result["vacancies"]) == 1
    assert result["vacancies"][0]["evaluation"]["score"] == 91
    json.dumps(result)


def test_public_api_exposes_no_write_methods(facade) -> None:
    public_methods = {name for name in dir(facade) if not name.startswith("_")}

    forbidden = {"create", "update", "apply", "reconcile", "send", "bootstrap"}
    assert not any(any(word in name for word in forbidden) for name in public_methods)


def test_module_does_not_import_write_heavy_job_intel_modules() -> None:
    source = (REPO_ROOT / "job_intel" / "recruiter_read_facade.py").read_text()

    assert "crm_service" not in source
    assert "crm_reconciler" not in source
    assert "OpportunityRepository" not in source
