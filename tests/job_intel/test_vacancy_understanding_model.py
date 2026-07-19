"""Contract tests for the Vacancy Understanding Layer (Step 2).

Schema/contract invariants + semantic invariants from the Step 2 agent task
§11. No scoring, no recommendations, no production integration.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from job_intel.vacancy_understanding.country_groups import (
    RESOLVER_VERSION,
    resolve_country_group,
)
from job_intel.vacancy_understanding.extractor import (
    EXTRACTOR_VERSION,
    RawVacancy,
    extract,
)
from job_intel.vacancy_understanding.model import (
    SCHEMA_PATH,
    SCHEMA_VERSION,
    BoolFact,
    Confidence,
    CountryGroup,
    Fact,
    ManagementLevel,
    Mandate,
    ScopeBreadth,
    SponsorshipStated,
    TriState,
    VacancyUnderstanding,
    WorkFormat,
    export_json_schema,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CREATED = datetime(2026, 7, 19, tzinfo=timezone.utc)


def _raw(**over) -> RawVacancy:
    base = dict(
        vacancy_key="test:1", source_system="test", company="TestCo",
        title="Product Director", location="London", description="",
    )
    base.update(over)
    return RawVacancy(**base)


# ---------------------------------------------------------------------------
# Schema / contract
# ---------------------------------------------------------------------------

def test_schema_artifact_in_sync():
    on_disk = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert on_disk == export_json_schema(), (
        "vacancy-understanding.schema.json is stale; regenerate via "
        "python -m job_intel.vacancy_understanding.model"
    )


def test_unknown_fields_rejected():
    doc = extract(_raw(), created_at=CREATED).model_dump()
    doc["surprise"] = 1
    with pytest.raises(ValidationError):
        VacancyUnderstanding.model_validate(doc)


def test_closed_enums_reject_typos():
    with pytest.raises(ValidationError):
        Fact[ScopeBreadth](value="regoin", confidence="high",
                           method="manual_gold_annotation")
    with pytest.raises(ValidationError):
        BoolFact(value="ture")


def test_versions_are_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", SCHEMA_VERSION)
    assert re.fullmatch(r"\d+\.\d+\.\d+", EXTRACTOR_VERSION)
    u = extract(_raw(), created_at=CREATED)
    assert u.metadata.schema_version == SCHEMA_VERSION
    assert u.metadata.extractor_version == EXTRACTOR_VERSION


def test_evidence_references_resolve():
    doc = extract(_raw(description="We can sponsor visas for this role " * 10),
                  created_at=CREATED).model_dump()
    # break a reference
    doc["feasibility_facts"]["sponsorship_stated"]["evidence"][0]["source_id"] = "nope"
    with pytest.raises(ValidationError, match="unknown source_id"):
        VacancyUnderstanding.model_validate(doc)


def test_tristate_never_collapses_missing_to_false():
    m = Mandate()  # nothing extracted
    for name in type(m).model_fields:
        value = getattr(m, name)
        if isinstance(value, Fact) and isinstance(value.value, TriState):
            assert value.value == TriState.unknown, name
    u = extract(_raw(description=""), created_at=CREATED)
    assert u.mandate.growth_mandate.value == TriState.unknown
    assert u.feasibility_facts.sponsorship_stated.value == SponsorshipStated.unknown


def test_production_integration_stays_false():
    u = extract(_raw(), created_at=CREATED)
    assert u.metadata.production_integration is False
    doc = u.model_dump()
    doc["metadata"]["production_integration"] = True
    with pytest.raises(ValidationError, match="production_integration"):
        VacancyUnderstanding.model_validate(doc)


def test_no_production_import():
    """No production job_intel module imports vacancy_understanding, and the
    package itself never imports the preference model."""
    pkg = REPO_ROOT / "job_intel"
    offenders = []
    for py in pkg.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        if "vacancy_understanding" in py.parts:
            if re.search(r"^\s*(from|import)\s+job_intel\.preference_model", text, re.M):
                offenders.append(f"{py} imports preference_model")
            continue
        if "preference_model" in py.parts:
            continue
        if re.search(r"^\s*(from|import)\s+job_intel\.vacancy_understanding", text, re.M):
            offenders.append(str(py))
    assert not offenders, offenders


def test_unknown_value_cannot_pretend_extracted():
    with pytest.raises(ValidationError):
        BoolFact(value=TriState.unknown, method="explicit_statement",
                 confidence="high")
    with pytest.raises(ValidationError):
        Fact[ScopeBreadth](value=ScopeBreadth.region, method="semantic_inference",
                           confidence="high")  # semantic inference needs evidence


# ---------------------------------------------------------------------------
# Country-group resolver
# ---------------------------------------------------------------------------

def test_country_group_resolver_is_versioned_and_explainable():
    r = resolve_country_group("United States")
    assert r.group == CountryGroup.usa
    assert r.resolver_version == RESOLVER_VERSION
    assert r.source == "curated_snapshot"
    assert resolve_country_group("Kazakhstan").group == CountryGroup.kazakhstan
    assert resolve_country_group("Russia").group == CountryGroup.sanctioned
    assert resolve_country_group("Nigeria").group == CountryGroup.africa
    assert resolve_country_group(None).group == CountryGroup.unknown


def test_resolver_never_guesses_sanctioned_from_unknown_country():
    # An unlisted country resolves to other — no free-text intuition.
    assert resolve_country_group("Freedonia").group == CountryGroup.other


# ---------------------------------------------------------------------------
# Semantic invariants (§11)
# ---------------------------------------------------------------------------

def test_title_alone_cannot_set_executive_scope_high_confidence():
    u = extract(_raw(title="Chief Product Officer"), created_at=CREATED)
    lvl = u.role_identity.management_level_observed
    assert lvl.value == ManagementLevel.c_level
    assert lvl.confidence != Confidence.high  # capped: title is evidence only
    # scope_breadth is semantic: the deterministic extractor must leave it unknown
    assert u.mandate.scope_breadth.value == ScopeBreadth.unknown


def test_platform_word_alone_sets_nothing():
    u = extract(_raw(description="Our platform infrastructure team ships weekly. " * 10),
                created_at=CREATED)
    assert u.mandate.platform_as_business.value == TriState.unknown
    assert u.mandate.platform_engineering.value == TriState.unknown


def test_platform_shapes_cannot_both_be_true():
    with pytest.raises(ValidationError, match="cannot both"):
        Mandate(
            platform_as_business=BoolFact(value="true", confidence="high",
                                          method="manual_gold_annotation"),
            platform_engineering=BoolFact(value="true", confidence="high",
                                          method="manual_gold_annotation"),
        )


def test_crypto_company_and_barrier_are_separate_facts():
    u = extract(_raw(company="okx", description="We are a leading crypto exchange. " * 10),
                created_at=CREATED)
    # company fact untouched by deterministic extraction (enrichment concern)
    assert u.company.is_crypto_exchange.value == TriState.unknown
    # and no entry barrier appears from crypto context alone
    assert u.requirements.entry_barriers == []


def test_remote_and_onsite_facts_stay_separate():
    u = extract(_raw(location="Remote US"), created_at=CREATED)
    assert u.feasibility_facts.work_format.value == WorkFormat.remote
    assert u.feasibility_facts.country_group.value == CountryGroup.usa
    # sponsorship unknown is a valid fact, not an error
    assert u.feasibility_facts.sponsorship_stated.value == SponsorshipStated.unknown


def test_kz_local_sponsorship_unknown_is_valid():
    u = extract(_raw(location="Almaty, Kazakhstan (On-site)"), created_at=CREATED)
    f = u.feasibility_facts
    assert f.country_group.value == CountryGroup.kazakhstan
    assert f.work_format.value == WorkFormat.onsite
    assert f.local_market_indicator.value == TriState.true
    assert f.sponsorship_stated.value == SponsorshipStated.unknown
    assert not any(r.kind.value == "internal_contradiction" for r in u.risks)


def test_missing_compensation_has_no_effect():
    u = extract(_raw(description="Great role. " * 30), created_at=CREATED)
    dumped = u.model_dump_json()
    assert "compensation" not in dumped and "salary" not in dumped


def test_extractor_is_deterministic_replayable():
    raw = _raw(description="We can sponsor visas. Hybrid working model. " * 5)
    a = extract(raw, created_at=CREATED).model_dump_json()
    b = extract(raw, created_at=CREATED).model_dump_json()
    assert a == b
