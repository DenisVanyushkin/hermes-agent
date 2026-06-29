from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_intel.store import JobIntelStore

from hermes_cli.recruiter_context import (
    RecruiterContextRequest,
    RecruiterContextStatus,
    build_recruiter_context,
)


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
                status TEXT NOT NULL DEFAULT discovered,
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
            CREATE TABLE IF NOT EXISTS vacancy_feedback_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vacancy_id INTEGER NOT NULL,
                run_id INTEGER,
                notification_id TEXT,
                vacancy_key TEXT,
                canonical_url TEXT,
                card_key TEXT,
                slack_channel TEXT,
                slack_message_ts TEXT,
                user_id TEXT,
                feedback_type TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
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
                json.dumps({"seniority": "executive"}),
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
                json.dumps({"kind": "test"}),
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
    return store



def _request(**kwargs: object) -> RecruiterContextRequest:
    return RecruiterContextRequest(**kwargs)


class TestRecruiterContextRequestValidation:
    def test_requires_exactly_one_identifier(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            build_recruiter_context(_request())

    def test_rejects_multiple_identifiers(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            build_recruiter_context(_request(vacancy_id=101, opportunity_id=501))


class TestRecruiterContextPacket:
    def test_builds_ready_packet_for_vacancy_id(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path)
        private_dir = tmp_path / "private-career"
        private_dir.mkdir()
        for name in [
            "denis_vanyushkin_structured_resume_v1_1.json",
            "opportunity-thesis.md",
            "company_intelligence_architecture.md",
            "scoring_v3.md",
        ]:
            (private_dir / name).write_text("present", encoding="utf-8")

        packet = build_recruiter_context(
            _request(
                vacancy_id=101,
                job_intel_db_path=store.db_path,
                private_career_dir=private_dir,
                repo_root=REPO_ROOT,
            )
        )

        assert packet.status is RecruiterContextStatus.READY
        assert packet.vacancy["vacancy_id"] == 101
        assert packet.opportunity["id"] == 501
        assert len(packet.company_context) >= 1
        assert packet.application_history["status"] == "found"
        assert packet.machine_score["status"] == "available"
        assert packet.machine_score["score"] == 91
        assert packet.role_package_context["package_id"] == "hermes-recruiter"
        assert packet.private_context["status"] == "PRIVATE_CONTEXT_AVAILABLE"
        assert packet.private_context["files"]["scoring_v3.md"]["present"] is True
        assert "get_vacancy_by_id" in packet.provenance["facade_methods"]
        assert "get_opportunity_for_vacancy" in packet.provenance["facade_methods"]
        assert "get_application_history" in packet.provenance["facade_methods"]
        encoded = json.dumps(packet.to_dict(), sort_keys=True)
        assert "hermes-recruiter" in encoded

    def test_supports_vacancy_url_lookup(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path)

        packet = build_recruiter_context(
            _request(
                vacancy_url="https://example.com/jobs/1",
                job_intel_db_path=store.db_path,
                private_career_dir=tmp_path / "missing-career",
                repo_root=REPO_ROOT,
            )
        )

        assert packet.status is RecruiterContextStatus.READY
        assert packet.request["vacancy_url"] == "https://example.com/jobs/1"
        assert packet.vacancy["vacancy_id"] == 101

    def test_supports_opportunity_id_lookup(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path)

        packet = build_recruiter_context(
            _request(
                opportunity_id=501,
                job_intel_db_path=store.db_path,
                private_career_dir=tmp_path / "missing-career",
                repo_root=REPO_ROOT,
            )
        )

        assert packet.status is RecruiterContextStatus.READY
        assert packet.opportunity["id"] == 501
        assert packet.vacancy["vacancy_id"] == 101

    def test_returns_vacancy_not_found(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path)

        packet = build_recruiter_context(
            _request(vacancy_id=999, job_intel_db_path=store.db_path, repo_root=REPO_ROOT)
        )

        assert packet.status is RecruiterContextStatus.VACANCY_NOT_FOUND
        assert packet.vacancy is None
        assert "vacancy_not_found" in packet.warnings

    def test_returns_opportunity_not_found(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path)

        packet = build_recruiter_context(
            _request(opportunity_id=999, job_intel_db_path=store.db_path, repo_root=REPO_ROOT)
        )

        assert packet.status is RecruiterContextStatus.OPPORTUNITY_NOT_FOUND
        assert packet.opportunity is None
        assert "opportunity_not_found" in packet.warnings

    def test_private_context_missing_is_not_fatal(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path)

        packet = build_recruiter_context(
            _request(vacancy_id=101, job_intel_db_path=store.db_path, repo_root=REPO_ROOT)
        )

        assert packet.status is RecruiterContextStatus.READY
        assert packet.private_context["status"] == "PRIVATE_CONTEXT_MISSING"
        assert "private_context_missing" in packet.warnings

    def test_private_context_partial(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path)
        private_dir = tmp_path / "private-career"
        private_dir.mkdir()
        (private_dir / "scoring_v3.md").write_text("present", encoding="utf-8")

        packet = build_recruiter_context(
            _request(
                vacancy_id=101,
                job_intel_db_path=store.db_path,
                private_career_dir=private_dir,
                repo_root=REPO_ROOT,
            )
        )

        assert packet.private_context["status"] == "PARTIAL"
        assert packet.private_context["files"]["scoring_v3.md"]["present"] is True
        assert packet.private_context["files"]["opportunity-thesis.md"]["present"] is False

    def test_package_context_error_is_controlled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _seed_store(tmp_path)

        def _boom(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("package exploded")

        monkeypatch.setattr("hermes_cli.recruiter_context.build_repo_role_package_skill_context", _boom)

        packet = build_recruiter_context(
            _request(vacancy_id=101, job_intel_db_path=store.db_path, repo_root=REPO_ROOT)
        )

        assert packet.status is RecruiterContextStatus.PACKAGE_CONTEXT_ERROR
        assert packet.role_package_context == {}
        assert any("package context" in item for item in packet.errors)

    def test_facade_error_is_controlled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _seed_store(tmp_path)

        def _boom(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("sensitive connection details")

        monkeypatch.setattr("hermes_cli.recruiter_context.RecruiterReadFacade.get_vacancy_by_id", _boom)

        packet = build_recruiter_context(
            _request(vacancy_id=101, job_intel_db_path=store.db_path, repo_root=REPO_ROOT)
        )

        assert packet.status is RecruiterContextStatus.FACADE_ERROR
        assert packet.vacancy is None
        assert any(item.startswith("facade_error:") for item in packet.errors)
        assert all("connection details" not in item for item in packet.errors)


class TestRecruiterContextBoundaries:
    def test_adapter_module_avoids_forbidden_imports_and_sqlite(self) -> None:
        source = (REPO_ROOT / "hermes_cli" / "recruiter_context.py").read_text(encoding="utf-8")

        assert "crm_service" not in source
        assert "crm_reconciler" not in source
        assert "OpportunityRepository" not in source
        assert "import sqlite3" not in source
        assert "from sqlite3" not in source
