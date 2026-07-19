"""Closure Part 1: specialized coverage for controls the generic single-text
runner is structurally incapable of expressing, plus the uncovered=0 gate."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from job_intel.vacancy_understanding.extractor import RawVacancy, extract as det_extract
from job_intel.vacancy_understanding.semantic.runtime.calibration import (
    build_control_coverage,
)
from job_intel.vacancy_understanding.semantic.runtime.models import (
    Observation,
    ObservationBasis,
)
from job_intel.vacancy_understanding.semantic.runtime.pipeline import extract_semantic

CREATED = datetime(2026, 7, 19, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[2]


class _Pair:
    provider_id = "specialized-pair"
    prompt_version = "0"

    def __init__(self, obs):
        self._obs = obs

    def extract_semantic_observations(self, *, title, text, structured):
        return self._obs


def _vu(title, text):
    return det_extract(RawVacancy(
        vacancy_key="cov:1", source_system="test", company="T", title=title,
        location="Remote", description=text), created_at=CREATED)


def _obs(i, excerpt, signal, basis, location="description"):
    leaf = signal.split("=")[0]
    fid = leaf if leaf.startswith(("company.", "organization.", "requirements.")) else f"mandate.{leaf}"
    return Observation(observation_id=f"c{i}", excerpt=excerpt, location=location,
                       signal_type=signal, interpretation="specialized control",
                       maps_to=[fid], basis=basis)


# The 7 conflicting controls that describe PAIRED observations in abstract
# wording ("acquiring title + devex duties"): expressed here as the exact
# observation pairs the control means. Expected: never the positive value.
_PAIR_CASES = [
    ("mandate.growth_mandate", "growth_mandate", "Growth"),
    ("mandate.expansion_mandate", "expansion_mandate", "Expansion Lead"),
    ("mandate.monetization_core", "monetization_core", "Head of Pricing"),
    ("mandate.pricing_core", "pricing_core", "Pricing Lead"),
    ("mandate.acquiring_core", "acquiring_core", "Acquiring Lead"),
    ("mandate.platform_engineering", "platform_engineering", "Platform PM"),
    ("company.platform_ecosystem", "company.platform_ecosystem", "Platform Co"),
]


@pytest.mark.parametrize("fact_id,leaf,title", _PAIR_CASES)
def test_conflicting_pair_controls(fact_id, leaf, title):
    title_word = title.split()[0]
    provider = _Pair([
        _obs(1, title_word, f"{leaf}=true", ObservationBasis.weak, location="title"),
        _obs(2, "contradicting body duties", f"{leaf}=false", ObservationBasis.direct),
    ])
    out = extract_semantic(_vu(title, "contradicting body duties"),
                           title=title, text="contradicting body duties",
                           provider=provider)
    sect, l = fact_id.split(".", 1)
    value = out.fragment[sect][l]["value"]
    assert value != "true", f"{fact_id}: conflicting control must never resolve positive"
    # level conflict (weak title vs direct body) or contradiction is traced
    assert out.conflicts, fact_id


def test_title_scope_mismatch_controls():
    # positive: title promises scope, body contradicts -> risk emitted
    provider = _Pair([
        _obs(1, "Director", "scope_breadth=region", ObservationBasis.weak, "title"),
        _obs(2, "purely execution duties", "scope_breadth=feature", ObservationBasis.weak),
    ])
    out = extract_semantic(_vu("Director", "purely execution duties"),
                           title="Director", text="purely execution duties",
                           provider=provider)
    assert any(r["kind"] == "title_scope_mismatch" for r in out.fragment["risks"])
    # negative: title and body agree -> no risk
    provider = _Pair([
        _obs(1, "Director", "scope_breadth=region", ObservationBasis.weak, "title"),
        _obs(2, "own the region", "scope_breadth=region", ObservationBasis.direct),
    ])
    out2 = extract_semantic(_vu("Director", "own the region"),
                            title="Director", text="own the region", provider=provider)
    assert not any(r["kind"] == "title_scope_mismatch" for r in out2.fragment["risks"])
    # unknown: title-only snapshot -> no mismatch risk (incomplete-text covers it)
    out3 = extract_semantic(_vu("Director", ""), title="Director", text="",
                            provider=_Pair([]))
    assert not any(r["kind"] == "title_scope_mismatch" for r in out3.fragment["risks"])


def test_mandate_summary_invariants():
    """mandate_summary controls: emission is optional per contract; the
    binding invariants are (a) never fabricated without evidence, (b) never
    contains desirability wording. Current runtime is conservative: no
    summary is synthesized -> value stays unknown/none always."""
    out = extract_semantic(_vu("Head of Product", "own the P&L and the region"),
                           title="Head of Product", text="own the P&L and the region",
                           provider=_Pair([]))
    summary = out.fragment["mandate"]["mandate_summary"]["value"]
    assert summary in (None, "unknown")
    dumped = str(out.fragment).lower()
    for forbidden in ("great role", "ambitious leader", "desirability"):
        assert forbidden not in dumped


def test_uncovered_controls_are_zero():
    cov = build_control_coverage()
    assert cov["uncovered"] == 0, cov["uncovered_list"]
    assert cov["total_controls"] == cov["generic_pass"] + cov["equivalently_covered"]
    for r in cov["records"]:
        if r["status"] == "equivalently_covered":
            path, name = r["coverage_evidence"].split("::")
            assert (REPO_ROOT / path).exists(), r
            assert name in (REPO_ROOT / path).read_text(), r
