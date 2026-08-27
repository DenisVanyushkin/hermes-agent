from pathlib import Path

import pytest

from job_intel.product_search.acquisition_probe import (
    ProbeSourceBlocked,
    SourceIsolation,
    run_probe,
    validate_gate_a_run_evidence,
)
import job_intel.product_search.acquisition_probe as acquisition_probe
from test_acquisition_probe import _B1DegradedSource, _b1_record, _b1_run

def test_b1_one_completed_family_below_minimum_is_insufficient_breadth(tmp_path: Path) -> None:
    result = _b1_run(tmp_path, {"alpha": [_b1_record("alpha-1")]})

    assert result.acquisition_outcomes["b1-cell"] == "insufficient_breadth"

def test_b1_product_state_is_absent_until_stage_four_evidence(tmp_path: Path) -> None:
    result = _b1_run(
        tmp_path,
        {"alpha": [_b1_record("alpha-1")], "beta": [_b1_record("beta-1")]},
    )

    assert result.acquisition_outcomes["b1-cell"] == "candidate_records_found"
    assert result.product_observability_state["b1-cell"] is None
    assert result.product_observability_reason["b1-cell"] == "stage_4_evidence_absent"

def test_b1_single_linkedin_family_with_many_rows_is_still_insufficient_breadth(tmp_path: Path) -> None:
    rows = [_b1_record(f"turkmenistan-{index}") for index in range(21)]
    result = _b1_run(tmp_path, {"linkedin": rows})

    assert result.acquisition_outcomes["b1-cell"] == "insufficient_breadth"

def test_b1_two_completed_independent_families_can_credit_candidate_records(tmp_path: Path) -> None:
    result = _b1_run(
        tmp_path,
        {"alpha": [_b1_record("alpha-1")], "beta": [_b1_record("beta-1")]},
        credited=1,
    )

    assert result.acquisition_outcomes["b1-cell"] == "candidate_records_found"

def test_b1_degraded_attempt_beats_breadth_shortfall(tmp_path: Path) -> None:
    result = _b1_run(
        tmp_path,
        {"alpha": [_b1_record("alpha-1")], "beta": _B1DegradedSource()},
    )

    assert result.acquisition_outcomes["b1-cell"] == "degraded"

def test_b1_completed_and_blocked_below_breadth_is_blocked(tmp_path: Path) -> None:
    result = _b1_run(
        tmp_path,
        {"alpha": [_b1_record("alpha-1")], "beta": []},
        blocked=frozenset({"beta"}),
    )

    assert result.acquisition_outcomes["b1-cell"] == "blocked"

def test_b1_completed_blocked_and_degraded_is_unambiguously_degraded(tmp_path: Path) -> None:
    result = _b1_run(
        tmp_path,
        {
            "alpha": [_b1_record("alpha-1")],
            "beta": [],
            "gamma": _B1DegradedSource(),
        },
        blocked=frozenset({"beta"}),
    )

    assert result.acquisition_outcomes["b1-cell"] == "degraded"

def test_b1_breadth_threshold_preserves_blocked_and_degraded_diagnostics(tmp_path: Path) -> None:
    result = _b1_run(
        tmp_path,
        {
            "alpha": [_b1_record("alpha-1")],
            "beta": [_b1_record("beta-1")],
            "gamma": [],
            "delta": _B1DegradedSource(),
        },
        blocked=frozenset({"gamma"}),
    )

    assert result.acquisition_outcomes["b1-cell"] == "candidate_records_found"
    assert result.degraded_families["b1-cell"] == ("delta",)
    assert result.blocked_families["b1-cell"] == ("gamma",)

def test_b1_transition_table_is_complete_and_non_overlapping() -> None:
    transition = getattr(acquisition_probe, "resolve_acquisition_outcome", None)
    assert callable(transition), "B1 transition function is missing"

    for completed in range(4):
        for blocked in range(4):
            for degraded in range(4):
                matches = acquisition_probe.matching_acquisition_rules(
                    completed=completed,
                    blocked=blocked,
                    degraded=degraded,
                    minimum_independent_families=2,
                )
                assert len(matches) == 1, (
                    completed,
                    blocked,
                    degraded,
                    matches,
                )
                decision = transition(
                    completed=completed,
                    blocked=blocked,
                    degraded=degraded,
                    minimum_independent_families=2,
                    credited=1 if matches[0] == "candidate_records_found" else 0,
                )
                assert decision.acquisition_outcome == matches[0]

