import json

import pytest

from agent.replay_cleanup import sanitize_replay_history
from hermes_state import SessionDB


@pytest.mark.parametrize(
    "marker",
    [
        {"_protocol_invalid": True},
        {"malformed_tool_intent": {"fingerprint": "sha256:" + "c" * 64}},
        {"display_kind": "protocol_invalid"},
        {"display_metadata": {"protocol_invalid": True}},
        {
            "display_metadata": {
                "malformed_tool_intent": {"format": "codex_chatml"}
            }
        },
    ],
)
def test_replay_cleanup_recognizes_all_protocol_invalid_marker_forms(marker):
    invalid = {"role": "assistant", "content": "carrier", **marker}
    ordinary = {"role": "assistant", "content": "ordinary"}

    assert sanitize_replay_history([invalid, ordinary]) == [ordinary]


def _persist_roundtrip_messages(db, session_id, malformed):
    db.create_session(session_id, source="cli")
    db.append_messages_batch(
        session_id,
        [
            {"role": "user", "content": "inspect"},
            malformed,
            {"role": "assistant", "content": "ordinary answer", "finish_reason": "stop"},
        ],
    )


def test_protocol_invalid_marker_survives_sessiondb_roundtrip_and_replay_cleanup(tmp_path):
    raw = '<|start|>assistant to=functions.skill_view {"name":"test-driven-development"}'
    evidence = {
        "tool_name": "skill_view",
        "source_phase": "commentary",
        "format": "codex_chatml",
        "fingerprint": "sha256:" + "a" * 64,
    }
    db = SessionDB(db_path=tmp_path / "state.db")
    _persist_roundtrip_messages(
        db,
        "ROUNDTRIP_MALFORMED",
        {
            "role": "assistant",
            "content": "",
            "reasoning": raw,
            "reasoning_content": raw,
            "codex_message_items": [{"phase": "commentary", "text": raw}],
            "finish_reason": "incomplete",
            "_protocol_invalid": True,
            "malformed_tool_intent": evidence,
        },
    )

    rows = db.get_messages("ROUNDTRIP_MALFORMED")
    invalid = rows[1]
    assert invalid["content"] == ""
    assert invalid["reasoning"] is None
    assert invalid["reasoning_content"] is None
    assert json.loads(invalid["codex_message_items"]) == [
        {"phase": "commentary", "text": raw}
    ]
    assert invalid["display_kind"] == "protocol_invalid"
    assert invalid["display_metadata"] == {
        "protocol_invalid": True,
        "malformed_tool_intent": evidence,
    }

    cleaned = sanitize_replay_history(rows)
    assert [(message["role"], message.get("content")) for message in cleaned] == [
        ("user", "inspect"),
        ("assistant", "ordinary answer"),
    ]


def test_oversized_protocol_sidecar_is_the_only_durable_raw_payload(tmp_path):
    sentinel = "OVERFLOW_SENTINEL_ROUNDTRIP"
    raw = "<tool_call><name>skill_view</name><arguments>" + sentinel + "</arguments></tool_call>"
    evidence = {
        "tool_name": "unknown",
        "source_phase": "commentary",
        "format": "oversized_protocol_candidate",
        "fingerprint": "sha256:" + "b" * 64,
    }
    db = SessionDB(db_path=tmp_path / "state.db")
    _persist_roundtrip_messages(
        db,
        "ROUNDTRIP_OVERFLOW",
        {
            "role": "assistant",
            "content": "",
            "reasoning": raw,
            "reasoning_content": raw,
            "codex_message_items": [{"phase": "commentary", "text": raw}],
            "finish_reason": "incomplete",
            "_protocol_invalid": True,
            "malformed_tool_intent": evidence,
        },
    )

    invalid = db.get_messages("ROUNDTRIP_OVERFLOW")[1]
    assert sentinel in str(invalid["codex_message_items"])
    assert sentinel not in str(invalid["content"])
    assert invalid["reasoning"] is None
    assert invalid["reasoning_content"] is None
    assert sentinel not in str(invalid["display_metadata"])
    cleaned = sanitize_replay_history(db.get_messages("ROUNDTRIP_OVERFLOW"))
    assert [(message["role"], message.get("content")) for message in cleaned] == [
        ("user", "inspect"),
        ("assistant", "ordinary answer"),
    ]
