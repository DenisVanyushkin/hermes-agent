from __future__ import annotations

import json

from job_intel import cli
from job_intel.models import Vacancy
from job_intel.store import JobIntelStore


class FakeDelivery:
    def __init__(self):
        self.calls: list[dict] = []
        self.counter = 0

    def __call__(self, message, channel=None, *, retries=3, prefer_gateway=False, thread_ts=None):
        from job_intel.cli import SlackDeliveryResult

        self.counter += 1
        self.calls.append({"message": message, "channel": channel, "thread_ts": thread_ts})
        return SlackDeliveryResult(
            success=True, attempts=1, error=None, status="sent",
            message_ts=f"1760000000.{2000 + self.counter}",
        )


def setup_tracked_vacancy(tmp_path, monkeypatch):
    db_path = tmp_path / "job_intel.sqlite3"
    monkeypatch.setattr(cli, "resolve_db_path", lambda: db_path)
    store = JobIntelStore(db_path)
    store.bootstrap()
    vacancy = Vacancy(
        source_id="linkedin-1",
        title="Head of Product",
        company="Acme",
        location="Remote",
        url="https://example.com/jobs/1",
        source="linkedin",
        description="Executive product role",
    )
    vacancy_id = store.upsert_vacancy(vacancy, "vac-key-1")
    store.insert_vacancy_slack_message(
        vacancy_id=vacancy_id,
        run_id=None,
        vacancy_key="vac-key-1",
        canonical_url="https://example.com/jobs/1",
        card_key="card-1",
        notification_id=None,
        slack_channel="C123",
        slack_message_ts="1760000000.100",
        message_type="vacancy_card",
        company="Acme",
        title="Head of Product",
        score=80,
        recommendation="strong_fit",
        url="https://example.com/jobs/1",
    )
    return store, vacancy_id


def reaction_payload(reaction="thumbsdown", event_type="reaction_added"):
    return {
        "type": event_type,
        "reaction": reaction,
        "user": "U1",
        "item": {"channel": "C123", "ts": "1760000000.100"},
        "event_ts": "1760000001.000",
    }


def test_negative_reaction_triggers_thread_prompt(tmp_path, monkeypatch):
    store, _ = setup_tracked_vacancy(tmp_path, monkeypatch)
    fake = FakeDelivery()
    monkeypatch.setattr(cli, "_deliver_to_slack", fake)

    result = json.loads(cli.run_feedback_event(reaction_payload()))
    assert result["status"] == "ok"
    assert result["feedback_prompt"]["status"] == "prompted"
    assert len(fake.calls) == 1
    assert fake.calls[0]["thread_ts"] == "1760000000.100"

    events = store.fetch_feedback_events()
    assert len(events) == 1
    assert events[0]["status"] == "awaiting_reply"


def test_repeated_negative_reaction_is_idempotent(tmp_path, monkeypatch):
    store, _ = setup_tracked_vacancy(tmp_path, monkeypatch)
    fake = FakeDelivery()
    monkeypatch.setattr(cli, "_deliver_to_slack", fake)

    json.loads(cli.run_feedback_event(reaction_payload()))
    second = json.loads(cli.run_feedback_event(reaction_payload()))
    assert second["feedback_prompt"]["status"] == "reused"
    assert len(fake.calls) == 1
    assert len(store.fetch_feedback_events()) == 1


def test_positive_reaction_does_not_prompt(tmp_path, monkeypatch):
    store, _ = setup_tracked_vacancy(tmp_path, monkeypatch)
    fake = FakeDelivery()
    monkeypatch.setattr(cli, "_deliver_to_slack", fake)

    result = json.loads(cli.run_feedback_event(reaction_payload(reaction="thumbsup")))
    assert result["status"] == "ok"
    assert result["feedback_prompt"] is None
    assert fake.calls == []


def test_reaction_removed_does_not_prompt(tmp_path, monkeypatch):
    store, _ = setup_tracked_vacancy(tmp_path, monkeypatch)
    fake = FakeDelivery()
    monkeypatch.setattr(cli, "_deliver_to_slack", fake)

    result = json.loads(cli.run_feedback_event(reaction_payload(event_type="reaction_removed")))
    assert result["status"] == "ok"
    assert result["feedback_prompt"] is None
    assert fake.calls == []
