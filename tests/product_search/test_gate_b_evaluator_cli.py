from __future__ import annotations

import json
from pathlib import Path

from job_intel.product_search import gate_b_evidence_runner_v1 as runner
from tests.product_search.test_gate_b_gate_evaluator import (
    _complete_adjudication,
    _manifest,
    _metrics,
)


def _write_canonical(path: Path, payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    path.write_bytes(encoded)
    return runner._sha256(encoded)


def _write_run_inputs(tmp_path: Path, adjudication: object) -> tuple[Path, str, Path, str, Path]:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_sha256 = _write_canonical(
        manifest_path,
        manifest.model_dump(mode="json"),
    )
    measurement_path = tmp_path / "measurement-report.json"
    _write_canonical(
        measurement_path,
        _metrics(
            adjudicated_count=0,
            adjudication_denominator=0,
            adjudicated_correct=0,
        ).model_dump(mode="json"),
    )
    adjudication_path = tmp_path / "adjudication.json"
    _write_canonical(
        adjudication_path,
        adjudication.model_dump(mode="json"),
    )
    adjudication_sha256 = adjudication.adjudication_sha256
    policy_path = (
        Path(runner.__file__).resolve().parents[2]
        / "config/product_search/gate_b_benchmark.v3.yaml"
    )
    return (
        manifest_path,
        manifest_sha256,
        measurement_path,
        adjudication_sha256,
        policy_path,
    )


def _evaluate_args(
    inputs: tuple[Path, str, Path, str, Path],
    adjudication_path: Path,
    output: Path,
) -> list[str]:
    manifest_path, manifest_sha256, measurement_path, adjudication_sha256, policy_path = inputs
    return [
        "evaluate-run",
        "--manifest",
        str(manifest_path),
        "--manifest-sha256",
        manifest_sha256,
        "--measurement-report",
        str(measurement_path),
        "--adjudication",
        str(adjudication_path),
        "--adjudication-sha256",
        adjudication_sha256,
        "--gate-policy",
        str(policy_path),
        "--output",
        str(output),
    ]


def test_evaluate_run_cli_publishes_gate_decision(tmp_path: Path) -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    adjudication = _complete_adjudication(manifest)
    inputs = _write_run_inputs(tmp_path, adjudication)
    adjudication_path = tmp_path / "adjudication.json"
    output = tmp_path / "evaluated"

    assert runner._main(_evaluate_args(inputs, adjudication_path, output)) == 0

    decision = json.loads((output / "gate-decision.json").read_bytes())
    assert decision["measurement_status"] == "complete"
    assert decision["decision"] == "proceed_to_shadow"
    assert decision["violated_rules"] == []
    assert (output / "gate-evaluation-report.json").is_file()


def test_evaluate_run_cli_publishes_revise_for_incomplete_adjudication(
    tmp_path: Path,
) -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    adjudication = runner.AdjudicationSet.from_verdicts(())
    inputs = _write_run_inputs(tmp_path, adjudication)
    adjudication_path = tmp_path / "adjudication.json"
    output = tmp_path / "evaluated"

    assert runner._main(_evaluate_args(inputs, adjudication_path, output)) == 0

    decision = json.loads((output / "gate-decision.json").read_bytes())
    assert decision["measurement_status"] == "incomplete"
    assert decision["decision"] == "revise"
    assert decision["violated_rules"] == ["adjudication_incomplete"]
