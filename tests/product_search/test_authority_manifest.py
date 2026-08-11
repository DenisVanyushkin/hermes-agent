from __future__ import annotations

import hashlib
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs" / "authority-manifest.yaml"
DESIGN_PATH = (
    ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-08-10-job-intel-search-product-redesign-design.md"
)

MESSAGE_KINDS = {
    "daily_digest",
    "urgent_exception",
    "weekly_market_company_review",
    "monthly_strategy_review",
    "opportunity_detail",
    "review_detail",
    "user_decision_prompt",
    "user_decision_ack",
    "vacancy_evaluation",
    "application_package",
}


def load_manifest() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_product_sot_identity_and_content_hash_are_pinned() -> None:
    manifest = load_manifest()
    product_sot = manifest["authorities"]["product_search_sot"]

    assert product_sot == {
        "id": "PS-SOT-2026-08-10-v1",
        "version": "1.0.0",
        "status": "Approved",
        "path": "docs/superpowers/specs/2026-08-10-job-intel-search-product-redesign-design.md",
        "sha256": "430340de2613ee733926d73ce276c93676fe64b1841bb2f68f3f9303b61fc3a8",
        "scope": "product_search_behavior",
    }
    assert hashlib.sha256(DESIGN_PATH.read_bytes()).hexdigest() == product_sot["sha256"]


def test_protected_channel_message_kinds_have_recognized_authorities() -> None:
    manifest = load_manifest()
    mappings = manifest["protected_channel"]["message_authorities"]
    recognized = set(manifest["recognized_authority_ids"])

    assert set(mappings) == MESSAGE_KINDS
    assert set(mappings.values()) <= recognized
    assert manifest["protected_channel"]["unknown_authority"] == "deny_and_record_operational_error"


def test_authority_precedence_and_parallel_boundaries_are_explicit() -> None:
    manifest = load_manifest()

    assert manifest["precedence"][:3] == [
        "candidate_facts_for_candidate_experience",
        "official_vacancy_evidence_for_vacancy_facts",
        "product_search_sot_for_product_behavior",
    ]
    assert manifest["conflicts"] == {
        "unresolved": "fail_closed",
        "requires": "named_versioned_migration_or_owner_decision",
    }
    assert manifest["parallel_authorities"] == {
        "crm": "application_and_outreach_lifecycle",
        "reaction_triggers": "vacancy_evaluation_and_application_package_triggers",
        "feedback": "feedback_capture_without_protected_channel_root_fallback",
        "semantic_contract_v1": "vacancy_evidence_semantics_until_versioned_migration",
        "decision_contract_v1": "legacy_counterfactual_only",
    }


def test_supersession_is_bounded_and_does_not_broaden_candidate_facts() -> None:
    manifest = load_manifest()
    supersession = manifest["supersession"]

    assert supersession["product_search_sot"] == [
        "legacy_opportunity_theses",
        "legacy_scoring_policy_where_named_by_product_sot_appendix_b",
        "legacy_company_discovery_rules_where_named_by_product_sot_appendix_b",
    ]
    assert "candidate_facts" in supersession["never_supersedes"]
    assert manifest["candidate_facts"]["broadening"] == "prohibited"


def test_owner_accepted_known_red_baseline_is_recorded_without_waiver() -> None:
    impact = (ROOT / "docs" / "product-search-impact-analysis.md").read_text(encoding="utf-8")

    assert "36 failed, 1273 passed, 15 warnings" in impact
    assert "owner-accepted known-red baseline" in impact
    assert "does not waive Product Search regression failures" in impact
