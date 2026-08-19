"""LLM as a function: one call per conflict hunk, validated, whole-file
rollback on any failure. The model is behind ``HERMES_SYNC_RESOLVER_CMD`` here
— a command that gets the hunk payload on stdin and prints the resolution — so
tests exercise the exact contract the host uses without a network."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from scripts import upstream_sync_llm as llm  # noqa: E402

APPLY = REPO_ROOT / "scripts" / "upstream_sync_apply.py"


def _block(ours, base, theirs):
    return (f"<<<<<<< HEAD\n{ours}||||||| base\n{base}=======\n{theirs}>>>>>>> up\n")


def _fake_resolver(tmp_path: Path, script_body: str) -> str:
    p = tmp_path / "resolver.py"
    p.write_text(script_body)
    return f"{sys.executable} {p}"


# A resolver that returns ours+theirs joined by a marker-free line.
CONCAT = """import json,sys
h=json.load(sys.stdin)
sys.stdout.write(h["ours"] + h["theirs"])
"""
# A resolver that leaks a conflict marker (must be rejected).
LEAKY = """import json,sys
h=json.load(sys.stdin)
sys.stdout.write("<<<<<<< HEAD\\n" + h["ours"])
"""
# A resolver that answers with unparsable Python.
BROKEN_PY = """import json,sys
sys.stdout.write("def broken(:\\n    pass\\n")
"""
# A resolver that records every call and answers per attempt: first leaky, then fine.
RETRY = """import json,sys,os
h=json.load(sys.stdin)
log=os.environ["RLOG"]
n=sum(1 for _ in open(log)) if os.path.exists(log) else 0
open(log,"a").write(json.dumps({"attempt": h.get("attempt"), "prev_error": h.get("previous_error")})+"\\n")
if n==0:
    sys.stdout.write("<<<<<<< leaked\\n")
else:
    sys.stdout.write(h["ours"] + h["theirs"])
