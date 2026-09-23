import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from job_intel_shortlist_backfill import BackfillError, prepare_legacy_seed
from job_intel_shortlist_issued import issued_keys


def _fixture(tmp_path):
    labels_path = tmp_path / "labels.json"
    db_path = tmp_path / "jobs.sqlite3"
    root = tmp_path / "releases"
    root.mkdir()
    rows = [
        {"vacancy_key": "old-a", "company": "Acme", "title": "Chief Product Officer", "verdict": "yes"},
        {"vacancy_key": "old-b", "company": "Beta", "title": "VP Product", "verdict": "no", "round": "2026-09-19-round3"},
        {"vacancy_key": "new-c", "company": "Gamma", "title": "Product Lead", "verdict": "yes",
         "round": "2026-09-23-shortlist-20260922T084423Z"},
    ]
    labels_path.write_text(json.dumps({"labels": rows, "rounds": [], "label_history": []}))
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE vacancies (vacancy_key TEXT, company TEXT, title TEXT)")
    connection.executemany("INSERT INTO vacancies VALUES (?, ?, ?)",
                           [(row["vacancy_key"], row["company"], row["title"]) for row in rows])
    connection.commit()
    connection.close()
    expected = hashlib.sha256(json.dumps(["old-a", "old-b"], separators=(",", ":")).encode()).hexdigest()
    return labels_path, db_path, root, expected


def test_seed_only_exact_historical_keys_and_is_idempotent(tmp_path):
    labels, db, root, expected = _fixture(tmp_path)
    marker = prepare_legacy_seed(labels, db, root, expected_count=2, expected_keys_sha256=expected)
    assert marker["keys"] == ["old-a", "old-b"]
    assert issued_keys(root) == frozenset({"old-a", "old-b"})
    assert prepare_legacy_seed(labels, db, root, expected_count=2,
                               expected_keys_sha256=expected) == marker


@pytest.mark.parametrize("mutation", ["missing_db", "duplicate_db", "wrong_title", "duplicate_label",
                                            "extra_old_label", "changed_expected_digest", "missing_key",
                                            "invalid_verdict"])
def test_seed_fails_closed_without_marker(tmp_path, mutation):
    labels, db, root, expected = _fixture(tmp_path)
    document = json.loads(labels.read_text())
    connection = sqlite3.connect(db)
    if mutation == "missing_db":
        connection.execute("DELETE FROM vacancies WHERE vacancy_key='old-a'")
    elif mutation == "duplicate_db":
        connection.execute("INSERT INTO vacancies VALUES ('old-a','Acme','Chief Product Officer')")
    elif mutation == "wrong_title":
        connection.execute("UPDATE vacancies SET title='Other role' WHERE vacancy_key='old-a'")
    elif mutation == "duplicate_label":
        document["labels"].append(dict(document["labels"][0]))
    elif mutation == "extra_old_label":
        document["labels"][2].pop("round")
    elif mutation == "changed_expected_digest":
        expected = "0" * 64
    elif mutation == "missing_key":
        document["labels"][0]["vacancy_key"] = None
    elif mutation == "invalid_verdict":
        document["labels"][0]["verdict"] = "maybe"
    connection.commit()
    connection.close()
    labels.write_text(json.dumps(document))
    with pytest.raises(BackfillError):
        prepare_legacy_seed(labels, db, root, expected_count=2, expected_keys_sha256=expected)
    assert not (root / "legacy-seed-v1.json").exists()


def test_corrupt_marker_blocks_issued_index(tmp_path):
    labels, db, root, expected = _fixture(tmp_path)
    prepare_legacy_seed(labels, db, root, expected_count=2, expected_keys_sha256=expected)
    marker_path = root / "legacy-seed-v1.json"
    marker = json.loads(marker_path.read_text())
    marker["keys"].append("new-c")
    marker_path.write_text(json.dumps(marker))
    with pytest.raises(ValueError, match="legacy seed"):
        issued_keys(root)
