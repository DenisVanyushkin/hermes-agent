from job_intel.digest import format_daily_digest
from job_intel.models import Evaluation, Vacancy


def test_daily_digest_returns_silent_when_empty() -> None:
    assert format_daily_digest([]) == "[SILENT]"


def test_daily_digest_includes_key_fields() -> None:
    vacancy = Vacancy(
        source="headhunter",
        source_id="1",
        company="Revolut",
        title="Head of Product",
        location="London",
        url="https://example.com",
        description="Own monetization and product strategy.",
    )
    evaluation = Evaluation(
        score=88,
        tier="exceptional_fit",
        recommendation="exceptional_fit",
        matched_signals=["monetization", "executive-level leadership"],
    )
    digest = format_daily_digest([(vacancy, evaluation)])

    assert "Revolut" in digest
    assert "Head of Product" in digest
    assert "88" in digest
    assert "exceptional_fit" in digest
