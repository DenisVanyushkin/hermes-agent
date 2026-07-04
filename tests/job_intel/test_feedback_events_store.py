from __future__ import annotations

from job_intel.store import JobIntelStore


def make_store(tmp_path) -> JobIntelStore:
    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    return store


def test_bootstrap_creates_feedback_tables(tmp_path):
    store = make_store(tmp_path)
    tables = set(store.list_tables())
    assert "feedback_events" in tables
    assert "scoring_calibration_proposals" in tables
    assert "scoring_calibration_events" in tables


def test_create_feedback_event_from_synthetic_reaction(tmp_path):
    store = make_store(tmp_path)
    event_id = store.create_feedback_event(
        slack_channel_id="C123",
        slack_message_ts="1760000000.100",
        user_id="U1",
        reaction_type="thumbsdown",
        raw_payload_json={"reaction": "thumbsdown", "type": "reaction_added"},
    )
    event = store.get_feedback_event(event_id)
    assert event is not None
    assert event["polarity"] == "negative"
    assert event["status"] == "awaiting_reply"
    assert event["source"] == "slack_reaction_thread"
    assert event["reaction_type"] == "thumbsdown"


def test_feedback_event_stores_multiple_reason_codes_and_free_text(tmp_path):
    store = make_store(tmp_path)
    event_id = store.create_feedback_event(
        slack_channel_id="C123",
        slack_message_ts="1760000000.100",
        user_id="U1",
        reaction_type="x",
    )
    store.update_feedback_event(
        event_id,
        status="classified",
        reason_category_codes_json=["seniority_scope_mismatch", "location_or_work_format_blocker"],
        reason_detail_codes_json=["no_pnl_ownership", "onsite_required"],
        attribution_targets_json=["seniority_scope", "work_format"],
        free_text="нет P&L и onsite only",
        classifier_version="rules-v1",
        classifier_confidence=0.8,
        hard_blocker=True,
        applies_to_company=False,
    )
    event = store.get_feedback_event(event_id)
    assert event["reason_category_codes"] == [
        "seniority_scope_mismatch",
        "location_or_work_format_blocker",
    ]
    assert event["reason_detail_codes"] == ["no_pnl_ownership", "onsite_required"]
    assert event["attribution_targets"] == ["seniority_scope", "work_format"]
    assert event["free_text"] == "нет P&L и onsite only"
    assert event["hard_blocker"] == 1
    assert event["applies_to_company"] == 0


def test_fetch_feedback_events_by_opportunity(tmp_path):
    from job_intel.crm_repository import OpportunityRepository

    store = make_store(tmp_path)
    repo = OpportunityRepository(store)
    first = repo.create_opportunity(
        company="Acme",
        company_normalized="acme",
        title="Head of Product",
        title_normalized="head of product",
        location="Remote",
        source="linkedin",
        canonical_url="https://example.com/jobs/acme-hop",
        status="notified",
    )
    second = repo.create_opportunity(
        company="Globex",
        company_normalized="globex",
        title="CPO",
        title_normalized="cpo",
        location="Remote",
        source="linkedin",
        canonical_url="https://example.com/jobs/globex-cpo",
        status="notified",
    )
    for index, opportunity_id in enumerate((first, first, second)):
        store.create_feedback_event(
            slack_channel_id="C123",
            slack_message_ts=f"1760000000.{index}",
            user_id="U1",
            reaction_type="-1",
            opportunity_id=opportunity_id,
        )
    events = store.fetch_feedback_events(opportunity_id=first)
    assert len(events) == 2
    assert all(event["opportunity_id"] == first for event in events)


def test_find_feedback_event_awaiting_reply_by_thread(tmp_path):
    store = make_store(tmp_path)
    event_id = store.create_feedback_event(
        slack_channel_id="C123",
        slack_message_ts="1760000000.100",
        user_id="U1",
        reaction_type="-1",
        slack_thread_ts="1760000000.100",
    )
    found = store.find_feedback_event_awaiting_reply(
        slack_channel_id="C123", slack_thread_ts="1760000000.100"
    )
    assert found is not None and found["id"] == event_id
    store.update_feedback_event(event_id, status="classified")
    assert (
        store.find_feedback_event_awaiting_reply(
            slack_channel_id="C123", slack_thread_ts="1760000000.100"
        )
        is None
    )


def test_unsupported_field_rejected(tmp_path):
    store = make_store(tmp_path)
    event_id = store.create_feedback_event(
        slack_channel_id="C1",
        slack_message_ts="1.1",
        user_id="U1",
        reaction_type="-1",
    )
    try:
        store.update_feedback_event(event_id, nonexistent_field="x")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unsupported field")


def test_scoring_proposal_lifecycle(tmp_path):
    store = make_store(tmp_path)
    proposal_id = store.create_scoring_proposal(
        evidence_window_days=30,
        evidence={"total_negative_events": 7, "matched_patterns": [{"reason_code": "no_pnl_ownership", "count": 5}]},
        proposed_changes=[{"scoring_feature": "PnL_ownership", "current_value": 25, "proposed_value": 30}],
    )
    proposal = store.get_scoring_proposal(proposal_id)
    assert proposal["status"] == "proposed"
    assert proposal["evidence"]["total_negative_events"] == 7
    assert proposal["proposed_changes"][0]["scoring_feature"] == "PnL_ownership"

    store.update_scoring_proposal(proposal_id, status="applied", applied_at="2026-07-05T00:00:00+00:00")
    store.add_scoring_calibration_event(proposal_id=proposal_id, event_type="applied", actor="denis")
    events = store.fetch_scoring_calibration_events(proposal_id)
    assert [event["event_type"] for event in events] == ["applied"]
    assert store.get_scoring_proposal(proposal_id)["status"] == "applied"
