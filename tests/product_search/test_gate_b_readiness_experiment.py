from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from scripts import gate_b_readiness_experiment as experiment
from job_intel.product_search.evidence_synthesis import _safe_output_sha256
ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "scripts/gate_b_readiness_corpus.v1.json"
ALLOWLIST = ROOT / "docs/evidence/product-search-gate-b/v3-fragment-allowlist.yaml"
POLICY = ROOT / "config/product_search/decision_contract.v2.yaml"
COMPANY_ROOT = Path("/home/hermes/.hermes/job_intel/experiments/gate-b-company-evidence")
COMPANY_CONTRACT_SHA256 = "4ea5a5ff3eb66340edfb57796efb0ffc6832a5fd70d18d26afe96554ba27cd32"
CLOCK = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)


def _run(results: Path, provider: experiment.Provider = experiment.fake_provider) -> dict[str, int]:
    if not COMPANY_ROOT.exists():
        pytest.skip("host composition fixture is not installed")
    return experiment.run_experiment(
        corpus_path=CORPUS,
        allowlist_path=ALLOWLIST,
        company_evidence_root=COMPANY_ROOT,
        company_evidence_contract_sha256=COMPANY_CONTRACT_SHA256,
        decision_policy_path=POLICY,
        decision_clock=CLOCK,
        results_path=results,
        provider=provider,
    )


def test_row_key_keeps_ordinal_for_duplicate_inputs() -> None:
    first = experiment._row_key("a" * 64, 0, "b" * 64, "c" * 64)
    duplicate = experiment._row_key("a" * 64, 5, "b" * 64, "c" * 64)
    assert first != duplicate


def test_allowlist_without_a_row_is_a_named_pre_dispatch_refusal() -> None:
    rows = json.loads(CORPUS.read_text())
    allowlist = SimpleNamespace(entries=())
    with pytest.raises(ValueError, match=r"allowlist_row_uncovered:0,1,2"):
        experiment.assert_allowlist_covers_rows(rows, allowlist)


def test_fake_provider_emits_v2_metadata_and_shared_output_hash() -> None:
    claims = [
        {
            "dimension": dimension,
            "status": "unknown",
            "claim_code": f"code:{dimension}",
            "statement": f"statement:{dimension}",
        }
        for dimension in experiment.DIMENSIONS
    ]
    projected_payload = {
        "fragments": [
            {
                "fragment_id": "fragment:test",
                "allowed_claims": claims,
            }
        ]
    }

    class Capability:
        def reserve(self, row_key: str) -> str:
            return f"reservation:{row_key}"

        def mark_dispatching(self, reservation_id: str) -> None:
            del reservation_id

        def reconcile(self, reservation_id: str, amount: Decimal, outcome: str) -> None:
            del reservation_id, amount, outcome

    call = experiment.fake_provider(projected_payload, "input:test", Capability())

    assert call.provider_record["schema_version"] == "2.0.0"
    assert call.provider_record["provider_version"] == "product-search-evidence-replay/2.0"
    assert call.provider_record["prompt_version"] == "product-search-evidence-synthesis-2.0.0"
    assert call.provider_record["output_sha256"] == _safe_output_sha256(call.response_payload)


