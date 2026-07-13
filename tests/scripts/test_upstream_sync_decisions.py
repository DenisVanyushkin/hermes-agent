import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "upstream_sync_decisions.py"
SPEC = importlib.util.spec_from_file_location("upstream_sync_decisions", SCRIPT_PATH)
mod = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(mod)


def test_fingerprint_ignores_sha_and_order():
    a = mod.feature_fingerprint(["b.py", "a.py"], ["feat: x", "fix: y"])
    b = mod.feature_fingerprint(["a.py", "b.py"], ["fix: y", "feat: x"])
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_fingerprint_changes_with_subject():
    a = mod.feature_fingerprint(["a.py"], ["feat: x"])
    b = mod.feature_fingerprint(["a.py"], ["feat: z"])
    assert a != b


def test_load_memory_missing_returns_skeleton(tmp_path):
    m = mod.load_memory(tmp_path / "nope.json")
    assert m == {"schema": "upstream-sync-decisions/v1", "updated_at": None, "entries": []}


def test_load_memory_blank_returns_skeleton(tmp_path):
    p = tmp_path / "blank.json"
    p.write_text("   \n")
    assert mod.load_memory(p)["entries"] == []


def test_load_memory_malformed_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    with pytest.raises(ValueError):
        mod.load_memory(p)


def test_load_memory_wrong_schema_raises(tmp_path):
    p = tmp_path / "wrong.json"
    p.write_text(json.dumps({"schema": "something/v9", "entries": []}))
    with pytest.raises(ValueError):
        mod.load_memory(p)


def test_save_then_load_roundtrips(tmp_path):
    p = tmp_path / "m.json"
    memory = {"schema": "upstream-sync-decisions/v1", "updated_at": "2026-07-13T00:00:00Z", "entries": []}
    mod.save_memory(p, memory)
    assert mod.load_memory(p) == memory


def _conflict(file, subjects):
    return {"file": file, "local_commits": [{"sha": "deadbeef", "subject": s} for s in subjects], "upstream_commits": []}


def test_group_clusters_files_sharing_subject_set():
    conflicts = [
        _conflict("gateway/router.py", ["feat: router"]),
        _conflict("gateway/router_helpers.py", ["feat: router"]),
        _conflict("agent/approval.py", ["feat: approval"]),
    ]
    feats = mod.group_features(conflicts)
    assert len(feats) == 2
    by_files = {f.files: f for f in feats}
    assert ("gateway/router.py", "gateway/router_helpers.py") in by_files
    assert ("agent/approval.py",) in by_files


def test_group_fingerprint_is_rebase_stable():
    # Same subjects, different SHAs -> same fingerprint.
    a = mod.group_features([{"file": "x.py", "local_commits": [{"sha": "aaa", "subject": "feat: x"}], "upstream_commits": []}])
    b = mod.group_features([{"file": "x.py", "local_commits": [{"sha": "zzz", "subject": "feat: x"}], "upstream_commits": []}])
    assert a[0].fingerprint == b[0].fingerprint


def test_group_deduplicates_and_strips_subjects():
    feats = mod.group_features([
        _conflict("x.py", ["feat: x ", "feat: x", "fix: y"]),
    ])
    assert feats[0].subjects == ("feat: x", "fix: y")
