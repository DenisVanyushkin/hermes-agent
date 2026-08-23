from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
import job_intel.product_search.gate_b as gate_b

from job_intel.product_search.gate_b import (
    GateBPreflightError,
    assert_paths_unchanged,
    authorize_record_run,
    build_dry_run_preflight,
    snapshot_paths,
    validate_gate_a_run_ids,
)


GATE_A_ROOT = Path(
    "/home/hermes/.hermes/job_intel/experiments/gate-a/"
    "65d60daae16093a9a7e34a11a159e2f789dd14dd"
)


def test_dry_run_materializes_content_addressed_corpus_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", tmp_path)
    first = build_dry_run_preflight(gate_a_root=GATE_A_ROOT)
    manifest_path = Path(first["corpus"]["manifest_path"])
    manifest_path.chmod(0o644)
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    second = build_dry_run_preflight(gate_a_root=GATE_A_ROOT)

    assert second["side_effect_evidence"]["corpus_files_created"] == 0
    assert second["side_effect_evidence"]["package_files_created"] == 0
    comparable_second = deepcopy(second)
    comparable_second["side_effect_evidence"]["corpus_files_created"] = 1
    comparable_second["side_effect_evidence"]["package_files_created"] = 97
    assert comparable_second == first
    assert manifest_path.read_bytes() == before[0]
    assert (manifest_path.stat().st_mode & 0o777) == 0o600
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
    assert len(coverage["lanes"]) >= 2
    assert len(coverage["source_families"]) >= 3
    assert len(coverage["role_patterns"]) >= 3
    assert coverage["companies"] >= 8
    assert set(coverage["sampling_case_types"]) >= {
        "core_hypothesis",
        "exploration_hypothesis",
        "hard_block_hypothesis",
    }
    eligibility = first["corpus"]["eligibility"]
    assert eligibility["eligible_count"] == 1193
    assert eligibility["min_description_chars_exclusive"] == 500
    assert "duckduckgo" in eligibility["collapsed_strata"]["source_family"]["values"]
    assert all(
        record["source_family"] in {"greenhouse", "ashby", "remoteok"}
        for record in manifest["records"]
    )
    scope = first["corpus"]["scope"]
    assert scope["selected_lane_counts"] == {"global_ats": 47, "global_remote": 1}
    assert scope["unrepresented_role_patterns"] == ["chief_product"]
    assert len(scope["unrepresented_search_contract_lanes"]) == 7
    assert scope["repeat_after_collection_fixes"] is True
    assert scope["collection_fix_issues"] == [
        "https://github.com/DenisVanyushkin/hermes-agent/issues/4",
        "https://github.com/DenisVanyushkin/hermes-agent/issues/5",
    ]
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", tmp_path)
    preflight = build_dry_run_preflight(gate_a_root=GATE_A_ROOT)
    capability = "owner-random-fixture-capability-90125"
    approval = {
        "schema_version": "2.0.0",
        "status": "approved",
        "run_identity_sha256": preflight["record_identity_sha256"],
        "capability_sha256": hashlib.sha256(capability.encode()).hexdigest(),
        "exact_call_cap": 48,
        "exact_spend_cap_usd": "0.48",
        "max_cost_per_call_usd": "0.01",
        "pricing_sha256": preflight["record_identity"]["pricing_sha256"],
        "corpus_manifest_sha256": preflight["corpus"]["manifest_sha256"],
        "input_manifest_sha256": preflight["inputs"]["manifest_sha256"],
        "ordered_input_hashes_sha256": preflight["inputs"][
            "ordered_input_hashes_sha256"
        ],
        "max_output_tokens": preflight["record_identity"]["max_output_tokens"],
    }

    for supplied_token in (None, "wrong"):
        with pytest.raises(GateBPreflightError, match="capability"):
            authorize_record_run(
                preflight,
                approval_record=approval,
                owner_capability=supplied_token,
            )

    wrong_identity = deepcopy(approval)
    wrong_identity["run_identity_sha256"] = "0" * 64
    with pytest.raises(GateBPreflightError, match="identity_mismatch"):
        authorize_record_run(
            preflight,
            approval_record=wrong_identity,
            owner_capability=capability,
        )
    with pytest.raises(GateBPreflightError, match="exact_caps"):
        authorize_record_run(
            preflight,
            approval_record={**approval, "exact_call_cap": 47},
            owner_capability=capability,
        )
    with pytest.raises(GateBPreflightError, match="exact_caps"):
        authorize_record_run(
            preflight,
            approval_record={**approval, "exact_spend_cap_usd": "0.47"},
            owner_capability=capability,
        )
    authorized = authorize_record_run(
        preflight,
        approval_record=approval,
        owner_capability=capability,
    )
    assert authorized.exact_call_cap == 48
    assert authorized.exact_spend_cap_usd == gate_b.EXACT_SPEND_CAP_USD


def test_forbidden_side_effect_mutation_fails_closed(tmp_path: Path) -> None:
    protected = tmp_path / "production.sqlite3"
    protected.write_bytes(b"before")
    before = snapshot_paths([protected])
    protected.write_bytes(b"after")
    with pytest.raises(GateBPreflightError, match="forbidden_side_effect_mutation"):
        assert_paths_unchanged(before, snapshot_paths([protected]))
