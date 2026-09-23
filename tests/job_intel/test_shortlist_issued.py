"""Delivered receipts, rather than a second index file, own issued identity."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "job_intel_shortlist_issued.py"
spec = importlib.util.spec_from_file_location("job_intel_shortlist_issued", MODULE_PATH)
assert spec and spec.loader
issued = importlib.util.module_from_spec(spec)
spec.loader.exec_module(issued)


def release(root: Path, release_id: str, *, state: str = "delivered", keys: tuple[str, ...] = ("k1", "k2")) -> Path:
    directory = root / release_id
    directory.mkdir()
    csv_path = directory / f"{release_id}.csv"
    projection = hashlib.sha256(json.dumps(list(keys)).encode()).hexdigest()
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["kind", "vacancy_key", "role_fit_verdict"])
        writer.writeheader()
        writer.writerow({"kind": "release", "vacancy_key": release_id, "role_fit_verdict": projection})
        for key in keys:
            writer.writerow({"kind": "shortlist", "vacancy_key": key})
    receipt = {
        "release_id": release_id, "state": state, "rows": len(keys),
        "projection_sha256": projection, "artifact_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
    }
    if state in {"delivered", "imported", "reported", "expired"}:
        receipt["bot_file_id"] = "F123"
    (directory / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    return directory


def test_only_verified_delivered_shortlists_issue_keys(tmp_path: Path) -> None:
    release(tmp_path, "shortlist-20260922T084423Z", keys=("a", "b"))
    release(tmp_path, "shortlist-20260922T070100Z", state="superseded", keys=("old",))
    release(tmp_path, "canary-20260922T051349Z", keys=("canary",))
    assert issued.issued_keys(tmp_path) == frozenset({"a", "b"})


def test_tampered_or_missing_delivered_csv_fails_closed(tmp_path: Path) -> None:
    directory = release(tmp_path, "shortlist-20260922T084423Z")
    path = directory / "shortlist-20260922T084423Z.csv"
    path.write_bytes(path.read_bytes().replace(b"k1", b"k9"))
    with pytest.raises(issued.IssuedIndexError, match="SHA-256"):
        issued.issued_keys(tmp_path)
    path.unlink()
    with pytest.raises(issued.IssuedIndexError, match="missing"):
        issued.issued_keys(tmp_path)


def test_duplicate_delivered_release_id_fails_closed(tmp_path: Path) -> None:
    first = release(tmp_path, "shortlist-20260922T084423Z")
    second = tmp_path / "other"
    second.mkdir()
    (second / "receipt.json").write_bytes((first / "receipt.json").read_bytes())
    with pytest.raises(issued.IssuedIndexError, match="duplicate delivered release"):
        issued.issued_keys(tmp_path)


def test_duplicate_vacancy_key_and_wrong_projection_fail_closed(tmp_path: Path) -> None:
    directory = release(tmp_path, "shortlist-20260922T084423Z", keys=("same", "same"))
    with pytest.raises(issued.IssuedIndexError, match="duplicate vacancy_key"):
        issued.issued_keys(tmp_path)
    receipt_path = directory / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["projection_sha256"] = "different"
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(issued.IssuedIndexError, match="projection"):
        issued.issued_keys(tmp_path)


def test_nested_delivered_manifest_is_source_of_future_issued_keys(tmp_path: Path) -> None:
    release_id = "shortlist-2026-W39"
    attempt_id = "attempt-001"
    directory = tmp_path / release_id / attempt_id
    directory.mkdir(parents=True)
    manifest = {
        "release_id": release_id, "attempt_id": attempt_id,
        "projection_sha256": "projection", "items": [
            {"vacancy_key": "new-role", "role_fit_verdict": "accept"},
        ],
        "rejected_sample": [{"vacancy_key": "review-only", "role_fit_verdict": "reject"}],
    }
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    receipt = {
        "release_id": release_id, "attempt_id": attempt_id, "state": "delivered",
        "rows": 1, "bot_file_id": "F123", "projection_sha256": "projection",
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }
    (directory / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    assert issued.issued_keys(tmp_path) == frozenset({"new-role"})
    manifest["items"][0]["vacancy_key"] = "tampered"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(issued.IssuedIndexError, match="manifest SHA-256"):
        issued.issued_keys(tmp_path)


def test_unreconciled_anchor_blocks_new_index(tmp_path: Path) -> None:
    release(tmp_path, "shortlist-20260922T084423Z", state="anchored")
    with pytest.raises(issued.IssuedIndexError, match="unresolved release state"):
        issued.issued_keys(tmp_path)


def test_delivered_supersede_requires_same_projection(tmp_path: Path) -> None:
    prior_id = "shortlist-20260922T070100Z"
    final_id = "shortlist-20260922T084423Z"
    prior = release(tmp_path, prior_id, state="superseded", keys=("a", "b"))
    release(tmp_path, final_id, keys=("a", "b"))
    receipt_path = prior / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt.update({"bot_file_id": "FOLD", "delivered_at": "2026-09-22T07:01:01Z", "superseded_by": final_id})
    receipt_path.write_text(json.dumps(receipt))
    assert issued.issued_keys(tmp_path) == frozenset({"a", "b"})

    final = tmp_path / final_id
    shutil.rmtree(final)
    release(tmp_path, final_id, keys=("a",))
    with pytest.raises(issued.IssuedIndexError, match="superseded"):
        issued.issued_keys(tmp_path)


def test_superseded_legacy_metadata_must_bind_projection(tmp_path: Path) -> None:
    prior_id = "shortlist-20260922T070100Z"
    final_id = "shortlist-20260922T084423Z"
    prior = release(tmp_path, prior_id, state="superseded", keys=("a",))
    release(tmp_path, final_id, keys=("a",))
    receipt_path = prior / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt.update({"bot_file_id": "FOLD", "delivered_at": "2026-09-22T07:01:01Z", "superseded_by": final_id})
    receipt_path.write_text(json.dumps(receipt))
    csv_path = prior / f"{prior_id}.csv"
    payload = csv_path.read_text()
    csv_path.write_text('# ' + json.dumps({"release_id": prior_id, "projection_sha256": "wrong"}) + '\n' + payload)
    receipt["artifact_sha256"] = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(issued.IssuedIndexError, match="projection mismatch"):
        issued.issued_keys(tmp_path)
