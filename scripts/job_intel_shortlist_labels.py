"""Idempotently append owner decisions to the existing Job Intel label file.

The poller records an import intent in its release receipt before calling this
module. If it crashes after the shared label write, replaying the same file
uses each operation key to make zero changes.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterator


class LabelStoreError(ValueError):
    """Existing labels or requested decisions cannot be safely reconciled."""


_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DECISIONS = frozenset({"yes", "no", "blocked_language"})


@contextmanager
def _global_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise LabelStoreError(f"cannot read existing labels: {path}") from error
    if (not isinstance(payload, dict)
            or not all(isinstance(payload.get(field), list) for field in ("labels", "rounds", "label_history"))):
        raise LabelStoreError("existing label schema is invalid")
    return payload


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    mode = stat.S_IMODE(path.stat().st_mode)
    with temporary.open("xb") as handle:
        os.fchmod(handle.fileno(), mode)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _vacancies(artifact: dict[str, Any], release_id: str) -> dict[str, tuple[str, dict[str, Any]]]:
    if artifact.get("release_id") != release_id:
        raise LabelStoreError("artifact release_id mismatch")
    result: dict[str, tuple[str, dict[str, Any]]] = {}
    for sheet, field in (("shortlist", "items"), ("rejected_sample", "rejected_sample")):
        rows = artifact.get(field)
        if not isinstance(rows, list):
            raise LabelStoreError(f"artifact lacks {field}")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("vacancy_key"), str) or not row["vacancy_key"]:
                raise LabelStoreError("artifact has invalid vacancy_key")
            key = row["vacancy_key"]
            if key in result:
                raise LabelStoreError(f"artifact duplicate vacancy_key: {key}")
            result[key] = sheet, row
    return result


def _reply_order(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError) as error:
        raise LabelStoreError(f"invalid Slack response timestamp: {value!r}") from error
    if not parsed.is_finite() or parsed <= 0:
        raise LabelStoreError(f"invalid Slack response timestamp: {value!r}")
    return parsed


def _validate_decisions(decisions: dict[str, Any], vacancies: dict[str, tuple[str, dict[str, Any]]]) -> None:
    if not isinstance(decisions, dict):
        raise LabelStoreError("decisions must be a keyed object")
    for key, value in decisions.items():
        if key not in vacancies:
            raise LabelStoreError(f"unknown vacancy_key: {key}")
        if not isinstance(value, dict):
            raise LabelStoreError(f"invalid decision: {key}")
        if value.get("sheet") != vacancies[key][0]:
            raise LabelStoreError(f"decision sheet mismatch: {key}")
        if value.get("owner_decision") not in _DECISIONS:
            raise LabelStoreError(f"invalid owner_decision: {key}")
        if not isinstance(value.get("owner_note"), str):
            raise LabelStoreError(f"invalid owner_note: {key}")


def apply_decisions(labels_path: Path, artifact: dict[str, Any], *, release_id: str,
                    attempt_id: str, file_hash: str, response_ts: str,
                    decisions: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Upsert each `(release, attempt, file_hash, vacancy_key)` exactly once."""
    if not release_id or not attempt_id or not _SHA.fullmatch(file_hash):
        raise LabelStoreError("invalid operation identity")
    reply_order = _reply_order(response_ts)
    vacancies = _vacancies(artifact, release_id)
    _validate_decisions(decisions, vacancies)
    if not decisions:
        return {"history_added": 0, "labels_added": 0, "labels_updated": 0, "noop": True}
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise LabelStoreError("changed_at must be timezone-aware")

    with _global_lock(labels_path):
        payload = _read(labels_path)
        current: dict[str, dict[str, Any]] = {}
        for label in payload["labels"]:
            if not isinstance(label, dict) or not isinstance(label.get("vacancy_key"), str):
                raise LabelStoreError("existing label schema is invalid")
            key = label["vacancy_key"]
            if key in current:
                raise LabelStoreError(f"duplicate existing label: {key}")
            current[key] = label

        operations: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for event in payload["label_history"]:
            if not isinstance(event, dict):
                raise LabelStoreError("existing label history schema is invalid")
            op_key = event.get("op_key")
            if op_key is None:  # legacy history has no operation key
                continue
            if not isinstance(op_key, list) or len(op_key) != 4 or not all(isinstance(part, str) for part in op_key):
                raise LabelStoreError("invalid existing operation key")
            key_tuple = tuple(op_key)
            if key_tuple in operations:
                raise LabelStoreError(f"duplicate existing operation key: {key_tuple}")
            operations[key_tuple] = event

        added = updated = history_added = 0
        for key in sorted(decisions):
            value = decisions[key]
            op_key = (release_id, attempt_id, file_hash, key)
            existing = operations.get(op_key)
            if existing is not None:
                if (existing.get("decision") != value["owner_decision"]
                        or existing.get("note") != value["owner_note"]
                        or existing.get("sheet") != value["sheet"]
                        or existing.get("response_ts") != response_ts):
                    raise LabelStoreError(f"operation key conflict: {key}")
                continue
            if any(operation[0] == release_id and operation[1] == attempt_id and operation[2] != file_hash
                   for operation in operations):
                raise LabelStoreError(f"release already imported from another file: {release_id}/{attempt_id}")
            sheet, vacancy = vacancies[key]
            prior = current.get(key)
            prior_order = _reply_order(prior["decision_ts"]) if prior and prior.get("decision_ts") else None
            effective = prior_order is None or reply_order >= prior_order
            event = {
                "op_key": list(op_key), "release_id": release_id, "attempt_id": attempt_id,
                "file_hash": file_hash, "vacancy_key": key,
                "response_ts": response_ts,
                "decision": value["owner_decision"], "note": value["owner_note"],
                "sheet": sheet, "prior": dict(prior) if prior else None,
                "effective": effective,
                "changed_at": now.astimezone(timezone.utc).isoformat(),
            }
            payload["label_history"].append(event)
            operations[op_key] = event
            history_added += 1
            if effective:
                label = {
                    "vacancy_key": key,
                    "company": vacancy.get("company") or "",
                    "title": vacancy.get("title") or "",
                    "location": vacancy.get("location") or "",
                    "url": vacancy.get("url") or "",
                    "description_chars": len(vacancy.get("description") or ""),
                    "found_in_db": True,
                    "verdict": value["owner_decision"], "why": value["owner_note"],
                    "decision_ts": response_ts, "source_release_id": release_id,
                    "source_attempt_id": attempt_id, "source_file_hash": file_hash,
                }
                if prior is None:
                    payload["labels"].append(label)
                    added += 1
                else:
                    payload["labels"][next(index for index, row in enumerate(payload["labels"])
                                           if row["vacancy_key"] == key)] = label
                    updated += 1
                current[key] = label
        if not history_added:
            return {"history_added": 0, "labels_added": 0, "labels_updated": 0, "noop": True}
        round_id = f"{release_id}/{attempt_id}"
        if round_id not in payload["rounds"]:
            payload["rounds"].append(round_id)
        _atomic_write(labels_path, payload)
        return {"history_added": history_added, "labels_added": added,
                "labels_updated": updated, "noop": False}
