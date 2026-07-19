"""Golden dataset tests: fixture validity, deterministic replay and the
required semantic contrasts (Step 2 agent task §10).

Gold values are manual annotations; the deterministic extractor is replayed
against stored bounded excerpts and compared only on deterministic fields.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from job_intel.vacancy_understanding.extractor import RawVacancy, extract
from job_intel.vacancy_understanding.model import (
    TriState,
    VacancyUnderstanding,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "vacancy_understanding"
CREATED = datetime(2026, 7, 19, tzinfo=timezone.utc)

FIXTURE_FILES = sorted(p for p in FIXTURE_DIR.glob("*.yaml"))


@pytest.fixture(scope="module")
def fixtures() -> dict[str, dict]:
    out = {}
    for p in FIXTURE_FILES:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        out[data["fixture_id"]] = data
    return out


@pytest.fixture(scope="module")
def gold(fixtures) -> dict[str, VacancyUnderstanding]:
    return {
        fid: VacancyUnderstanding.model_validate(f["vacancy_understanding"])
        for fid, f in fixtures.items()
    }


def _dig(obj, path: str):
    node = obj
    for part in path.split("."):
        node = node[part] if isinstance(node, dict) else getattr(node, part)
    return node


def test_dataset_has_required_coverage(fixtures):
    assert len(fixtures) >= 21
    synthetic = {fid for fid, f in fixtures.items() if f["is_synthetic"]}
    assert synthetic == {"synthetic_us_onsite_with_sponsorship"}, (
        "synthetic fixtures must be explicitly labeled and minimal"
    )


def test_every_fixture_gold_doc_validates(gold):
    for fid, doc in gold.items():
        assert doc.metadata.production_integration is False, fid


def test_synthetic_flag_matches_metadata(fixtures, gold):
    for fid, f in fixtures.items():
        assert gold[fid].metadata.is_synthetic_fixture == f["is_synthetic"], fid


def test_gold_inferred_fields_carry_evidence(gold):
    """Every known gold mandate fact must have at least one evidence item."""
    for fid, doc in gold.items():
        for name in type(doc.mandate).model_fields:
            fact = getattr(doc.mandate, name)
            value = getattr(fact, "value", None)
            if value is None or not hasattr(fact, "evidence"):
                continue
            raw_value = getattr(value, "value", value)
            if isinstance(raw_value, list):
                continue
            if raw_value not in ("unknown", None) and fact.method.value == "manual_gold_annotation":
                assert fact.evidence, f"{fid}.mandate.{name} lacks evidence"


def test_deterministic_replay_matches_expectations(fixtures):
    for fid, f in fixtures.items():
        result = extract(RawVacancy(**f["replay_input"]), created_at=CREATED)
        doc = result.model_dump(mode="json")
        for path, expected in f.get("deterministic_expected", {}).items():
            actual = _dig(doc, path)
            assert actual == expected, f"{fid}: {path} = {actual!r} != {expected!r}"


# ---------------------------------------------------------------------------
# Required contrasts (§10)
# ---------------------------------------------------------------------------

def test_wise_contrasts(gold):
    apac = gold["wise_apac_growth_expansion"].mandate
    assert apac.scope_breadth.value.value == "region"
    assert apac.growth_mandate.value == TriState.true
    assert apac.expansion_mandate.value == TriState.true
    assert apac.revenue_proximity.value.value in ("direct_revenue", "direct_pnl")

    pricing = gold["wise_pricing"].mandate
    acquiring = gold["wise_acquiring"].mandate
    for m in (pricing, acquiring):
        assert m.scope_breadth.value.value == "domain"
        assert m.monetization_core.value == TriState.true

    fincrime = gold["wise_financial_crime"].mandate
    onboarding = gold["wise_onboarding_experience"].mandate
    assert fincrime.scope_breadth.value.value in ("feature", "domain")
    assert onboarding.scope_breadth.value.value in ("feature", "domain")
    # no automatic broad-mandate inference from the company brand
    for m in (fincrime, onboarding):
        assert m.scope_breadth.value.value not in ("region", "business_line", "portfolio")
        assert m.growth_mandate.value != TriState.true


def test_airwallex_contrasts(gold):
    gpni = gold["airwallex_gpni"].mandate
    assert gpni.platform_as_business.value == TriState.true
    assert gpni.platform_engineering.value == TriState.false
    assert gpni.scope_breadth.value.value in ("business_line", "portfolio")

    fraud = gold["airwallex_payment_fraud"].mandate
    assert fraud.scope_breadth.value.value == "domain"
    assert fraud.risk_compliance_heavy.value == TriState.true
    # platform-as-business must NOT arrive by company association
    assert fraud.platform_as_business.value != TriState.true


def test_monzo_contrasts(gold):
    bb = gold["monzo_business_banking"].mandate
    flex = gold["monzo_flex_borrowing"].mandate
    assert bb.scope_breadth.value.value == "business_line"
    assert flex.scope_breadth.value.value in ("feature", "domain")


def test_coinbase_platform_engineering(gold):
    infra = gold["coinbase_core_infrastructure"].mandate
    assert infra.platform_engineering.value == TriState.true
    assert infra.platform_as_business.value in (TriState.false, TriState.unknown)
    company = gold["coinbase_core_infrastructure"].company
    assert company.is_crypto_exchange.value == TriState.true


def test_hybrid_function_not_forced_to_support(gold):
    sales = gold["block_strategic_product_sales"]
    assert "sales" in [f.value for f in sales.role_identity.function_families]
    # hybrid title families preserved (sales + product), no single forced label
    assert len(sales.role_identity.title_families) >= 2

    fpna = gold["canva_fpna"]
    assert [f.value for f in fpna.role_identity.function_families] == ["finance"]
    assert fpna.mandate.digital_business_ownership.value == TriState.false


def test_crypto_barrier_vs_crypto_employer(gold):
    okx = gold["okx_kyb_onboarding_mandarin_barrier"]
    assert okx.company.is_crypto_exchange.value == TriState.true
    barriers = okx.requirements.entry_barriers
    assert len(barriers) == 1
    b = barriers[0]
    assert b.transferability.value == "non_transferable_barrier"
    assert "Mandarin" in b.requirement
    assert b.evidence, "barrier classification requires evidence"
    # the barrier is the language requirement — crypto context is NOT cited
    assert "crypto" not in b.why.lower()


def test_internal_tools_backoffice(gold):
    okx = gold["okx_internal_hr_finance"]
    assert okx.mandate.internal_tools_backoffice.value == TriState.true
    assert okx.feasibility_facts.must_be_already_authorized.value == TriState.true
    assert okx.feasibility_facts.sponsorship_stated.value.value == "no"


def test_geography_contrasts(gold):
    remote_us = gold["affirm_senior_director_pm_remote_us"].feasibility_facts
    assert remote_us.work_format.value.value == "remote"
    assert remote_us.country_group.value.value == "usa"
    assert remote_us.sponsorship_stated.value.value == "unknown"

    us_onsite = gold["brex_growth_ai_sf_no_sponsorship"].feasibility_facts
    assert us_onsite.country_group.value.value == "usa"
    assert us_onsite.work_format.value.value in ("onsite", "hybrid")
    assert us_onsite.sponsorship_stated.value.value in ("unknown", "no")

    us_sponsored = gold["synthetic_us_onsite_with_sponsorship"].feasibility_facts
    assert us_sponsored.sponsorship_stated.value.value == "yes"
    assert us_sponsored.relocation_support.value.value == "explicit"

    kz = gold["kz_local_zeekr_almaty"].feasibility_facts
    assert kz.country_group.value.value == "kazakhstan"
    assert kz.local_market_indicator.value == TriState.true
    assert kz.sponsorship_stated.value.value == "unknown"  # valid combination
    assert not any(
        r.kind.value == "internal_contradiction"
        for r in gold["kz_local_zeekr_almaty"].risks
    )


def test_no_verdicts_anywhere(gold):
    """Step 2 output contains no scores, bands or recommendations."""
    forbidden = ("recommendation", "score_band", "is_good_for", "desirability")
    for fid, doc in gold.items():
        dumped = doc.model_dump_json().lower()
        for word in forbidden:
            assert word not in dumped, f"{fid} contains {word!r}"
