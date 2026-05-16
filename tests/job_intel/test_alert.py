from job_intel.cli import run_alert_scan
from job_intel.models import Vacancy


def test_alert_scan_deduplicates_canonical_vacancies(monkeypatch) -> None:
    vacancy = Vacancy(
        source="linkedin",
        source_id="1",
        company="Revolut",
        title="Head of Product",
        location="London",
        url="https://example.com/1",
        description="Own monetization and product strategy for a platform.",
    )
    duplicate = vacancy.model_copy(update={"source_id": "2", "url": "https://example.com/2"})
    monkeypatch.setattr("job_intel.cli._collect_vacancies", lambda: [vacancy, duplicate])
    monkeypatch.setattr("job_intel.cli.score_vacancy", lambda vacancy: __import__("job_intel.models", fromlist=["Evaluation"]).Evaluation(score=95, tier="exceptional_fit", recommendation="exceptional_fit"))
    monkeypatch.delenv("JOB_INTEL_SLACK_WEBHOOK_URL", raising=False)

    digest = run_alert_scan()

    assert digest.count("Revolut") == 1
    assert digest.count("Head of Product") == 1
