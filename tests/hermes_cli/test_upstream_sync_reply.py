"""Tests for upstream-sync operator decision-reply detection (Task 1)."""

import json
from pathlib import Path

import pytest

from hermes_cli.upstream_sync_reply import (
    parse_upstream_sync_decision_reply,
    has_pending_upstream_decision,
    record_operator_decisions,
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
            "/home/hermes/.hermes/state/upstream-sync"
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


def _pending_with(tmp_path, features, status="awaiting_decision"):
    p = tmp_path / "pending.json"
    p.write_text(json.dumps({"schema": "upstream-sync-pending/v1", "status": status,
                             "upstream_head": "bbbb2222", "features": features}))
    return p


def _feat(fid, path, decision=None):
    return {"id": fid, "files": [path], "local_subjects": ["x"],
            "status": "decided" if decision else "awaiting_decision",
            "source": "policy" if decision else None, "decision": decision}


class TestRecordOperatorDecisions:
    """The intercept no longer spawns an agent: it writes the operator's answers
    into pending.json and hands the host an apply-decisions request — but only
    when every feature has an answer."""

    def test_full_answer_records_and_requests_apply(self, tmp_path):
        _pending_with(tmp_path, [_feat("F1", "gateway/run.py", "merge-both"), _feat("F2", "tools/approval.py")])
        out = record_operator_decisions(tmp_path, {2: "merge both"},
                                        {"platform": "slack", "chat_id": "C1", "thread_id": "1786.001", "user_id": "U1"})
        assert out["applied"] == ["F2"] and out["still_awaiting"] == []
        assert out["requested"] is True
        pending = json.loads((tmp_path / "pending.json").read_text())
        f2 = pending["features"][1]
        assert f2["decision"] == "merge-both" and f2["source"] == "operator" and f2["status"] == "decided"
        assert pending["status"] == "auto_apply"
        assert pending["slack_channel"] == "C1" and pending["slack_thread_ts"] == "1786.001"
        req = json.loads((tmp_path / "finalize-request.json").read_text())
        assert req["action"] == "apply-decisions"
        assert req["origin"]["thread_id"] == "1786.001"

    def test_partial_answer_records_but_does_not_request(self, tmp_path):
        _pending_with(tmp_path, [_feat("F1", "a.py"), _feat("F2", "tools/approval.py")])
        out = record_operator_decisions(tmp_path, {1: "keep local"}, {"platform": "slack", "chat_id": "C1"})
        assert out["applied"] == ["F1"] and out["still_awaiting"] == ["F2"]
        assert out["requested"] is False
        assert not (tmp_path / "finalize-request.json").exists()
        pending = json.loads((tmp_path / "pending.json").read_text())
        assert pending["status"] == "awaiting_decision"
        assert pending["features"][0]["decision"] == "keep-local"

    def test_unknown_feature_number_is_ignored_and_reported(self, tmp_path):
        _pending_with(tmp_path, [_feat("F1", "a.py")])
        out = record_operator_decisions(tmp_path, {1: "merge both", 7: "merge both"}, {"platform": "slack"})
        assert out["applied"] == ["F1"] and out["unknown"] == [7]
        assert out["requested"] is True

    def test_in_flight_finalize_blocks_a_second_request(self, tmp_path):
        _pending_with(tmp_path, [_feat("F1", "a.py")])
        (tmp_path / "finalize-request.processing.json").write_text("{}")
        out = record_operator_decisions(tmp_path, {1: "merge both"}, {"platform": "slack"})
        assert out["applied"] == ["F1"] and out["requested"] is False
        assert out["reason"] and "in flight" in out["reason"]
        assert not (tmp_path / "finalize-request.json").exists()

    def test_missing_pending_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            record_operator_decisions(tmp_path, {1: "merge both"}, {})


# ---------------------------------------------------------------------------
# Triage gate: the host proposes a test patch, the operator answers one word
# ---------------------------------------------------------------------------

from hermes_cli.upstream_sync_reply import (  # noqa: E402
    parse_upstream_sync_triage_reply,
    has_pending_upstream_triage,
    record_triage_decision,
)


class TestParseTriageReply:
    """Whole-message equality, like ops_gate_service.parse_ops_reply: a strict
    parser cannot steal an answer meant for the decision gate, which is why it
    is consulted first."""

    @pytest.mark.parametrize("text", [
        "apply fix", "Apply Fix", "  apply   fix  ", "apply fix.", "применить правку",
    ])
    def test_accepts_the_apply_forms(self, text):
        assert parse_upstream_sync_triage_reply(text) == "apply_fix"

    @pytest.mark.parametrize("text", [
        "keep test", "KEEP TEST", "keep test!", "оставить тест",
    ])
    def test_accepts_the_keep_forms(self, text):
        assert parse_upstream_sync_triage_reply(text) == "keep_test"

    @pytest.mark.parametrize("text", [
        "он написал: apply fix",
        "ok, apply fix",
        "apply fix and push",
        "",
        None,
        "1: merge both",
        "x" * 60,
    ])
    def test_rejects_anything_that_is_not_the_bare_word(self, text):
        assert parse_upstream_sync_triage_reply(text) is None


def _triage(tmp_path, status="awaiting_triage", proposals=None):
    payload = {
        "schema": "upstream-sync-triage/v1",
        "status": status,
        "merge_sha": "abc123",
        "proposals": proposals if proposals is not None else [
            {"test_file": "tests/x.py", "verdict": "test_outdated", "patch": "print(1)\n"}
        ],
        "created_at": "2026-08-16T00:00:00+00:00",
    }
    (tmp_path / "gate-triage.json").write_text(json.dumps(payload))
    return payload


class TestHasPendingTriage:
    def test_true_only_while_awaiting(self, tmp_path):
        _triage(tmp_path)
        assert has_pending_upstream_triage(tmp_path) is True

    @pytest.mark.parametrize("status", ["applied", "rejected"])
    def test_false_once_answered(self, tmp_path, status):
        _triage(tmp_path, status=status)
        assert has_pending_upstream_triage(tmp_path) is False

    def test_false_when_absent(self, tmp_path):
        assert has_pending_upstream_triage(tmp_path) is False


class TestRecordTriageDecision:
    def test_apply_fix_requests_the_finalizer_and_marks_the_file(self, tmp_path):
        _triage(tmp_path)
        out = record_triage_decision(tmp_path, "apply_fix", {"platform": "Platform.SLACK",
                                                             "chat_id": "C1", "thread_id": "1.1"})
        assert out["requested"] is True
        req = json.loads((tmp_path / "finalize-request.json").read_text())
        assert req["action"] == "apply-triage-fixes"
        assert req["origin"]["platform"] == "slack"
        assert json.loads((tmp_path / "gate-triage.json").read_text())["status"] == "applying"

    def test_keep_test_rejects_without_requesting_anything(self, tmp_path):
        _triage(tmp_path)
        out = record_triage_decision(tmp_path, "keep_test", {})
        assert out["requested"] is False
        assert not (tmp_path / "finalize-request.json").exists()
        assert json.loads((tmp_path / "gate-triage.json").read_text())["status"] == "rejected"

    def test_apply_fix_with_no_proposals_does_not_request(self, tmp_path):
        _triage(tmp_path, proposals=[])
        out = record_triage_decision(tmp_path, "apply_fix", {})
        assert out["requested"] is False
        assert "no patch" in (out["reason"] or "")
        assert not (tmp_path / "finalize-request.json").exists()

    def test_apply_fix_defers_while_a_finalize_is_in_flight(self, tmp_path):
        _triage(tmp_path)
        (tmp_path / "finalize-request.processing.json").write_text("{}")
        out = record_triage_decision(tmp_path, "apply_fix", {})
        assert out["requested"] is False
        assert "in flight" in (out["reason"] or "")


from hermes_cli.upstream_sync_reply import (  # noqa: E402
    parse_upstream_sync_ack_reply,
    record_ack_findings,
)


class TestParseAckReply:
    """The operator's only channel for acknowledging a structural finding.

    The finalizer is started by a systemd path unit, so nothing an operator
    types reaches it as an environment variable — the acknowledgement has to
    travel as a reply, the way every other answer in this pipeline does.
    """

    def test_a_single_entry(self):
        assert parse_upstream_sync_ack_reply("ack mod.py:local_only") == ["mod.py:local_only"]

    def test_several_entries_on_one_line_or_several(self):
        assert parse_upstream_sync_ack_reply("ack a.py:foo b.py:bar") == ["a.py:foo", "b.py:bar"]
        assert parse_upstream_sync_ack_reply("ack a.py:foo\nack b.py:bar") == ["a.py:foo", "b.py:bar"]

    def test_commas_separate_too(self):
        assert parse_upstream_sync_ack_reply("ack a.py:foo, b.py:bar") == ["a.py:foo", "b.py:bar"]

    def test_conversation_is_not_an_acknowledgement(self):
        assert parse_upstream_sync_ack_reply("ack") is None
        assert parse_upstream_sync_ack_reply("looks fine to me") is None
        assert parse_upstream_sync_ack_reply("") is None
        assert parse_upstream_sync_ack_reply(None) is None

    def test_an_entry_without_a_symbol_is_not_an_entry(self):
        """`path:` names no symbol, and only a symbol-bearing finding is ackable."""
        assert parse_upstream_sync_ack_reply("ack mod.py") is None
        assert parse_upstream_sync_ack_reply("ack mod.py:") is None

    def test_a_path_that_contains_the_keyword_is_not_cut_in_half(self):
        """The prefix is where the pattern matched, not the next literal "ack".

        Splitting the line on the substring finds the one inside the path when
        the keyword itself was typed in another case, and hands back a mangled
        entry that matches no finding — which reads to the operator exactly like
        the acknowledgement being ignored.
        """
        assert parse_upstream_sync_ack_reply("ACK pack.py:foo") == ["pack.py:foo"]
        assert parse_upstream_sync_ack_reply("ack backup.py:foo") == ["backup.py:foo"]

    def test_it_cannot_swallow_the_other_gates_answers(self):
        """Every gate in this pipeline answers to plain text in the same thread."""
        assert parse_upstream_sync_ack_reply("F2: merge-both") is None
        assert parse_upstream_sync_ack_reply("apply fix") is None
        assert parse_upstream_sync_ack_reply("keep test") is None
        assert parse_upstream_sync_ack_reply("выполни") is None
        assert parse_upstream_sync_ack_reply("подтверждаю git_push") is None


class TestRecordAckFindings:
    def test_it_requests_the_apply_carrying_the_entries(self, tmp_path):
        _pending_with(tmp_path, [_feat("F1", "a.py", "merge-both")], status="auto_apply")

        outcome = record_ack_findings(tmp_path, ["mod.py:local_only"], {"platform": "slack"})

        assert outcome["requested"] is True
        req = json.loads((tmp_path / "finalize-request.json").read_text())
        assert req["action"] == "apply-decisions"
        assert req["ack_findings"] == ["mod.py:local_only"]

    def test_it_refuses_while_a_finalize_is_in_flight(self, tmp_path):
        """Re-arming over a running apply would answer a gate nobody is holding."""
        _pending_with(tmp_path, [_feat("F1", "a.py", "merge-both")], status="auto_apply")
        (tmp_path / "finalize-request.processing.json").write_text("{}")

        outcome = record_ack_findings(tmp_path, ["mod.py:local_only"], {"platform": "slack"})

        assert outcome["requested"] is False
        assert outcome["reason"]

    def test_it_refuses_when_no_decision_is_armed(self, tmp_path):
        """Nothing to re-apply: an ack is an answer to a refusal, not a command."""
        outcome = record_ack_findings(tmp_path, ["mod.py:local_only"], {"platform": "slack"})

        assert outcome["requested"] is False
        assert not (tmp_path / "finalize-request.json").exists()
