"""Validation tests for the Semantic Vacancy Understanding SoT (Step 4A).

Structural contract validation only — no runtime extraction exists.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from job_intel.vacancy_understanding.model import Mandate
from job_intel.vacancy_understanding.semantic.contract import (
    CONTRACT_PATH,
    SCHEMA_PATH,
    Confidence,
    ExtractionClass,
    SemanticFactContract,
    export_json_schema,
    load_semantic_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def contract() -> SemanticFactContract:
    return load_semantic_contract()


def test_contract_validates(contract):
    assert contract.metadata.contract_version == "1.0.0"
    assert contract.metadata.production_integration is False
    assert contract.metadata.no_silent_learning is True


def test_schema_artifact_in_sync():
    on_disk = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert on_disk == export_json_schema(), (
        "semantic-fact-contract.schema.json stale; regenerate via "
        "python -m job_intel.vacancy_understanding.semantic.contract")


def test_unknown_fields_rejected():
    data = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    data["semantic_fact_contract"]["surprise"] = 1
    with pytest.raises(ValidationError):
        SemanticFactContract.model_validate(data["semantic_fact_contract"])


def test_every_fact_has_single_canonical_definition(contract):
    ids = [f.id for f in contract.facts]
    assert len(ids) == len(set(ids))
    for fid in ids:
        assert re.fullmatch(r"(mandate|organization|company|requirements|risks)\.[a-z_]+", fid), fid


def test_covers_every_semantic_step2_mandate_field(contract):
    """Coverage invariant: every semantically-fillable Step 2 mandate field
    has exactly one definition in this contract."""
    covered = {f.id for f in contract.facts}
    deterministic_or_na = set()  # none in Mandate: all mandate facts are semantic-fillable
    for name in Mandate.model_fields:
        if name in deterministic_or_na:
            continue
        assert f"mandate.{name}" in covered, f"mandate.{name} lacks a semantic definition"


def test_semantic_and_hybrid_facts_have_all_five_controls(contract):
    for f in contract.facts:
        if f.extraction_class in (ExtractionClass.semantic_only, ExtractionClass.hybrid):
            assert f.controls is not None, f.id
            for kind in ("positive", "negative", "ambiguous", "unknown", "conflicting"):
                assert getattr(f.controls, kind).strip(), f"{f.id}: empty {kind} control"


def test_every_fact_has_evidence_and_unknown_policy(contract):
    for f in contract.facts:
        assert f.evidence_required.strip(), f.id
        assert f.unknown.unknown_required_when.strip(), f.id
        assert f.unknown.inference_forbidden_when.strip(), f.id
        assert f.deterministic_relation.strip() and f.evaluator_relation.strip(), f.id


def test_evidence_hierarchy_and_prohibited_sources(contract):
    levels = [l.value for l in contract.evidence_hierarchy.levels]
    assert levels == ["explicit_text", "structured_source_field",
                      "deterministic_derivation", "semantic_inference", "human_gold"]
    assert contract.evidence_hierarchy.only_vacancy_evidence_produces_vacancy_facts
    joined = " ".join(contract.evidence_hierarchy.prohibited_sources).lower()
    for banned in ("reputation", "intuition", "historical assumptions", "previous evaluations"):
        assert banned in joined


def test_observation_layer_is_mandatory(contract):
    om = contract.observation_model
    assert om.facts_never_written_directly_from_text
    assert om.min_observations_per_semantic_fact >= 1
    assert om.excerpt_must_be_verbatim
    assert set(om.required_fields) >= {"observation_id", "excerpt", "location",
                                       "signal_type", "interpretation", "maps_to"}


def test_provenance_forbids_chain_of_thought(contract):
    assert contract.provenance_model.chain_of_thought_forbidden
    assert "reasoning_summary" in contract.provenance_model.required_fields


def test_confidence_is_evidence_based(contract):
    assert contract.confidence_policy.provider_confidence_is_not_a_source
    by_level = {r.level: r.allowed_when for r in contract.confidence_policy.rules}
    assert set(by_level) == set(Confidence)
    assert "explicit" in by_level[Confidence.high]
    assert "never" in by_level[Confidence.unknown] or "guess" in by_level[Confidence.unknown]


def test_conflict_policy_is_deterministic_and_complete(contract):
    ids = {c.id for c in contract.conflict_policy}
    assert {"cf_contradictory_observations", "cf_evidence_level_conflict",
            "cf_deterministic_vs_semantic", "cf_impossible_combination",
            "cf_insufficient_evidence"} <= ids
    det_rule = next(c for c in contract.conflict_policy if c.id == "cf_deterministic_vs_semantic")
    assert "deterministic wins" in det_rule.resolution.lower()
    assert "overwrite" in det_rule.resolution.lower()


def test_impossible_state_platform_shapes(contract):
    rule = next(c for c in contract.conflict_policy if c.id == "cf_impossible_combination")
    assert "platform" in rule.situation.lower()


def test_brand_recognition_is_enrichment_only(contract):
    brand = next(f for f in contract.facts if f.id == "company.brand_recognition")
    assert brand.extraction_class == ExtractionClass.enrichment_only
    assert any("semantic_inference" in p for p in brand.prohibited_evidence)
    assert "ALWAYS" in brand.unknown.inference_forbidden_when


def test_provider_independence(contract):
    assert contract.provider_contract.provider_independent
    assert contract.provider_contract.prompt_is_implementation_detail
    text = CONTRACT_PATH.read_text(encoding="utf-8").lower()
    for vendor in ("gpt-", "gpt4", "gpt5", "claude", "openai", "anthropic",
                   "gemini", "llama", "mistral"):
        assert vendor not in text, f"vendor name {vendor!r} in provider-independent contract"


def test_calibration_has_no_aggregate_score(contract):
    assert contract.calibration_contract.no_aggregate_score
    assert "per_fact_precision_vs_gold" in contract.calibration_contract.metrics
    assert "contract_gap" in contract.calibration_contract.disagreement_classes


def test_replay_missing_evidence_class_preserved(contract):
    assert contract.replay_integration.missing_semantic_evidence_class == \
        "insufficient_vacancy_evidence"
    assert set(contract.replay_integration.fact_origin_classes) == \
        {"deterministic", "semantic", "missing"}


def test_no_runtime_extraction_exists():
    pkg = REPO_ROOT / "job_intel" / "vacancy_understanding" / "semantic"
    files = sorted(p.name for p in pkg.glob("*.py"))
    assert files == ["__init__.py", "contract.py"], (
        "Step 4A must not contain extraction runtime modules")
    src = (pkg / "contract.py").read_text(encoding="utf-8")
    assert "def extract" not in src


def test_human_review_cannot_silently_change_policy(contract):
    assert contract.human_review.policy_changes_only_via_sot_amendment