def test_started_without_terminal_becomes_ambiguous_without_dispatch(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    assert _run(results) == {"transport_success": 48, "terminal_failure": 0, "ambiguous": 0, "cached": 0, "assessed": 45, "decision_fail_closed": 3}
    events = experiment._events(results)
    last_key = events[-1]["row_key"]
    results.write_bytes(b"".join(experiment._canonical(event) + b"\n" for event in events[:-1]))

    def forbidden_provider(payload: dict[str, object], input_sha256: str, capability: object) -> experiment.ProviderCall:
        del payload, input_sha256, capability
        raise AssertionError("ambiguous row must never dispatch again")

    counts = _run(results, forbidden_provider)
    assert counts == {"transport_success": 0, "terminal_failure": 0, "ambiguous": 1, "cached": 47, "assessed": 0, "decision_fail_closed": 0}
    terminal = [event for event in experiment._events(results) if event.get("row_key") == last_key][-1]
    assert terminal["outcome"] == "ambiguous"
    assert terminal["actual_cost_usd"] is None
    assert terminal["conservative_cost_usd"] == "0.01"


def test_full_fake_run_replays_without_sqlite_and_summarizes_for_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOB_INTEL_DB_PATH", "/definitely-live-database-must-not-be-opened")

    def forbidden_connect(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("readiness experiment must not open sqlite")

    monkeypatch.setattr(sqlite3, "connect", forbidden_connect)
    results = tmp_path / "results.jsonl"
    assert _run(results)["transport_success"] == 48
    terminal_events = [
        event for event in experiment._events(results)
        if event.get("event") == "terminal_result"
    ]
    events = experiment._events(results)
    assert sum(event.get("event") == "dispatch_reconciled" for event in events) == 48
    event_positions: dict[str, dict[str, int]] = {}
    for index, event in enumerate(events):
        row_key = event.get("row_key")
        if row_key and event.get("event") in {"dispatch_started", "dispatch_reconciled", "terminal_result"}:
            event_positions.setdefault(str(row_key), {})[str(event["event"])] = index
    assert all(
        positions["dispatch_started"] < positions["dispatch_reconciled"] < positions["terminal_result"]
        for positions in event_positions.values()
    )
    assert sum(event["decision_result"]["status"] == "assessed" for event in terminal_events) == 45
    assert [event["ordinal"] for event in terminal_events if event["decision_result"]["status"] != "assessed"] == [1, 17, 34]
    before = results.read_bytes()
    assert _run(results) == {"transport_success": 0, "terminal_failure": 0, "ambiguous": 0, "cached": 48, "assessed": 0, "decision_fail_closed": 0}
    assert results.read_bytes() == before
    assert experiment.replay(results, POLICY) == {"success": 48, "terminal_failure": 0, "ambiguous": 0}

    markdown = tmp_path / "adjudication.md"
    adjudication = tmp_path / "adjudication.jsonl"
    experiment.summarize(results, markdown, adjudication)
    text = markdown.read_text()
    assert "| Row | Company | Vacancy | Verdict |" in text
    assert len(adjudication.read_text().splitlines()) == 48
    assert all(json.loads(line)["correct"] is None for line in adjudication.read_text().splitlines())
    rows = [json.loads(line) for line in adjudication.read_text().splitlines()]
    rows[0]["correct"] = True
    rows[0]["note"] = "checked against vacancy"
    adjudication.write_bytes(b"".join(experiment._canonical(row) + b"\n" for row in rows))
    experiment.summarize(results, markdown, adjudication)
    assert json.loads(adjudication.read_text().splitlines()[0])["note"] == "checked against vacancy"
    assert "Reviewed: 1/48. Correct: 1/1." in markdown.read_text()
    summary = markdown.read_text()
    assert "Transport completed: 48/48. Decisions assessed: 45/48. Not measurable: 3/48." in summary
    assert "Evidence admission: 394/2594 fragments admitted; 1252 ambiguous and 948 company-fact fragments excluded." in summary
    assert "NOT MEASURABLE" in summary
    assert "one unheaded description block" in summary


def test_provider_refusal_is_terminal_and_cached(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    calls = 0

    def refusing_provider(payload: dict[str, object], input_sha256: str, capability: object) -> experiment.ProviderCall:
        nonlocal calls
        del payload
        calls += 1
        reservation = capability.reserve(input_sha256)
        capability.mark_dispatching(reservation)
        capability.reconcile(reservation, Decimal("0.01"), "terminal_failure")
        return experiment.ProviderCall(
            outcome="terminal_failure",
            response_payload={},
            raw_response_text="{}",
            provider_record={"measured_cost_usd": "0.01", "conservative_cost_usd": "0.01"},
        )

    first = _run(results, refusing_provider)
    assert first["terminal_failure"] == 48
    assert calls == 48
    second = _run(results, refusing_provider)
    assert second["cached"] == 48
    assert calls == 48
    terminal = [event for event in experiment._events(results) if event.get("event") == "terminal_result"]
    assert terminal[0]["actual_cost_usd"] == "0.01"
    assert terminal[0]["conservative_cost_usd"] == "0.01"
