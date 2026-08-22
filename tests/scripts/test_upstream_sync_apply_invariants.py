"""commit/handoff must refuse a structurally broken resolution.

The checks live in _commit_merge because every path reaches it: plain commit,
handoff, and the --amend used to fold a hand repair into the merge. A separate
verify step would be bypassed by calling handoff directly, and --amend would
skip it entirely - which is precisely the route a human takes after a red gate,
when a second mistake is most likely.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APPLY = REPO_ROOT / "scripts" / "upstream_sync_apply.py"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def _run(cmd: str, state: Path, live: Path, *extra: str, env=None) -> subprocess.CompletedProcess:
    import os
    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run(
        [sys.executable, str(APPLY), cmd, "--state", str(state), "--live", str(live), *extra],
        capture_output=True, text=True, timeout=120, env=full_env,
    )


def _out(proc: subprocess.CompletedProcess) -> dict:
    assert proc.stdout.strip(), proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


MODULE_BASE = '''def kept():
    return 1


def local_only():
    return 2
'''


@pytest.fixture()
def pyworld(tmp_path: Path):
    """A live checkout and an upstream commit that both edit the same module."""
    live = tmp_path / "live"
    live.mkdir()
    _git(live, "init", "-q", "-b", "local/customizations")
    _git(live, "config", "user.email", "t@t")
    _git(live, "config", "user.name", "t")
    (live / "mod.py").write_text(MODULE_BASE)
    (live / "untouched.py").write_text("def untouched():\n    return 7\n")
    _git(live, "add", "-A")
    _git(live, "commit", "-qm", "base")
    base = _git(live, "rev-parse", "HEAD")

    (live / "mod.py").write_text(MODULE_BASE.replace("return 1", "return 100"))
    _git(live, "add", "-A")
    _git(live, "commit", "-qm", "local change")
    local_head = _git(live, "rev-parse", "HEAD")

    _git(live, "checkout", "-qb", "up", base)
    (live / "mod.py").write_text(MODULE_BASE.replace("return 1", "return 999"))
    _git(live, "add", "-A")
    _git(live, "commit", "-qm", "upstream change")
    upstream_head = _git(live, "rev-parse", "HEAD")
    _git(live, "checkout", "-q", "local/customizations")

    state = tmp_path / "state"
    state.mkdir()
    (state / "pending.json").write_text(json.dumps({
        "schema": "upstream-sync-pending/v1",
        "status": "awaiting_decision",
        "local_head": local_head,
        "upstream_head": upstream_head,
        "slack_platform": "slack",
        "slack_channel": "C0B3X1E5SJZ",
        "slack_thread_ts": "1783420000.000",
        "slack_user_id": "U123",
        "features": [{"id": "F1", "decision": "keep-local", "files": ["mod.py"],
                      "local_subjects": ["local change"]}],
    }))
    return {"live": live, "state": state, "local_head": local_head,
            "upstream_head": upstream_head}


def _prepared(pyworld):
    proc = _run("prepare", pyworld["state"], pyworld["live"])
    assert _out(proc)["status"] == "ready", proc.stdout
    return pyworld["state"] / "scratch"


class TestCommitRefusesBrokenResolutions:
    def test_unparseable_result_blocks_the_commit(self, pyworld):
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept(:\n    return 1\n")
        _git(scratch, "add", "mod.py")

        proc = _run("commit", pyworld["state"], pyworld["live"])
        out = _out(proc)

        assert proc.returncode != 0
        assert out["status"] == "invariants_failed"
        assert [f["kind"] for f in out["findings"]] == ["unparseable"]

    def test_no_commit_object_is_created_when_invariants_fail(self, pyworld):
        scratch = _prepared(pyworld)
        before = _git(scratch, "rev-parse", "HEAD")
        (scratch / "mod.py").write_text("def kept(:\n    return 1\n")
        _git(scratch, "add", "mod.py")

        _run("commit", pyworld["state"], pyworld["live"])

        assert _git(scratch, "rev-parse", "HEAD") == before

    def test_a_dropped_definition_blocks_the_commit(self, pyworld):
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept():\n    return 100\n")   # local_only gone
        _git(scratch, "add", "mod.py")

        out = _out(_run("commit", pyworld["state"], pyworld["live"]))

        assert out["status"] == "invariants_failed"
        assert [f["symbol"] for f in out["findings"]] == ["local_only"]

    def test_a_sound_resolution_still_commits(self, pyworld):
        _prepared(pyworld)

        out = _out(_run("commit", pyworld["state"], pyworld["live"]))

        assert out["status"] == "committed"
        assert out["merge_sha"]

    def test_the_scratch_clone_survives_for_repair(self, pyworld):
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept(:\n    return 1\n")
        _git(scratch, "add", "mod.py")

        _run("commit", pyworld["state"], pyworld["live"])

        assert (scratch / ".git").is_dir()
        assert (scratch / "mod.py").exists()


@pytest.fixture()
def pyworld_upstream_deletes(tmp_path: Path):
    """Same shape as ``pyworld``, except upstream removes ``local_only``.

    The fork only edits ``kept`` here — it never removes the deleted function,
    which is the ordinary shape of an upstream refactor landing next to local
    work (2026-08-22: c1693d7dcc removed 27 duplicate helpers).
    """
    live = tmp_path / "live"
    live.mkdir()
    _git(live, "init", "-q", "-b", "local/customizations")
    _git(live, "config", "user.email", "t@t")
    _git(live, "config", "user.name", "t")
    (live / "mod.py").write_text(MODULE_BASE)
    (live / "untouched.py").write_text("def untouched():\n    return 7\n")
    _git(live, "add", "-A")
    _git(live, "commit", "-qm", "base")
    base = _git(live, "rev-parse", "HEAD")

    (live / "mod.py").write_text(MODULE_BASE.replace("return 1", "return 100"))
    _git(live, "add", "-A")
    _git(live, "commit", "-qm", "local change")
    local_head = _git(live, "rev-parse", "HEAD")

    _git(live, "checkout", "-qb", "up", base)
    (live / "mod.py").write_text("def kept():\n    return 999\n")
    (live / "untouched.py").unlink()
    _git(live, "add", "-A")
    _git(live, "commit", "-qm", "upstream drops local_only")
    upstream_head = _git(live, "rev-parse", "HEAD")
    _git(live, "checkout", "-q", "local/customizations")

    state = tmp_path / "state"
    state.mkdir()
    (state / "pending.json").write_text(json.dumps({
        "schema": "upstream-sync-pending/v1",
        "status": "awaiting_decision",
        "local_head": local_head,
        "upstream_head": upstream_head,
        "features": [{"id": "F1", "decision": "keep-local", "files": ["mod.py"],
                      "local_subjects": ["local change"]}],
    }))
    return {"live": live, "state": state, "local_head": local_head,
            "upstream_head": upstream_head}


class TestAcceptedDeletionsDoNotBlock:
    """The same resolution, judged against two different bases.

    ``test_a_dropped_definition_blocks_the_commit`` writes this exact file and
    is refused, because there both parents still defined ``local_only``. Here
    upstream deleted it, so following the deletion is the merge working — and
    the gate must be able to tell the two apart, or its findings are noise and
    the operator learns to bypass it wholesale.
    """

    def test_following_an_upstream_deletion_commits(self, pyworld_upstream_deletes):
        scratch = _prepared(pyworld_upstream_deletes)
        (scratch / "mod.py").write_text("def kept():\n    return 100\n")
        _git(scratch, "add", "mod.py")

        out = _out(_run("commit", pyworld_upstream_deletes["state"],
                        pyworld_upstream_deletes["live"]))

        assert out["status"] == "committed", out
        assert out["invariant_report"]["findings"] == []
        assert "invariants_skipped" not in out

    def test_dropping_a_definition_both_parents_kept_still_blocks(self, pyworld):
        """Guards against over-suppression: the original alarm must still fire."""
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept():\n    return 100\n")
        _git(scratch, "add", "mod.py")

        out = _out(_run("commit", pyworld["state"], pyworld["live"]))

        assert out["status"] == "invariants_failed"
        assert [f["symbol"] for f in out["findings"]] == ["local_only"]


class TestAmendIsCheckedToo:
    def test_amend_cannot_smuggle_a_broken_file_into_the_merge(self, pyworld):
        scratch = _prepared(pyworld)
        assert _out(_run("commit", pyworld["state"], pyworld["live"]))["status"] == "committed"

        (scratch / "mod.py").write_text("def kept(:\n    return 1\n")
        _git(scratch, "add", "mod.py")
        out = _out(_run("commit", pyworld["state"], pyworld["live"], "--amend"))

        assert out["status"] == "invariants_failed"


class TestHandoffIsCheckedToo:
    def test_handoff_writes_no_request_when_invariants_fail(self, pyworld):
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept(:\n    return 1\n")
        _git(scratch, "add", "mod.py")

        out = _out(_run("handoff", pyworld["state"], pyworld["live"]))

        assert out["status"] == "invariants_failed"
        assert not (pyworld["state"] / "finalize-request.json").exists()


class TestOverride:
    def test_environment_escape_hatch_is_ignored_and_gate_stays_blocked(self, pyworld):
        """The old global skip cannot discharge a finding."""
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept():\n    return 100\n")
        _git(scratch, "add", "mod.py")

        out = _out(_run("commit", pyworld["state"], pyworld["live"],
                        env={"HERMES_SYNC_SKIP_INVARIANTS": "1"}))

        assert out["status"] == "invariants_failed"
        assert "invariants_skipped" not in out
        assert not (pyworld["state"] / "finalize-request.json").exists()


class TestMechanicalResolutionValidatesItsOutput:
    """merge-both is a text concatenation, so it can produce invalid code.

    On 2026-08-19 it did: the two sides of the block shared a single closing
    brace that lived outside it - in ours it closed "pipelines", in theirs
    "session" - and one closer cannot serve two bodies. The resolver reported
    the hunk as closed and moved on.
    """

    FIXTURES = REPO_ROOT / "tests" / "fixtures" / "upstream_sync"

    def _zdiff3(self, ours: str, base: str, theirs: str) -> str:
        return (
            "<<<<<<< HEAD\n" + ours + "||||||| base\n" + base + "=======\n" + theirs + ">>>>>>> upstream\n"
        )

    def test_a_concatenation_that_does_not_parse_is_not_resolved(self):
        from scripts.upstream_sync_apply import resolve_merge_both_text

        block = self._zdiff3(
            ours='    "pipelines": {\n        "execution": {},\n',
            base="",
            theirs='    "session": {\n        "on": True,\n',
        )
        text = "CONFIG = {\n" + block + "    },\n}\n"

        new_text, resolved, remaining = resolve_merge_both_text(text, path="cfg.py")

        assert (resolved, remaining) == (0, 1)
        assert new_text == text          # markers kept for a human

    def test_a_concatenation_that_parses_is_resolved_as_before(self):
        from scripts.upstream_sync_apply import resolve_merge_both_text

        # Distinct bodies on purpose: a line shared by both sides is left for a
        # human by an older rule, which would mask what this test checks.
        block = self._zdiff3(ours="def a():\n    return 1\n", base="", theirs="def b():\n    return 2\n")

        new_text, resolved, remaining = resolve_merge_both_text(block, path="mod.py")

        assert (resolved, remaining) == (1, 0)
        assert "def a():" in new_text and "def b():" in new_text
        assert "<<<<<<<" not in new_text

    def test_non_python_paths_are_not_parsed(self):
        """A Markdown merge must not be judged by a Python parser."""
        from scripts.upstream_sync_apply import resolve_merge_both_text

        block = self._zdiff3(ours="# local heading\n", base="", theirs="# upstream heading\n")

        _, resolved, remaining = resolve_merge_both_text(block, path="docs/x.md")

        assert (resolved, remaining) == (1, 0)

    def test_the_real_incident_block_is_left_for_a_human(self):
        from scripts.upstream_sync_apply import resolve_merge_both_text

        raw = (self.FIXTURES / "config_defaults_shared_closer.conflict").read_text(encoding="utf-8")
        # The saved fixture is a plain (non-zdiff3) block; give it a base section
        # so the mechanical resolver treats it as a both-added hunk.
        zdiff3 = raw.replace("=======\n", "||||||| base\n=======\n", 1)

        new_text, resolved, remaining = resolve_merge_both_text(zdiff3, path="hermes_cli/config_defaults.py")

        assert remaining == 1
        assert "<<<<<<<" in new_text


class TestTheFinalizerReportsInvariantsDistinctly:
    """A structural refusal must not read like a red test gate.

    The two demand opposite responses. A red gate can legitimately mean the
    tests are stale, which is what the triage flow offers to patch. A tripped
    invariant means the merge itself lost something - patching tests there
    would delete the finding along with the code it was pointing at, which is
    exactly the trap of 2026-08-19.
    """

    FINALIZE = REPO_ROOT / "scripts" / "upstream-sync-finalize.sh"

    def _script(self) -> str:
        return self.FINALIZE.read_text(encoding="utf-8")

    def test_the_commit_stage_recognises_the_invariant_exit(self):
        assert "invariants_failed" in self._script()

    def test_the_report_names_the_findings_file_not_just_a_log_tail(self):
        script = self._script()
        stage = script[script.index("FAILED_STAGE=commit"):]
        assert "invariants" in stage.split("land_merge")[0]

    def test_it_does_not_offer_the_triage_vocabulary(self):
        """`apply fix` patches tests; it cannot help here and would hide the loss."""
        script = self._script()
        stage = script[script.index("FAILED_STAGE=commit"):].split("land_merge")[0]
        assert "apply fix" not in stage


class TestFindingsRenderer:
    """The operator's only explanation of a structural refusal.

    Kept out of the finalizer as a real module: as a shell heredoc it was one
    quoting mistake away from turning that explanation into a shell error.
    """

    def _mod(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import upstream_sync_findings
        return upstream_sync_findings

    def test_reads_the_findings_out_of_a_detail_log(self):
        m = self._mod()
        log = (
            "some git noise\n"
            '{"status": "committed", "merge_sha": "abc"}\n'
            '{"status": "invariants_failed", "findings": '
            '[{"path": "a.py", "kind": "unparseable", "line": 7}]}\n'
        )

        assert m.findings_from_log(log) == [{"path": "a.py", "kind": "unparseable", "line": 7}]

    def test_takes_the_last_payload_when_a_run_retried(self):
        m = self._mod()
        log = (
            '{"status": "invariants_failed", "findings": [{"path": "old.py", "kind": "unparseable"}]}\n'
            '{"status": "invariants_failed", "findings": [{"path": "new.py", "kind": "unparseable"}]}\n'
        )

        assert m.findings_from_log(log)[0]["path"] == "new.py"

    def test_survives_a_log_with_no_payload(self):
        m = self._mod()

        assert m.findings_from_log("just git output\n") == []

    def test_renders_a_symbol_finding_by_name(self):
        m = self._mod()

        out = m.render([{"path": "gateway/run.py", "kind": "lost_definition", "symbol": "_stale_guard_tick"}])

        assert out == "- gateway/run.py: lost_definition (_stale_guard_tick)"

    def test_renders_a_parse_finding_by_line(self):
        m = self._mod()

        out = m.render([{"path": "cfg.py", "kind": "unparseable", "line": 7}])

        assert out == "- cfg.py: unparseable (line 7)"

    def test_says_so_when_there_is_nothing_to_render(self):
        """Silence here would read as "no problem found", which is never true."""
        m = self._mod()

        assert "finalize-detail.log" in m.render([])


class TestStageZeroCommitContract:
    def test_unstaged_tracked_edit_is_refused_with_add_hint(self, pyworld):
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept():\n    return 100\n")
        out = _out(_run("commit", pyworld["state"], pyworld["live"]))
        assert out["status"] == "unstaged_tree"
        assert "git add -- <paths>" in out["reason"]

    def test_untracked_file_is_refused(self, pyworld):
        scratch = _prepared(pyworld)
        (scratch / "operator-note.txt").write_text("not part of the merge")
        out = _out(_run("commit", pyworld["state"], pyworld["live"]))
        assert out["status"] == "unstaged_tree"
        assert "untracked" in out["reason"]

    def test_break_glass_is_explicit_and_audited(self, pyworld):
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept():\n    return 100\n")
        _git(scratch, "add", "mod.py")
        out = _out(_run("commit", pyworld["state"], pyworld["live"], "--break-glass"))
        assert out["status"] == "committed"
        assert out["invariants_break_glass"]["mode"] == "manual-only"


    def test_deleted_stage_zero_path_is_one_ackable_file_finding(self, pyworld):
        scratch = _prepared(pyworld)
        (scratch / "mod.py").unlink()
        _git(scratch, "add", "-u", "mod.py")

        out = _out(_run("commit", pyworld["state"], pyworld["live"]))

        assert out["status"] == "invariants_failed"
        assert len(out["findings"]) == 1
        finding = out["findings"][0]
        assert finding["kind"] == "deleted_in_result"
        assert finding["path"] == "mod.py"
        assert finding.get("finding_id")


class TestResolutionPolicySnapshot:
    def test_conflicting_policies_for_one_path_are_a_hard_refusal(self, pyworld):
        pending_path = pyworld["state"] / "pending.json"
        data = json.loads(pending_path.read_text())
        data["features"].append({"id": "F2", "decision": "take-upstream", "files": ["mod.py"]})
        pending_path.write_text(json.dumps(data))
        out = _out(_run("prepare", pyworld["state"], pyworld["live"]))
        assert out["status"] == "policy_error"
        assert "ambiguous" in out["reason"]

    def test_keep_local_journals_expected_upstream_contribution_loss(self, pyworld):
        scratch = _prepared(pyworld)
        out = _out(_run("commit", pyworld["state"], pyworld["live"]))

        assert out["status"] == "committed"
        prep = json.loads((pyworld["state"] / "apply-prepare.json").read_text())
        expected = prep["invariant_report"]["expected_policy_losses"]
        assert any(item["symbol"] == "kept" for item in expected)
        pending = json.loads((pyworld["state"] / "invariants-pending.json").read_text())
        assert any(item["event"] == "expected_policy_loss" for item in pending["journal"])
        assert pending["status"] == "reported"


    def test_invariant_mode_is_snapshotted_at_prepare(self, pyworld, monkeypatch):
        pending_path = pyworld["state"] / "pending.json"
        pending = json.loads(pending_path.read_text())
        pending["features"][0]["decision"] = "merge-both"
        pending_path.write_text(json.dumps(pending))
        monkeypatch.setenv("HERMES_SYNC_INVARIANT_MODE", "report")
        scratch = _prepared(pyworld)
        prep = json.loads((pyworld["state"] / "apply-prepare.json").read_text())
        assert prep["invariant_mode"] == "report"

        monkeypatch.setenv("HERMES_SYNC_INVARIANT_MODE", "block")
        (scratch / "mod.py").write_text(
            "def kept():\n    return 100\n\n\ndef local_only():\n    return 2\n"
        )
        _git(scratch, "add", "mod.py")
        out = _out(_run("commit", pyworld["state"], pyworld["live"]))
        assert out["status"] == "committed"
        assert out["invariant_report"]["mode"] == "report"


    def test_report_only_mode_records_contribution_without_blocking(self, pyworld):
        pending_path = pyworld["state"] / "pending.json"
        pending = json.loads(pending_path.read_text())
        pending["features"][0]["decision"] = "merge-both"
        pending_path.write_text(json.dumps(pending))
        prep = _run(
            "prepare", pyworld["state"], pyworld["live"], "--invariant-mode", "report",
        )
        assert _out(prep)["status"] == "ready", prep.stdout
        scratch = pyworld["state"] / "scratch"
        (scratch / "mod.py").write_text(
            "def kept():\n    return 100\n\n\ndef local_only():\n    return 2\n"
        )
        _git(scratch, "add", "mod.py")

        out = _out(_run(
            "commit", pyworld["state"], pyworld["live"],
        ))

        assert out["status"] == "committed"
        report = out["invariant_report"]
        assert report["mode"] == "report"
        assert any(f["kind"] == "discarded_contribution" for f in report["findings"])


    def test_incomplete_origin_is_reported_before_arming_receipts(self, pyworld):
        scratch = _prepared(pyworld)
        pending_path = pyworld["state"] / "pending.json"
        pending = json.loads(pending_path.read_text())
        pending.pop("slack_user_id")
        pending_path.write_text(json.dumps(pending))
        (scratch / "mod.py").write_text("def kept():\n    return 100\n")
        _git(scratch, "add", "mod.py")

        out = _out(_run("commit", pyworld["state"], pyworld["live"]))
        assert out["status"] == "invariant_origin_incomplete"
        assert "user_id" in out["reason"]
        assert not (pyworld["state"] / "invariants-pending.json").exists()


    def test_blocked_hard_finding_explains_that_receipts_are_disabled(self, pyworld):
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept(:\n    return 1\n")
        _git(scratch, "add", "mod.py")

        out = _out(_run("commit", pyworld["state"], pyworld["live"]))

        assert out["status"] == "invariants_failed"
        assert "blocked" in out["hint"].lower()
        assert "receipt" in out["hint"].lower()


    def test_merge_both_catches_a_dropped_body_contribution(self, pyworld):
        pending_path = pyworld["state"] / "pending.json"
        data = json.loads(pending_path.read_text())
        data["features"][0]["decision"] = "merge-both"
        pending_path.write_text(json.dumps(data))
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept():\n    return 100\n")
        _git(scratch, "add", "mod.py")
        out = _out(_run("commit", pyworld["state"], pyworld["live"]))
        assert out["status"] == "invariants_failed"
        assert any(f.get("symbol") == "kept" for f in out["findings"])


class TestReceiptAndAmendLifecycle:
    def test_matching_receipt_survives_amend_of_unrelated_file(self, pyworld):
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept():\n    return 100\n")
        _git(scratch, "add", "mod.py")
        failed = _out(_run("commit", pyworld["state"], pyworld["live"]))
        assert failed["status"] == "invariants_failed"
        finding_id = failed["acknowledgements_required"][0]
        from hermes_cli.upstream_sync_reply import record_invariant_ack
        ack = record_invariant_ack(
            pyworld["state"], finding_id,
            {"platform": "slack", "chat_id": "C0B3X1E5SJZ",
             "thread_id": "1783420000.000", "user_id": "U123"},
        )
        assert ack["requested"] is True
        committed = _out(_run("commit", pyworld["state"], pyworld["live"]))
        assert committed["status"] == "committed"
        (scratch / "unrelated.txt").write_text("amend\n")
        _git(scratch, "add", "unrelated.txt")
        amended = _out(_run("commit", pyworld["state"], pyworld["live"], "--amend"))
        assert amended["status"] == "committed"

    def test_new_prepare_invalidates_old_receipt_state_when_live_head_moves(self, pyworld):
        (pyworld["state"] / "invariants-pending.json").write_text(json.dumps({"merge_scope": {"local_parent": "old"}}))
        (pyworld["live"] / "new.txt").write_text("moved\n")
        _git(pyworld["live"], "add", "new.txt")
        _git(pyworld["live"], "commit", "-qm", "move live head")
        out = _out(_run("prepare", pyworld["state"], pyworld["live"]))
        assert out["status"] == "ready"
        assert not (pyworld["state"] / "invariants-pending.json").exists()
