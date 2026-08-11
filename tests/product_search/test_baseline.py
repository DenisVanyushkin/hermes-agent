from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

from job_intel.product_search.baseline import (
    BaselineInputs,
    aggregate_baseline,
    classify_outbound_path,
    extract_read_only_database_baseline,
    validate_public_baseline,
)


def test_baseline_counts_unique_delivery_user_outcomes_and_activated_opportunities() -> None:
    inputs = BaselineInputs(
        deliveries=(
            {"delivery_id": "d1", "opportunity_id": "o1", "company_id": "c1"},
            {"delivery_id": "d1", "opportunity_id": "o1", "company_id": "c1"},
            {"delivery_id": "d2", "opportunity_id": "o2", "company_id": "c1"},
            {"delivery_id": "d3", "opportunity_id": "o3", "company_id": "c2"},
        ),
        decisions=(
            {"event_id": "u1", "opportunity_id": "o1", "decision": "Pursue", "actor": "user"},
            {"event_id": "u2", "opportunity_id": "o2", "decision": "Investigate", "actor": "user"},
            {"event_id": "m1", "opportunity_id": "o3", "decision": "Priority", "actor": "machine"},
        ),
        actions=(
            {"action_id": "a1", "opportunity_id": "o1", "status": "completed"},
            {"action_id": "a1", "opportunity_id": "o1", "status": "completed"},
            {"action_id": "a2", "opportunity_id": "o2", "status": "open"},
        ),
        attention_sessions=(
            {"session_id": "s1", "state": "completed", "measured_seconds": 1800},
            {"session_id": "s2", "state": "abandoned", "measured_seconds": None},
        ),
    )

    summary = aggregate_baseline(inputs)

    assert summary == {
        "unique_deliveries": 3,
        "duplicate_delivery_observations": 1,
        "unique_companies": 2,
        "positive_user_decisions": 2,
        "completed_actions": 1,
        "activated_opportunities": 1,
        "completed_attention_sessions": 1,
        "incomplete_attention_sessions": 1,
        "actual_completed_review_minutes": 30.0,
        "activated_opportunities_per_60_review_minutes": 2.0,
    }


def test_unknown_or_zero_attention_is_not_computable() -> None:
    for sessions in (
        (),
        ({"session_id": "s1", "state": "completed", "measured_seconds": 0},),
        ({"session_id": "s1", "state": "abandoned", "measured_seconds": None},),
    ):
        summary = aggregate_baseline(BaselineInputs(attention_sessions=sessions))
        assert summary["actual_completed_review_minutes"] is None
        assert summary["activated_opportunities_per_60_review_minutes"] is None


def test_machine_verdict_never_counts_as_user_outcome() -> None:
    summary = aggregate_baseline(
        BaselineInputs(
            decisions=(
                {"event_id": "m1", "opportunity_id": "o1", "decision": "Priority", "actor": "machine"},
                {"event_id": "m2", "opportunity_id": "o2", "decision": "Investigate", "actor": "machine"},
            ),
            actions=({"action_id": "a1", "opportunity_id": "o1", "status": "completed"},),
            attention_sessions=({"session_id": "s1", "state": "completed", "measured_seconds": 600},),
        )
    )

    assert summary["positive_user_decisions"] == 0
    assert summary["activated_opportunities"] == 0
    assert summary["activated_opportunities_per_60_review_minutes"] == 0.0


def test_public_baseline_rejects_private_or_message_content_recursively() -> None:
    forbidden = (
        {"message_body": "private"},
        {"nested": {"candidate_facts": "private"}},
        {"rows": [{"token": "xoxb-secret"}]},
        {"application_artifact": "/tmp/resume.pdf"},
        {"user_notes": "private"},
    )

    for payload in forbidden:
        try:
            validate_public_baseline(payload)
        except ValueError as exc:
            assert "forbidden baseline field" in str(exc)
        else:
            raise AssertionError(f"private payload was accepted: {payload}")


def test_outbound_paths_are_classified_without_conflating_transports() -> None:
    assert classify_outbound_path("typed envelope to ProductSearchSlackPublisher") == "typed_product_search"
    assert classify_outbound_path("SlackAdapter.send(channel, text)") == "live_adapter_generic"
    assert classify_outbound_path("_standalone_send(channel, text)") == "standalone_sender"
    assert classify_outbound_path("requests.post(JOB_INTEL_SLACK_WEBHOOK_URL)") == "webhook"
    assert classify_outbound_path("client.chat_postMessage(channel=channel)") == "raw_slack_api"
    assert classify_outbound_path("_deliver_to_slack daily vacancy_card") == "legacy_job_intel"
    assert classify_outbound_path("custom sender with no known marker") == "unknown"


def test_database_extractor_is_read_only_and_emits_only_aggregates(tmp_path: Path) -> None:
    db_path = tmp_path / "job-intel.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE notifications (
                id INTEGER PRIMARY KEY,
                message_type TEXT,
                delivery_status TEXT,
                sent_at TEXT,
                body TEXT,
                card_key TEXT
            );
            INSERT INTO notifications VALUES
              (1, 'vacancy_card', 'sent', '2026-08-01T00:00:00+00:00', 'secret one', 'v1'),
              (2, 'vacancy_card', 'sent', '2026-08-02T00:00:00+00:00', 'secret duplicate', 'v1'),
              (3, 'daily_digest', 'sent', '2026-08-03T00:00:00+00:00', 'secret digest', 'd1'),
              (4, 'vacancy_card', 'failed', '2026-08-04T00:00:00+00:00', 'secret failed', 'v2');
            """
        )
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    result = extract_read_only_database_baseline(
        db_path,
        since="2026-07-01T00:00:00+00:00",
        until="2026-08-11T00:00:00+00:00",
    )

    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before
    assert result["notification_counts"] == {"daily_digest": 1, "vacancy_card": 2}
    assert result["unique_vacancy_cards"] == 1
    assert result["source_snapshot"]["sha256"] == before
    serialized = json.dumps(result)
    assert "secret" not in serialized
    assert str(db_path) not in serialized
    validate_public_baseline(result)


def test_committed_baseline_preserves_audit_denominators_without_private_content() -> None:
    root = Path(__file__).resolve().parents[2]
    payload = json.loads(
        (root / "docs/evidence/product-search-baseline/baseline-summary.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload["slack_audit"]["root_total"] == 634
    assert payload["slack_audit"]["reply_total"] == 336
    assert payload["slack_audit"]["root_categories"]["individual_vacancy_card"] == 487
    assert payload["database_audit"]["vacancy_cards_accounted"] == 104
    assert payload["attention"]["status"] == "not_computable"
    assert payload["outbound_inventory"]["typed_product_search"] == []
    validate_public_baseline(payload)
