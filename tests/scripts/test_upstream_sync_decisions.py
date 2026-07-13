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


def _entry(files, subjects, decision, apply_count=1):
    return {
        "fingerprint": mod.feature_fingerprint(files, subjects),
        "files": list(files), "local_subjects": list(subjects),
        "decision": decision, "created_at": "2026-07-01T00:00:00Z",
        "last_applied_at": "2026-07-01T00:00:00Z", "apply_count": apply_count,
    }


def _mem(*entries):
    return {"schema": "upstream-sync-decisions/v1", "updated_at": None, "entries": list(entries)}


def test_partition_exact_match_is_remembered():
    memory = _mem(_entry(["a.py"], ["feat: a"], "keep-local"))
    feats = mod.group_features([_conflict("a.py", ["feat: a"])])
    result = mod.partition(feats, memory)
    assert len(result["remembered"]) == 1
    assert not result["new"]
    r = result["remembered"][0]
    assert r.decision == "keep-local" and r.source == "memory"


def test_partition_subset_of_files_is_remembered():
    memory = _mem(_entry(["a.py", "b.py"], ["feat: a"], "keep-local"))
    feats = mod.group_features([_conflict("a.py", ["feat: a"])])  # fewer files
    result = mod.partition(feats, memory)
    assert len(result["remembered"]) == 1


def test_partition_superset_of_files_is_new():
    memory = _mem(_entry(["a.py"], ["feat: a"], "keep-local"))
    feats = mod.group_features([_conflict("a.py", ["feat: a"]), _conflict("b.py", ["feat: a"])])
    # a.py and b.py share subject set -> one feature with 2 files (superset of memory)
    result = mod.partition(feats, memory)
    assert not result["remembered"]
    assert len(result["new"]) == 1


def test_partition_changed_subject_set_is_new():
    memory = _mem(_entry(["a.py"], ["feat: a"], "keep-local"))
    feats = mod.group_features([_conflict("a.py", ["feat: DIFFERENT"])])
    result = mod.partition(feats, memory)
    assert not result["remembered"] and len(result["new"]) == 1


def test_partition_security_path_forced_to_new_even_on_match():
    memory = _mem(_entry(["gateway/auth_pairing.py"], ["feat: a"], "keep-local"))
    feats = mod.group_features([_conflict("gateway/auth_pairing.py", ["feat: a"])])
    result = mod.partition(feats, memory)
    assert not result["remembered"]
    assert len(result["new"]) == 1


def test_partition_empty_subjects_is_new():
    memory = _mem()
    feats = mod.group_features([{"file": "a.py", "local_commits": [], "upstream_commits": []}])
    result = mod.partition(feats, memory)
    assert not result["remembered"] and len(result["new"]) == 1


def test_partition_ambiguous_conflicting_decisions_is_new():
    memory = _mem(
        _entry(["a.py"], ["feat: a"], "keep-local"),
        _entry(["a.py"], ["feat: a"], "take-upstream"),
    )
    feats = mod.group_features([_conflict("a.py", ["feat: a"])])
    result = mod.partition(feats, memory)
    assert not result["remembered"]
    assert len(result["new"]) == 1


def test_partition_ambiguous_same_decision_is_remembered():
    memory = _mem(
        _entry(["a.py", "b.py"], ["feat: a"], "keep-local"),
        _entry(["a.py", "c.py"], ["feat: a"], "keep-local"),
    )
    feats = mod.group_features([_conflict("a.py", ["feat: a"])])
    result = mod.partition(feats, memory)
    assert len(result["remembered"]) == 1
    assert result["remembered"][0].decision == "keep-local"


def _feat(files, subjects, decision):
    files = tuple(sorted(files)); subjects = tuple(sorted(subjects))
    return mod.Feature(files=files, subjects=subjects,
                       fingerprint=mod.feature_fingerprint(files, subjects), decision=decision)


