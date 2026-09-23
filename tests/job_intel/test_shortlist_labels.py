"""Owner label writes are replayable and preserve every prior decision."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib.util
import json
import stat
from pathlib import Path
import sys

import pytest


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "job_intel_shortlist_labels.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("job_intel_shortlist_labels", MODULE_PATH)
assert spec and spec.loader
labels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(labels)


def artifact() -> dict:
    return {
        "release_id": "shortlist-2026-W39",
        "items": [
            {"vacancy_key": "k1", "company": "Example", "title": "CPO",
             "location": "Remote", "url": "https://example.org/k1", "description": "Lead product."},
        ],
        "rejected_sample": [
            {"vacancy_key": "k2", "company": "Other", "title": "VP Product",
             "location": "Berlin", "url": "https://example.org/k2", "description": "Own roadmap."},
        ],
    }


def existing_file(path: Path) -> None:
    path.write_text(json.dumps({
        "labels": [{"vacancy_key": "k1", "company": "Example", "title": "CPO",
                    "location": "Remote", "url": "https://example.org/k1",
                    "verdict": "yes", "why": "old review", "found_in_db": True,
                    "description_chars": 13}],
        "rounds": ["2026-09-23-shortlist-20260922T084423Z"],
        "label_history": [{"backup": "old.json", "change": "manual fix",
                           "changed_at": "2026-09-22T00:00:00Z", "reason": "prior"}],
    }), encoding="utf-8")


def apply(path: Path, decisions: dict, *, file_hash: str = "a" * 64,
          reply_ts: str = "1790161200.000001",
          release_id: str = "shortlist-2026-W39") -> dict:
    source = artifact()
    source["release_id"] = release_id
    return labels.apply_decisions(
        path, source, release_id=release_id, attempt_id="attempt-001",
        file_hash=file_hash, response_ts=reply_ts, decisions=decisions,
        now=datetime(2026, 9, 23, 11, tzinfo=timezone.utc),
    )


def test_history_append_conflict_current_view_and_exact_replay(tmp_path: Path) -> None:
    path = tmp_path / "labels.json"
    existing_file(path)
    decisions = {
        "k1": {"owner_decision": "no", "owner_note": "Scope too narrow", "sheet": "shortlist"},
        "k2": {"owner_decision": "yes", "owner_note": "Interesting", "sheet": "rejected_sample"},
    }
    delta = apply(path, decisions)
    result = json.loads(path.read_text())
    assert delta == {"history_added": 2, "labels_added": 1, "labels_updated": 1, "noop": False}
    assert result["label_history"][0]["reason"] == "prior"
    assert len(result["label_history"]) == 3
    assert {row["vacancy_key"]: row["verdict"] for row in result["labels"]} == {"k1": "no", "k2": "yes"}
    assert result["label_history"][1]["prior"]["verdict"] == "yes"
    assert result["label_history"][1]["effective"] is True
    assert result["rounds"][-1] == "shortlist-2026-W39/attempt-001"
    before = path.read_bytes()
    assert apply(path, decisions) == {"history_added": 0, "labels_added": 0, "labels_updated": 0, "noop": True}
    assert path.read_bytes() == before


def test_same_operation_key_with_changed_decision_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "labels.json"
    existing_file(path)
    apply(path, {"k1": {"owner_decision": "no", "owner_note": "A", "sheet": "shortlist"}})
    before = path.read_bytes()
    with pytest.raises(labels.LabelStoreError, match="operation key conflict"):
        apply(path, {"k1": {"owner_decision": "yes", "owner_note": "B", "sheet": "shortlist"}})
    assert path.read_bytes() == before


def test_older_reply_is_recorded_but_does_not_replace_effective_label(tmp_path: Path) -> None:
    path = tmp_path / "labels.json"
    existing_file(path)
    apply(path, {"k1": {"owner_decision": "no", "owner_note": "new", "sheet": "shortlist"}},
          reply_ts="1790161200.000002")
    delta = apply(path, {"k1": {"owner_decision": "yes", "owner_note": "old", "sheet": "shortlist"}},
                  file_hash="b" * 64, reply_ts="1790161200.000001", release_id="shortlist-2026-W38")
    result = json.loads(path.read_text())
    assert delta == {"history_added": 1, "labels_added": 0, "labels_updated": 0, "noop": False}
    assert result["labels"][0]["verdict"] == "no"
    assert result["label_history"][-1]["effective"] is False


def test_concurrent_distinct_file_operations_keep_both_events(tmp_path: Path) -> None:
    path = tmp_path / "labels.json"
    existing_file(path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(apply, path, {"k1": {"owner_decision": "no", "owner_note": "A", "sheet": "shortlist"}},
                        file_hash="a" * 64, reply_ts="1790161200.000001"),
            pool.submit(apply, path, {"k2": {"owner_decision": "yes", "owner_note": "B", "sheet": "rejected_sample"}},
                        file_hash="a" * 64, reply_ts="1790161200.000001"),
        ]
        for future in futures:
            future.result()
    result = json.loads(path.read_text())
    assert len(result["label_history"]) == 3
    assert {row["vacancy_key"]: row["verdict"] for row in result["labels"]} == {"k1": "no", "k2": "yes"}


def test_unknown_key_or_malformed_existing_labels_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "labels.json"
    existing_file(path)
    with pytest.raises(labels.LabelStoreError, match="unknown vacancy_key"):
        apply(path, {"other": {"owner_decision": "yes", "owner_note": "", "sheet": "shortlist"}})
    path.write_text('{"labels": []}')
    with pytest.raises(labels.LabelStoreError, match="schema"):
        apply(path, {"k1": {"owner_decision": "no", "owner_note": "", "sheet": "shortlist"}})


def test_second_file_for_same_release_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "labels.json"
    existing_file(path)
    apply(path, {"k1": {"owner_decision": "no", "owner_note": "first", "sheet": "shortlist"}})
    with pytest.raises(labels.LabelStoreError, match="already imported"):
        apply(path, {"k1": {"owner_decision": "yes", "owner_note": "replacement", "sheet": "shortlist"}},
              file_hash="b" * 64, reply_ts="1790161200.000002")


def test_new_decision_drops_stale_round_fields_and_preserves_file_mode(tmp_path: Path) -> None:
    path = tmp_path / "labels.json"
    existing_file(path)
    payload = json.loads(path.read_text())
    payload["labels"][0].update({
        "round": "2026-09-19-round2", "held_back_reason": "old reason",
        "verdict_previous": "no", "owner_ruling_2026_09_19": "yes",
    })
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    apply(path, {"k1": {"owner_decision": "no", "owner_note": "new", "sheet": "shortlist"}})
    result = json.loads(path.read_text())
    current = result["labels"][0]
    assert current["verdict"] == "no"
    assert set(current).isdisjoint({"round", "held_back_reason", "verdict_previous", "owner_ruling_2026_09_19"})
    assert result["label_history"][-1]["prior"]["round"] == "2026-09-19-round2"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
