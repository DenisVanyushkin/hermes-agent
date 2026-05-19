from job_intel.cli import run_alert_scan
from job_intel.dedup import canonical_vacancy_key
from job_intel.models import Evaluation, Vacancy
from job_intel.store import JobIntelStore


def test_alert_scan_uses_persisted_inventory(monkeypatch, tmp_path) -> None:
    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    vacancy = Vacancy(
        source="linkedin",
        source_id="1",
        company="Revolut",
        title="Head of Product",
        location="London",
        url="https://example.com/1",
        description="Own monetization and product strategy for a platform.",
    )
    vacancy_key = canonical_vacancy_key(vacancy)
    store.upsert_vacancy(vacancy, vacancy_key)
    store.save_evaluation(vacancy_key, Evaluation(score=95, tier="exceptional_fit", recommendation="exceptional_fit"))

    def fail_collect(*args, **kwargs):
        raise AssertionError("alert scan should not collect sources")

    monkeypatch.setattr("job_intel.cli._store", lambda: store)
    monkeypatch.setattr("job_intel.cli._collect_vacancies", fail_collect)
    monkeypatch.delenv("JOB_INTEL_SLACK_WEBHOOK_URL", raising=False)

    digest = run_alert_scan()

    assert digest.count("Revolut") == 1
    assert digest.count("Head of Product") == 1
    assert "persisted inventory" in digest
