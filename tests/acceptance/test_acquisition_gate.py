from __future__ import annotations

import pytest

from job_intel.product_search.acquisition_probe import validate_gate_a_run_evidence


def valid_evidence() -> dict:
    return {
        "candidate_commit": "a" * 40,
        "manifest_sha256": "b" * 64,
        "scheduled_attempts": 7,
        "completed_attempts": 6,
        "missed_attempts": 1,
        "overlap_attempts": 1,
        "isolated_paths": {
            "database": "/home/hermes/.hermes/job_intel/experiments/gate-a/a/experiment.sqlite3",
            "evidence": "/home/hermes/.hermes/job_intel/experiments/gate-a/a/raw-evidence",
            "logs": "/home/hermes/.hermes/job_intel/experiments/gate-a/a/logs",
            "locks": "/home/hermes/.hermes/job_intel/experiments/gate-a/a/locks",
            "browser_profile": "/home/hermes/.hermes/job_intel/experiments/gate-a/a/browser-profile",
            "cache": "/home/hermes/.hermes/job_intel/experiments/gate-a/a/cache",
            "tmp": "/home/hermes/.hermes/job_intel/experiments/gate-a/a/tmp",
        },
        "stage_counts": {
            "raw_observed": 100,
            "canonical_current": 80,
            "minimum_evidence_sufficient": 50,
        },
        "provisional_labels": {
            "provisionally_eligible": 30,
            "known_hard_block": 10,
            "unresolved_for_decision_v2": 10,
        },
        "family_attempts": {"linkedin": 20, "headhunter": 20},
        "acquisition_outcomes": {"uk": "blocked", "kazakhstan": "not_attempted"},
        "new_company_candidates": 12,
        "duplicates": 20,
        "cost_usd": 0.0,
        "latency_seconds": 42.0,
        "side_effects": {
            "production_database_writes": 0,
            "slack_calls": 0,
            "product_state_writes": 0,
            "production_profile_writes": 0,
        },
        "evidence_hashes_verified": True,
        "teardown_state": "pending_window_completion",
    }


def test_gate_a_accepts_only_stages_one_to_three_and_provisional_labels() -> None:
    validate_gate_a_run_evidence(valid_evidence())

    evidence = valid_evidence()
    evidence["cell_states"] = {"uk": "qualified_results_found"}
    with pytest.raises(ValueError, match="legacy cell_states"):
        validate_gate_a_run_evidence(evidence)

    evidence = valid_evidence()
    evidence["stage_counts"]["hard_gate_eligible"] = 1
    with pytest.raises(ValueError, match="stage 4"):
        validate_gate_a_run_evidence(evidence)

    evidence = valid_evidence()
    evidence["provisional_labels"]["Priority"] = 1
    with pytest.raises(ValueError, match="provisional label"):
        validate_gate_a_run_evidence(evidence)


def test_gate_a_rejects_live_state_or_slack_side_effects() -> None:
    for effect in (
        "production_database_writes",
        "slack_calls",
        "product_state_writes",
        "production_profile_writes",
    ):
        evidence = valid_evidence()
        evidence["side_effects"][effect] = 1
        with pytest.raises(ValueError, match="forbidden side effect"):
            validate_gate_a_run_evidence(evidence)


def test_gate_a_requires_attempt_accounting_family_cells_hashes_and_isolated_paths() -> None:
    mutations = (
        ("scheduled_attempts", 0, "scheduled attempts"),
        ("family_attempts", {}, "family attempts"),
        ("acquisition_outcomes", {}, "acquisition outcomes"),
        ("evidence_hashes_verified", False, "evidence hashes"),
    )
    for field, value, error in mutations:
        evidence = valid_evidence()
        evidence[field] = value
        with pytest.raises(ValueError, match=error):
            validate_gate_a_run_evidence(evidence)

    evidence = valid_evidence()
    evidence["isolated_paths"]["database"] = "/var/lib/job-intel/state/job_intel.sqlite3"
    with pytest.raises(ValueError, match="isolated path"):
        validate_gate_a_run_evidence(evidence)


def test_gate_a_attempt_accounting_includes_missed_and_overlap_attempts() -> None:
    evidence = valid_evidence()
    evidence["scheduled_attempts"] = 3
    evidence["completed_attempts"] = 3
    evidence["missed_attempts"] = 1

    with pytest.raises(ValueError, match="attempt accounting"):
        validate_gate_a_run_evidence(evidence)
