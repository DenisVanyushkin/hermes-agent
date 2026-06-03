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
        _make_vacancy("linkedin", "li-2", "Initech", "Director of Product"),
        _make_vacancy("headhunter", "hh-2", "Umbrella", "Product Manager, Platform"),
    ]
    evaluations = {
        "li-1": Evaluation(score=95, tier="strong_fit", recommendation="strong_fit", matched_signals=["core product title"]),
        "hh-1": Evaluation(score=91, tier="possible_fit", recommendation="potential_fit", matched_signals=["leadership"]),
        "li-2": Evaluation(score=87, tier="possible_fit", recommendation="near_miss", concerns=["missing direct scope"]),
        "hh-2": Evaluation(score=54, tier="weak_fit", recommendation="reject"),
    }
    source_statuses = {
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
    monkeypatch.setattr(cli, "score_vacancy", lambda vacancy: evaluations[vacancy.source_id])
    monkeypatch.setattr(cli, "_should_notify_vacancy", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "update_strategic_layer", lambda store, persist=True: SimpleNamespace(predictions=[]))

    messages: list[str] = []
    counter = {"value": 0}

    def fake_deliver(message: str, channel: str | None = None, *, retries: int = 3, prefer_gateway: bool = False):
        messages.append(message)
        counter["value"] += 1
        return cli.SlackDeliveryResult(success=True, attempts=1, message_ts=f"1760000000.{counter['value']:06d}")

    monkeypatch.setattr(cli, "_deliver_to_slack", fake_deliver)

    result = cli.run_daily()

    assert "Daily Executive Review" in result
    assert messages[0].startswith("📊 *Daily Executive Review*")
    assert sum(message.startswith("*Search source update*") for message in messages) == 0

    vacancy_messages = [message for message in messages if "Location:" in message and "URL:" in message]
    assert len(vacancy_messages) == 3
    assert any("Acme" in message and "Head of Product" in message for message in vacancy_messages)
    assert any("Globex" in message and "VP Product" in message for message in vacancy_messages)
    assert any("Initech" in message and "Director of Product" in message for message in vacancy_messages)

    store = cli.JobIntelStore(db_path)
    notifications = store.fetch_notifications(limit=20)
    assert sum(1 for row in notifications if row["message_type"] == "source_search") == 0
    assert sum(1 for row in notifications if row["message_type"] == "vacancy_opportunity") == 3

    with store.connect() as conn:
        rows = conn.execute(
            "SELECT company, title, score, recommendation, slack_channel, slack_message_ts, url FROM vacancy_slack_messages ORDER BY id"
        ).fetchall()

    assert len(rows) == 3
    assert {row["company"] for row in rows} == {"Acme", "Globex", "Initech"}
    assert all(row["slack_channel"] == "C-search" for row in rows)
    assert all(row["slack_message_ts"] for row in rows)
