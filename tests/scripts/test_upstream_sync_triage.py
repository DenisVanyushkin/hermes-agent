"""Triage of a red fork-test gate: diagnose, propose, never apply.

When an upstream merge makes a fork test fail, exactly two things can be true
and the model cannot tell them apart reliably: the test is stale (upstream
changed an API our test still asserts the old shape of — 2026-08-15,
``transcribe_audio(path)`` gaining two parameters) or the merge lost our
behaviour (the test is the only sensor for a silently dropped customization).
So the host collects evidence, asks the model for a verdict and a candidate
patch, VALIDATES the patch before showing it, and stops. The operator answers
with one word.

Everything here is offline: the model is replaced by ``HERMES_SYNC_TRIAGE_CMD``
and the verification pytest by ``HERMES_SYNC_TRIAGE_PYTEST_CMD``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import upstream_sync_triage as triage  # noqa: E402
import upstream_sync_gate as gate  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


# --------------------------------------------------------------------------- modules under test

class TestModulesUnderTest:
    """Which production files a failing test is about — the diff the model gets
    is cut to these, because handing it all of gateway/run.py (32k lines) buries
    the change that broke the test."""

    def _repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "r"
        for rel in ("gateway/run.py", "gateway/__init__.py", "agent/codex_runtime.py",
                    "hermes_cli/ops_catalog.py", "tests/gateway/test_voice.py"):
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("")
        return repo

    def test_resolves_imported_modules_to_repo_paths(self, tmp_path):
        repo = self._repo(tmp_path)
        text = "import agent.codex_runtime\nfrom hermes_cli.ops_catalog import CATALOG\n"
        mods = triage.modules_under_test("tests/gateway/test_voice.py", text, repo)
        assert "agent/codex_runtime.py" in mods
        assert "hermes_cli/ops_catalog.py" in mods

    def test_resolves_a_package_import_to_its_init(self, tmp_path):
        repo = self._repo(tmp_path)
        mods = triage.modules_under_test("tests/gateway/test_voice.py", "import gateway\n", repo)
        assert "gateway/__init__.py" in mods

    def test_adds_the_twin_directory_of_the_test(self, tmp_path):
        repo = self._repo(tmp_path)
        mods = triage.modules_under_test("tests/gateway/test_voice.py", "", repo)
        assert "gateway/run.py" in mods

    def test_ignores_stdlib_and_third_party_imports(self, tmp_path):
        repo = self._repo(tmp_path)
        mods = triage.modules_under_test("tests/x.py", "import json, pytest\nfrom pathlib import Path\n", repo)
        assert mods == []

    def test_conflicting_paths_are_included(self, tmp_path):
        repo = self._repo(tmp_path)
        mods = triage.modules_under_test("tests/x.py", "", repo, conflicts=["agent/codex_runtime.py", "gone.py"])
        assert "agent/codex_runtime.py" in mods
        assert "gone.py" not in mods          # not in the tree — nothing to diff


# --------------------------------------------------------------------------- pytest output

class TestPytestExcerpt:
    LOG = """\
=================================== FAILURES ===================================
_______________________ test_alpha ________________________
    def test_alpha():
>       assert transcribe("a") == "x"
E       TypeError: transcribe() missing 2 required positional arguments
tests/a.py:10: TypeError
_______________________ test_beta _________________________
    def test_beta():
>       assert 1 == 2
E       assert 1 == 2
tests/b.py:4: AssertionError
=========================== short test summary info ============================
FAILED tests/a.py::test_alpha - TypeError: transcribe() missing 2 required
FAILED tests/b.py::test_beta - assert 1 == 2
2 failed, 5 passed in 2.00s
"""

    def test_returns_only_the_section_for_that_test(self):
        out = triage.pytest_excerpt(self.LOG, "tests/a.py::test_alpha")
        assert "missing 2 required positional arguments" in out
        assert "test_beta" not in out

    def test_falls_back_to_the_summary_line_when_no_section_matches(self):
        out = triage.pytest_excerpt(self.LOG, "tests/zzz.py::test_missing")
        assert out == "" or "zzz" in out

    def test_truncates_a_huge_section(self):
        log = "____ test_x ____\n" + ("noise\n" * 5000) + "==== short test summary info ====\n"
        assert len(triage.pytest_excerpt(log, "tests/a.py::test_x").splitlines()) <= triage.MAX_EXCERPT_LINES


# --------------------------------------------------------------------------- validation

def _patch_repo(tmp_path: Path) -> tuple[Path, str]:
    """A repo whose single test asserts twice and passes."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "mod.py").write_text("def f():\n    return 1\n")
    (repo / "tests" / "test_mod.py").write_text(
        "from mod import f\n\n\ndef test_f():\n    assert f() == 1\n    assert f() > 0\n"
    )
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _pytest_cmd(tmp_path: Path, *, ok: bool) -> str:
    """Stand-in for the verification pytest run."""
    p = tmp_path / ("py_ok.sh" if ok else "py_bad.sh")
    p.write_text("#!/usr/bin/env bash\nexit %d\n" % (0 if ok else 1))
    p.chmod(0o755)
    return str(p)


