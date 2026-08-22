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

    The fork only edits ``kept`` here — it never touches the deleted function,
    which is the ordinary shape of an upstream refactor landing next to local
    work (2026-08-22: c1693d7dcc removed 27 duplicate helpers).
    """
    live = tmp_path / "live"
    live.mkdir()
    _git(live, "init", "-q", "-b", "local/customizations")
    _git(live, "config", "user.email", "t@t")
    _git(live, "config", "user.name", "t")
    (live / "mod.py").write_text(MODULE_BASE)
    _git(live, "add", "-A")
    _git(live, "commit", "-qm", "base")
    base = _git(live, "rev-parse", "HEAD")

    (live / "mod.py").write_text(MODULE_BASE.replace("return 1", "return 100"))
    _git(live, "add", "-A")
    _git(live, "commit", "-qm", "local change")
    local_head = _git(live, "rev-parse", "HEAD")

    _git(live, "checkout", "-qb", "up", base)
    (live / "mod.py").write_text("def kept():\n    return 999\n")
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
        "features": [{"id": "F1", "decision": "merge-both", "files": ["mod.py"],
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
    def test_the_escape_hatch_commits_and_says_so(self, pyworld):
        """A legitimate mass deletion must not wedge the pipeline forever."""
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept():\n    return 100\n")
        _git(scratch, "add", "mod.py")

        out = _out(_run("commit", pyworld["state"], pyworld["live"],
                        env={"HERMES_SYNC_SKIP_INVARIANTS": "1"}))

        assert out["status"] == "committed"
        assert out["invariants_skipped"] is True


class TestPerFindingAcknowledgement:
    """Confirming one finding must leave the check armed for everything else.

    HERMES_SYNC_SKIP_INVARIANTS=1 answers a single legitimate finding by
    disarming the whole merge — on 2026-08-22 that meant five other resolved
    files went through unchecked. Naming the finding you accept keeps the rest
    of the gate doing its job, and puts the scope of the override on the merge
    record instead of a bare "the operator turned it off".
    """

    def test_acknowledging_the_only_finding_commits_and_records_the_scope(self, pyworld):
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept():\n    return 100\n")
        _git(scratch, "add", "mod.py")

        out = _out(_run("commit", pyworld["state"], pyworld["live"],
                        env={"HERMES_SYNC_ACK_FINDINGS": "mod.py:local_only"}))

        assert out["status"] == "committed", out
        assert out["invariants_acked"] == ["mod.py:local_only"]
        # the whole-merge bypass was NOT what let this through
        assert "invariants_skipped" not in out
        prep = json.loads((pyworld["state"] / "apply-prepare.json").read_text())
        assert prep["invariants_acked"] == ["mod.py:local_only"]

    def test_an_unrelated_acknowledgement_leaves_the_finding_blocking(self, pyworld):
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept():\n    return 100\n")
        _git(scratch, "add", "mod.py")

        out = _out(_run("commit", pyworld["state"], pyworld["live"],
                        env={"HERMES_SYNC_ACK_FINDINGS": "other.py:gone"}))

        assert out["status"] == "invariants_failed"
        assert [f["symbol"] for f in out["findings"]] == ["local_only"]
        assert out["invariants_ack_unmatched"] == ["other.py:gone"]

    def test_an_acknowledgement_cannot_silence_an_unparseable_file(self, pyworld):
        """Broken syntax is not an intent call, so no entry may wave it through."""
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept(:\n    return 1\n")
        _git(scratch, "add", "mod.py")

        out = _out(_run("commit", pyworld["state"], pyworld["live"],
                        env={"HERMES_SYNC_ACK_FINDINGS": "mod.py:kept mod.py:None mod.py:"}))

        assert out["status"] == "invariants_failed"
        assert [f["kind"] for f in out["findings"]] == ["unparseable"]

    def test_the_refusal_tells_the_operator_the_per_finding_form(self, pyworld):
        """Discoverability is the point: an unknown option gets bypassed instead."""
        scratch = _prepared(pyworld)
        (scratch / "mod.py").write_text("def kept():\n    return 100\n")
        _git(scratch, "add", "mod.py")

        out = _out(_run("commit", pyworld["state"], pyworld["live"]))

        assert "HERMES_SYNC_ACK_FINDINGS" in out["hint"]


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
