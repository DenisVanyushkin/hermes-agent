"""One-time, fail-closed seed of the 20 pre-automation owner decisions."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from job_intel_shortlist_issued import IssuedIndexError, legacy_seed_keys


class BackfillError(ValueError):
    """The historical decisions cannot be resolved without ambiguity."""


LIVE_2026_09_KEYS_SHA256 = "43c8253b686bfbfc751a210162eb149195f20215ce74957c3202ba7b9d310bd3"
LIVE_2026_09_KEYS = (
    "20b5aca098b7b934a4d6503f7565bab35723d6efc95c174bdfca75ede5f5c49c",
    "3e9d401102b5f215a9909b302149e82277feae85c9016643749c190429e630b9",
    "448d40159e3bdc1efa853af5d169cdf7969e772d0be075805719b91bc7ed8a76",
    "459e37d52b4f4d7ac2f33dba19114f581cedf2b29cb3e46a11dde7f2b95251f9",
    "4d89285605c9c1861bbe69f8491e91124b963b3aa2fc9293714ec8b5552f196c",
    "505352279398bf034d143869ce1ca6aa2e023af24a33c0c67db1ae3707d5a504",
    "638e93d2089e5c870724a255e45b2024afc42c3e55090530bced881d45e648ae",
    "6efb55305d7162605ea5a252ea213832f50f39f4023fa32e7d4978e1cd2e60f1",
    "84d39856a3667e85314343dabc5cf5ff31ea5a857c72d4396594818c8e924c67",
    "85243f75680a11ec2b38e8329e1cbf55803c23e0f1498c3f1a468039eb8f5478",
    "86193a7ff7f6d0deb97bdd1d945cd7bc89fe2055bb607fba94a229b6bee4a889",
    "8fb09e918091ab4274c5ce0b3edd35211767e94dcd75341c3ff522956a7d8bb4",
    "b8e4b0c1c177e491cab90457004eab47ea6dc6576711128fc35bd43e1292554e",
    "be93f21243b797eff4e8f2287c68a28a5a4074fe7c90c0829601dbef04089cf4",
    "ce53dbd364fe3d4032a5c5fc5399b162571d8ac0db4af0bd2f7f77d98ab42de9",
    "cf93f4eb6db8fd502f21ede7130f8218b1f3ccf68a2599e5b196a228fa3e97c6",
    "d2f820d4ab4bf1d5914dccdaba19245e8695d7eb769cf8a6fe17b538eea1b265",
    "edf4787f88c1cc51cebf7045b5858bd8d023b484b383a90f7e6da66e9a0728d2",
    "f7b7dc2956836c77ad012f8d12d89de2c7ecf190f9c84fec8e09c852ec354f8e",
    "ff772e7a599c381811bbbf3d3a5ff7696eec2d934bf69deb40fda0005f0f087c",
)
MARKER_NAME = "legacy-seed-v1.json"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def prepare_legacy_seed(labels_path: Path, db_path: Path, release_root: Path, *,
                        expected_count: int = 20,
                        expected_keys_sha256: str = LIVE_2026_09_KEYS_SHA256,
                        audited_keys: tuple[str, ...] = LIVE_2026_09_KEYS) -> dict:
    """Create a durable marker only after every historical key maps once to vacancies."""
    if expected_count < 1 or not re.fullmatch(r"[0-9a-f]{64}", expected_keys_sha256):
        raise BackfillError("invalid seed expectation")
    if len(audited_keys) != expected_count or len(set(audited_keys)) != expected_count:
        raise BackfillError("audited key list is invalid")
    if hashlib.sha256(_canonical_bytes(sorted(audited_keys))).hexdigest() != expected_keys_sha256:
        raise BackfillError("audited key list does not match its digest")
    marker_path = release_root / MARKER_NAME
    if marker_path.exists():
        try:
            keys = legacy_seed_keys(release_root)
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (IssuedIndexError, OSError, UnicodeError, json.JSONDecodeError) as error:
            raise BackfillError("existing legacy seed marker is invalid") from error
        if len(keys) != expected_count or marker.get("keys_sha256") != expected_keys_sha256:
            raise BackfillError("existing legacy seed differs from expected 20 keys")
        return marker
    try:
        raw = labels_path.read_bytes()
        document = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BackfillError("cannot read owner labels") from error
    if not isinstance(document, dict) or not isinstance(document.get("labels"), list):
        raise BackfillError("owner labels have no labels list")
    audited_set = set(audited_keys)
    old = [row for row in document["labels"] if isinstance(row, dict)
           and isinstance(row.get("vacancy_key"), str)
           and row["vacancy_key"] in audited_set]
    if len(old) != expected_count:
        raise BackfillError(f"expected {expected_count} historical decisions; found {len(old)}")
    raw_keys = [row.get("vacancy_key") for row in old]
    if any(not isinstance(key, str) or not key for key in raw_keys) or len(set(raw_keys)) != expected_count:
        raise BackfillError("historical vacancy keys missing or duplicated")
    keys = sorted(raw_keys)
    if any(row.get("verdict") not in {"yes", "no", "reject"} for row in old):
        raise BackfillError("historical decision is invalid")
    digest = hashlib.sha256(_canonical_bytes(keys)).hexdigest()
    if digest != expected_keys_sha256:
        raise BackfillError("historical key set differs from audited 20 decisions")
    problems = []
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
        for row in old:
            key = row["vacancy_key"]
            matches = connection.execute("SELECT company, title FROM vacancies WHERE vacancy_key = ?", (key,)).fetchall()
            if len(matches) != 1 or not all(isinstance(row.get(field), str) and row[field].strip()
                                            for field in ("company", "title", "verdict")):
                problems.append(key)
                continue
            if any(str(actual).strip().casefold() != row[field].strip().casefold()
                   for actual, field in zip(matches[0], ("company", "title"))):
                problems.append(key)
        connection.close()
    except sqlite3.Error as error:
        raise BackfillError("cannot verify historical keys in vacancy DB") from error
    if problems:
        raise BackfillError(f"historical keys unresolved or inconsistent: {','.join(sorted(problems))}")
    marker = {
        "schema": "job_intel_legacy_seed_v1", "count": expected_count,
        "keys": keys, "keys_sha256": digest,
        "labels_sha256": hashlib.sha256(raw).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    release_root.mkdir(parents=True, exist_ok=True)
    staged_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=release_root, prefix=".legacy-seed-",
                                         delete=False) as staged:
            staged_name = staged.name
            staged.write(_canonical_bytes(marker) + b"\n")
            staged.flush()
            os.fsync(staged.fileno())
        os.link(staged_name, marker_path)
        directory_fd = os.open(release_root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError as error:
        raise BackfillError("legacy seed was concurrently created; verify before retry") from error
    finally:
        if staged_name is not None:
            Path(staged_name).unlink(missing_ok=True)
    return marker


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_legacy_seed(args.labels, args.db, args.release_root),
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