NEW_TEST = "from mod import f\n\n\ndef test_f():\n    assert f() == 1\n    assert f() >= 0\n"


class TestValidateProposal:
    """Nothing reaches the operator unvalidated: a patch that touches a
    non-test file, does not parse, weakens the assertions, or does not actually
    make the test pass is downgraded to a diagnosis."""

    def test_accepts_a_patch_that_parses_keeps_asserts_and_passes(self, tmp_path, monkeypatch):
        repo, sha = _patch_repo(tmp_path)
        monkeypatch.setenv("HERMES_SYNC_TRIAGE_PYTEST_CMD", _pytest_cmd(tmp_path, ok=True))
        ok, reason = triage.validate_proposal(
            repo=repo, merge_sha=sha, test_file="tests/test_mod.py",
            patch=NEW_TEST, test_ids=["tests/test_mod.py::test_f"])
        assert ok, reason

    def test_rejects_a_patch_for_a_non_test_file(self, tmp_path, monkeypatch):
        repo, sha = _patch_repo(tmp_path)
        monkeypatch.setenv("HERMES_SYNC_TRIAGE_PYTEST_CMD", _pytest_cmd(tmp_path, ok=True))
        ok, reason = triage.validate_proposal(
            repo=repo, merge_sha=sha, test_file="mod.py",
            patch="def f():\n    return 2\n", test_ids=["tests/test_mod.py::test_f"])
        assert not ok and "test file" in reason

    def test_rejects_a_patch_that_does_not_parse(self, tmp_path, monkeypatch):
        repo, sha = _patch_repo(tmp_path)
        monkeypatch.setenv("HERMES_SYNC_TRIAGE_PYTEST_CMD", _pytest_cmd(tmp_path, ok=True))
        ok, reason = triage.validate_proposal(
            repo=repo, merge_sha=sha, test_file="tests/test_mod.py",
            patch="def test_f(:\n", test_ids=["tests/test_mod.py::test_f"])
        assert not ok and "parse" in reason

    def test_rejects_a_patch_that_drops_assertions(self, tmp_path, monkeypatch):
        """The cheapest way to make a red test green is to assert less. A patch
        that does is the shape of the failure mode this whole gate exists to
        prevent, so it never reaches the operator as a suggestion."""
        repo, sha = _patch_repo(tmp_path)
        monkeypatch.setenv("HERMES_SYNC_TRIAGE_PYTEST_CMD", _pytest_cmd(tmp_path, ok=True))
        ok, reason = triage.validate_proposal(
            repo=repo, merge_sha=sha, test_file="tests/test_mod.py",
            patch="from mod import f\n\n\ndef test_f():\n    assert f() == 1\n",
            test_ids=["tests/test_mod.py::test_f"])
        assert not ok and "assert" in reason

    def test_rejects_a_patch_whose_tests_still_fail(self, tmp_path, monkeypatch):
        repo, sha = _patch_repo(tmp_path)
        monkeypatch.setenv("HERMES_SYNC_TRIAGE_PYTEST_CMD", _pytest_cmd(tmp_path, ok=False))
        ok, reason = triage.validate_proposal(
            repo=repo, merge_sha=sha, test_file="tests/test_mod.py",
            patch=NEW_TEST, test_ids=["tests/test_mod.py::test_f"])
        assert not ok and "still" in reason

    def test_leaves_no_worktree_behind(self, tmp_path, monkeypatch):
        repo, sha = _patch_repo(tmp_path)
        monkeypatch.setenv("HERMES_SYNC_TRIAGE_PYTEST_CMD", _pytest_cmd(tmp_path, ok=True))
        triage.validate_proposal(repo=repo, merge_sha=sha, test_file="tests/test_mod.py",
                                 patch=NEW_TEST, test_ids=["tests/test_mod.py::test_f"])
        assert len(_git(repo, "worktree", "list").splitlines()) == 1


# --------------------------------------------------------------------------- end to end