"""


class TestResolveText:
    def test_each_hunk_is_one_call_with_context_and_decision(self, tmp_path, monkeypatch):
        rec = tmp_path / "calls.jsonl"
        monkeypatch.setenv("HERMES_SYNC_RESOLVER_CMD", _fake_resolver(tmp_path,
            "import json,sys,os\nh=json.load(sys.stdin)\n"
            f"open({str(rec)!r},'a').write(json.dumps(h)+'\\n')\n"
            "sys.stdout.write(h['ours']+h['theirs'])\n"))
        text = ("head\n" + _block("a = 1\n", "", "b = 2\n") + "mid\n"
                + _block("x = 3\n", "x = 1\n", "x = 2\n") + "tail\n")
        out, report = llm.resolve_text(text, path="m.py", decision="merge-both",
                                       local_subjects=["feat: a"], upstream_head="bbbb")
        assert out == "head\na = 1\nb = 2\nmid\nx = 3\nx = 2\ntail\n"
        assert report["resolved"] == 2 and report["failed"] == 0
        calls = [json.loads(l) for l in rec.read_text().splitlines()]
        assert len(calls) == 2
        assert calls[0]["path"] == "m.py" and calls[0]["decision"] == "merge-both"
        assert calls[0]["ours"] == "a = 1\n" and calls[0]["theirs"] == "b = 2\n" and calls[0]["base"] == ""
        assert "head" in calls[0]["before"] and "mid" in calls[0]["after"]
        assert calls[0]["local_subjects"] == ["feat: a"] and calls[0]["upstream_head"] == "bbbb"
        assert calls[1]["base"] == "x = 1\n"

    def test_a_leaked_marker_is_retried_once_with_the_error_then_accepted(self, tmp_path, monkeypatch):
        rlog = tmp_path / "rlog"
        monkeypatch.setenv("RLOG", str(rlog))
        monkeypatch.setenv("HERMES_SYNC_RESOLVER_CMD", _fake_resolver(tmp_path, RETRY))
        text = _block("a\n", "", "b\n")
        out, report = llm.resolve_text(text, path="f.txt", decision="merge-both")
        assert out == "a\nb\n" and report["resolved"] == 1
        attempts = [json.loads(l) for l in rlog.read_text().splitlines()]
        assert [a["attempt"] for a in attempts] == [1, 2]
        assert "marker" in (attempts[1]["prev_error"] or "").lower()

    def test_persistent_marker_leak_fails_the_hunk(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_SYNC_RESOLVER_CMD", _fake_resolver(tmp_path, LEAKY))
        text = _block("a\n", "", "b\n")
        out, report = llm.resolve_text(text, path="f.txt", decision="merge-both")
        assert out == text                                     # untouched
        assert report["resolved"] == 0 and report["failed"] == 1
        assert "marker" in report["errors"][0].lower()

    def test_python_file_must_parse_after_resolution(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_SYNC_RESOLVER_CMD", _fake_resolver(tmp_path, BROKEN_PY))
        text = "import os\n" + _block("a = 1\n", "", "b = 2\n")
        out, report = llm.resolve_text(text, path="m.py", decision="merge-both")
        assert out == text
        assert report["failed"] == 1 and "syntax" in report["errors"][0].lower()

    def test_non_python_files_skip_the_parse_check(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_SYNC_RESOLVER_CMD", _fake_resolver(tmp_path, BROKEN_PY))
        text = _block("a\n", "", "b\n")
        out, report = llm.resolve_text(text, path="notes.md", decision="merge-both")
        assert report["resolved"] == 1 and "def broken(:" in out

    def test_resolver_command_failure_is_a_hunk_failure_not_a_crash(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_SYNC_RESOLVER_CMD", _fake_resolver(tmp_path, "import sys\nsys.exit(3)\n"))
        text = _block("a\n", "", "b\n")
        out, report = llm.resolve_text(text, path="f.txt", decision="merge-both")
        assert out == text and report["failed"] == 1

    def test_no_resolver_and_no_model_is_reported_not_raised(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HERMES_SYNC_RESOLVER_CMD", raising=False)
        text = _block("a\n", "", "b\n")
        out, report = llm.resolve_text(text, path="f.txt", decision="merge-both",
                                       call_model=lambda payload: (_ for _ in ()).throw(RuntimeError("no creds")))
        assert out == text and report["failed"] == 1 and "no creds" in report["errors"][0]


class TestModelAnswerParsing:
    def test_json_object_answer_is_unwrapped(self):
        assert llm.parse_model_answer('{"resolution": "x = 1\\n"}') == "x = 1\n"

    def test_fenced_json_is_tolerated(self):
        assert llm.parse_model_answer('```json\n{"resolution": "y\\n"}\n```') == "y\n"

    def test_plain_text_answer_is_taken_verbatim(self):
        assert llm.parse_model_answer("just code\n") == "just code\n"

    def test_missing_trailing_newline_is_added_when_sides_had_one(self):
        assert llm.normalize_resolution("a\nb", ours="a\n", theirs="b\n") == "a\nb\n"


class TestResolveLlmSubcommand:
    """resolve-llm walks needs_manual from apply-prepare.json, rewrites the files
    the resolver fully closes, git-adds them, and records the rest as unresolved
    with a reason. Whole-file: a file with any failed hunk keeps ALL its markers."""

    @pytest.fixture()
    def world(self, tmp_path):
        from tests.scripts.test_upstream_sync_apply import _git, _pending, _run, _out  # noqa
        live = tmp_path / "live"; live.mkdir()
        _git(live, "init", "-q", "-b", "local/customizations")
        _git(live, "config", "user.email", "t@t"); _git(live, "config", "user.name", "t")
        (live / "m.py").write_text("import os\n\n\ndef f():\n    return 1\n")
        (live / "n.py").write_text("X = 1\n")
        _git(live, "add", "-A"); _git(live, "commit", "-qm", "base")
        base = _git(live, "rev-parse", "HEAD")
        (live / "m.py").write_text("import os\nimport local\n\n\ndef f():\n    return 1\n")
        (live / "n.py").write_text("X = 3\n")
        _git(live, "add", "-A"); _git(live, "commit", "-qm", "local change")
        local_head = _git(live, "rev-parse", "HEAD")
        _git(live, "checkout", "-qb", "up", base)
        (live / "m.py").write_text("import os\nimport upstream\n\n\ndef f():\n    return 1\n")
        (live / "n.py").write_text("X = 2\n")
        _git(live, "add", "-A"); _git(live, "commit", "-qm", "upstream change")
        upstream_head = _git(live, "rev-parse", "HEAD")
        _git(live, "checkout", "-q", "local/customizations")
        state = tmp_path / "state"; state.mkdir()
        w = {"live": live, "state": state, "local_head": local_head, "upstream_head": upstream_head}
        _pending(w, decision="merge-both", files=("m.py", "n.py"))
        assert _out(_run("prepare", state, live))["status"] == "ready"
        return w

    def _run_resolve(self, world, resolver_cmd):
        env = dict(os.environ)
        env["HERMES_SYNC_RESOLVER_CMD"] = resolver_cmd
        return subprocess.run(
            [sys.executable, str(APPLY), "resolve-llm", "--state", str(world["state"]),
             "--live", str(world["live"])],
            capture_output=True, text=True, timeout=120, env=env,
        )

    def test_files_the_resolver_closes_are_staged_and_reported(self, world, tmp_path):
        from tests.scripts.test_upstream_sync_apply import _git, _out  # noqa
        # m.py: both added an import → mechanical already? No: shared line "import os"
        # is context, ours/theirs are distinct one-liners → mechanical closes it.
        # n.py: real three-way edit → left for the resolver.
        proc = self._run_resolve(world, _fake_resolver(tmp_path,
            "import json,sys\nh=json.load(sys.stdin)\nsys.stdout.write('X = 5\\n')\n"))
        out = _out(proc)
        assert proc.returncode == 0, proc.stderr
        assert out["status"] == "resolved"
        assert out["llm_resolved"] == ["n.py"] and out["unresolved"] == []
        scratch = world["state"] / "scratch"
        assert (scratch / "n.py").read_text() == "X = 5\n"
        assert _git(scratch, "ls-files", "-u") == ""            # staged
        prep = json.loads((world["state"] / "apply-prepare.json").read_text())
        assert prep["llm_resolved"] == ["n.py"] and prep["unresolved"] == []

    def test_a_file_the_resolver_cannot_close_keeps_its_markers_and_is_reported(self, world, tmp_path):
        from tests.scripts.test_upstream_sync_apply import _out  # noqa
        proc = self._run_resolve(world, _fake_resolver(tmp_path, LEAKY))
        out = _out(proc)
        assert proc.returncode == 6
        assert out["status"] == "unresolved"
        assert [u["path"] for u in out["unresolved"]] == ["n.py"]
        assert "marker" in out["unresolved"][0]["reason"].lower()
        text = (world["state"] / "scratch" / "n.py").read_text()
        assert "<<<<<<< " in text and ">>>>>>> " in text     # untouched, still conflicted
