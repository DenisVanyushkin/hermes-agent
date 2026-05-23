from __future__ import annotations

from types import SimpleNamespace

from job_intel import cli
from job_intel.cli import CollectedVacancies
from job_intel.models import Evaluation, Vacancy


def _make_vacancy(source: str, source_id: str, company: str, title: str) -> Vacancy:
    return Vacancy(
        source=source,
        source_id=source_id,
        company=company,
        title=title,
        location="Remote",
        url=f"https://example.com/{source_id}",
        description=f"{company} is hiring for {title}.",
    )


def test_run_daily_emits_per_source_notifications(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "job_intel.sqlite3"

    monkeypatch.setattr(cli, "resolve_db_path", lambda: db_path)
    monkeypatch.setattr(
        cli,
        "load_config_bundle",
        lambda: {
            "runtime": {
                "slack": {
                    "batch_size": 5,
                    "channel": "C-search",
                    "alerts_channel": "C-alerts",
                    "search_report_channel": "C-search",
                }
            },
            "deduplication": {"secondary_similarity": {"description_similarity_threshold": 0.9}, "repost_detection": {"repost_window_days": 30}},
            "scoring": {"thresholds": {"exceptional_fit": 90}},
        },
    )

    vacancies = [
        _make_vacancy("linkedin", "li-1", "Acme", "Head of Product"),
        _make_vacancy("headhunter", "hh-1", "Globex", "VP Product"),
    ]
    source_statuses = {
        "duckduckgo": {"source": "duckduckgo", "status": "empty", "hits": 0, "errors": []},
        "headhunter": {
            "source": "headhunter",
            "status": "ok",
            "hits": 1,
            "errors": [],
            "acquisition": "browser-native-first",
            "session_health": {"browser_profile": "/var/lib/browser-desktop/profiles/hh", "pages_fetched": 1, "login_walls": 0, "auth_redirects": 0},
        },
        "linkedin": {
            "source": "linkedin",
            "status": "ok",
            "hits": 1,
            "errors": [],
            "acquisition": "browser-native",
            "session_health": {"browser_profile": "/var/lib/browser-desktop/profiles/linkedin", "pages_fetched": 2, "login_walls": 1, "auth_redirects": 0},
        },
    }
    monkeypatch.setattr(
        cli,
        "_collect_vacancies_compat",
        lambda store=None: CollectedVacancies(vacancies=vacancies, source_statuses=source_statuses),
    )
    monkeypatch.setattr(
        cli,
        "score_vacancy",
        lambda vacancy: Evaluation(
            score=95 if vacancy.source == "linkedin" else 92,
            tier="strong_fit",
            recommendation="strong_fit",
        ),
    )
    monkeypatch.setattr(cli, "_should_notify_vacancy", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "format_daily_digest", lambda *args, **kwargs: "RUN DIGEST")
    monkeypatch.setattr(cli, "update_strategic_layer", lambda store, persist=True: SimpleNamespace(predictions=[]))

    messages: list[str] = []

    def fake_deliver(message: str, channel: str | None = None, *, retries: int = 3):
        messages.append(message)
        return cli.SlackDeliveryResult(success=True, attempts=1)

    monkeypatch.setattr(cli, "_deliver_to_slack", fake_deliver)

    result = cli.run_daily()

    assert result == "RUN DIGEST"
    assert sum(message.startswith("*Search source update*") for message in messages) == 3
    assert any("duckduckgo" in message for message in messages)
    assert any("headhunter" in message for message in messages)
    assert any("linkedin" in message for message in messages)
    assert messages[-1] == "RUN DIGEST"

    store = cli.JobIntelStore(db_path)
    notifications = store.fetch_notifications(limit=20)
    assert sum(1 for row in notifications if row["message_type"] == "source_search") == 3
    assert sum(1 for row in notifications if row["message_type"] == "daily_digest") == 2
