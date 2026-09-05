from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_fingerprint_changes_for_result_or_policy_but_not_unrelated_tree_generation():
    from upstream_sync_receipts import fingerprint

    side = {"presence": "PRESENT", "mode": "100644", "oid": "a" * 40}
    first = fingerprint(path="mod.py", kind="lost_definition", symbol="gone", policy="merge-both", base=side, ours=side, theirs=side, result=side)
    changed_result = dict(side, oid="b" * 40)
    second = fingerprint(path="mod.py", kind="lost_definition", symbol="gone", policy="merge-both", base=side, ours=side, theirs=side, result=changed_result)
    changed_policy = fingerprint(path="mod.py", kind="lost_definition", symbol="gone", policy="keep-local", base=side, ours=side, theirs=side, result=side)
    assert first["id"] != second["id"]
    assert first["sha256"] != changed_policy["sha256"]


def test_absent_side_is_explicit():
    from upstream_sync_receipts import fingerprint

    absent = {"presence": "ABSENT", "mode": "ABSENT", "oid": "ABSENT"}
    present = {"presence": "PRESENT", "mode": "120000", "oid": "c" * 40}
    fp = fingerprint(path="link.py", kind="lost_definition", symbol="x", policy="merge-both", base=absent, ours=present, theirs=absent, result=present)
    assert fp["payload"]["base"] == absent
    assert fp["payload"]["ours"]["mode"] == "120000"
