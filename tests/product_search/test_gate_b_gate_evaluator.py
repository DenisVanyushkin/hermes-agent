from __future__ import annotations

from pathlib import Path

import pytest

from tests.product_search.test_gate_b_evidence_skeleton import _manifest
from job_intel.product_search.gate_b_benchmark_policy_v3 import (
    load_gate_b_benchmark_policy_v3,
)
from job_intel.product_search.gate_b_evidence_runner_v1 import (
    AdjudicationSet,
    AdjudicationSetStore,
    AdjudicationVerdict,
    GateDecisionKind,
    GateEvaluator,
    MeasurementReport,
)


def _decision_hashes() -> tuple[str, ...]:
    return tuple(f"{index + 100:064x}" for index in range(48))


def _metrics(**overrides: object) -> MeasurementReport:
    values: dict[str, object] = {
        "expected_row_count": 48,
        "observed_row_count": 48,
        "deliverable_count": 48,
        "terminal_unknown_count": 0,
        "adjudicated_count": 48,
        "adjudication_denominator": 48,
        "adjudicated_correct": 40,
        "recording_sha256s": tuple(f"{index:064x}" for index in range(48)),
        "decision_sha256s": _decision_hashes(),
    }
    values.update(overrides)
    return MeasurementReport(**values)


def _policy():
    return load_gate_b_benchmark_policy_v3()


def _complete_adjudication(manifest: object, *, correct_count: int = 48) -> AdjudicationSet:
    verdicts = tuple(
        AdjudicationVerdict(
            manifest_ref=manifest.row_ref(index),
            decision_sha256=_decision_hashes()[index],
            correct=index < correct_count,
        )
        for index in range(48)
    )
    return AdjudicationSet.from_verdicts(verdicts)


def test_evaluator_uses_loaded_policy_thresholds() -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    policy = load_gate_b_benchmark_policy_v3().model_copy(
        update={"minimum_deliverable_results": 48}
    )
    report = GateEvaluator.evaluate_report(
        manifest,
        _metrics(deliverable_count=47, adjudicated_correct=48),
        _complete_adjudication(manifest),
        policy=policy,
    )

    assert report.gate_decision.decision is GateDecisionKind.REFUSE
    assert report.gate_decision.violated_rules == ("minimum_deliverable_results",)
    default_report = GateEvaluator.evaluate_report(
        manifest,
        _metrics(adjudicated_correct=48),
        _complete_adjudication(manifest),
        policy=_policy(),
    )
    assert (
        report.gate_decision.evaluator_contract_sha256
        != default_report.gate_decision.evaluator_contract_sha256
    )


def test_evaluator_publishes_proceed_to_shadow_for_complete_passing_measurement() -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    report = GateEvaluator.evaluate_report(
        manifest,
        _metrics(adjudicated_correct=48),
        _complete_adjudication(manifest),
        policy=load_gate_b_benchmark_policy_v3(),
    )

    assert report.gate_decision.measurement_status == "complete"
    assert report.gate_decision.decision is GateDecisionKind.PROCEED_TO_SHADOW
    assert report.gate_decision.violated_rules == ()


def test_evaluator_rejects_verdict_with_foreign_manifest_ref() -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    verdicts = list(_complete_adjudication(manifest).verdicts)
    verdicts[0] = verdicts[0].model_copy(
        update={
            "manifest_ref": verdicts[0].manifest_ref.model_copy(
                update={"manifest_sha256": "e" * 64}
            )
        }
    )
    forged = AdjudicationSet.from_verdicts(tuple(verdicts))

    decision = GateEvaluator.evaluate(
        manifest,
        _metrics(adjudicated_correct=48),
        forged,
        policy=load_gate_b_benchmark_policy_v3(),
    )

    assert decision.decision is GateDecisionKind.REVISE
    assert decision.violated_rules == ("adjudication_incomplete",)


