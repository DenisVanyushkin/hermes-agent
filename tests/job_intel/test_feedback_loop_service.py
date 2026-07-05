from __future__ import annotations

from job_intel.crm_service import CRMService
from job_intel.feedback_service import (
    CLARIFICATION_TEXT,
    FEEDBACK_PROMPT_TEXT,
    FeedbackLoopService,
    classify_feedback,
    parse_feedback_reply,
)
from job_intel.store import JobIntelStore


class FakeDeliverer:
    def __init__(self):
        self.sent: list[tuple[str, str, str | None]] = []
        self.counter = 0

    def __call__(self, message: str, channel: str, thread_ts: str | None) -> str:
        self.counter += 1
        self.sent.append((message, channel, thread_ts))
        return f"1760000000.{1000 + self.counter}"


def make_service(tmp_path, with_opportunity: bool = True):
    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    crm = CRMService.from_store(store)
    deliverer = FakeDeliverer()
    service = FeedbackLoopService(store=store, crm=crm, deliver=deliverer)
    opportunity_id = None
    if with_opportunity:
        opportunity_id = crm.repo.create_opportunity(
            company="Acme",
            company_normalized="acme",
            title="Head of Product",
            title_normalized="head of product",
            location="Remote",
            source="linkedin",
            canonical_url="https://example.com/jobs/acme-hop",
            status="notified",
        )
        crm.repo.link_slack_message_to_opportunity(
            opportunity_id=opportunity_id,
            slack_channel_id="C123",
            slack_message_ts="1760000000.100",
            slack_thread_ts=None,
        )
    return store, crm, deliverer, service, opportunity_id


# --- Slice 3: prompt -------------------------------------------------------


def test_negative_reaction_creates_single_thread_prompt(tmp_path):
    store, crm, deliverer, service, opportunity_id = make_service(tmp_path)
    result = service.handle_negative_reaction(
        slack_channel_id="C123",
        slack_message_ts="1760000000.100",
        user_id="U1",
        reaction="thumbsdown",
    )
    assert result["status"] == "prompted"
    assert result["opportunity_id"] == opportunity_id
    assert len(deliverer.sent) == 1
    message, channel, thread_ts = deliverer.sent[0]
    assert message == FEEDBACK_PROMPT_TEXT
    assert channel == "C123"
    assert thread_ts == "1760000000.100"  # thread reply, not a channel message

    event = store.get_feedback_event(result["feedback_event_id"])
    assert event["prompt_message_ts"]
    assert event["slack_thread_ts"] == "1760000000.100"


def test_repeated_reaction_reuses_existing_prompt(tmp_path):
    store, crm, deliverer, service, _ = make_service(tmp_path)
    first = service.handle_negative_reaction(
        slack_channel_id="C123", slack_message_ts="1760000000.100", user_id="U1", reaction="-1"
    )
    second = service.handle_negative_reaction(
        slack_channel_id="C123", slack_message_ts="1760000000.100", user_id="U1", reaction="-1"
    )
    assert second["status"] == "reused"
    assert second["feedback_event_id"] == first["feedback_event_id"]
    assert len(deliverer.sent) == 1


def test_unresolved_slack_message_is_handled_safely(tmp_path):
    store, crm, deliverer, service, _ = make_service(tmp_path, with_opportunity=False)
    result = service.handle_negative_reaction(
        slack_channel_id="C999", slack_message_ts="42.42", user_id="U1", reaction="x"
    )
    assert result["status"] == "prompted"
    assert result["opportunity_id"] is None
    event = store.get_feedback_event(result["feedback_event_id"])
    assert event["opportunity_id"] is None


# --- Slice 4: parser -------------------------------------------------------


def test_numeric_reply_maps_to_categories():
    parsed = parse_feedback_reply("2 3")
    assert parsed.category_codes == [
        "seniority_scope_mismatch",
        "location_or_work_format_blocker",
    ]
    assert parsed.recognized


def test_mixed_numbers_and_free_text():
    parsed = parse_feedback_reply("2 3, нет P&L и onsite only")
    assert "seniority_scope_mismatch" in parsed.category_codes
    assert "location_or_work_format_blocker" in parsed.category_codes
    assert "no_pnl_ownership" in parsed.detail_codes
    assert "onsite_required" in parsed.detail_codes
    assert parsed.free_text == "2 3, нет P&L и onsite only"


def test_verbatim_reason_code_reply():
    parsed = parse_feedback_reply("too_junior_title")
    assert parsed.detail_codes == ["too_junior_title"]
    assert parsed.category_codes == ["seniority_scope_mismatch"]


def test_russian_phrases():
    assert "no_remote" in parse_feedback_reply("Нет удаленки").detail_codes
    assert "company_reputation_risk" in parse_feedback_reply("Компания мутная").detail_codes
    assert "duplicate" in parse_feedback_reply("Дубль, уже видел").detail_codes
    junior = parse_feedback_reply("слишком junior, нет P&L")
    assert "too_junior_title" in junior.detail_codes
    assert "no_pnl_ownership" in junior.detail_codes


