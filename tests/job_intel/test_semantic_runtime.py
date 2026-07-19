"""Semantic extractor runtime tests (Step 4B).

Covers pipeline stages, every conflict rule, determinism, provider isolation
and the full synthetic-control suite. No production integration.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from job_intel.vacancy_understanding.extractor import RawVacancy, extract as det_extract
from job_intel.vacancy_understanding.model import VacancyUnderstanding
from job_intel.vacancy_understanding.semantic.runtime.calibration import (
    run_synthetic_controls,
)
from job_intel.vacancy_understanding.semantic.runtime.models import (
    Observation,
    ObservationBasis,
)
from job_intel.vacancy_understanding.semantic.runtime.pipeline import extract_semantic
from job_intel.vacancy_understanding.semantic.runtime.provider import (
    DeterministicPhraseProvider,
    LLMProvider,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CREATED = datetime(2026, 7, 19, tzinfo=timezone.utc)
PROVIDER = DeterministicPhraseProvider()


def _vu(title="Product Director", text="") -> VacancyUnderstanding:
    return det_extract(RawVacancy(
        vacancy_key="t:1", source_system="test", company="T", title=title,
        location="Remote", description=text), created_at=CREATED)


def _run(title, text, provider=PROVIDER):
    return extract_semantic(_vu(title, text), title=title, text=text, provider=provider)


class _FakeProvider:
    provider_id = "fake"
    prompt_version = "0"

    def __init__(self, obs):
        self._obs = obs

    def extract_semantic_observations(self, *, title, text, structured):
        return self._obs


def _obs(i, excerpt, signal, basis=ObservationBasis.direct, location="description",
         maps=None):
    leaf = signal.split("=")[0]
    fid = leaf if leaf.startswith(("company.", "requirements.", "organization.")) else f"mandate.{leaf}"
    return Observation(observation_id=f"o{i}", excerpt=excerpt, location=location,
                       signal_type=signal, interpretation="t", maps_to=maps or [fid],
                       basis=basis)


# ---------------------------------------------------------------------------
# Stages 1-4: validation
# ---------------------------------------------------------------------------

def test_stage1_rejects_malformed_input():
    with pytest.raises(ValueError, match="title"):
        extract_semantic(_vu(), title="", text="x", provider=PROVIDER)


def test_stage3_rejects_non_verbatim_excerpt():
    out = _run("T", "some body text",
               provider=_FakeProvider([_obs(1, "NOT IN TEXT", "growth_mandate=true")]))
    assert out.rejected_observations[0].reason == "excerpt_not_verbatim"
    assert out.diagnostics.facts_emitted == 0


def test_stage3_rejects_enrichment_only_fact():
    out = _run("T", "tier1 brand here",
               provider=_FakeProvider([_obs(1, "tier1 brand here",
                                            "company.brand_recognition=tier1_scaleup")]))
    assert out.rejected_observations[0].reason.startswith("enrichment_only")


def test_stage3_rejects_unknown_fact_and_bad_value():
    out = _run("T", "abc def",
               provider=_FakeProvider([
                   _obs(1, "abc", "nonexistent_fact=true"),
                   _obs(2, "def", "scope_breadth=galactic")]))
    reasons = {r.reason for r in out.rejected_observations}
    assert reasons == {"unknown_fact_reference", "invalid_value_for_fact"}


def test_stage4_merges_duplicates():
    out = _run("T", "own the P&L now",
               provider=_FakeProvider([
                   _obs(1, "own the P&L", "pnl_ownership=true", ObservationBasis.explicit),
                   _obs(2, "own the P&L", "pnl_ownership=true", ObservationBasis.explicit)]))
    assert len(out.observations) == 1
    assert out.fragment["mandate"]["pnl_ownership"]["value"] == "true"


# ---------------------------------------------------------------------------
# Stage 6: every conflict rule individually
# ---------------------------------------------------------------------------

def test_cf_contradictory_observations_equal_level():
    out = _run("T", "grow it maintain it",
               provider=_FakeProvider([
                   _obs(1, "grow it", "growth_mandate=true"),
                   _obs(2, "maintain it", "growth_mandate=false")]))
    assert any(c.rule_id == "cf_contradictory_observations" for c in out.conflicts)
    assert out.fragment["mandate"]["growth_mandate"]["value"] == "unknown"
    assert any(r["kind"] == "internal_contradiction" for r in out.fragment["risks"])


def test_cf_evidence_level_conflict_stronger_wins():
    out = _run("T", "we explicitly own the P&L maybe not",
               provider=_FakeProvider([
                   _obs(1, "we explicitly own the P&L", "pnl_ownership=true",
                        ObservationBasis.explicit),
                   _obs(2, "maybe not", "pnl_ownership=false", ObservationBasis.weak)]))
    assert any(c.rule_id == "cf_evidence_level_conflict" for c in out.conflicts)
    assert out.fragment["mandate"]["pnl_ownership"]["value"] == "true"


def test_cf_deterministic_vs_semantic_never_overwrites():
    text = "We can sponsor visas. " * 3 + "full P&L ownership of the line"
    vu = _vu("T", text)
    # deterministic extractor already set pnl true (explicit wording); a fake
    # semantic false must lose
    out = extract_semantic(vu, title="T", text=text, provider=_FakeProvider([
        _obs(1, "full P&L ownership", "pnl_ownership=false")]))
    assert any(c.rule_id == "cf_deterministic_vs_semantic" for c in out.conflicts)
    assert out.fragment["mandate"]["pnl_ownership"]["value"] == "true"


def test_cf_impossible_combination_equal_levels_both_unknown():
    out = _run("T", "customer rails internal platform",
               provider=_FakeProvider([
                   _obs(1, "customer rails", "platform_as_business=true"),
                   _obs(2, "internal platform", "platform_engineering=true")]))
    assert any(c.rule_id == "cf_impossible_combination" for c in out.conflicts)
    m = out.fragment["mandate"]
    assert m["platform_as_business"]["value"] == "unknown"
    assert m["platform_engineering"]["value"] == "unknown"


def test_cf_impossible_combination_higher_evidence_wins():
    out = _run("T", "customer rails internal platform",
               provider=_FakeProvider([
                   _obs(1, "customer rails", "platform_as_business=true",
                        ObservationBasis.explicit),
                   _obs(2, "internal platform", "platform_engineering=true",
                        ObservationBasis.weak)]))
    m = out.fragment["mandate"]
    assert m["platform_as_business"]["value"] == "true"
    assert m["platform_engineering"]["value"] == "unknown"


# ---------------------------------------------------------------------------
# Stages 7-10
# ---------------------------------------------------------------------------

def test_confidence_from_evidence_quality_only():
    out = _run("T", "own the P&L … own user acquisition",
               provider=_FakeProvider([
                   _obs(1, "own the P&L", "pnl_ownership=true", ObservationBasis.explicit),
                   _obs(2, "own user acquisition", "growth_mandate=true",
                        ObservationBasis.direct)]))
    assert out.fragment["mandate"]["pnl_ownership"]["confidence"] == "high"
    assert out.fragment["mandate"]["growth_mandate"]["confidence"] == "medium"


def test_no_numeric_confidence_anywhere():
    pkg = REPO_ROOT / "job_intel" / "vacancy_understanding" / "semantic" / "runtime"
    for py in pkg.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert "logprob" not in src.lower(), py.name
        assert not re.search(r"confidence\s*[<>=]+\s*0\.\d", src), py.name


def test_unknowns_and_clarifications_reference_contract():
    out = _run("Product Director", "nothing semantic here at all")
    assert out.diagnostics.facts_emitted == 0
    facts = {c.fact_id for c in out.clarifications}
    assert "mandate.scope_breadth" in facts
    for c in out.clarifications:
        assert c.priority in ("blocking", "recommendation_changing")


def test_provenance_fields_complete():
    # use a fact the deterministic extractor never sets (strategy ownership)
    out = _run("T", "you define the strategy here",
               provider=_FakeProvider([_obs(1, "you define the strategy",
                                            "strategy_ownership=true",
                                            ObservationBasis.explicit)]))
    p = out.provenance["mandate.strategy_ownership"]
    assert p.origin == "semantic_inference" and p.provider == "fake"
    assert p.observation_ids == ["o1"] and p.confidence == "high"
    assert "o1" in p.reasoning_summary  # cites observations, no chain-of-thought


def test_stage9_output_validates_as_step2_fragment():
    out = _run("Product Lead - Pricing", "own our pricing engine and experimentation")
    VacancyUnderstanding.model_validate(out.fragment)  # must not raise


def test_stage10_byte_identical_repeated_runs():
    title, text = "Head of Product APAC", "Own APAC growth and expansion across all markets. full P&L ownership"
    a = json.dumps(_run(title, text).semantic_dump(), sort_keys=True)
    b = json.dumps(_run(title, text).semantic_dump(), sort_keys=True)
    assert a == b


# ---------------------------------------------------------------------------
# Provider isolation / policy drift guards
# ---------------------------------------------------------------------------

def test_provider_isolation_no_decision_or_preference_imports():
    pkg = REPO_ROOT / "job_intel" / "vacancy_understanding" / "semantic" / "runtime"
    for py in pkg.glob("*.py"):
        if py.name == "replay_flagships.py":
            continue  # the replay harness legitimately drives the evaluator read-only
        src = py.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import)\s+job_intel\.(shadow_evaluator|preference_model)",
                             src, re.M), py.name


def test_llm_provider_is_gated():
    with pytest.raises(NotImplementedError, match="approval"):
        LLMProvider().extract_semantic_observations(title="t", text="x", structured={})


def test_no_hidden_desirability_policy():
    pkg = REPO_ROOT / "job_intel" / "vacancy_understanding" / "semantic" / "runtime"
    for py in pkg.glob("*.py"):
        src = py.read_text(encoding="utf-8").lower()
        for word in ("recommendation =", "desirability", "apply\"", "not_recommended"):
            assert word not in src, f"{py.name}: {word}"


# ---------------------------------------------------------------------------
# Synthetic controls (all of them)
# ---------------------------------------------------------------------------

def test_all_synthetic_controls_pass():
    res = run_synthetic_controls()
    assert res["fail"] == 0, res["failures"]
    assert res["pass"] >= 120
    # every exemption is explicit and justified
    for row in res["results"]:
        if row["status"].startswith("exempt"):
            assert row.get("note") or row["fact"] in res["exemptions"]
