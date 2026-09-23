"""Derive issued vacancy keys from delivered shortlist receipts.

The delivery receipt and its SHA-256-bound outgoing artifact are the source of
truth. A separately mutable issued-keys file could diverge after a crash.
This reader covers the existing CSV release and future attempt directories
whose immutable manifest is bound to the delivered receipt by SHA-256.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import re


class IssuedIndexError(ValueError):
    """The delivery history is ambiguous or cannot be verified."""


_RELEASE_ID = re.compile(r"shortlist-[A-Za-z0-9TZ-]+\Z")
_ISSUED_STATES = frozenset({"delivered", "imported", "reported", "expired"})
_SKIP_STATES = frozenset({"prepared"})


def legacy_seed_keys(release_root: Path, *, required: bool = True) -> frozenset[str]:
    """Read the audited pre-automation seed if present, rejecting corruption."""
    path = release_root / "legacy-seed-v1.json"
    if not path.exists():
        if required:
            raise IssuedIndexError("legacy seed marker missing")
        return frozenset()
    marker = _receipt(path)
    keys = marker.get("keys")
    if (marker.get("schema") != "job_intel_legacy_seed_v1"
            or not isinstance(keys, list) or not keys
            or any(not isinstance(key, str) or not key for key in keys)
            or len(keys) != len(set(keys)) or keys != sorted(keys)
            or marker.get("count") != len(keys)):
        raise IssuedIndexError("invalid legacy seed marker")
    digest = hashlib.sha256(json.dumps(keys, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    if marker.get("keys_sha256") != digest:
        raise IssuedIndexError("legacy seed digest mismatch")
    return frozenset(keys)


def _receipt(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise IssuedIndexError(f"cannot read receipt {path}") from error
    if not isinstance(value, dict):
        raise IssuedIndexError(f"receipt is not an object: {path}")
    return value


def _artifact_bytes(path: Path, receipt: dict) -> bytes:
    release_id = receipt["release_id"]
    expected_sha = receipt.get("artifact_sha256")
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise IssuedIndexError(f"delivered release lacks artifact SHA-256: {release_id}")
    csv_path = path.parent / f"{release_id}.csv"
    try:
        payload = csv_path.read_bytes()
    except OSError as error:
        raise IssuedIndexError(f"delivered artifact missing: {csv_path}") from error
    if hashlib.sha256(payload).hexdigest() != expected_sha:
        raise IssuedIndexError(f"artifact SHA-256 mismatch: {release_id}")
    return payload


def _delivered_keys(path: Path, receipt: dict) -> frozenset[str]:
    release_id = receipt["release_id"]
    if path.parent.name != release_id:
        raise IssuedIndexError(f"receipt directory does not match release_id: {path}")
    if not receipt.get("bot_file_id"):
        raise IssuedIndexError(f"delivered release lacks bot_file_id: {release_id}")
    payload = _artifact_bytes(path, receipt)
    try:
        with io.StringIO(payload.decode("utf-8-sig"), newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise IssuedIndexError(f"invalid CSV columns: {release_id}")
            if not {"kind", "vacancy_key", "role_fit_verdict"} <= set(reader.fieldnames):
                raise IssuedIndexError(f"missing CSV columns: {release_id}")
            rows = list(reader)
    except (UnicodeError, csv.Error) as error:
        raise IssuedIndexError(f"cannot parse CSV artifact: {release_id}") from error
    if not rows or rows[0].get("kind") != "release" or rows[0].get("vacancy_key") != release_id:
        raise IssuedIndexError(f"missing release row: {release_id}")
    if rows[0].get("role_fit_verdict") != receipt.get("projection_sha256"):
        raise IssuedIndexError(f"projection mismatch: {release_id}")
    keys: list[str] = []
    for row in rows[1:]:
        kind = row.get("kind")
        if kind == "shortlist":
            key = row.get("vacancy_key")
            if not key:
                raise IssuedIndexError(f"empty vacancy_key: {release_id}")
            keys.append(key)
        elif kind != "rejected_sample":
            raise IssuedIndexError(f"unexpected CSV row kind: {release_id}")
    if len(keys) != len(set(keys)):
        raise IssuedIndexError(f"duplicate vacancy_key: {release_id}")
    if receipt.get("rows") != len(keys):
        raise IssuedIndexError(f"row count mismatch: {release_id}")
    return frozenset(keys)


def _manifest_keys(path: Path, receipt: dict) -> frozenset[str]:
    release_id = receipt["release_id"]
    attempt_id = receipt.get("attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id or path.parent.name != attempt_id or path.parent.parent.name != release_id:
        raise IssuedIndexError(f"manifest attempt path mismatch: {path}")
    if not receipt.get("bot_file_id"):
        raise IssuedIndexError(f"delivered release lacks bot_file_id: {release_id}")
    expected_sha = receipt.get("manifest_sha256")
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise IssuedIndexError(f"delivered release lacks manifest SHA-256: {release_id}")
    manifest_path = path.parent / "manifest.json"
    try:
        payload = manifest_path.read_bytes()
    except OSError as error:
        raise IssuedIndexError(f"delivered manifest missing: {manifest_path}") from error
    if hashlib.sha256(payload).hexdigest() != expected_sha:
        raise IssuedIndexError(f"manifest SHA-256 mismatch: {release_id}")
    try:
        manifest = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise IssuedIndexError(f"invalid delivered manifest: {release_id}") from error
    if not isinstance(manifest, dict) or manifest.get("release_id") != release_id or manifest.get("attempt_id") != attempt_id:
        raise IssuedIndexError(f"manifest identity mismatch: {release_id}")
    if manifest.get("projection_sha256") != receipt.get("projection_sha256"):
        raise IssuedIndexError(f"manifest projection mismatch: {release_id}")
    items = manifest.get("items")
    if not isinstance(items, list):
        raise IssuedIndexError(f"manifest items missing: {release_id}")
    keys: list[str] = []
    for item in items:
        if not isinstance(item, dict) or item.get("role_fit_verdict") != "accept":
            raise IssuedIndexError(f"invalid manifest shortlist row: {release_id}")
        key = item.get("vacancy_key")
        if not isinstance(key, str) or not key:
            raise IssuedIndexError(f"empty vacancy_key: {release_id}")
        keys.append(key)
    if len(keys) != len(set(keys)):
        raise IssuedIndexError(f"duplicate vacancy_key: {release_id}")
    if receipt.get("rows") != len(keys):
        raise IssuedIndexError(f"row count mismatch: {release_id}")
    return frozenset(keys)


def _empty_keys(path: Path, receipt: dict) -> frozenset[str]:
    release_id = receipt["release_id"]
    if (receipt.get("state") != "reported" or receipt.get("rows") != 0
            or receipt.get("rejected_sample_rows") != 0 or not receipt.get("thread_ts")
            or receipt.get("report_ts") != receipt["thread_ts"]
            or path.parent.name != receipt.get("attempt_id")
            or path.parent.parent.name != release_id):
        raise IssuedIndexError(f"invalid empty release receipt: {release_id}")
    manifest_path = path.parent / "manifest.json"
    try:
        payload = manifest_path.read_bytes()
        manifest = json.loads(payload)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise IssuedIndexError(f"invalid empty release manifest: {release_id}") from error
    if (hashlib.sha256(payload).hexdigest() != receipt.get("manifest_sha256")
            or not isinstance(manifest, dict) or manifest.get("release_id") != release_id
            or manifest.get("attempt_id") != receipt.get("attempt_id")
            or manifest.get("projection_sha256") != receipt.get("projection_sha256")
            or manifest.get("items") != [] or manifest.get("rejected_sample") != []):
        raise IssuedIndexError(f"empty release manifest mismatch: {release_id}")
    return frozenset()


def issued_keys(release_root: Path, *, require_seed: bool = True) -> frozenset[str]:
    """Return exactly the shortlist keys from verified delivered releases."""
    if not release_root.is_dir():
        raise IssuedIndexError(f"release root missing: {release_root}")
    delivered: dict[str, tuple[Path, dict]] = {}
    superseded_after_delivery: list[tuple[Path, dict]] = []
    for path in sorted(release_root.rglob("receipt.json")):
        receipt = _receipt(path)
        release_id = receipt.get("release_id")
        if not isinstance(release_id, str):
            raise IssuedIndexError(f"receipt lacks release_id: {path}")
        if release_id.startswith("canary-"):
            continue
        if not _RELEASE_ID.fullmatch(release_id):
            raise IssuedIndexError(f"invalid release_id: {release_id!r}")
        state = receipt.get("state")
        if state == "superseded":
            bot_file = bool(receipt.get("bot_file_id"))
            delivered_at = bool(receipt.get("delivered_at"))
            if bot_file != delivered_at:
                raise IssuedIndexError(f"ambiguous superseded delivery: {release_id}")
            if bot_file:
                superseded_after_delivery.append((path, receipt))
            continue
        if state in _SKIP_STATES:
            continue
        if state not in _ISSUED_STATES:
            raise IssuedIndexError(f"unresolved release state {state!r}: {release_id}")
        if release_id in delivered:
            raise IssuedIndexError(f"duplicate delivered release: {release_id}")
        delivered[release_id] = path, receipt
    keys: set[str] = set(legacy_seed_keys(release_root, required=require_seed))
    for path, receipt in superseded_after_delivery:
        release_id = receipt["release_id"]
        if path.parent.name != release_id:
            raise IssuedIndexError(f"receipt directory does not match release_id: {path}")
        target_id = receipt.get("superseded_by")
        target = delivered.get(target_id) if isinstance(target_id, str) else None
        if target is None or receipt.get("projection_sha256") != target[1].get("projection_sha256"):
            raise IssuedIndexError(f"delivered superseded release lacks identical replacement: {release_id}")
        payload = _artifact_bytes(path, receipt)
        try:
            content = payload.decode("utf-8-sig")
            if content.startswith("# "):
                metadata = json.loads(content.splitlines()[0][2:])
                artifact_id = metadata.get("release_id")
                artifact_projection = metadata.get("projection_sha256")
            else:
                row = next(csv.DictReader(io.StringIO(content, newline="")))
                artifact_id = row.get("vacancy_key") if row.get("kind") == "release" else None
                artifact_projection = row.get("role_fit_verdict")
        except (UnicodeError, json.JSONDecodeError, csv.Error, StopIteration, AttributeError) as error:
            raise IssuedIndexError(f"cannot parse superseded artifact: {release_id}") from error
        if artifact_id != release_id or artifact_projection != receipt.get("projection_sha256"):
            raise IssuedIndexError(f"superseded artifact projection mismatch: {release_id}")
    for path, receipt in delivered.values():
        if receipt.get("empty") is True:
            keys.update(_empty_keys(path, receipt))
        elif "attempt_id" in receipt:
            keys.update(_manifest_keys(path, receipt))
        else:
            keys.update(_delivered_keys(path, receipt))
    return frozenset(keys)
