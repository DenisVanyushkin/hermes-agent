from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from tests.product_search.test_gate_b_evidence_skeleton import _manifest
from job_intel.product_search.gate_b_evidence_runner_v1 import (
    RecordingRef,
    RecordingStore,
    SealedRecording,
    TerminalOutcome,
)


def test_terminal_unknown_replays_cost_and_manifest_bound_metadata(tmp_path: Path) -> None:
    manifest = _manifest(
        input_sha256=sha256(b"request").hexdigest(),
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    ref = manifest.row_ref(0)
    store = RecordingStore(tmp_path / "recordings")
    recording_ref = store.save_exclusive(
        SealedRecording(
            manifest_ref=ref,
            request_bytes=b"request",
            response_bytes=b"",
            outcome=TerminalOutcome.TERMINAL_UNKNOWN,
            metadata={
                "input_sha256": ref.input_sha256,
                "projection_sha256": ref.projection_sha256,
                "response_sha256": sha256(b"").hexdigest(),
                "conservative_cost_usd": "0.010000",
            },
        )
    )
    replay = store.replay(recording_ref, manifest)
    assert replay.response_bytes == b""
    assert replay.outcome is TerminalOutcome.TERMINAL_UNKNOWN
    assert replay.metadata["conservative_cost_usd"] == "0.010000"
    store.verify(recording_ref, manifest)


def test_mutated_recording_fails_closed_against_manifest_and_internal_hashes(
    tmp_path: Path,
) -> None:
    manifest = _manifest(
        input_sha256=sha256(b"request").hexdigest(),
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    ref = manifest.row_ref(0)
    store = RecordingStore(tmp_path / "recordings")
    recording_ref = store.save_exclusive(
        SealedRecording(
            manifest_ref=ref,
            request_bytes=b"request",
            response_bytes=b"response",
            outcome=TerminalOutcome.SUCCESS,
            metadata={
                "input_sha256": ref.input_sha256,
                "projection_sha256": ref.projection_sha256,
                "response_sha256": sha256(b"response").hexdigest(),
            },
        )
    )
    path = store._path(recording_ref.recording_sha256)
    payload = path.read_bytes().replace(b"cmVzcG9uc2U=", b"dGFtcGVyZWQ=")
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="recording bytes do not match"):
        store.verify(recording_ref, manifest)


def test_rehashed_refiled_recording_is_refused_by_replay_manifest_binding(
    tmp_path: Path,
) -> None:
    manifest = _manifest(
        input_sha256=sha256(b"request").hexdigest(),
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    ref = manifest.row_ref(0)
    store = RecordingStore(tmp_path / "recordings")
    recording_ref = store.save_exclusive(
        SealedRecording(
            manifest_ref=ref,
            request_bytes=b"request",
            response_bytes=b"response",
            outcome=TerminalOutcome.SUCCESS,
            metadata={
                "input_sha256": ref.input_sha256,
                "projection_sha256": ref.projection_sha256,
                "response_sha256": sha256(b"response").hexdigest(),
            },
        )
    )
    path = store._path(recording_ref.recording_sha256)
    payload = json.loads(path.read_bytes())
    payload["metadata"]["projection_sha256"] = manifest.row(1).projection_sha256
    mutated = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    mutated_hash = sha256(mutated).hexdigest()
    path.unlink()
    store._path(mutated_hash).write_bytes(mutated)
    forged_ref = RecordingRef(
        manifest_ref=recording_ref.manifest_ref,
        recording_sha256=mutated_hash,
    )
    with pytest.raises(ValueError, match="recording projection hash mismatch"):
        store.replay(forged_ref, manifest)
