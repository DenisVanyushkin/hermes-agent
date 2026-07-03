from __future__ import annotations

import argparse
import json

import pytest

from hermes_cli.recruiter_decision_cli import (
    cmd_recruiter_decision_run,
    register_recruiter_decision_subparser,
)


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    register_recruiter_decision_subparser(subparsers)
    return parser.parse_args(argv)


def test_run_partial_bundle_prints_packet(tmp_path, capsys) -> None:
    request_payload = {
        "requested_outputs": ["company_assessment", "company_risk_register"],
        "company_identity": "Example Corp",
        "company_research_claims": [
            {
                "claim": "Raised Series F",
                "category": "funding",
                "source": "https://example.com/press",
                "source_type": "press_release",
                "date_or_access_timestamp": "2026-06-20",
                "confidence": "high",
                "fact_vs_inference": "fact",
            },
            {
                "claim": "Docs cover 60 countries",
                "category": "product",
                "source": "https://example.com/docs",
                "source_type": "developer_docs",
                "date_or_access_timestamp": "2026-06-21",
                "confidence": "medium",
                "fact_vs_inference": "fact",
            },
        ],
    }
    input_file = tmp_path / "request.json"
    input_file.write_text(json.dumps(request_payload), encoding="utf-8")

    args = _parse(["recruiter-decision", "run", "--input-json", str(input_file), "--json"])
    # Without provider execution the modules are INCONCLUSIVE, exit code 1.
    with pytest.raises(SystemExit) as excinfo:
        cmd_recruiter_decision_run(args)

    assert excinfo.value.code == 1
    packet = json.loads(capsys.readouterr().out)
    assert packet["schema_version"] == "recruiter_decision_support_packet_v1"
    assert packet["requested_outputs"] == [
        "company_assessment",
        "company_risk_register",
        "manual_review_warnings",
    ]
    assert packet["safety"]["manual_review_required"] is True
    assert packet["modules"]["recommendation"]["status"] == "SKIPPED_NOT_REQUESTED"


def test_requires_json_flag() -> None:
    args = _parse(["recruiter-decision", "run", "--requested-outputs", "company_assessment"])
    with pytest.raises(SystemExit) as excinfo:
        cmd_recruiter_decision_run(args)
    assert excinfo.value.code == 2


def test_outbound_in_input_json_is_rejected(tmp_path) -> None:
    input_file = tmp_path / "request.json"
    input_file.write_text(json.dumps({"outbound_enabled": True}), encoding="utf-8")
    args = _parse(["recruiter-decision", "run", "--input-json", str(input_file), "--json"])
    with pytest.raises(SystemExit) as excinfo:
        cmd_recruiter_decision_run(args)
    assert excinfo.value.code == 2
