from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from job_intel.product_search.gate_b_benchmark_policy_v3 import (
    load_gate_b_benchmark_policy_v3,
)
from job_intel.product_search.gate_b_evidence_v3 import (
    ReviewedFragmentDecisionV3,
    classify_reviewed_fragment_v3,
    generate_reviewed_fragment_allowlist_v3,
)
from job_intel.product_search.gate_b_evidence_runner_v1 import (
    load_gate_b_corpus_rows_from_corpus_manifest,
)


def test_classifier_uses_policy_and_section_rules() -> None:
    policy = load_gate_b_benchmark_policy_v3()
    assert classify_reviewed_fragment_v3(
        section="what_you_will_do",
        text="Lead the product roadmap.",
        policy=policy,
    ) == (
        ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY,
        "role_responsibility_section_v1",
    )
    assert classify_reviewed_fragment_v3(
        section="requirements",
        text="Customers rely on this platform.",
        policy=policy,
    ) == (
        ReviewedFragmentDecisionV3.EXCLUDE_COMPANY_FACT,
        "company_fact_deny_pattern_v1",
    )


def test_allowlist_generation_is_deterministic_and_names_each_rule() -> None:
    policy = load_gate_b_benchmark_policy_v3()
    rows = (
        (
            {"selection_key": "a" * 64},
            {"description": "<h2>What you will do</h2>Lead the product roadmap."},
        ),
    )
    first = generate_reviewed_fragment_allowlist_v3(
        rows,
        corpus_sha256="b" * 64,
        gate_a_run_id="gate-a-20260816T141344Z",
        classified_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        policy=policy,
    )
    second = generate_reviewed_fragment_allowlist_v3(
        rows,
        corpus_sha256="b" * 64,
        gate_a_run_id="gate-a-20260816T141344Z",
        classified_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        policy=policy,
    )
    assert first == second
    assert first.gate_b_corpus_sha256 == "b" * 64
    assert first.entries[0].classifier_id == "automated_fragment_classifier_v1"
    assert first.entries[0].classified_at == datetime(2026, 8, 23, tzinfo=timezone.utc)
    assert first.entries[0].classification_rule == "role_responsibility_section_v1"


def test_corpus_manifest_loader_rejects_a_stale_external_hash(tmp_path) -> None:
    manifest_path = tmp_path / "corpus-manifest.json"
    manifest_path.write_text(
        json.dumps({"gate": "gate-b", "records": []}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="corpus_manifest_sha256_mismatch"):
        load_gate_b_corpus_rows_from_corpus_manifest(
            gate_a_root=tmp_path,
            corpus_manifest_path=manifest_path,
            expected_corpus_sha256="0" * 64,
        )
