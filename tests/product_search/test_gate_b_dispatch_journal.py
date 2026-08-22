from __future__ import annotations

from decimal import Decimal
import os
import signal
from pathlib import Path

import pytest

from tests.product_search.test_gate_b_evidence_skeleton import _manifest
from job_intel.product_search.gate_b_evidence_runner_v1 import (
    AppendOnlyJournal,
    JournalState,
    TerminalOutcome,
)


def test_journal_reopens_and_terminal_commit_is_idempotent(tmp_path: Path) -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    path = tmp_path / "dispatch.jsonl"
    journal = AppendOnlyJournal.create(manifest, path)
    ref = manifest.row_ref(0)
    receipt = journal.append_pre_dispatch(ref)

    reopened = AppendOnlyJournal.open(manifest, path)
    assert reopened.state(0) is JournalState.DISPATCHED
    reopened.commit_terminal(
        receipt,
        TerminalOutcome.SUCCESS,
        recording_sha256="a" * 64,
        measured_cost_usd=Decimal("0.001000"),
        conservative_cost_usd=Decimal("0.001000"),
    )
    reopened_again = AppendOnlyJournal.open(manifest, path)
    assert reopened_again.state(0) is JournalState.SUCCESS
    reopened_again.commit_terminal(
        receipt,
        TerminalOutcome.SUCCESS,
        recording_sha256="a" * 64,
        measured_cost_usd=Decimal("0.001000"),
        conservative_cost_usd=Decimal("0.001000"),
    )
    with pytest.raises(ValueError, match="terminal commit conflict"):
        reopened_again.commit_terminal(
            receipt,
            TerminalOutcome.SUCCESS,
            recording_sha256="b" * 64,
            measured_cost_usd=Decimal("0.001000"),
            conservative_cost_usd=Decimal("0.001000"),
        )


def test_ambiguous_dispatch_counts_toward_exact_cap(tmp_path: Path) -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    journal = AppendOnlyJournal.create(manifest, tmp_path / "dispatch.jsonl")
    for ordinal in range(30):
        journal.append_pre_dispatch(manifest.row_ref(ordinal))
    assert len(journal.entries()) == 30
    journal = AppendOnlyJournal.open(manifest, journal.path)
    for ordinal in range(30, 48):
        journal.append_pre_dispatch(manifest.row_ref(ordinal))
    assert len(journal.entries()) == 48
    assert all(entry.state is JournalState.DISPATCHED for entry in journal.entries())


def test_sigkill_partial_tail_is_truncated_without_resetting_state(tmp_path: Path) -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    path = tmp_path / "dispatch.jsonl"
    journal = AppendOnlyJournal.create(manifest, path)
    journal.append_pre_dispatch(manifest.row_ref(0))
    valid_bytes = path.read_bytes()
    child = os.fork()
    if child == 0:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
        os.write(descriptor, b'{"event":"terminal"')
        os.kill(os.getpid(), signal.SIGSTOP)
        os._exit(0)
    _, status = os.waitpid(child, os.WUNTRACED)
    assert os.WIFSTOPPED(status)
    os.kill(child, signal.SIGKILL)
    os.waitpid(child, 0)

    reopened = AppendOnlyJournal.open(manifest, path)
    assert reopened.state(0) is JournalState.DISPATCHED
    assert path.read_bytes() == valid_bytes


def test_verify_reports_partial_tail_without_mutating_the_journal(tmp_path: Path) -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    path = tmp_path / "dispatch.jsonl"
    journal = AppendOnlyJournal.create(manifest, path)
    journal.append_pre_dispatch(manifest.row_ref(0))
    valid_bytes = path.read_bytes()
    path.write_bytes(valid_bytes + b'{"event":"terminal"')

    with pytest.raises(ValueError, match="journal incomplete tail"):
        journal.verify()

    assert path.read_bytes() == valid_bytes + b'{"event":"terminal"'


def test_existing_corrupt_journal_is_not_reinitialized(tmp_path: Path) -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    path = tmp_path / "dispatch.jsonl"
    path.write_bytes(b"not-json\n")
    with pytest.raises(ValueError, match="journal corrupt"):
        AppendOnlyJournal.open(manifest, path)


def test_unlinked_journal_cannot_be_reopened_as_a_fresh_run(tmp_path: Path) -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    path = tmp_path / "dispatch.jsonl"
    AppendOnlyJournal.create(manifest, path)
    path.unlink()
    with pytest.raises(ValueError, match="journal missing"):
        AppendOnlyJournal.open(manifest, path)


def test_create_refuses_to_replace_existing_journal(tmp_path: Path) -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    path = tmp_path / "dispatch.jsonl"
    AppendOnlyJournal.create(manifest, path)
    with pytest.raises(ValueError, match="journal already exists"):
        AppendOnlyJournal.create(manifest, path)
