from __future__ import annotations

from job_intel.crm_service import CRMService
from job_intel.models import Vacancy
from job_intel.store import JobIntelStore


def make_service(tmp_path):
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()
    return CRMService.from_store(store)


def build_vacancy(url: str = "https://example.com/jobs/1") -> Vacancy:
    return Vacancy(
        source="linkedin",
        source_id="example-1",
        company="Acme",
        title="Head of Product",
        location="Remote",
        url=url,
        description="Acme is hiring a Head of Product.",
    )


def seed_vacancy(service: CRMService) -> int:
    with service.repo.store.connect() as conn:
        conn.execute(
            """
            INSERT INTO vacancies (
                vacancy_key, source, source_id, company, title, location, url, description,
                posted_at, scraped_at, salary, company_url, metadata_json, first_seen_at,
                last_seen_at, repost_count, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "linkedin:example:1",
                "linkedin",
                "example-1",
                "Acme",
                "Head of Product",
                "Remote",
                "https://example.com/jobs/1",
                "Acme is hiring a Head of Product.",
                None,
                None,
                None,
                None,
                None,
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T00:00:00+00:00",
                0,
                "active",
            ),
        )
        return int(
            conn.execute(
                "SELECT id FROM vacancies WHERE vacancy_key=?",
                ("linkedin:example:1",),
            ).fetchone()[0]
        )


def test_ensure_opportunity_for_vacancy_is_idempotent_for_same_canonical_url(tmp_path):
    service = make_service(tmp_path)
    vacancy = build_vacancy(url="https://example.com/jobs/1")
    vacancy_id = seed_vacancy(service)

    first = service.ensure_opportunity_for_vacancy(vacancy=vacancy, vacancy_id=vacancy_id)
    second = service.ensure_opportunity_for_vacancy(vacancy=vacancy, vacancy_id=vacancy_id)

    assert first["id"] == second["id"]


def test_link_slack_message_to_opportunity_creates_crm_mapping_only(tmp_path):
    service = make_service(tmp_path)
    vacancy_id = seed_vacancy(service)
    opportunity = service.ensure_opportunity_for_vacancy(
        vacancy=build_vacancy(url="https://example.com/jobs/1"),
        vacancy_id=vacancy_id,
    )

    service.link_slack_message_to_opportunity(
        opportunity_id=opportunity["id"],
        slack_channel_id="C123",
        slack_message_ts="1760000000.123",
        slack_thread_ts=None,
    )

    crm = service.find_opportunity_by_slack_message("C123", "1760000000.123")
    assert crm is not None
    assert crm["id"] == opportunity["id"]
