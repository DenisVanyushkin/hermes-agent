from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest
import yaml

from job_intel.product_search.acquisition_probe import (
    _canonical_url,
    _minimum_evidence_sufficient,
)
from job_intel.product_search.decision_v2 import DecisionResultV2
from job_intel.product_search.evidence_synthesis import (
    EvidenceDimension,
    ProviderEvidencePayloadV1,
    load_evidence_synthesis_policy,
)
from job_intel.product_search.gate_b import (
    build_dry_run_preflight,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_A_ROOT = Path(
    os.environ.get(
        "PRODUCT_SEARCH_GATE_A_ROOT",
        "/home/hermes/.hermes/job_intel/experiments/gate-a/"
        "65d60daae16093a9a7e34a11a159e2f789dd14dd",
    )
)
GATE_A_MANIFEST_SHA256 = (
    "6ecc500c291061a34c4482edb5c2a0d6c547993bea0d346ad306041dfa81df3d"
)
GATE_A_RUN_ID = "gate-a-20260816T141344Z"
SUMMARY_PATH = REPO_ROOT / "docs/evidence/product-search-gate-b/benchmark-summary.json"
OWNER_DECISION_PATH = REPO_ROOT / "docs/evidence/product-search-gate-b/owner-decision.md"
UNAVAILABLE_RESULT_STATUSES = frozenset(
    {"not_selected", "not_run", "not_computable"}
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_summary() -> dict:
    return json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))


def _load_owner_decision_frontmatter() -> dict:
    text = OWNER_DECISION_PATH.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, frontmatter, _ = text.split("---\n", 2)
    return yaml.safe_load(frontmatter)


def _assert_exact_gate_a_run_identity(
    evidence_rows: list[dict], probe_run_ids: list[str]
) -> None:
    evidence_run_ids = {str(row["run_id"]) for row in evidence_rows}
    assert evidence_run_ids == {GATE_A_RUN_ID}, (
        f"probe_evidence contains unexpected run IDs: {sorted(evidence_run_ids)}"
    )
    assert probe_run_ids == [GATE_A_RUN_ID], (
        f"probe_runs must contain exactly the pinned run: {probe_run_ids}"
    )


