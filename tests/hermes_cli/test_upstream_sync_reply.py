"""Tests for upstream-sync operator decision-reply detection (Task 1)."""

import json

import pytest

from hermes_cli.upstream_sync_reply import (
    parse_upstream_sync_decision_reply,
    has_pending_upstream_decision,
)


class TestParseDecisionReply:
    def test_parses_three_merge_both(self):
        result = parse_upstream_sync_decision_reply(
            "1: merge both, 2: merge both, 3: merge both"
        )
        assert result == {1: "merge both", 2: "merge both", 3: "merge both"}

    def test_parses_mixed_decisions(self):
        result = parse_upstream_sync_decision_reply(
            "1: keep local, 2: take upstream, 3: merge both"
        )
        assert result == {1: "keep local", 2: "take upstream", 3: "merge both"}

    def test_parses_newline_separated(self):
        result = parse_upstream_sync_decision_reply(
            "1: merge both\n2: merge both\n3: keep local"
        )
        assert result == {1: "merge both", 2: "merge both", 3: "keep local"}

    def test_case_and_whitespace_insensitive(self):
        result = parse_upstream_sync_decision_reply(
            "  1 :  Merge Both , 2:KEEP LOCAL "
        )
        assert result == {1: "merge both", 2: "keep local"}

    def test_ignores_surrounding_prose_but_keeps_decisions(self):
        result = parse_upstream_sync_decision_reply(
            "ok my decisions are 1: merge both and 2: take upstream thanks"
        )
        assert result == {1: "merge both", 2: "take upstream"}

    def test_plain_text_returns_none(self):
        assert parse_upstream_sync_decision_reply("please rebase everything") is None

    def test_empty_returns_none(self):
        assert parse_upstream_sync_decision_reply("") is None
        assert parse_upstream_sync_decision_reply(None) is None

    def test_number_without_valid_option_returns_none(self):
        assert parse_upstream_sync_decision_reply("1: do whatever, 2: yolo") is None

    def test_invalid_option_among_valid_drops_invalid(self):
        # A recognizable decision-set with one bad option keeps the valid ones.
        result = parse_upstream_sync_decision_reply(
            "1: merge both, 2: whatever you think"
        )
        assert result == {1: "merge both"}


class TestHasPendingUpstreamDecision:
    def _write_pending(self, tmp_path, status):
        state_dir = tmp_path / "upstream-sync"
        state_dir.mkdir()
        (state_dir / "pending.json").write_text(
            json.dumps({"schema": "upstream-sync-pending/v1", "status": status})
        )
        return state_dir

    def test_true_when_awaiting_decision(self, tmp_path):
        state_dir = self._write_pending(tmp_path, "awaiting_decision")
        assert has_pending_upstream_decision(state_dir) is True

    def test_false_when_other_status(self, tmp_path):
        state_dir = self._write_pending(tmp_path, "applied")
        assert has_pending_upstream_decision(state_dir) is False

    def test_false_when_missing(self, tmp_path):
        assert has_pending_upstream_decision(tmp_path / "nope") is False

    def test_false_on_malformed_json(self, tmp_path):
        state_dir = tmp_path / "upstream-sync"
        state_dir.mkdir()
        (state_dir / "pending.json").write_text("{not json")
        assert has_pending_upstream_decision(state_dir) is False
