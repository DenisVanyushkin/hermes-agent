from __future__ import annotations

from datetime import datetime, timezone

from tests.product_search.test_gate_b_evidence_skeleton import _manifest


def test_manifest_has_identity_bound_decision_clock() -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    assert manifest.decision_clock == datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
