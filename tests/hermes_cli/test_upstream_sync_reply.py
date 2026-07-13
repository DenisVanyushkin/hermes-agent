"""Tests for upstream-sync operator decision-reply detection (Task 1)."""

import json
from pathlib import Path

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

    def test_parses_hyphenated_options(self):
        # The conflict report presents the skill canonical hyphenated tokens
        # (pending.json options: keep-local / take-upstream / merge-both); an
        # operator replying verbatim must be recognized. Regression: 2026-07-13.
        result = parse_upstream_sync_decision_reply(
            "1: merge-both, 2: merge-both, 3: merge-both, 4: merge-both, 5: merge-both"
        )
        assert result == {1: "merge both", 2: "merge both", 3: "merge both",
                          4: "merge both", 5: "merge both"}

    def test_parses_mixed_hyphenated(self):
        result = parse_upstream_sync_decision_reply(
            "1: keep-local, 2: take-upstream, 3: merge-both"
        )
        assert result == {1: "keep local", 2: "take upstream", 3: "merge both"}

    def test_parses_underscore_options(self):
        result = parse_upstream_sync_decision_reply("1: merge_both, 2: keep_local")
        assert result == {1: "merge both", 2: "keep local"}

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


class TestDefaultStateDir:
    def test_env_override_wins(self, monkeypatch):
        from hermes_cli.upstream_sync_reply import default_upstream_sync_state_dir
        monkeypatch.setenv("HERMES_SYNC_STATE_DIR", "/custom/state")
        assert default_upstream_sync_state_dir() == Path("/custom/state")

    def test_derives_from_hermes_home(self, monkeypatch):
        from hermes_cli.upstream_sync_reply import default_upstream_sync_state_dir
        monkeypatch.delenv("HERMES_SYNC_STATE_DIR", raising=False)
        monkeypatch.setenv("HERMES_HOME", "/home/hermes/.hermes")
        assert default_upstream_sync_state_dir() == Path(
            "/home/hermes/.hermes/sandboxes/docker/default/home/.hermes/state/upstream-sync"
        )


class TestBuildJobSpec:
    def _source(self):
        return {
            "platform": "slack",
            "chat_id": "C0B3X1E5SJZ",
            "thread_id": "1783420000.000",
            "user_id": "U123",
        }

    def test_spec_carries_reply_skill_and_role_pin(self):
        from hermes_cli.upstream_sync_reply import build_upstream_sync_decision_job_spec
        msg = "1: merge both, 2: merge both, 3: merge both"
        spec = build_upstream_sync_decision_job_spec(
            msg, self._source(), {1: "merge both", 2: "merge both", 3: "merge both"}
        )
        assert msg in spec["prompt"]
        assert spec["skills"] == ["upstream-sync"]
        assert spec["role"] == "engineer"
        assert spec["schedule"] == "1m"
        assert spec["deliver"] == "origin"

    def test_origin_routes_back_to_thread(self):
        from hermes_cli.upstream_sync_reply import build_upstream_sync_decision_job_spec
        spec = build_upstream_sync_decision_job_spec(
            "1: merge both", self._source(), {1: "merge both"}
        )
        origin = spec["origin"]
        assert origin["platform"] == "slack"
        assert origin["chat_id"] == "C0B3X1E5SJZ"
        assert origin["thread_id"] == "1783420000.000"

    def test_prompt_signals_mode_b_apply(self):
        from hermes_cli.upstream_sync_reply import build_upstream_sync_decision_job_spec
        spec = build_upstream_sync_decision_job_spec(
            "1: merge both", self._source(), {1: "merge both"}
        )
        low = spec["prompt"].lower()
        assert "pending.json" in low and "apply" in low


class TestHasPendingPermissionTolerance:
    def test_unreadable_but_existing_pending_is_treated_as_pending(self, tmp_path, monkeypatch):
        # Gateway runs as `hermes`; pending.json is written root:root 0600 by the
        # sandbox. The host can stat (exists) but not read it. Treat an existing
        # but unreadable pending.json as pending — the sandbox re-checks status.
        from hermes_cli import upstream_sync_reply as usr
        state_dir = tmp_path / "upstream-sync"
        state_dir.mkdir()
        pending = state_dir / "pending.json"
        pending.write_text('{"status": "awaiting_decision"}')

        real_read_text = usr.Path.read_text

        def fake_read_text(self, *a, **k):
            if self.name == "pending.json":
                raise PermissionError(13, "Permission denied")
            return real_read_text(self, *a, **k)

        monkeypatch.setattr(usr.Path, "read_text", fake_read_text)
        assert usr.has_pending_upstream_decision(state_dir) is True

    def test_missing_pending_still_false_under_permission_logic(self, tmp_path):
        from hermes_cli.upstream_sync_reply import has_pending_upstream_decision
        assert has_pending_upstream_decision(tmp_path / "nope") is False


class TestPlatformNormalization:
    def test_enum_repr_platform_normalized(self):
        from hermes_cli.upstream_sync_reply import build_upstream_sync_decision_job_spec
        src = {"platform": "Platform.SLACK", "chat_id": "C1", "thread_id": "T1", "user_id": "U1"}
        spec = build_upstream_sync_decision_job_spec("1: merge both", src, {1: "merge both"})
        assert spec["origin"]["platform"] == "slack"

    def test_plain_platform_unchanged(self):
        from hermes_cli.upstream_sync_reply import build_upstream_sync_decision_job_spec
        src = {"platform": "telegram", "chat_id": "C1", "thread_id": None, "user_id": "U1"}
        spec = build_upstream_sync_decision_job_spec("1: merge both", src, {1: "merge both"})
        assert spec["origin"]["platform"] == "telegram"


class TestReporterArgv:
    def test_builds_argv_with_thread_target(self):
        from hermes_cli.upstream_sync_reply import build_progress_reporter_argv
        origin = {"platform": "slack", "chat_id": "C1", "thread_id": "T1", "user_id": "U1"}
        argv = build_progress_reporter_argv(origin, repo="/repo", hermes_bin="/hb",
                                            script_path="/repo/scripts/r.py")
        assert argv is not None
        assert "/repo/scripts/r.py" in argv
        assert "--target" in argv and "slack:C1:T1" in argv
        assert "--repo" in argv and "/repo" in argv
        assert "--hermes-bin" in argv and "/hb" in argv

    def test_none_without_thread(self):
        from hermes_cli.upstream_sync_reply import build_progress_reporter_argv
        origin = {"platform": "slack", "chat_id": "C1", "thread_id": None, "user_id": "U1"}
        assert build_progress_reporter_argv(origin, repo="/r", hermes_bin="/hb",
                                            script_path="/s") is None

    def test_none_without_chat(self):
        from hermes_cli.upstream_sync_reply import build_progress_reporter_argv
        origin = {"platform": "slack", "chat_id": None, "thread_id": "T1", "user_id": "U1"}
        assert build_progress_reporter_argv(origin, repo="/r", hermes_bin="/hb",
                                            script_path="/s") is None
