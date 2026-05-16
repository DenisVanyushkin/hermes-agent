from __future__ import annotations

from datetime import datetime, timedelta, timezone

from job_intel import cli
from job_intel.models import Vacancy
from job_intel.store import JobIntelStore


def test_retire_stale_marks_old_vacancies_stale(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()

    vacancy = Vacancy(
        source="greenhouse",
        source_id="1",
        company="Wise",
        title="Head of Product",
        location="Remote, Europe",
        url="https://example.com/1",
        description="Own product strategy and monetization.",
    )
    vacancy_id = store.upsert_vacancy(vacancy, "key-1")
    stale_at = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE vacancies SET last_seen_at = ?, status = 'active' WHERE id = ?", (stale_at, vacancy_id))

    monkeypatch.setenv("JOB_INTEL_DB_PATH", str(db_path))
    summary = cli.retire_stale_vacancies(days=30)

    assert summary["stale"] == 1
    with store.connect() as conn:
        row = conn.execute("SELECT status FROM vacancies WHERE id = ?", (vacancy_id,)).fetchone()

    assert row[0] == "stale"


def test_retire_stale_archives_old_stale_vacancies(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()

    vacancy = Vacancy(
        source="lever",
        source_id="1",
        company="Miro",
        title="Chief Product Officer",
        location="Remote",
        url="https://example.com/2",
        description="Own product strategy.",
    )
    vacancy_id = store.upsert_vacancy(vacancy, "key-2")
    stale_at = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE vacancies SET last_seen_at = ?, status = 'stale' WHERE id = ?", (stale_at, vacancy_id))

    monkeypatch.setenv("JOB_INTEL_DB_PATH", str(db_path))
    summary = cli.retire_stale_vacancies(days=30)

    assert summary["archived"] == 1
    with store.connect() as conn:
        row = conn.execute("SELECT status FROM vacancies WHERE id = ?", (vacancy_id,)).fetchone()

    assert row[0] == "archived"