def _contains_null(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, dict):
        return any(_contains_null(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_null(item) for item in value)
    return False


def _assert_no_ambiguous_zero(
    value: object,
    *,
    path: str = "$",
    unavailable_ancestor: bool = False,
) -> None:
    if isinstance(value, dict):
        status = value.get("status")
        if status in UNAVAILABLE_RESULT_STATUSES:
            unavailable_ancestor = True
            assert isinstance(value.get("reason"), str) and value["reason"].strip(), (
                f"{path} requires a nonempty machine-readable reason"
            )
            assert _contains_null(value), f"{path} requires null unavailable results"
        elif status == "observed":
            unavailable_ancestor = False
        for key, item in value.items():
            _assert_no_ambiguous_zero(
                item,
                path=f"{path}.{key}",
                unavailable_ancestor=unavailable_ancestor,
            )
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_no_ambiguous_zero(
                item,
                path=f"{path}[{index}]",
                unavailable_ancestor=unavailable_ancestor,
            )
        return
    if unavailable_ancestor and not isinstance(value, bool):
        assert not isinstance(value, (int, float)) or value != 0, (
            f"{path} contains an ambiguous numeric zero in unavailable results"
        )


def _canonical_gate_a_rows() -> tuple[list[dict], list[dict]]:
    database = GATE_A_ROOT / "experiment.sqlite3"
    connection = sqlite3.connect(
        f"file:{database}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    evidence_rows = [
        dict(row)
        for row in connection.execute(
            "SELECT run_id, raw_content_sha256, query_id, source_family, "
            "source_id, raw_reference, redaction_class "
            "FROM probe_evidence ORDER BY raw_content_sha256"
        )
    ]
    probe_run_ids = [
        str(row[0])
        for row in connection.execute("SELECT run_id FROM probe_runs ORDER BY run_id")
    ]
    connection.close()
    _assert_exact_gate_a_run_identity(evidence_rows, probe_run_ids)

    canonical: dict[str, dict] = {}
    for evidence in evidence_rows:
        raw_path = GATE_A_ROOT / evidence["raw_reference"]
        assert _sha256(raw_path) == evidence["raw_content_sha256"]
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        assert payload["source_id"] == evidence["source_id"]
        assert payload["query_id"] == evidence["query_id"]
        assert payload["source_family"] == evidence["source_family"]
        identity = _canonical_url(str(payload.get("url") or ""))
        if not identity:
            identity = hashlib.sha256(
                f"{payload.get('company')}\0{payload.get('title')}".encode()
            ).hexdigest()
        current = canonical.get(identity)
        candidate_key = (
            evidence["source_family"],
            evidence["source_id"],
            evidence["raw_content_sha256"],
        )
        if current is None or candidate_key < current[0]:
            canonical[identity] = (candidate_key, payload)
    records = [item[1] for item in canonical.values()]
    sufficient = [record for record in records if _minimum_evidence_sufficient(record)]
    return records, sufficient


def test_gate_b_imports_the_exact_approved_gate_a_package_read_only() -> None:
    """Break caught: Gate B drifts to fixtures, a different run, or modified evidence."""
    assert GATE_A_ROOT.is_dir()
    manifest_path = GATE_A_ROOT / "manifest.yaml"
    database_path = GATE_A_ROOT / "experiment.sqlite3"
    before = {
        path: (path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns)
        for path in (manifest_path, database_path)
    }
    assert _sha256(manifest_path) == GATE_A_MANIFEST_SHA256
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert manifest["commit"] == "65d60daae16093a9a7e34a11a159e2f789dd14dd"
    assert manifest["paths"]["experiment.sqlite3"] == str(database_path)

    canonical, sufficient = _canonical_gate_a_rows()
    assert len(canonical) == 1814
    assert len(sufficient) == 1314

    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro&immutable=1",
        uri=True,
    )
    assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert connection.execute("SELECT COUNT(*) FROM probe_evidence").fetchone() == (2414,)
    assert connection.execute(
        "SELECT COUNT(DISTINCT run_id) FROM probe_evidence"
    ).fetchone() == (1,)
    assert connection.execute(
        "SELECT MIN(run_id), MAX(run_id) FROM probe_evidence"
    ).fetchone() == (GATE_A_RUN_ID, GATE_A_RUN_ID)
    assert connection.execute(
        "SELECT COUNT(*), MIN(run_id), MAX(run_id) FROM probe_runs"
    ).fetchone() == (1, GATE_A_RUN_ID, GATE_A_RUN_ID)
    assert connection.execute(
        "SELECT COUNT(*) FROM probe_evidence "
        "WHERE redaction_class != 'vacancy_public_evidence'"
    ).fetchone() == (0,)
    connection.close()
    after = {
        path: (path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns)
        for path in (manifest_path, database_path)
    }
    assert after == before


def test_gate_a_import_rejects_a_mixed_run_evidence_set() -> None:
    """Break caught: extra probe rows from another run enter the denominator."""
    mixed_rows = [
        {"run_id": GATE_A_RUN_ID},
        {"run_id": "gate-a-unexpected-run"},
    ]
    with pytest.raises(AssertionError, match="unexpected run IDs"):
        _assert_exact_gate_a_run_identity(mixed_rows, [GATE_A_RUN_ID])

    with pytest.raises(AssertionError, match="exactly the pinned run"):
        _assert_exact_gate_a_run_identity(
            [{"run_id": GATE_A_RUN_ID}],
            [GATE_A_RUN_ID, "gate-a-unexpected-run"],
        )