def _model_cmd(tmp_path: Path, answer: dict, name="model.py") -> str:
    p = tmp_path / name
    p.write_text(
        "import json,sys\n"
        "sys.stdin.read()\n"
        f"sys.stdout.write(json.dumps({answer!r}))\n"
    )
    return f"{sys.executable} {p}"


class TestRunTriage:
    def _world(self, tmp_path: Path, state: Path) -> tuple[Path, str]:
        repo, sha = _patch_repo(tmp_path)
        (state / "gate-post.log").write_text(
            "____ test_f ____\nE   TypeError: f() missing 1 required positional argument\n"
            "==== short test summary info ====\n"
            "FAILED tests/test_mod.py::test_f - TypeError\n1 failed in 1.00s\n"
        )
        (state / "gate-failures.json").write_text(json.dumps(
            {"merge_sha": sha, "before": sha, "new_failures": ["tests/test_mod.py::test_f"],
             "created_at": "2026-08-16T00:00:00+00:00"}))
        (state / "apply-prepare.json").write_text(json.dumps(
            {"schema": "upstream-sync-apply/v1", "status": "ready", "local_base": sha,
             "upstream_head": sha, "merge_sha": sha, "conflicts": ["mod.py"]}))
        return repo, sha

    @pytest.fixture()
    def state(self, tmp_path):
        d = tmp_path / "state"
        d.mkdir()
        return d

    def test_writes_an_armed_proposal_the_operator_can_answer(self, tmp_path, state, monkeypatch):
        repo, sha = self._world(tmp_path, state)
        monkeypatch.setenv("HERMES_SYNC_TRIAGE_CMD", _model_cmd(tmp_path, {
            "verdict": "test_outdated", "explanation": "upstream added a parameter",
            "assertion_delta": "none", "patch": NEW_TEST}))
        monkeypatch.setenv("HERMES_SYNC_TRIAGE_PYTEST_CMD", _pytest_cmd(tmp_path, ok=True))

        rc = triage.run_triage(state=state, repo=repo)

        assert rc == 0
        out = json.loads((state / "gate-triage.json").read_text())
        assert out["schema"] == "upstream-sync-triage/v1"
        assert out["status"] == "awaiting_triage"
        assert out["merge_sha"] == sha
        prop = out["proposals"][0]
        assert prop["test_file"] == "tests/test_mod.py"
        assert prop["verdict"] == "test_outdated"
        assert prop["patch"] == NEW_TEST
        assert prop["test_ids"] == ["tests/test_mod.py::test_f"]
        assert "TypeError" in prop["excerpt"]

    def test_a_patch_that_fails_validation_is_downgraded_to_a_diagnosis(self, tmp_path, state, monkeypatch):
        repo, sha = self._world(tmp_path, state)
        monkeypatch.setenv("HERMES_SYNC_TRIAGE_CMD", _model_cmd(tmp_path, {
            "verdict": "test_outdated", "explanation": "weakened",
            "patch": "from mod import f\n\n\ndef test_f():\n    assert True\n"}))
        monkeypatch.setenv("HERMES_SYNC_TRIAGE_PYTEST_CMD", _pytest_cmd(tmp_path, ok=True))

        triage.run_triage(state=state, repo=repo)

        prop = json.loads((state / "gate-triage.json").read_text())["proposals"][0]
        assert prop["verdict"] == "unsure"
        assert not prop["patch"]
        assert "assert" in prop["rejected_reason"]

    def test_a_behaviour_lost_verdict_carries_no_patch(self, tmp_path, state, monkeypatch):
        """A merge that dropped our behaviour is fixed in the merge, not in the
        test — offering a test patch there is offering to hide the regression."""
        repo, sha = self._world(tmp_path, state)
        monkeypatch.setenv("HERMES_SYNC_TRIAGE_CMD", _model_cmd(tmp_path, {
            "verdict": "behaviour_lost", "explanation": "the local guard is gone", "patch": NEW_TEST}))
        monkeypatch.setenv("HERMES_SYNC_TRIAGE_PYTEST_CMD", _pytest_cmd(tmp_path, ok=True))

        triage.run_triage(state=state, repo=repo)

        prop = json.loads((state / "gate-triage.json").read_text())["proposals"][0]
        assert prop["verdict"] == "behaviour_lost"
        assert not prop["patch"]

    def test_a_dead_model_still_produces_a_diagnosis_file(self, tmp_path, state, monkeypatch):
        """Triage is best effort: its failure must not change the outcome of the
        gate, and the operator still gets the failing tests named."""
        repo, sha = self._world(tmp_path, state)
        monkeypatch.setenv("HERMES_SYNC_TRIAGE_CMD", "false")

        rc = triage.run_triage(state=state, repo=repo)

        assert rc == 0
        out = json.loads((state / "gate-triage.json").read_text())
        assert out["status"] == "awaiting_triage"
        assert out["proposals"][0]["verdict"] == "unsure"
        assert not out["proposals"][0]["patch"]

    def test_no_failures_writes_nothing(self, tmp_path, state, monkeypatch):
        repo, sha = self._world(tmp_path, state)
        (state / "gate-failures.json").write_text(json.dumps(
            {"merge_sha": sha, "before": sha, "new_failures": []}))
        assert triage.run_triage(state=state, repo=repo) == 0
        assert not (state / "gate-triage.json").exists()

    def test_v2_blocking_failures_preserve_legacy_triage_count(
        self, tmp_path, monkeypatch
    ):
        """The v2 blocking list must drive the same findings as v1 new_failures."""
        repo, sha = _patch_repo(tmp_path)
        test_id = "tests/test_mod.py::test_f"
        post_log = (
            "FAILED tests/test_mod.py::test_f - TypeError\n"
            "1 failed in 1.00s\n"
        )
        prepare = {
            "schema": "upstream-sync-apply/v1",
            "status": "ready",
            "local_base": sha,
            "upstream_head": sha,
            "merge_sha": sha,
            "conflicts": ["mod.py"],
        }
        item = {
            "path": "tests/test_mod.py",
            "nodeid": test_id,
            "classification": "fork_regression",
        }
        monkeypatch.setenv(
            "HERMES_SYNC_TRIAGE_CMD",
            _model_cmd(tmp_path, {
                "verdict": "test_outdated",
                "explanation": "upstream changed the contract",
                "assertion_delta": "none",
                "patch": NEW_TEST,
            }),
        )
        monkeypatch.setenv("HERMES_SYNC_TRIAGE_PYTEST_CMD", _pytest_cmd(tmp_path, ok=True))

        def run(payload: dict, name: str) -> dict:
            state = tmp_path / name
            state.mkdir()
            (state / "gate-post.log").write_text(post_log)
            (state / "apply-prepare.json").write_text(json.dumps(prepare))
            (state / "gate-failures.json").write_text(json.dumps(payload))
            assert triage.run_triage(state=state, repo=repo) == 0
            return json.loads((state / "gate-triage.json").read_text())

        legacy = run(
            {
                "schema_version": "upstream-sync-gate-failures/v1",
                "merge_sha": sha,
                "before": sha,
                "new_failures": [test_id],
            },
            "legacy",
        )
        v2 = run(
            {
                "schema_version": "upstream-sync-gate-failures/v2",
                "merge_sha": sha,
                "before": sha,
                "common_path": [item],
                "post_only_path": [],
                "pre_existing": [],
                "unknown": [],
                "unreadable_runs": [],
                "blocking_failures": [item],
                "new_failures": [],
            },
            "v2",
        )

        assert len(v2["proposals"]) == len(legacy["proposals"]) == 1
        assert v2["proposals"][0]["test_ids"] == legacy["proposals"][0]["test_ids"]

    def test_v2_persistence_is_sorted_unique_blocking_union(self):
        common = {
            "path": "tests/a.py",
            "nodeid": "tests/a.py::test_a",
            "classification": "fork_regression",
        }
        duplicate = {**common, "classification": "duplicate_must_not_win"}
        post_only = {
            "path": "tests/b.py",
            "nodeid": "tests/b.py::test_b",
            "classification": "merge_resolution_or_local_introduced",
        }
        payload = gate.build_gate_failures_payload(
            classification={
                "common_path": [post_only, common, duplicate],
                "post_only_path": [post_only],
                "pre_existing": [{"path": "tests/c.py", "nodeid": "tests/c.py::test_c"}],
                "unknown": [{"path": "tests/d.py", "nodeid": "tests/d.py::test_d"}],
                "unreadable_runs": [{"source": "merged", "stage": "collect"}],
            },
            merge_sha="a" * 40,
            before="b" * 40,
            legacy_failures=["tests/b.py::test_b", "tests/b.py::test_b"],
            created_at="2026-08-24T00:00:00+00:00",
        )

        assert payload["schema_version"] == "upstream-sync-gate-failures/v2"
        assert [item["nodeid"] for item in payload["blocking_failures"]] == [
            "tests/a.py::test_a",
            "tests/b.py::test_b",
        ]
        assert payload["blocking_failures"][0]["classification"] == "fork_regression"
        assert payload["pre_existing"]
        assert payload["unknown"]
        assert payload["unreadable_runs"]
        assert payload["new_failures"] == ["tests/b.py::test_b"]
