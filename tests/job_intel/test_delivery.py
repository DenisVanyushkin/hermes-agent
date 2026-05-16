from __future__ import annotations

from pathlib import Path

from job_intel import cli
from job_intel.models import Vacancy
from job_intel.store import JobIntelStore


def test_daily_run_marks_notification_sent_after_retries(monkeypatch, tmp_path) -> None:
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
    monkeypatch.setattr(cli, "_collect_vacancies", lambda: ([vacancy], {"duckduckgo": {"status": "ok"}}))

    attempts = {"count": 0}

    def fake_post(url, json, timeout):
        attempts["count"] += 1

        class Response:
            def raise_for_status(self):
                if attempts["count"] < 3:
                    raise RuntimeError("temporary slack failure")

        return Response()

    monkeypatch.setenv("JOB_INTEL_DB_PATH", str(db_path))
    monkeypatch.setenv("JOB_INTEL_SLACK_WEBHOOK_URL", "https://hooks.slack.test/example")
    monkeypatch.setattr("job_intel.cli.requests.post", fake_post)

    result = cli.run_daily()

    assert "Revolut" in result
    assert attempts["count"] == 3

    with store.connect() as conn:
        row = conn.execute(
            "SELECT delivery_status, delivery_attempts, delivery_error, vacancy_id FROM notifications ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert row[0] == "sent"
    assert row[1] == 3
    assert row[2] is None
    assert row[3] is not None


def test_daily_run_marks_failed_notification_after_retry_exhaustion(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()

    vacancy = Vacancy(
        source="linkedin",
        source_id="2",
        company="Adapty",
        title="Director of Product",
        location="Remote, Europe",
        url="https://example.com/2",
        description="Own product strategy, monetization, and P&L for a B2C platform.",
    )
    monkeypatch.setattr(cli, "_collect_vacancies", lambda: ([vacancy], {"duckduckgo": {"status": "ok"}}))

    attempts = {"count": 0}

    def failing_post(url, json, timeout):
        attempts["count"] += 1

        class Response:
            def raise_for_status(self):
                raise RuntimeError("permanent slack failure")

        return Response()

    monkeypatch.setenv("JOB_INTEL_DB_PATH", str(db_path))
    monkeypatch.setenv("JOB_INTEL_SLACK_WEBHOOK_URL", "https://hooks.slack.test/example")
    monkeypatch.setattr("job_intel.cli.requests.post", failing_post)

    result = cli.run_daily()

    assert "Operator note" in result or "Adapty" in result
    assert attempts["count"] == 3

    with store.connect() as conn:
        row = conn.execute(
            "SELECT delivery_status, delivery_attempts, delivery_error FROM notifications ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert row[0] == "failed"
    assert row[1] == 3
    assert "permanent slack failure" in row[2]
