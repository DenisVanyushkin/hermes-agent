"""Slack side of the host-owned upstream-sync: posting and message composition.

The composers are pure (dict in, str out) so every operator-facing text is
asserted here rather than discovered in the channel. Posting is exercised
against an injected transport — no network in tests.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts import upstream_sync_slack as slack  # noqa: E402

SCRIPT = REPO_ROOT / "scripts" / "upstream_sync_slack.py"


class _FakeHTTP:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, payload, token):
        self.calls.append((url, payload, token))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class TestPost:
    def test_posts_to_channel_and_returns_ts(self):
        http = _FakeHTTP([{"ok": True, "ts": "1786.001"}])
        ts = slack.post("C123", "hello", token="xoxb-t", http=http)
        assert ts == "1786.001"
        url, payload, token = http.calls[0]
        assert url.endswith("/chat.postMessage")
        assert payload["channel"] == "C123" and payload["text"] == "hello"
        assert "thread_ts" not in payload
        assert token == "xoxb-t"

    def test_thread_reply_carries_thread_ts(self):
        http = _FakeHTTP([{"ok": True, "ts": "1786.002"}])
        slack.post("C123", "reply", thread_ts="1786.001", token="t", http=http)
        assert http.calls[0][1]["thread_ts"] == "1786.001"

    def test_api_error_raises_with_slack_reason(self):
        http = _FakeHTTP([{"ok": False, "error": "channel_not_found"}])
        with pytest.raises(slack.SlackError, match="channel_not_found"):
            slack.post("C123", "x", token="t", http=http)

    def test_transport_error_raises_slack_error(self):
        http = _FakeHTTP([OSError("boom")])
        with pytest.raises(slack.SlackError, match="boom"):
            slack.post("C123", "x", token="t", http=http)

    def test_missing_token_raises_before_any_call(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        http = _FakeHTTP([])
        with pytest.raises(slack.SlackError, match="token"):
            slack.post("C123", "x", env_file=tmp_path / "missing.env", http=http)
        assert http.calls == []

    def test_token_is_read_from_the_env_file_like_the_finalizer_does(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        env = tmp_path / ".env"
        env.write_text('OTHER=1\nexport SLACK_BOT_TOKEN="xoxb-from-file"\n')
        http = _FakeHTTP([{"ok": True, "ts": "1"}])
        slack.post("C1", "x", env_file=env, http=http)
        assert http.calls[0][2] == "xoxb-from-file"


def _pending(**over):
    base = {
        "schema": "upstream-sync-pending/v1",
        "status": "awaiting_decision",
        "local_head": "aaaa1111",
        "upstream_head": "bbbb2222",
        "upstream_ahead": 1154,
        "local_ahead": 1162,
        "features": [
            {"id": "F1", "status": "decided", "source": "policy", "decision": "merge-both",
             "files": [".gitignore"], "local_subjects": ["chore: gitignore"]},
            {"id": "F2", "status": "pre_decided", "source": "memory", "decision": "merge-both",
             "files": ["agent/tool_executor.py"], "local_subjects": ["feat(roles): observe-warn"]},
            {"id": "F3", "status": "awaiting_decision", "source": None, "decision": None,
             "files": ["tools/approval.py"], "local_subjects": ["feat: smart cron approvals"]},
        ],
    }
    base.update(over)
    return base


class TestReportText:
    def test_asks_only_for_awaiting_features_and_lists_the_rest_as_auto(self):
        text = slack.report_text(_pending())
        assert "F3" in text and "tools/approval.py" in text
        assert "F3: merge-both" in text                       # exact reply format is shown
        # auto-decided ones are listed, but not asked for
        assert ".gitignore" in text and "agent/tool_executor.py" in text
        assert "policy" in text and "memory" in text
        ask_section = text.split("decision needed")[1]
        assert "F1" not in ask_section and "F2" not in ask_section

    def test_nothing_to_ask_says_so(self):
        p = _pending()
        p["features"][2].update(status="decided", source="policy", decision="merge-both")
        p["status"] = "auto_apply"
        text = slack.report_text(p)
        assert "no decision needed" in text.lower() or "applying automatically" in text.lower()
        assert "F3: merge-both" not in text

    def test_counts_and_heads_are_present(self):
        text = slack.report_text(_pending())
        assert "1,154" in text or "1154" in text
        assert "bbbb2222"[:8] in text


class TestOutcomeTexts:
    def _prep(self):
        return {"status": "ready", "local_base": "aaaa1111", "upstream_head": "bbbb2222",
                "conflicts": ["a.py", "b.py", "c.py"],
                "auto_resolved": ["a.py"],
                "llm_resolved": ["b.py"],
                "needs_manual": [{"path": "c.py", "resolved_hunks": 1, "remaining_hunks": 2}],
                "unresolved": []}

    def test_applied_text_summarizes_who_resolved_what(self):
        result = {"status": "ok", "backup_ref": "backup/pre-upstream-sync-20260815-1",
                  "finished_at": "2026-08-15T15:43:01+00:00"}
        text = slack.applied_text(self._prep(), result)
        assert "backup/pre-upstream-sync-20260815-1" in text
        assert "a.py" in text and "b.py" in text
        assert "tests" in text.lower() and "smoketest" in text.lower()

    def test_failed_text_names_stage_and_what_is_left(self):
        prep = self._prep()
        prep["unresolved"] = [{"path": "c.py", "reason": "markers remained after 2 attempts"}]
        result = {"status": "failed", "failed_stage": "resolve", "detail": "x",
                  "backup_ref": ""}
        text = slack.failed_text(prep, result, scratch="/state/scratch")
        assert "resolve" in text
        assert "c.py" in text and "markers remained" in text
        assert "/state/scratch" in text                      # where the human picks it up
        assert "kept" in text.lower() or "preserved" in text.lower()

    def test_reminder_text_is_self_contained(self):
        text = slack.reminder_text(_pending())
        assert "F3" in text and "tools/approval.py" in text and "F3: merge-both" in text


class TestCli:
    def test_cli_post_prints_ts_using_a_command_override(self, tmp_path, monkeypatch):
        # HERMES_SYNC_SLACK_CMD replaces the transport: the command gets the
        # JSON payload on stdin and prints the ts. Bash callers and tests use it.
        fake = tmp_path / "fake-slack.sh"
        fake.write_text("#!/usr/bin/env bash\ncat > \"$0.last\"\necho 1786.999\n")
        fake.chmod(0o755)
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "post", "--channel", "C1", "--text", "hi",
             "--thread", "1786.001"],
            env={"PATH": "/usr/bin:/bin", "HERMES_SYNC_SLACK_CMD": str(fake)},
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "1786.999"
        sent = json.loads(Path(str(fake) + ".last").read_text())
        assert sent == {"channel": "C1", "text": "hi", "thread_ts": "1786.001"}