def test_gate_b_dry_preflight_materializes_exact_corpus_without_calls(
    tmp_path: Path,
) -> None:
    """Break caught: readiness uses another corpus or attempts provider/runtime work."""
    preflight = build_dry_run_preflight(
        gate_a_root=GATE_A_ROOT, output_root=tmp_path
    )
    summary = _load_summary()
    assert preflight["status"] == "ready_for_record_approval"
    assert preflight["corpus"]["manifest_sha256"] == summary["corpus"][
        "manifest_sha256"
    ]
    assert preflight["corpus"]["selected_count"] == 48
    assert preflight["budget"] == summary["budget"]
    assert preflight["record_identity"] == summary["record_identity"]
    assert preflight["provider"] == {"calls_attempted": 0, "network_enabled": False}
    assert preflight["record_authorized"] is False
    assert preflight["task_13_authorized"] is False


def test_ready_gate_b_package_preserves_denominators_and_no_fake_results() -> None:
    """Break caught: a readiness preflight is presented as a real benchmark."""
    summary = _load_summary()
    assert summary["schema_version"] == "1.0.0"
    assert summary["gate"] == "gate-b"
    assert summary["status"] == "ready_for_record_approval"
    assert summary["gate_a_input"] == {
        "commit": "65d60daae16093a9a7e34a11a159e2f789dd14dd",
        "manifest_sha256": GATE_A_MANIFEST_SHA256,
        "run_id": GATE_A_RUN_ID,
        "raw_observed": 2414,
        "corrected_canonical_current": 1814,
        "minimum_evidence_sufficient": 1314,
        "minimum_evidence_is_not_qualified": True,
    }
    assert summary["corpus"]["status"] == "materialized"
    assert summary["corpus"]["selection_denominator"] == 1314
    assert summary["corpus"]["selected_count"] == 48
    assert len(summary["corpus"]["manifest_sha256"]) == 64
    assert summary["stage_4"] == {
        "status": "not_run",
        "reason": "live_benchmark_not_authorized",
        "input_denominator": None,
        "hard_gate_eligible": None,
        "fail_closed": None,
    }
    assert set(summary["dimensions"]) == {dimension.value for dimension in EvidenceDimension}
    for result in summary["dimensions"].values():
        assert result == {
            "status": "not_run",
            "reason": "live_benchmark_not_authorized",
            "evaluated": None,
            "outcomes": None,
        }
    assert summary["verdicts"] == {
        "status": "not_run",
        "reason": "decision_v2_not_run",
        "counts": None,
    }
    assert summary["unresolved_questions"] == {
        "status": "not_computable",
        "reason": "evidence_synthesis_not_run",
        "vacancies": None,
        "questions": None,
    }
    assert summary["delivery"] == {
        "status": "not_computable",
        "reason": "decision_v2_not_run",
        "daily_eligible": None,
        "urgent_eligible": None,
    }


def test_ready_gate_b_accounts_for_provider_replay_audit_cost_and_side_effects() -> None:
    """Break caught: absent calls or audits are hidden, or a side effect is tolerated."""
    summary = _load_summary()
    assert summary["provider"] == {
        "status": "not_run_pending_owner_approval",
        "reason": "live_benchmark_not_authorized",
        "operational_counters": {
            "status": "observed",
            "reason": "dry_preflight_forbids_provider_attempts",
            "calls_attempted": 0,
            "calls_succeeded": 0,
            "calls_failed": 0,
        },
        "results": {
            "status": "not_computable",
            "reason": "no_provider_attempts",
            "failures_by_reason": None,
            "cost_usd": None,
            "latency_ms": None,
        },
    }
    assert summary["offline_replay"] == {
        "status": "not_run",
        "reason": "no_task10_recordings",
        "recordings": None,
        "byte_stable_matches": None,
        "mismatches": None,
        "network_enabled": None,
    }
    assert summary["decision_trace"] == {
        "status": "not_run",
        "reason": "decision_v2_not_run",
        "traces": None,
        "replays": None,
        "exact_matches": None,
        "mismatches": None,
        "invariant_violations": None,
    }
    assert summary["human_audit"] == {
        "status": "not_run",
        "reason": "no_decision_v2_results",
        "high_risk_invariants_reviewed": None,
        "random_sample_reviewed": None,
        "factual_errors": None,
        "policy_errors": None,
        "interpretation_disagreements": None,
    }
    assert summary["legacy_counterfactual"] == {
        "status": "not_run",
        "reason": "canonical_benchmark_not_run",
        "result": None,
        "authority": False,
        "automatic_truth": False,
    }
    assert summary["side_effects"] == {
        "status": "observed",
        "reason": "dry_preflight_observed_no_forbidden_side_effects",
        "production_database_writes": 0,
        "product_store_writes": 0,
        "slack_calls": 0,
        "outbox_writes": 0,
        "profile_writes": 0,
        "cache_writes": 0,
        "protected_source_writes": 0,
    }


