"""Small cross-attempt committed-budget record for supervised Gate B runs.

This module deliberately owns only aggregate reserve accounting.  It has no
row state, replay, recovery, or crash-window semantics; those are not part of
the supervised contract.  Provisioning is a separate create-once act.  The
collection process opens an existing record and delegates each reserve update
to the narrow privileged updater command.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Sequence


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SCHEMA_VERSION = "gate-b-committed-budget-v1"
RECORD_NAME = "committed-budget.json"


class SpendRecordError(RuntimeError):
    """Fail-closed error from the aggregate committed-budget record."""


@dataclass(frozen=True)
class SpendRecord:
    manifest_sha256: str
    aggregate_maximum_cents: int
    committed_budget_cents: int

    @property
    def remaining_cents(self) -> int:
        return self.aggregate_maximum_cents - self.committed_budget_cents


def _validate_sha(value: str) -> None:
    if not SHA256_PATTERN.fullmatch(value):
        raise SpendRecordError("manifest_sha256_invalid")


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _path(root: Path, manifest_sha256: str) -> Path:
    _validate_sha(manifest_sha256)
    return root / manifest_sha256 / RECORD_NAME


def _decode(path: Path, payload: object) -> SpendRecord:
    if not isinstance(payload, dict):
        raise SpendRecordError("spend_record_invalid")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise SpendRecordError("spend_record_schema_mismatch")
    try:
        record = SpendRecord(
            manifest_sha256=str(payload["manifest_sha256"]),
            aggregate_maximum_cents=int(payload["aggregate_maximum_cents"]),
            committed_budget_cents=int(payload["committed_budget_cents"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SpendRecordError("spend_record_invalid") from exc
    _validate_sha(record.manifest_sha256)
    if record.aggregate_maximum_cents <= 0:
        raise SpendRecordError("spend_record_limit_invalid")
    if not 0 <= record.committed_budget_cents <= record.aggregate_maximum_cents:
        raise SpendRecordError("spend_record_total_invalid")
    if record.manifest_sha256 != path.parent.name:
        raise SpendRecordError("spend_record_manifest_mismatch")
    return record


def _payload(record: SpendRecord) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_sha256": record.manifest_sha256,
        "aggregate_maximum_cents": record.aggregate_maximum_cents,
        "committed_budget_cents": record.committed_budget_cents,
    }


def _grant_hermes_group(path: Path) -> None:
    if os.geteuid() != 0:
        return
    try:
        import grp

        os.chown(path, 0, grp.getgrnam("hermes").gr_gid)
    except (KeyError, PermissionError):
        raise SpendRecordError("spend_record_group_setup_failed")


class SpendRecordStore:
    """Open and reserve against one pre-provisioned aggregate record."""

    def __init__(self, path: Path, record: SpendRecord) -> None:
        self.path = path
        self.record = record

    @property
    def committed_budget_cents(self) -> int:
        return self.record.committed_budget_cents

    @property
    def remaining_cents(self) -> int:
        return self.record.remaining_cents

    @classmethod
    def provision(
        cls, *, root: Path, manifest_sha256: str, aggregate_maximum_cents: int
    ) -> SpendRecord:
        """Create exactly one record; never reset an existing manifest."""
        path = _path(root, manifest_sha256)
        if aggregate_maximum_cents <= 0:
            raise SpendRecordError("spend_record_limit_invalid")
        root.mkdir(parents=True, exist_ok=True, mode=0o750)
        os.chmod(root, 0o750)
        _grant_hermes_group(root)
        run_dir = path.parent
        try:
            run_dir.mkdir(mode=0o750)
        except FileExistsError as exc:
            raise SpendRecordError("spend_record_exists") from exc
        os.chmod(run_dir, 0o750)
        _grant_hermes_group(run_dir)
        record = SpendRecord(manifest_sha256, aggregate_maximum_cents, 0)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        except FileExistsError as exc:
            raise SpendRecordError("spend_record_exists") from exc
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(_canonical(_payload(record)))
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o640)
            _grant_hermes_group(path)
        except Exception:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise
        return record

    @classmethod
    def open(cls, *, root: Path, manifest_sha256: str) -> "SpendRecordStore":
        path = _path(root, manifest_sha256)
        if path.is_symlink() or not path.is_file():
            raise SpendRecordError("spend_record_missing")
        try:
            payload = json.loads(path.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SpendRecordError("spend_record_unreadable") from exc
        return cls(path, _decode(path, payload))

    def reserve(self, amount_cents: int) -> int:
        """Commit a conservative reserve; never decrement or silently reset."""
        if amount_cents <= 0:
            raise SpendRecordError("reserve_amount_invalid")
        lock_path = self.path.with_suffix(".lock")
        lock_path.touch(exist_ok=True)
        with lock_path.open("r+") as lock_stream:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            current = self.open(root=self.path.parent.parent, manifest_sha256=self.path.parent.name).record
            if current.remaining_cents < amount_cents:
                raise SpendRecordError("committed_budget_exhausted")
            updated = SpendRecord(
                current.manifest_sha256,
                current.aggregate_maximum_cents,
                current.committed_budget_cents + amount_cents,
            )
            fd, temporary_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(_canonical(_payload(updated)))
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.fchmod(stream.fileno(), 0o640)
                os.replace(temporary, self.path)
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
            self.record = updated
            return updated.committed_budget_cents


def _main(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="gate_b_spend_record_v1")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_run = subparsers.add_parser("init-run")
    init_run.add_argument("--root", type=Path, required=True)
    init_run.add_argument("--manifest-sha256", required=True)
    init_run.add_argument("--aggregate-maximum-cents", type=int, required=True)
    reserve = subparsers.add_parser("reserve")
    reserve.add_argument("--root", type=Path, required=True)
    reserve.add_argument("--manifest-sha256", required=True)
    reserve.add_argument("--amount-cents", type=int, required=True)
    args = parser.parse_args(arguments)
    if args.command == "init-run":
        SpendRecordStore.provision(
            root=args.root,
            manifest_sha256=args.manifest_sha256,
            aggregate_maximum_cents=args.aggregate_maximum_cents,
        )
        return 0
    store = SpendRecordStore.open(root=args.root, manifest_sha256=args.manifest_sha256)
    print(store.reserve(args.amount_cents))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(__import__("sys").argv[1:]))