def test_b1_zero_attempts_are_not_attempted() -> None:
    decision = acquisition_probe.resolve_acquisition_outcome(
        completed=0,
        blocked=0,
        degraded=0,
        minimum_independent_families=2,
        credited=0,
    )

    assert decision.acquisition_outcome == "not_attempted"
    assert decision.product_observability_state == "not_observed"

def test_b1_only_blocked_attempts_are_blocked() -> None:
    decision = acquisition_probe.resolve_acquisition_outcome(
        completed=0,
        blocked=1,
        degraded=0,
        minimum_independent_families=2,
        credited=0,
    )

    assert decision.acquisition_outcome == "blocked"
    assert decision.product_observability_state == "blocked"

def test_b1_completed_breadth_without_credited_rows_has_no_candidate_records() -> None:
    decision = acquisition_probe.resolve_acquisition_outcome(
        completed=2,
        blocked=0,
        degraded=0,
        minimum_independent_families=2,
        credited=0,
    )

    assert decision.acquisition_outcome == "no_candidate_records"
    assert decision.product_observability_reason == "stage_4_evidence_absent"

def test_b1_same_family_is_accounted_per_cell_not_globally(tmp_path: Path) -> None:
    class SplitFamily:
        def __call__(self, query: str) -> list[dict[str, str]]:
            if query == "blocked-cell":
                raise ProbeSourceBlocked("anti_bot", "blocked in one cell")
            return [_b1_record("shared-family-row")]

    result = run_probe(
        run_id="b1-per-cell",
        queries=(
            {
                "query_id": "q-blocked",
                "cell_id": "blocked-cell",
                "source_family": "shared",
                "query": "blocked-cell",
            },
            {
                "query_id": "q-completed",
                "cell_id": "completed-cell",
                "source_family": "shared",
                "query": "completed-cell",
            },
        ),
        sources={"shared": SplitFamily()},
        output_dir=tmp_path,
        isolation={
            "shared": SourceIsolation(
                mode="api", path=tmp_path / "shared.lock", collection_method="api"
            )
        },
        minimum_independent_families_by_cell={
            "blocked-cell": 1,
            "completed-cell": 1,
        },
        max_attempts=1,
    )

    assert result.acquisition_outcomes == {
        "blocked-cell": "blocked",
        "completed-cell": "candidate_records_found",
    }

def test_b1_acquisition_outcomes_never_use_product_qualified_label(tmp_path: Path) -> None:
    result = _b1_run(tmp_path, {"alpha": [_b1_record("alpha-1")]})

    assert all("qualified" not in outcome for outcome in result.acquisition_outcomes.values())

def test_b1_legacy_summary_refuses_to_reconstruct_attempt_outcomes() -> None:
    legacy = Path(
        "/home/hermes/.hermes/job_intel/experiments/gate-a/"
        "65d60daae16093a9a7e34a11a159e2f789dd14dd/summary.json"
    )
    if not legacy.is_file():
        pytest.skip("legacy Gate A corpus exists only on the VPS")

    report = acquisition_probe.classify_legacy_attempt_evidence(legacy)

    assert report["acquisition_outcomes"] == {}
    assert set(report["cell_outcomes"].values()) == {
        "legacy_attempt_evidence_insufficient"
    }
    assert len(report["cell_outcomes"]) == 30
    assert report["attempt_evidence_criterion"] == "legacy_pre_b1_cell_states"

def test_b1_rejects_legacy_cell_states_even_next_to_new_outcomes() -> None:
    evidence = {
        "stage_counts": {
            "raw_observed": 0,
            "canonical_current": 0,
            "minimum_evidence_sufficient": 0,
        },
        "provisional_labels": {},
        "scheduled_attempts": 1,
        "completed_attempts": 1,
        "missed_attempts": 0,
        "family_attempts": {"alpha": 1},
        "acquisition_outcomes": {"uk": "blocked"},
        "cell_states": {"uk": "qualified_results_found"},
        "evidence_hashes_verified": True,
        "isolated_paths": {},
        "side_effects": {},
    }

    with pytest.raises(ValueError, match="legacy cell_states"):
        validate_gate_a_run_evidence(evidence)

def test_b1_summary_exposes_credited_records_provenance(tmp_path: Path) -> None:
    families = {
        "alpha": [_b1_record("alpha-1")],
        "beta": [_b1_record("beta-1")],
    }

    fallback = _b1_run(tmp_path / "fallback", families)
    attributed = _b1_run(tmp_path / "attributed", families, credited=1)

    assert fallback.credited_records_provenance == {
        "b1-cell": "received_rows_fallback"
    }
    assert attributed.credited_records_provenance == {
        "b1-cell": "caller_supplied"
    }

