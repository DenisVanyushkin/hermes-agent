from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from job_intel.product_search.gate_b import (
    GateBPreflightError,
    assert_paths_unchanged,
    authorize_record_run,
    build_dry_run_preflight,
    expected_record_approval_token,
    snapshot_paths,
    validate_gate_a_run_ids,
)


GATE_A_ROOT = Path(
    "/home/hermes/.hermes/job_intel/experiments/gate-a/"
    "65d60daae16093a9a7e34a11a159e2f789dd14dd"
)


def test_dry_run_materializes_content_addressed_corpus_and_is_idempotent(
    tmp_path: Path,
) -> None:
    first = build_dry_run_preflight(gate_a_root=GATE_A_ROOT, output_root=tmp_path)
    manifest_path = Path(first["corpus"]["manifest_path"])
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    second = build_dry_run_preflight(gate_a_root=GATE_A_ROOT, output_root=tmp_path)

    assert second == first
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before
    assert first["status"] == "ready_for_record_approval"
    assert first["gate_a"]["raw_observed"] == 2414
    assert first["gate_a"]["corrected_canonical_current"] == 1814
    assert first["gate_a"]["minimum_evidence_sufficient"] == 1314
    assert first["corpus"]["selection_denominator"] == 1314
    assert first["corpus"]["selected_count"] == 48
    assert first["corpus"]["manifest_sha256"] == manifest_path.parent.name
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["records"]) == 48
    assert all(
        record["run_id"] == "gate-a-20260816T141344Z" for record in manifest["records"]
    )
    assert all(len(record["source_id"]) > 0 for record in manifest["records"])
    assert all(
        len(record["raw_content_sha256"]) == 64 for record in manifest["records"]
    )
    assert all(record["origin"] == "open_market" for record in manifest["records"])
    assert all(
        record["decision_selection_mode"] is None for record in manifest["records"]
    )
    coverage = first["corpus"]["coverage"]
    assert len(coverage["lanes"]) >= 3
    assert len(coverage["source_families"]) >= 3
    assert len(coverage["role_patterns"]) >= 3
    assert coverage["companies"] >= 12
    assert set(coverage["sampling_case_types"]) >= {
        "core_hypothesis",
        "exploration_hypothesis",
        "hard_block_hypothesis",
        "important_unknown",
    }
    assert first["provider"]["calls_attempted"] == 0
    assert first["side_effects"]["forbidden_mutations"] == 0
    assert len(first["record_identity"]["task10_prompt_sha256"]) == 64


def test_preflight_rejects_mixed_gate_a_runs() -> None:
    with pytest.raises(GateBPreflightError, match="mixed_gate_a_run_ids"):
        validate_gate_a_run_ids(
            evidence_run_ids=["gate-a-20260816T141344Z", "gate-a-other"],
            probe_run_ids=["gate-a-20260816T141344Z"],
        )


def test_record_authorization_requires_exact_identity_token_and_caps(
    tmp_path: Path,
) -> None:
    preflight = build_dry_run_preflight(gate_a_root=GATE_A_ROOT, output_root=tmp_path)
    identity = deepcopy(preflight["record_identity"])
    token = expected_record_approval_token(identity)

    for supplied_token in (None, "wrong"):
        with pytest.raises(GateBPreflightError, match="approval_token"):
            authorize_record_run(
                preflight,
                supplied_identity=identity,
                approval_token=supplied_token,
                call_cap=48,
                spend_cap_usd="0.48",
            )

    wrong_identity = deepcopy(identity)
    wrong_identity["model_sha256"] = "0" * 64
    with pytest.raises(GateBPreflightError, match="record_identity_mismatch"):
        authorize_record_run(
            preflight,
            supplied_identity=wrong_identity,
            approval_token=token,
            call_cap=48,
            spend_cap_usd="0.48",
        )
    with pytest.raises(GateBPreflightError, match="call_cap"):
        authorize_record_run(
            preflight,
            supplied_identity=identity,
            approval_token=token,
            call_cap=47,
            spend_cap_usd="0.48",
        )
    with pytest.raises(GateBPreflightError, match="spend_cap"):
        authorize_record_run(
            preflight,
            supplied_identity=identity,
            approval_token=token,
            call_cap=48,
            spend_cap_usd="0.47",
        )
    authorized = authorize_record_run(
        preflight,
        supplied_identity=identity,
        approval_token=token,
        call_cap=48,
        spend_cap_usd="0.48",
    )
    assert authorized["record_authorized"] is True
    assert authorized["provider_calls_started"] is False


def test_forbidden_side_effect_mutation_fails_closed(tmp_path: Path) -> None:
    protected = tmp_path / "production.sqlite3"
    protected.write_bytes(b"before")
    before = snapshot_paths([protected])
    protected.write_bytes(b"after")
    with pytest.raises(GateBPreflightError, match="forbidden_side_effect_mutation"):
        assert_paths_unchanged(before, snapshot_paths([protected]))