def test_unrun_result_sections_recursively_reject_ambiguous_numeric_zeros() -> None:
    """Break caught: an unavailable result is serialized as an observed zero."""
    summary = _load_summary()
    for section in (
        "corpus",
        "stage_4",
        "dimensions",
        "verdicts",
        "unresolved_questions",
        "delivery",
        "provider",
        "offline_replay",
        "decision_trace",
        "human_audit",
        "legacy_counterfactual",
        "side_effects",
    ):
        _assert_no_ambiguous_zero(summary[section], path=f"$.{section}")

    ambiguous = {
        "status": "not_run",
        "reason": "fixture_was_not_executed",
        "counts": {"Priority": 0},
        "result": None,
    }
    with pytest.raises(AssertionError, match="ambiguous numeric zero"):
        _assert_no_ambiguous_zero(ambiguous, path="$.mutation")


def test_owner_decision_stays_pending_and_task13_remains_blocked() -> None:
    """Break caught: recommendation or passing tests are misrecorded as approval."""
    summary = _load_summary()
    assert summary["owner_decision"] == "pending"
    assert summary["recommendation"] == "request_revision"
    assert summary["task_13_authorized"] is False
    assert all(value is not None for value in summary["candidate_hashes"].values())

    decision = _load_owner_decision_frontmatter()
    assert decision == {
        "schema_version": "1.0.0",
        "gate": "gate-b",
        "owner_decision": "pending",
        "recommendation": "request_revision",
        "task_13_authorized": False,
        "record_run_authorized": False,
        "corpus_manifest_sha256": summary["corpus"]["manifest_sha256"],
    }


def test_candidate_hashes_are_recomputed_but_not_accepted() -> None:
    """Break caught: Gate B points at drifting policy, profile, or schema bytes."""
    summary = _load_summary()

    def schema_hash(model: type) -> str:
        payload = json.dumps(
            model.model_json_schema(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    assert summary["candidate_hashes"] == {
        "decision_contract_policy_sha256": _sha256(
            REPO_ROOT / "config/product_search/decision_contract.v2.yaml"
        ),
        "decision_result_schema_sha256": schema_hash(DecisionResultV2),
        "decision_v2_code_sha256": _sha256(
            REPO_ROOT / "job_intel/product_search/decision_v2.py"
        ),
        "career_profile_sha256": _sha256(
            REPO_ROOT / "config/product_search/career_profile.v2.yaml"
        ),
        "semantic_contract_sha256": _sha256(
            REPO_ROOT
            / "job_intel/vacancy_understanding/semantic/semantic-fact-contract.yaml"
        ),
        "evidence_synthesis_policy_sha256": _sha256(
            REPO_ROOT / "config/product_search/evidence_synthesis.v1.yaml"
        ),
        "provider_output_schema_sha256": schema_hash(ProviderEvidencePayloadV1),
    }
    assert summary["owner_decision"] == "pending"
