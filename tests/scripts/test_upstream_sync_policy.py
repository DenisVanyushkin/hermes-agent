"""The decision policy: merge-both by default, ask only for security paths,
memory beats policy. Operator decision of 2026-08-15 (39 of 40 recorded
answers were merge-both)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts import upstream_sync_policy as policy  # noqa: E402
from scripts.upstream_sync_decisions import Feature, feature_fingerprint  # noqa: E402


def _feature(files, subjects):
    files, subjects = tuple(files), tuple(sorted(subjects))
    return Feature(files=files, subjects=subjects, fingerprint=feature_fingerprint(files, subjects))


def _memory(*entries):
    return {"schema": "upstream-sync-decisions/v1", "updated_at": None, "entries": list(entries)}


def _entry(files, subjects, decision):
    return {"fingerprint": feature_fingerprint(files, subjects), "files": list(files),
            "local_subjects": list(subjects), "decision": decision,
            "apply_count": 1, "last_applied_at": "2026-08-01T00:00:00Z"}


class TestDecideFeatures:
    def test_non_security_paths_are_merged_both_by_policy_without_asking(self):
        out = policy.decide_features([_feature(["gateway/run.py"], ["fix(gateway): x"])], _memory())
        assert out[0]["decision"] == "merge-both"
        assert out[0]["source"] == "policy"
        assert out[0]["status"] == "decided"

    def test_security_paths_are_asked(self):
        out = policy.decide_features([_feature(["tools/approval.py"], ["feat: smart approvals"])], _memory())
        assert out[0]["decision"] is None
        assert out[0]["status"] == "awaiting_decision"
        assert "security" in out[0]["why"]

    def test_security_paths_are_asked_even_when_remembered(self):
        # The 2026-07-13 invariant: memory never auto-applies a security path.
        f = _feature(["tools/approval.py"], ["feat: smart approvals"])
        mem = _memory(_entry(f.files, f.subjects, "keep-local"))
        out = policy.decide_features([f], mem)
        assert out[0]["decision"] is None
        assert out[0]["status"] == "awaiting_decision"

    def test_memory_beats_policy_for_plain_paths(self):
        f = _feature(["cron/scheduler.py"], ["fix(cron): topics"])
        mem = _memory(_entry(f.files, f.subjects, "take-upstream"))
        out = policy.decide_features([f], mem)
        assert out[0]["decision"] == "take-upstream"
        assert out[0]["source"] == "memory"

    def test_a_feature_mixing_security_and_plain_files_is_asked(self):
        f = _feature(["gateway/run.py", "hermes_cli/auth_flow.py"], ["feat: sso"])
        out = policy.decide_features([f], _memory())
        assert out[0]["status"] == "awaiting_decision"

    def test_order_is_preserved_and_ids_are_sequential(self):
        feats = [_feature(["b.py"], ["b"]), _feature(["a.py"], ["a"]), _feature(["tools/approval.py"], ["s"])]
        out = policy.number_features(policy.decide_features(feats, _memory()))
        assert [d["id"] for d in out] == ["F1", "F2", "F3"]
        assert [d["files"][0] for d in out] == ["b.py", "a.py", "tools/approval.py"]
        assert policy.needs_operator(out) is True

    def test_nothing_to_ask_when_everything_is_plain(self):
        out = policy.decide_features([_feature(["a.py"], ["a"]), _feature(["b.py"], ["b"])], _memory())
        assert policy.needs_operator(out) is False


class TestDecidePaths:
    def test_bare_paths_get_the_same_policy(self):
        out = policy.decide_paths(["cron/scheduler.py", "tools/approval.py"], _memory())
        by = {d["files"][0]: d for d in out}
        assert by["cron/scheduler.py"]["decision"] == "merge-both"
        assert by["tools/approval.py"]["status"] == "awaiting_decision"

    def test_subjects_feed_the_memory_match(self):
        mem = _memory(_entry(("cron/scheduler.py",), ("fix(cron): topics",), "take-upstream"))
        out = policy.decide_paths(["cron/scheduler.py"], mem,
                                  subjects_by_path={"cron/scheduler.py": ["fix(cron): topics"]})
        assert out[0]["decision"] == "take-upstream" and out[0]["source"] == "memory"

    def test_empty(self):
        assert policy.decide_paths([], _memory()) == []