def test_record_creates_entry():
    memory = _mem()
    mod.record_decisions(memory, [_feat(["a.py"], ["feat: a"], "keep-local")], now="2026-07-13T10:00:00Z")
    assert len(memory["entries"]) == 1
    e = memory["entries"][0]
    assert e["decision"] == "keep-local" and e["apply_count"] == 1
    assert e["created_at"] == e["last_applied_at"] == "2026-07-13T10:00:00Z"
    assert memory["updated_at"] == "2026-07-13T10:00:00Z"


def test_record_bumps_on_new_run():
    memory = _mem()
    f = _feat(["a.py"], ["feat: a"], "keep-local")
    mod.record_decisions(memory, [f], now="2026-07-13T10:00:00Z")
    mod.record_decisions(memory, [f], now="2026-07-20T10:00:00Z")
    assert len(memory["entries"]) == 1
    assert memory["entries"][0]["apply_count"] == 2
    assert memory["entries"][0]["last_applied_at"] == "2026-07-20T10:00:00Z"


def test_record_idempotent_within_same_now():
    memory = _mem()
    f = _feat(["a.py"], ["feat: a"], "keep-local")
    mod.record_decisions(memory, [f], now="2026-07-13T10:00:00Z")
    mod.record_decisions(memory, [f], now="2026-07-13T10:00:00Z")
    assert memory["entries"][0]["apply_count"] == 1


def test_record_rejects_invalid_decision():
    memory = _mem()
    with pytest.raises(ValueError):
        mod.record_decisions(memory, [_feat(["a.py"], ["feat: a"], "keep-remote")], now="2026-07-13T10:00:00Z")


def test_record_invalid_in_batch_is_atomic():
    memory = _mem()
    good = _feat(["a.py"], ["feat: a"], "keep-local")
    bad = _feat(["b.py"], ["feat: b"], "not-a-decision")
    with pytest.raises(ValueError):
        mod.record_decisions(memory, [good, bad], now="2026-07-13T10:00:00Z")
    assert memory["entries"] == []          # nothing partially applied
    assert memory.get("updated_at") in (None,)  # untouched


def test_load_memory_non_dict_json_raises(tmp_path):
    p = tmp_path / "arr.json"
    p.write_text("[1, 2, 3]")
    with pytest.raises(ValueError):
        mod.load_memory(p)


def test_cli_partition_reports_new_and_remembered(tmp_path, capsys):
    preflight = {"schema": "upstream-sync-preflight/v1", "conflicts": [
        _conflict("a.py", ["feat: a"]),
        _conflict("b.py", ["feat: b"]),
    ]}
    pf = tmp_path / "preflight.json"; pf.write_text(json.dumps(preflight))
    mem = tmp_path / "memory.json"
    mod.save_memory(mem, _mem(_entry(["a.py"], ["feat: a"], "keep-local")))
    mod.main(["partition", "--preflight", str(pf), "--memory", str(mem)])
    out = json.loads(capsys.readouterr().out)
    assert [r["files"] for r in out["remembered"]] == [["a.py"]]
    assert out["remembered"][0]["decision"] == "keep-local"
    assert [n["files"] for n in out["new"]] == [["b.py"]]


def test_cli_record_persists_memory(tmp_path, capsys):
    pending = {"schema": "upstream-sync-pending/v1", "features": [
        {"id": 1, "name": "A", "files": ["a.py"],
         "local_commits": [{"sha": "x", "subject": "feat: a"}], "decision": "keep-local"},
        {"id": 2, "name": "B (undecided)", "files": ["b.py"],
         "local_commits": [{"sha": "y", "subject": "feat: b"}]},  # no decision -> skipped
    ]}
    pj = tmp_path / "pending.json"; pj.write_text(json.dumps(pending))
    mem = tmp_path / "memory.json"
    mod.main(["record", "--pending", str(pj), "--memory", str(mem), "--now", "2026-07-13T10:00:00Z"])
    out = json.loads(capsys.readouterr().out)
    assert out == {"entries": 1}
    saved = mod.load_memory(mem)
    assert saved["entries"][0]["local_subjects"] == ["feat: a"]
    assert saved["updated_at"] == "2026-07-13T10:00:00Z"
