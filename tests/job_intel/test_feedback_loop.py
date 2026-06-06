from __future__ import annotations

import json

import pytest

from job_intel import cli
from job_intel.store import JobIntelStore


def _seed_tracked_slack_message(store: JobIntelStore) -> tuple[int, str]:
    store.bootstrap()
    with store.connect() as conn:
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
        vacancy_id = int(conn.execute("SELECT id FROM vacancies WHERE vacancy_key=?", ("linkedin:example:1",)).fetchone()[0])
        conn.execute(
            """
            INSERT INTO vacancy_slack_messages (
                vacancy_id, run_id, slack_channel, slack_message_ts, company, title, score,
                recommendation, url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vacancy_id,
                None,
                "C-search",
                "1760000000.123456",
                "Acme",
                "Head of Product",
                95,
                "strong_fit",
                "https://example.com/jobs/1",
                "2026-06-01T00:00:00+00:00",
            ),
        )
    return vacancy_id, "1760000000.123456"


@pytest.mark.parametrize(
    ("reaction", "expected_feedback_type"),
    [
        ("+1", "interesting"),
        ("-1", "not_interesting"),
        ("star", "exceptional"),
        ("rocket", "applied"),
    ],
)
def test_feedback_event_maps_reactions_and_persists_state(tmp_path, monkeypatch, reaction: str, expected_feedback_type: str) -> None:
    db_path = tmp_path / "job_intel.sqlite3"
    monkeypatch.setattr(cli, "resolve_db_path", lambda: db_path)
    store = JobIntelStore(db_path)
    vacancy_id, slack_message_ts = _seed_tracked_slack_message(store)

    payload_add = {
        "type": "reaction_added",
        "user": "U_TEST",
        "reaction": reaction,
        "item": {"channel": "C-search", "ts": slack_message_ts},
        "event_ts": "1760001111.000001",
    }
    payload_remove = {
        "type": "reaction_removed",
        "user": "U_TEST",
        "reaction": reaction,
        "item": {"channel": "C-search", "ts": slack_message_ts},
        "event_ts": "1760002222.000002",
    }

    result_add = json.loads(cli.run_feedback_event(payload_add))
    result_remove = json.loads(cli.run_feedback_event(payload_remove))

    assert result_add["status"] == "ok"
    assert result_add["vacancy_id"] == vacancy_id
    assert result_add["feedback_type"] == expected_feedback_type
    assert result_add["event_type"] == "reaction_added"

    assert result_remove["status"] == "ok"
    assert result_remove["feedback_type"] == expected_feedback_type
    assert result_remove["event_type"] == "reaction_removed"

    with store.connect() as conn:
        feedback_rows = conn.execute(
            "SELECT feedback_type, event_type, user_id FROM vacancy_feedback ORDER BY id"
        ).fetchall()
        state_row = conn.execute(
            "SELECT feedback_type, active, user_id, slack_message_ts FROM vacancy_feedback_state ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert [row["feedback_type"] for row in feedback_rows] == [expected_feedback_type, expected_feedback_type]
    assert [row["event_type"] for row in feedback_rows] == ["reaction_added", "reaction_removed"]
    assert [row["user_id"] for row in feedback_rows] == ["U_TEST", "U_TEST"]
    assert state_row["feedback_type"] == expected_feedback_type
    assert state_row["active"] == 0
    assert state_row["user_id"] == "U_TEST"
    assert state_row["slack_message_ts"] == slack_message_ts
