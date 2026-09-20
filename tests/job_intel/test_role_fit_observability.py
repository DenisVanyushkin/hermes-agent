from __future__ import annotations

import importlib
import json
import sqlite3
from typing import Any

import pytest

from job_intel.cli import _record_role_fit_trace
from job_intel.models import Evaluation, Vacancy
from job_intel.observability import record_daily_observability
from job_intel.performance import RunPerformanceRecorder
from job_intel.product_search.role_fit import ROLE_FIT_RULESET_VERSION
from job_intel.store import JobIntelStore


def vacancy(**overrides: object) -> Vacancy:
    values: dict[str, object] = {
        "source": "greenhouse",
        "source_id": "fixture-1",
        "company": "CloudWorks",
        "title": "Chief Product Officer",
        "location": "Remote",
        "url": "https://example.test/jobs/1",
        "description": (
            "CloudWorks is a B2B SaaS software company. Own the product strategy "
            "and lead product management across the business."
        ),
    }
    values.update(overrides)
    return Vacancy(**values)


def evaluation() -> Evaluation:
    return Evaluation(score=90, tier="strong_fit", recommendation="strong_fit", reasons=[])


def classification() -> dict[str, Any]:
    return {"classification": "cpo", "executive_detected": True}


def test_bootstrap_adds_role_fit_columns_to_empty_database(tmp_path) -> None:
    store = JobIntelStore(tmp_path / "empty.sqlite3")

    store.bootstrap()
    store.bootstrap()

    with store.connect(read_only=True) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(vacancy_observability)")
        }
    assert {"role_fit_verdict", "role_fit_rules_json"} <= columns


def test_bootstrap_adds_role_fit_columns_to_existing_database(tmp_path) -> None:
    db_path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY,
                mode TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                notes TEXT,
                metadata_json TEXT,
                provenance_json TEXT,
                run_type TEXT NOT NULL DEFAULT 'production'
            );
            CREATE TABLE vacancy_observability (
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
                selection_boundary_reasons_json TEXT,
                selection_boundary_unknowns_json TEXT,
                UNIQUE(run_id, vacancy_key, url)
            );
            """
        )

    JobIntelStore(db_path).bootstrap()
    JobIntelStore(db_path).bootstrap()

    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(vacancy_observability)")
        }
    assert {"role_fit_verdict", "role_fit_rules_json"} <= columns


def test_role_fit_verdict_and_rules_are_persisted_once_per_vacancy(tmp_path, monkeypatch) -> None:
    store = JobIntelStore(tmp_path / "observability.sqlite3")
    store.bootstrap()
    run_id = store.start_run("test")
    item = vacancy()
    original = importlib.import_module("job_intel.observability").evaluate_role_fit
    calls = 0

    def counted_evaluate(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr("job_intel.observability.evaluate_role_fit", counted_evaluate)
    stats = record_daily_observability(
        store,
        run_id,
        [
            (item, evaluation(), classification(), 1, False),
            (item, evaluation(), classification(), 1, False),
        ],
    )

    with store.connect(read_only=True) as connection:
        row = connection.execute(
            "SELECT role_fit_verdict, role_fit_rules_json FROM vacancy_observability"
        ).fetchone()
    assert calls == 1
    assert stats["evaluated_count"] == 1
    assert row[0] == "accept"
    assert json.loads(row[1]) == {
        "ruleset_version": ROLE_FIT_RULESET_VERSION,
        "rule_ids": ["software_product_leadership"],
    }


def test_role_fit_error_is_persisted_without_failing_observability(tmp_path, monkeypatch) -> None:
    store = JobIntelStore(tmp_path / "observability-error.sqlite3")
    store.bootstrap()
    run_id = store.start_run("test")

    def fail_evaluate(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("role-fit fixture failure")

    monkeypatch.setattr("job_intel.observability.evaluate_role_fit", fail_evaluate)
    stats = record_daily_observability(
        store,
        run_id,
        [(vacancy(), evaluation(), classification(), 1, False)],
    )

    with store.connect(read_only=True) as connection:
        row = connection.execute(
            "SELECT role_fit_verdict, role_fit_rules_json FROM vacancy_observability"
        ).fetchone()
    assert stats["evaluated_count"] == 1
    assert stats["error_count"] == 1
    assert row[0] == "error"
    assert json.loads(row[1]) == {
        "ruleset_version": ROLE_FIT_RULESET_VERSION,
        "error": "role-fit fixture failure",
    }


def test_role_fit_trace_counts_include_errors_and_elapsed_time(tmp_path, monkeypatch) -> None:
    store = JobIntelStore(tmp_path / "observability-trace.sqlite3")
    store.bootstrap()
    run_id = store.start_run("test")
    failing = vacancy(
        source_id="fixture-2", title="VP Product", url="https://example.test/jobs/2"
    )

    def fail_one(title: str, *args: Any, **kwargs: Any) -> Any:
        if title == failing.title:
            raise ValueError("one bad vacancy")
        return importlib.import_module("job_intel.product_search.role_fit").evaluate_role_fit(
            title, *args, **kwargs
        )

    monkeypatch.setattr("job_intel.observability.evaluate_role_fit", fail_one)
    stats = record_daily_observability(
        store,
        run_id,
        [
            (vacancy(), evaluation(), classification(), 1, False),
            (failing, evaluation(), classification(), 2, False),
        ],
    )

    assert stats["evaluated_count"] == 2
    assert stats["error_count"] == 1
    assert stats["duration_seconds"] >= 0


def test_role_fit_trace_is_written_to_performance_span() -> None:
    performance = RunPerformanceRecorder(7)
    with performance.span("observability_persistence_total") as span:
        _record_role_fit_trace(
            span,
            {"evaluated_count": 5000, "error_count": 2, "duration_seconds": 1.25},
        )

    stored = performance.spans()[0]
    assert stored.normalized_count == 5000
    assert stored.error_count == 2
    assert json.loads(stored.metadata_json or "{}") == {
        "role_fit_evaluated_count": 5000,
        "role_fit_error_count": 2,
        "role_fit_duration_seconds": 1.25,
    }


def test_role_fit_report_counts_verdicts_rules_and_pipeline_disagreements(tmp_path) -> None:
    module = importlib.import_module("scripts.job_intel_role_fit_observation")
    db_path = tmp_path / "report.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE vacancy_observability (
                run_id INTEGER, vacancy_key TEXT, accepted INTEGER,
                role_fit_verdict TEXT, role_fit_rules_json TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO vacancy_observability VALUES (?, ?, ?, ?, ?)",
            [
                (7, "accepted-reject", 1, "reject", '["domain_expertise_required"]'),
                (7, "accepted-accept", 1, "accept", '["software_product_leadership"]'),
                (7, "rejected-accept", 0, "accept", '["software_product_leadership"]'),
                (7, "rejected-blocked", 0, "blocked", '["required_language_unavailable"]'),
                (7, "error", 0, "error", '{"error":"boom"}'),
                (7, "old", 0, None, None),
                (8, "other-run", 1, "reject", '["ignored"]'),
            ],
        )

    with module._connect_read_only(db_path) as connection:
        report = module.render_report(connection, 7)

    assert "accept=2" in report
    assert "blocked=1" in report
    assert "error=1" in report
    assert "not_evaluated=1" in report
    assert "domain_expertise_required 1" in report
    assert "software_product_leadership 2" in report
    assert "accepted_pipeline_rejected_by_role_fit=1" in report
    assert "rejected_pipeline_accepted_by_role_fit=1" in report
