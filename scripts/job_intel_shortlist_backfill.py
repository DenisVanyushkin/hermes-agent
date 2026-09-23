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
LEGACY_ROUNDS = frozenset({None, "2026-09-19-round3"})
MARKER_NAME = "legacy-seed-v1.json"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def prepare_legacy_seed(labels_path: Path, db_path: Path, release_root: Path, *,
                        expected_count: int = 20,
                        expected_keys_sha256: str = LIVE_2026_09_KEYS_SHA256) -> dict:
    """Create a durable marker only after every historical key maps once to vacancies."""
    if expected_count < 1 or not re.fullmatch(r"[0-9a-f]{64}", expected_keys_sha256):
        raise BackfillError("invalid seed expectation")
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
    old = [row for row in document["labels"] if isinstance(row, dict) and row.get("round") in LEGACY_ROUNDS]
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
