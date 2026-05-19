from __future__ import annotations

from job_intel import cli
from job_intel.models import Evaluation, Vacancy
from job_intel.store import JobIntelStore


def _vacancy(source: str, source_id: str, company: str, title: str, location: str, url: str) -> Vacancy:
    return Vacancy(
        source=source,
        source_id=source_id,
        company=company,
        title=title,
        location=location,
        url=url,
        description=f"{title} for {company}",
    )


def _evaluation(score: int, tier: str, recommendation: str) -> Evaluation:
    return Evaluation(score=score, tier=tier, recommendation=recommendation)


def test_health_report_summarizes_daily_pipeline_and_deltas(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()

    prev_run = store.start_run(
        "daily",
        metadata={
            "source_statuses": {
                "linkedin": {
                    "status": "degraded",
                    "hits": 2,
                    "metrics": {"acquisition_quality_score": 0.41, "source_reliability": 0.33},
                    "session_health": {
                        "pages_fetched": 2,
                        "successful_extractions": 1,
                        "failed_extractions": 1,
                        "detail_pages_opened": 0,
                        "pagination_depth_reached": 2,
                        "avg_page_load_time_seconds": 1.2,
                        "login_walls": 1,
                        "auth_redirects": 0,
                        "anti_bot_events": 1,
                        "status": "degraded",
                        "session_age_hours": 1.5,
                        "last_successful_authenticated_request": "https://www.linkedin.com/jobs/search/?keywords=VP+Product",
                    },
                }
            }
        },
    )
    prev_vacancy = _vacancy("linkedin", "prev-1", "Acme Prev", "Director of Product", "Remote", "https://example.com/prev")
    prev_key = "prev-key"
    store.upsert_vacancy(prev_vacancy, prev_key)
    store.save_evaluation(prev_key, _evaluation(75, "strong_fit", "strong_fit"), run_id=prev_run)
    store.finish_run(prev_run, status="ok", notes="previous cycle")

    current_run = store.start_run(
        "daily",
        metadata={
            "source_statuses": {
                "linkedin": {
                    "status": "blocked",
                    "hits": 3,
                    "metrics": {"acquisition_quality_score": 0.77, "source_reliability": 0.62},
                    "session_health": {
                        "pages_fetched": 3,
                        "successful_extractions": 2,
                        "failed_extractions": 1,
                        "detail_pages_opened": 1,
                        "pagination_depth_reached": 3,
                        "avg_page_load_time_seconds": 1.5,
                        "login_walls": 1,
                        "auth_redirects": 1,
                        "anti_bot_events": 2,
                        "status": "blocked",
                        "session_age_hours": 2.75,
                        "last_successful_authenticated_request": "https://www.linkedin.com/jobs/search/?keywords=VP+Product",
                    },
                },
                "headhunter": {
                    "status": "ok",
                    "hits": 2,
                    "metrics": {"acquisition_quality_score": 0.88, "source_reliability": 0.79},
                    "session_health": {
                        "pages_fetched": 2,
                        "successful_extractions": 2,
                        "failed_extractions": 0,
                        "detail_pages_opened": 1,
                        "pagination_depth_reached": 2,
                        "avg_page_load_time_seconds": 1.0,
                        "login_walls": 0,
                        "auth_redirects": 0,
                        "anti_bot_events": 0,
                        "status": "healthy",
                        "session_age_hours": 3.0,
                        "last_successful_authenticated_request": "https://hh.ru/search/vacancy?text=VP+Product",
                    },
                },
                "target_companies": {"status": "ok", "hits": 1},
            }
        },
    )

    linkedin_good = _vacancy("linkedin", "cur-1", "Acme", "VP Product", "Dubai, UAE", "https://example.com/1")
    linkedin_dup = _vacancy("linkedin", "cur-2", "Acme", "Director of Product", "Remote", "https://example.com/2")
    hh_good = _vacancy("headhunter", "cur-3", "Fintech Group", "Head of Product", "Almaty, KZ", "https://example.com/3")
    target_good = _vacancy("target-company", "cur-4", "Ecosystem Co", "GM Digital", "Remote", "https://example.com/4")

    key1 = "cur-key-1"
    key2 = "cur-key-2"
    key3 = "cur-key-3"
    key4 = "cur-key-4"
    store.upsert_vacancy(linkedin_good, key1)
    store.save_evaluation(key1, _evaluation(96, "exceptional_fit", "exceptional_fit"), run_id=current_run)
    store.upsert_vacancy(linkedin_dup, key2)
    store.save_evaluation(key2, _evaluation(81, "strong_fit", "strong_fit"), run_id=current_run)
    store.set_vacancy_status(store.get_vacancy_by_key(key2)["id"], "duplicate")
    store.upsert_vacancy(hh_good, key3)
    store.save_evaluation(key3, _evaluation(91, "exceptional_fit", "exceptional_fit"), run_id=current_run)
    store.upsert_vacancy(target_good, key4)
    store.save_evaluation(key4, _evaluation(88, "strong_fit", "strong_fit"), run_id=current_run)

    store.create_notification(current_run, "C0B42K4H4KV", "daily_digest", "daily digest body", delivery_status="sent")
    store.create_notification(current_run, "C0B42K4H4KV", "alert", "alert body", delivery_status="sent")
    failed_id = store.create_notification(current_run, "C0B42K4H4KV", "alert", "failed alert body", delivery_status="pending")
    store.mark_notification_delivery(failed_id, "failed", attempts=2, delivery_error="boom")
    store.finish_run(current_run, status="ok", notes="current cycle")

    captured: dict[str, str] = {}

    def fake_deliver(message: str, channel: str | None = None, *, retries: int = 3):
        captured["message"] = message
        captured["channel"] = channel or ""
        return cli.SlackDeliveryResult(success=True, attempts=1)

    monkeypatch.setenv("JOB_INTEL_DB_PATH", str(db_path))
    monkeypatch.setenv("JOB_INTEL_SLACK_WEBHOOK_URL", "https://hooks.slack.test/example")
    monkeypatch.setattr(cli, "_deliver_to_slack", fake_deliver)

    report = cli.run_health_report()

    assert "Nightly Executive Intelligence Health Report" in report
    assert "Source health summary" in report
    assert "Session/auth health summary" in report
    assert "Acquisition summary" in report
    assert "Signal quality summary" in report
    assert "Pipeline metrics" in report
    assert "Delivery metrics" in report
    assert "Top accepted opportunities" in report
    assert "Metric deltas vs previous daily run" in report
    assert "linkedin: source_status=blocked" in report
    assert "head_roles" in report or "head_roles=" in report
    assert "acquisition_quality_score=" in report
    assert "alerts_sent=1" in report
    assert "digests_sent=1" in report
    assert "delivery_failures=1" in report
    assert captured["channel"] == "C0B42K4H4KV"
    assert "Nightly Executive Intelligence Health Report" in captured["message"]
