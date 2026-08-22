from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli.upstream_sync_reply import (
    has_pending_upstream_invariant_ack,
    parse_upstream_sync_ack_reply,
    record_invariant_ack,
)


def _armed(tmp_path: Path) -> Path:
    (tmp_path / "invariants-pending.json").write_text(json.dumps({
        "schema": "upstream-sync-invariants-pending/v1",
        "version": 1,
        "status": "awaiting_ack",
        "merge_scope": {"local_parent": "a", "upstream_parent": "b", "merge_base": "c"},
        "origin": {"platform": "slack", "chat_id": "C1", "thread_id": "T1", "user_id": "U1"},
        "findings": [{
            "finding_id": "INV-abc123456789",
            "kind": "lost_definition",
            "path": "mod.py",
            "symbol": "gone",
            "fingerprint": {"sha256": "f" * 64},
        }],
        "receipts": [],
    }))
    return tmp_path


def test_ack_parser_rejects_prose_times_urls_and_path_symbol_forms():
    assert parse_upstream_sync_ack_reply("ack, see you at 14:00") is None
    assert parse_upstream_sync_ack_reply("ack https://github.com/a/b") is None
    assert parse_upstream_sync_ack_reply("ack mod.py:B — это правильная команда?") is None
    assert parse_upstream_sync_ack_reply("ack INV-abc123456789") == "INV-abc123456789"


def test_unarmed_or_malformed_state_is_fail_closed(tmp_path):
    assert has_pending_upstream_invariant_ack(tmp_path) is False
    (tmp_path / "invariants-pending.json").write_text("not json")
    assert has_pending_upstream_invariant_ack(tmp_path) is False


def test_receipt_is_fingerprint_bound_and_requests_one_host_action(tmp_path):
    state = _armed(tmp_path)
    source = {"platform": "slack", "chat_id": "C1", "thread_id": "T1", "user_id": "U1"}
    out = record_invariant_ack(state, "INV-abc123456789", source)
    assert out["requested"] is True
    req = json.loads((state / "finalize-request.json").read_text())
    assert req["action"] == "ack-invariant"
    assert req["receipt"]["fingerprint_sha256"] == "f" * 64
    duplicate = record_invariant_ack(state, "INV-abc123456789", source)
    assert duplicate["duplicate"] is True


def test_hard_findings_cannot_be_acknowledged(tmp_path):
    state = _armed(tmp_path)
    data = json.loads((state / "invariants-pending.json").read_text())
    data["findings"][0]["kind"] = "unparseable"
    (state / "invariants-pending.json").write_text(json.dumps(data))
    out = record_invariant_ack(state, "INV-abc123456789", {"platform": "slack", "chat_id": "C1", "thread_id": "T1", "user_id": "U1"})
    assert out["requested"] is False
    assert "hard" in out["reason"]
