from job_intel.dedup import canonical_vacancy_key, is_duplicate
from job_intel.models import Vacancy


def test_canonical_key_normalizes_company_title_and_location() -> None:
    vacancy = Vacancy(
        source="linkedin",
        source_id="abc",
        company="Revolut",
        title="VP Product",
        location="London, UK",
        url="https://example.com/1",
        description="Lead monetization and product strategy.",
    )

    assert canonical_vacancy_key(vacancy) == canonical_vacancy_key(
        vacancy.model_copy(update={"title": "Vice President Product"})
    )


def test_duplicate_detection_uses_similarity_and_repost_window() -> None:
    canonical = Vacancy(
        source="greenhouse",
        source_id="1",
        company="Wise",
        title="Head of Product Monetization",
        location="Remote, Europe",
        url="https://boards.example/jobs/1",
        description="Own product strategy, monetization, and growth for a B2C platform.",
        posted_at="2026-05-01T00:00:00Z",
    )
    repost = canonical.model_copy(update={"source_id": "2", "url": "https://boards.example/jobs/2"})

    assert is_duplicate(repost, canonical, similarity_threshold=0.7, repost_window_days=45)