def test_english_phrases():
    parsed = parse_feedback_reply("no remote, too junior, bad parse")
    assert "no_remote" in parsed.detail_codes
    assert "too_junior_title" in parsed.detail_codes
    assert "bad_parse" in parsed.detail_codes


# --- Slice 5: attribution --------------------------------------------------


def test_no_remote_attributes_to_work_format_not_company():
    classification = classify_feedback(parse_feedback_reply("Нет удаленки"))
    assert "work_format" in classification["attribution_targets"]
    assert "company" not in classification["attribution_targets"]
    assert classification["applies_to_company"] is False
    assert classification["hard_blocker"] is True


def test_company_shady_attributes_to_company():
    classification = classify_feedback(parse_feedback_reply("компания мутная"))
    assert "company" in classification["attribution_targets"]
    assert classification["applies_to_company"] is True


def test_junior_no_pnl_attributes_to_seniority_not_company():
    classification = classify_feedback(parse_feedback_reply("слишком junior, нет P&L"))
    assert "seniority_scope" in classification["attribution_targets"]
    assert "company" not in classification["attribution_targets"]
    assert classification["applies_to_role"] is True
    assert "PnL_ownership" in classification["scoring_features_impacted"]


def test_duplicate_attributes_to_data_quality_not_preference():
    classification = classify_feedback(parse_feedback_reply("дубль"))
    assert {"source", "parser", "dedup"} <= set(classification["attribution_targets"])
    assert classification["data_quality_only"] is True
    assert classification["scoring_features_impacted"] == []


# --- Slices 4+6: end-to-end reply handling ---------------------------------


def start_feedback(service):
    return service.handle_negative_reaction(
        slack_channel_id="C123", slack_message_ts="1760000000.100", user_id="U1", reaction="-1"
    )


def test_reply_classifies_updates_state_and_confirms(tmp_path):
    store, crm, deliverer, service, opportunity_id = make_service(tmp_path)
    started = start_feedback(service)
    result = service.handle_thread_reply(
        slack_channel_id="C123",
        slack_thread_ts="1760000000.100",
        user_id="U1",
        text="2 3, нет P&L и onsite only",
    )
    assert result["status"] == "classified"
    event = store.get_feedback_event(started["feedback_event_id"])
    assert event["status"] == "classified"
    assert "no_pnl_ownership" in event["reason_detail_codes"]
    assert event["free_text"] == "2 3, нет P&L и onsite only"
    # hard blocker -> declined_by_me, but company must not be flagged
    assert crm.get_opportunity(opportunity_id)["status"] == "declined_by_me"
    assert event["applies_to_company"] == 0
    # confirmation posted in thread
    assert any("Записал negative feedback" in message for message, _, _ in deliverer.sent)


def test_data_quality_reply_does_not_reject_opportunity(tmp_path):
    store, crm, deliverer, service, opportunity_id = make_service(tmp_path)
    start_feedback(service)
    result = service.handle_thread_reply(
        slack_channel_id="C123",
        slack_thread_ts="1760000000.100",
        user_id="U1",
        text="плохой парсинг, мусор",
    )
    assert result["status"] == "classified"
    assert crm.get_opportunity(opportunity_id)["status"] == "notified"  # unchanged
    tasks = store.connect(read_only=True).execute(
        "SELECT task_type FROM opportunity_tasks WHERE opportunity_id=?", (opportunity_id,)
    ).fetchall()
    assert any(row[0] == "reenrich_opportunity" for row in tasks)


def test_maybe_later_goes_on_hold_not_declined(tmp_path):
    store, crm, deliverer, service, opportunity_id = make_service(tmp_path)
    start_feedback(service)
    service.handle_thread_reply(
        slack_channel_id="C123",
        slack_thread_ts="1760000000.100",
        user_id="U1",
        text="не сейчас, позже",
    )
    assert crm.get_opportunity(opportunity_id)["status"] == "on_hold"


def test_unrecognized_reply_asks_clarification_once(tmp_path):
    store, crm, deliverer, service, _ = make_service(tmp_path)
    started = start_feedback(service)
    first = service.handle_thread_reply(
        slack_channel_id="C123", slack_thread_ts="1760000000.100", user_id="U1", text="ну такое"
    )
    assert first["status"] == "clarification_requested"
    assert any(message == CLARIFICATION_TEXT for message, _, _ in deliverer.sent)
    # second unrecognized reply is stored, no second clarification
    second = service.handle_thread_reply(
        slack_channel_id="C123", slack_thread_ts="1760000000.100", user_id="U1", text="все равно не то"
    )
    assert second["status"] == "classified"
    event = store.get_feedback_event(started["feedback_event_id"])
    assert event["needs_manual_review"] == 1
    assert "ну такое" in event["free_text"] and "все равно не то" in event["free_text"]
    clarifications = [message for message, _, _ in deliverer.sent if message == CLARIFICATION_TEXT]
    assert len(clarifications) == 1


def test_reply_outside_feedback_thread_is_ignored(tmp_path):
    store, crm, deliverer, service, _ = make_service(tmp_path)
    result = service.handle_thread_reply(
        slack_channel_id="C123", slack_thread_ts="9999.9999", user_id="U1", text="2"
    )
    assert result["status"] == "not_feedback_thread"