def test_evaluator_always_returns_metrics_and_refuses_only_gate_decision() -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    report = GateEvaluator.evaluate_report(
        manifest,
        _metrics(deliverable_count=42, adjudicated_correct=48),
        _complete_adjudication(manifest),
        policy=_policy(),
    )

    assert report.metrics.deliverable_count == 42
    assert report.metrics.adjudication_set_sha256 is not None
    assert report.gate_decision.decision is GateDecisionKind.REFUSE
    assert report.gate_decision.violated_rules == ("minimum_deliverable_results",)


def test_missing_adjudication_is_incomplete_not_zero_accuracy() -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    report = GateEvaluator.evaluate_report(
        manifest,
        _metrics(adjudicated_count=0, adjudication_denominator=0, adjudicated_correct=0),
        AdjudicationSet.from_verdicts(()),
        policy=_policy(),
    )

    assert report.gate_decision.measurement_status == "incomplete"
    assert report.gate_decision.decision is GateDecisionKind.REVISE
    assert report.gate_decision.violated_rules == ("adjudication_incomplete",)


def test_incomplete_collection_is_revise_even_with_complete_adjudication() -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    report = GateEvaluator.evaluate_report(
        manifest,
        _metrics(observed_row_count=47, adjudicated_correct=48),
        _complete_adjudication(manifest),
        policy=_policy(),
    )

    assert report.metrics.observed_row_count == 47
    assert report.gate_decision.measurement_status == "incomplete"
    assert report.gate_decision.decision is GateDecisionKind.REVISE
    assert report.gate_decision.violated_rules == ("collection_incomplete",)


def test_threshold_rules_are_exactly_three_and_sorted() -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    report = GateEvaluator.evaluate_report(
        manifest,
        _metrics(deliverable_count=42, terminal_unknown_count=6, adjudicated_correct=38),
        _complete_adjudication(manifest, correct_count=38),
        policy=_policy(),
    )

    assert report.gate_decision.decision is GateDecisionKind.REFUSE
    assert report.gate_decision.violated_rules == (
        "maximum_terminal_unknown",
        "minimum_deliverable_results",
        "minimum_manual_triage_accuracy",
    )


def test_adjudication_decision_hash_must_match_finalized_row(tmp_path: Path) -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    verdicts = list(_complete_adjudication(manifest).verdicts)
    verdicts[0] = verdicts[0].model_copy(update={"decision_sha256": "f" * 64})
    forged = AdjudicationSet.from_verdicts(tuple(verdicts))

    with pytest.raises(ValueError, match="decision hash mismatch"):
        AdjudicationSetStore(tmp_path).save_exclusive(
            forged,
            manifest,
            _decision_hashes(),
        )
    report = GateEvaluator.evaluate_report(
        manifest,
        _metrics(adjudicated_correct=48),
        forged,
        policy=_policy(),
    )
    assert report.gate_decision.decision is GateDecisionKind.REVISE
    assert report.gate_decision.violated_rules == ("adjudication_incomplete",)


def test_adjudication_set_is_create_once_and_manifest_bound(tmp_path: Path) -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    adjudication = _complete_adjudication(manifest)
    store = AdjudicationSetStore(tmp_path)
    ref = store.save_exclusive(adjudication, manifest, _decision_hashes())

    assert ref.adjudication_sha256 == adjudication.adjudication_sha256
    assert store.load(ref, manifest, _decision_hashes()) == adjudication
    assert store.save_exclusive(adjudication, manifest, _decision_hashes()) == ref


def test_adjudication_totals_are_derived_and_mismatch_is_incomplete() -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    report = GateEvaluator.evaluate_report(
        manifest,
        _metrics(adjudicated_count=47, adjudication_denominator=47),
        _complete_adjudication(manifest),
        policy=_policy(),
    )

    assert report.gate_decision.decision is GateDecisionKind.REVISE
    assert report.gate_decision.violated_rules == ("adjudication_incomplete",)
