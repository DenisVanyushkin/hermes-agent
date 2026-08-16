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
    RecordedEvidenceSynthesisProvider,
    load_evidence_synthesis_policy,
)
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    LLMObservationProvider,
    RecordingStore,
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_summary() -> dict:
    return json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))


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
    connection.close()

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
        "SELECT COUNT(DISTINCT run_id) FROM probe_evidence WHERE run_id = ?",
        (GATE_A_RUN_ID,),
    ).fetchone() == (1,)
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


def test_gate_b_fails_closed_when_task10_has_no_governed_record_adapter(
    tmp_path: Path,
) -> None:
    """Break caught: Task 10 silently accepts live mode or an arbitrary client."""
    policy = load_evidence_synthesis_policy()
    live_semantic_provider = LLMObservationProvider(
        store=RecordingStore(tmp_path),
        mode="record",
        model_id=policy.model_id,
        transport=object(),
        prompt_version=policy.semantic_prompt_version,
    )
    with pytest.raises(ValueError, match="only offline Semantic replay"):
        RecordedEvidenceSynthesisProvider(
            semantic_provider=live_semantic_provider,
            policy=policy,
        )


def test_blocked_gate_b_package_preserves_denominators_and_no_fake_results() -> None:
    """Break caught: a blocked readiness check is presented as a real benchmark."""
    summary = _load_summary()
    assert summary["schema_version"] == "1.0.0"
    assert summary["gate"] == "gate-b"
    assert summary["status"] == "blocked"
    assert summary["blocker"]["code"] == "governed_task10_record_mode_missing"
    assert summary["gate_a_input"] == {
        "commit": "65d60daae16093a9a7e34a11a159e2f789dd14dd",
        "manifest_sha256": GATE_A_MANIFEST_SHA256,
        "run_id": GATE_A_RUN_ID,
        "raw_observed": 2414,
        "corrected_canonical_current": 1814,
        "minimum_evidence_sufficient": 1314,
        "minimum_evidence_is_not_qualified": True,
    }
    assert summary["corpus"]["status"] == "not_selected"
    assert summary["corpus"]["selection_denominator"] == 1314
    assert summary["corpus"]["selected_count"] == 0
    assert summary["stage_4"] == {
        "status": "not_run",
        "input_denominator": 0,
        "hard_gate_eligible": None,
        "fail_closed": None,
    }
    assert set(summary["dimensions"]) == {dimension.value for dimension in EvidenceDimension}
    for result in summary["dimensions"].values():
        assert result == {
            "status": "not_run",
            "evaluated": 0,
            "positive": 0,
            "mixed": 0,
            "negative": 0,
            "unknown": 0,
        }
    assert summary["verdicts"] == {
        "Priority": 0,
        "Investigate": 0,
        "Save": 0,
        "Reject": 0,
    }
    assert summary["unresolved_questions"] == {"vacancies": 0, "questions": 0}
    assert summary["delivery"] == {"daily_eligible": 0, "urgent_eligible": 0}


def test_blocked_gate_b_accounts_for_provider_replay_audit_cost_and_side_effects() -> None:
    """Break caught: absent calls or audits are hidden, or a side effect is tolerated."""
    summary = _load_summary()
    assert summary["provider"] == {
        "record_mode": "blocked_before_call",
        "calls_attempted": 0,
        "calls_succeeded": 0,
        "calls_failed": 0,
        "failures_by_reason": {},
        "cost_usd": "0.000000",
        "latency_ms": {"total": 0, "p50": None, "p95": None, "max": None},
    }
    assert summary["offline_replay"] == {
        "status": "not_run",
        "recordings": 0,
        "byte_stable_matches": 0,
        "mismatches": 0,
        "network_enabled": False,
    }
    assert summary["decision_trace"] == {
        "status": "not_run",
        "traces": 0,
        "replays": 0,
        "exact_matches": 0,
        "mismatches": 0,
        "invariant_violations": 0,
    }
    assert summary["human_audit"] == {
        "status": "not_run",
        "high_risk_invariants_reviewed": 0,
        "random_sample_reviewed": 0,
        "factual_errors": 0,
        "policy_errors": 0,
        "interpretation_disagreements": 0,
    }
    assert summary["legacy_counterfactual"] == {
        "status": "not_run",
        "authority": False,
        "automatic_truth": False,
    }
    assert summary["side_effects"] == {
        "production_database_writes": 0,
        "product_store_writes": 0,
        "slack_calls": 0,
        "outbox_writes": 0,
        "profile_writes": 0,
        "cache_writes": 0,
        "protected_source_writes": 0,
    }


def test_owner_decision_stays_pending_and_task13_remains_blocked() -> None:
    """Break caught: recommendation or passing tests are misrecorded as approval."""
    summary = _load_summary()
    assert summary["owner_decision"] == "pending"
    assert summary["recommendation"] == "request_revision"
    assert summary["task_13_authorized"] is False
    assert all(value is not None for value in summary["candidate_hashes"].values())

    decision_text = OWNER_DECISION_PATH.read_text(encoding="utf-8")
    assert "Owner decision: `pending`" in decision_text
    assert "Recommendation: `request_revision`" in decision_text
    assert "Task 13 authorized: `false`" in decision_text
    assert "approve" not in decision_text.casefold().replace("not approved", "")


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
