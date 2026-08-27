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


# ---------------------------------------------------------------------------
# Gate triage: the proposal the operator answers with one word
# ---------------------------------------------------------------------------


def _triage(proposals, status="awaiting_triage"):
    return {"schema": "upstream-sync-triage/v1", "status": status, "merge_sha": "deadbeefcafe",
            "proposals": proposals}


def _prop(**kw):
    base = {"test_file": "tests/gateway/test_voice.py", "test_ids": ["tests/gateway/test_voice.py::test_stt"],
            "test_kind": "fork", "verdict": "test_outdated",
            "explanation": "upstream added two parameters to transcribe_audio.",
            "assertion_delta": "same assertions, new call signature",
            "patch": "def test_stt():\n    assert transcribe('a', 'm', 's')\n",
            "excerpt": "E   TypeError: transcribe_audio() missing 2 required positional arguments",
            "modules_under_test": ["gateway/voice.py"], "rejected_reason": ""}
    base.update(kw)
    return base


def _gate_failures(**over):
    base = {
        "schema_version": "upstream-sync-gate-failures/v2",
        "common_path": [],
        "post_only_path": [],
        "pre_existing": [],
        "unknown": [],
        "unreadable_runs": [],
        "blocking_failures": [],
    }
    base.update(over)
    return base


class TestGateReport:
    @pytest.mark.parametrize(
        ("source", "stage", "label"),
        [
            ("baseline", "collect", "baseline (before merge)"),
            ("merged", "collect", "post (after merge)"),
            ("upstream_parent", "collect", "upstream-parent probe"),
            ("upstream_parent", "probe", "upstream-parent probe"),
        ],
    )
    def test_unreadable_run_is_infrastructure_unknown_not_merge_regression(
        self, source, stage, label
    ):
        text = slack.gate_report_text(
            _gate_failures(
                unreadable_runs=[{"source": source, "stage": stage}],
                unknown=[
                    {
                        "path": "tests/broken.py",
                        "nodeid": "tests/broken.py::test_broken",
                        "source": source,
                        "stage": stage,
                    }
                ],
            )
        )

        assert label in text
        assert "infrastructure" in text.lower()
        assert "not a merge regression" in text.lower()
        assert "tests/broken.py::test_broken" in text

    def test_clean_run_is_distinct_from_unreadable_run(self):
        clean = slack.gate_report_text(_gate_failures())
        unreadable = slack.gate_report_text(
            _gate_failures(unreadable_runs=[{"source": "merged", "stage": "collect"}])
        )

        assert "clean" in clean.lower()
        assert "unreadable" not in clean.lower()
        assert "unreadable" in unreadable.lower()
        assert clean != unreadable

    def test_blocking_buckets_have_separate_counts_and_run_labels(self):
        common = {
            "path": "tests/common.py",
            "nodeid": "tests/common.py::test_common",
            "classification": "fork_regression",
        }
        post_only = [
            {
                "path": "tests/upstream.py",
                "nodeid": "tests/upstream.py::test_one",
                "classification": "upstream_red_admission_failure",
            },
            {
                "path": "tests/upstream.py",
                "nodeid": "tests/upstream.py::test_two",
                "classification": "fork_compatibility_failure",
            },
        ]
        text = slack.gate_report_text(
            _gate_failures(
                common_path=[common],
                post_only_path=post_only,
                blocking_failures=[common, *post_only],
                pre_existing=[
                    {
                        "path": "tests/old.py",
                        "nodeid": "tests/old.py::test_old",
                        "classification": "pre_existing_failure",
                    }
                ],
            )
        )

        assert "baseline (before merge)" in text
        assert "post (after merge)" in text
        assert "common_path: 1" in text
        assert "post_only_path: 2" in text
        assert "pre_existing: 1" in text
        assert "tests/upstream.py::test_one" in text
        assert "fork_regression" in text
        assert "upstream_red_admission_failure" in text

    def test_report_prints_blocking_class_breakdown_and_unknown_class(self):
        text = slack.gate_report_text(
            _gate_failures(
                blocking_failures=[
                    {
                        "path": "tests/common.py",
                        "nodeid": "tests/common.py::test_common",
                        "classification": "fork_regression",
                    },
                    {
                        "path": "tests/common.py",
                        "nodeid": "tests/common.py::test_future",
                        "classification": "future_failure_class",
                    },
                ],
                blocking_failures_by_class={
                    "fork_regression": 1,
                    "future_failure_class": 1,
                },
                unknown_blocking_classifications=["future_failure_class"],
            )
        )

        assert "blocking_failures: 2" in text
        assert "`fork_regression`: 1" in text
        assert "`future_failure_class`: 1" in text
        assert "unknown blocking classification" in text.lower()


class TestTriageText:
    def test_shows_the_verdict_explanation_and_patch(self):
        text = slack.triage_text(_triage([_prop()]))
        assert "tests/gateway/test_voice.py" in text
        assert "test_outdated" in text
        assert "transcribe_audio" in text
        assert "same assertions, new call signature" in text
        assert "```" in text and "def test_stt" in text

    def test_spells_out_the_exact_words_the_operator_must_reply(self):
        """The reply parser matches the whole message, so a paraphrase does
        nothing. The instruction has to be the literal accepted word."""
        text = slack.triage_text(_triage([_prop()]))
        assert "apply fix" in text
        assert "keep test" in text

    def test_a_diagnosis_without_a_patch_says_why_and_offers_no_apply(self):
        text = slack.triage_text(_triage([
            _prop(verdict="behaviour_lost", patch="",
                  rejected_reason="the fix belongs in the merge, not in the test")]))
        assert "behaviour_lost" in text
        assert "the fix belongs in the merge" in text
        assert "```" not in text or "def test_stt" not in text

    def test_apply_is_not_offered_when_no_proposal_carries_a_patch(self):
        text = slack.triage_text(_triage([_prop(verdict="unsure", patch="")]))
        assert "apply fix" not in text
        assert "keep test" in text

    def test_a_long_patch_is_truncated_with_a_pointer_to_the_state_file(self):
        text = slack.triage_text(_triage([_prop(patch="x = 1\n" * 500)]))
        assert len(text) < 8000
        assert "gate-triage.json" in text


class TestFailedTextMentionsTriage:
    def test_a_test_gate_failure_points_at_the_triage_instead_of_raw_output(self):
        prep = {"upstream_head": "a" * 40, "conflicts": ["f.py"]}
        result = {"failed_stage": "test-gate", "detail": "the merge introduces test failures"}
        text = slack.failed_text(prep, result, scratch="/s/scratch", triage=_triage([_prop()]))
        assert "test-gate" in text
        assert "tests/gateway/test_voice.py" in text
        assert "apply fix" in text

    def test_without_a_triage_the_old_summary_still_stands(self):
        prep = {"upstream_head": "a" * 40, "conflicts": ["f.py"]}
        result = {"failed_stage": "test-gate", "detail": "boom"}
        text = slack.failed_text(prep, result, scratch="/s/scratch")
        assert "test-gate" in text
        assert "apply fix" not in text


class TestTriageReminder:
    def test_names_the_pending_proposal_and_the_two_answers(self):
        text = slack.triage_reminder_text(_triage([_prop()]))
        assert "tests/gateway/test_voice.py" in text
        assert "apply fix" in text and "keep test" in text
