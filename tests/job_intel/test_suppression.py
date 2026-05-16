from __future__ import annotations

from job_intel import cli
from job_intel.models import Vacancy
from job_intel.store import JobIntelStore


def test_cross_run_notification_suppression(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()

    vacancy = Vacancy(
        source="linkedin",
        source_id="1",
        company="Revolut",
        title="Head of Product",
        location="London",
        url="https://example.com/1",
        description="Own monetization, product strategy, and P&L for a B2C platform.",
    )
    monkeypatch.setenv("JOB_INTEL_DB_PATH", str(db_path))
    monkeypatch.setenv("JOB_INTEL_SLACK_WEBHOOK_URL", "https://hooks.slack.test/example")
    monkeypatch.setattr(cli, "_collect_vacancies", lambda: ([vacancy], {"duckduckgo": {"status": "ok"}}))
    monkeypatch.setattr("job_intel.cli.requests.post", lambda *args, **kwargs: type("R", (), {"raise_for_status": lambda self: None})())

    first = cli.run_daily()
    second = cli.run_daily()

    assert "Revolut" in first
    assert second == "[SILENT]" or "Operator note" in second

    with store.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM notifications WHERE vacancy_id IS NOT NULL").fetchone()[0]

    assert count == 1


def test_notification_resends_when_score_improves(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()

    base = Vacancy(
        source="linkedin",
        source_id="1",
        company="Adapty",
        title="Product Director",
        location="Remote, Europe",
        url="https://example.com/1",
        description="Own monetization and product strategy for a B2C subscription platform.",
    )
    improved = base.model_copy(update={"description": "Own monetization, product strategy, and P&L for a B2C subscription platform."})

    monkeypatch.setenv("JOB_INTEL_DB_PATH", str(db_path))
    monkeypatch.setenv("JOB_INTEL_SLACK_WEBHOOK_URL", "https://hooks.slack.test/example")
    monkeypatch.setattr("job_intel.cli.requests.post", lambda *args, **kwargs: type("R", (), {"raise_for_status": lambda self: None})())

    monkeypatch.setattr(cli, "_collect_vacancies", lambda: ([base], {"duckduckgo": {"status": "ok"}}))
    cli.run_daily()

    monkeypatch.setattr(cli, "_collect_vacancies", lambda: ([improved], {"duckduckgo": {"status": "ok"}}))
    result = cli.run_daily()

    assert "Adapty" in result
    with store.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM notifications WHERE vacancy_id IS NOT NULL AND delivery_status = 'sent'").fetchone()[0]

    assert count == 2
