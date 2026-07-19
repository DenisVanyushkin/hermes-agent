"""Mandatory invariants for the Shadow Evaluator runtime (task §13)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from job_intel.shadow_evaluator.engine import evaluate
from job_intel.shadow_evaluator.models import EvaluationError
from job_intel.shadow_evaluator.policy import load_policy
from job_intel.shadow_evaluator.signals import derive_signals
from job_intel.vacancy_understanding.model import VacancyUnderstanding

from tests.job_intel.test_shadow_evaluator_golden import (  # reuse builders
    REGISTRY,
    SRC_GOLD,
    _fact,
    _feas,
    _synthetic,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CREATED = datetime(2026, 7, 19, tzinfo=timezone.utc)


def test_infeasible_always_rejects():
    vu = _synthetic("inv-sanctioned", feasibility=_feas(work="onsite", group="sanctioned"))
    r = evaluate(vu, evaluated_at=CREATED)
    assert r.overall.recommendation.value == "not_recommended"
    assert r.overall.action.value == "reject"


def test_company_cannot_rescue_weak_mandate():
    vu = _synthetic(
        "inv-weak-mandate",
        mandate={"scope_breadth": _fact("feature")},
        company={"scale": _fact("global"), "brand_recognition": _fact("tier1_scaleup")},
        feasibility=_feas())
    r = evaluate(vu, evaluated_at=CREATED)
    assert r.mandate_fit.band.value == "weak"
    assert r.overall.recommendation.value == "not_recommended"


def test_company_mismatch_always_rejects():
    vu = _synthetic(
        "inv-outsourcing",
        mandate={"scope_breadth": _fact("region"), "growth_mandate": _fact("true")},
        company={"is_outsourcing": _fact("true")},
        feasibility=_feas())
    r = evaluate(vu, evaluated_at=CREATED)
    assert r.company_fit.band.value == "mismatch"
    assert r.overall.recommendation.value == "not_recommended"


def test_uncertain_never_yields_strong():
    vu = _synthetic(
        "inv-uncertain",
        mandate={"scope_breadth": _fact("region"), "growth_mandate": _fact("true"),
                 "revenue_proximity": _fact("direct_revenue")},
        company={"scale": _fact("global"), "brand_recognition": _fact("tier1_scaleup")},
        feasibility=_feas(work="onsite", country="Germany"))  # sponsorship unknown
    r = evaluate(vu, evaluated_at=CREATED)
    assert r.feasibility.verdict.value == "uncertain"
    assert r.overall.recommendation.value not in ("strong", "exceptional")
    assert "cap_uncertain" in r.overall.applied_caps


def test_unknown_is_never_false():
    vu = _synthetic("inv-unknown", feasibility=_feas())
    sig = derive_signals(vu)
    # unknown facts produce NO signals (neither true nor false)
    assert "crypto_exchange_employer" not in sig.signals
    assert "growth_mandate" not in sig.signals
    assert "mandate.scope_breadth" in sig.unknown_fields


def test_remote_us_does_not_trigger_onsite_gate():
    for sponsorship in (None, "no"):
        vu = _synthetic(
            "inv-remote-us",
            mandate={"scope_breadth": _fact("portfolio")},
            feasibility=_feas(work="remote", group="usa", country="United States",
                              sponsorship=sponsorship))
        r = evaluate(vu, evaluated_at=CREATED)
        assert r.feasibility.verdict.value == "feasible", sponsorship
        assert not r.feasibility.blockers


def test_crypto_concern_affects_only_company_fit():
    vu = _synthetic(
        "inv-crypto",
        mandate={"scope_breadth": _fact("business_line"), "growth_mandate": _fact("true")},
        company={"is_crypto_exchange": _fact("true")},
        feasibility=_feas())
    r = evaluate(vu, evaluated_at=CREATED)
    crypto_items = [i for i in r.items if i.preference_rule_id == "crypto_exchange_employer"]
    assert crypto_items and all(i.section.value == "company" for i in crypto_items)
    assert r.mandate_fit.band.value == "strong"  # mandate untouched
    assert all("crypto" not in (i or "") for i in r.mandate_fit.concerns)


def test_platform_engineering_never_supports_platform_business():
    vu = _synthetic(
        "inv-platform",
        mandate={"platform_engineering": _fact("true"),
                 "scope_breadth": _fact("domain")},
        feasibility=_feas())
    r = evaluate(vu, evaluated_at=CREATED)
    supports = [i for i in r.items if i.kind.value == "support" and i.active]
    assert not any(i.preference_rule_id == "platform_as_the_business" for i in supports)


def test_internal_tools_yields_mandate_mismatch():
    vu = _synthetic(
        "inv-internal-tools",
        mandate={"internal_tools_backoffice": _fact("true"),
                 "scope_breadth": _fact("domain")},
        feasibility=_feas())
    r = evaluate(vu, evaluated_at=CREATED)
    assert r.mandate_fit.band.value == "mismatch"
    assert r.overall.recommendation.value == "not_recommended"


def test_every_blocking_unknown_has_clarification():
    vu = _synthetic("inv-blocking-unknown",
                    feasibility={"country_group": _fact("other")})  # work format unknown
    r = evaluate(vu, evaluated_at=CREATED)
    blocking = [u for u in r.unknown_ledger if u.clarification_priority == "blocking"]
    assert blocking
    facts_with_clarification = {c.required_fact for c in r.clarifications}
    for u in blocking:
        assert u.field in facts_with_clarification, u.field


def test_interaction_application_is_idempotent():
    doc_path = REPO_ROOT / "tests" / "fixtures" / "vacancy_understanding" / "wise_pricing.yaml"
    import yaml
    vu = VacancyUnderstanding.model_validate(
        yaml.safe_load(doc_path.read_text())["vacancy_understanding"])
    r = evaluate(vu, evaluated_at=CREATED)
    seen = [(t.rule_id, t.effect, tuple(t.target_ids)) for t in r.interaction_trace]
    assert len(seen) == len(set(seen)), "duplicate interaction applications"


def test_unsupported_major_yields_error_record():
    vu = _synthetic("inv-major", feasibility=_feas())
    doc = vu.model_dump(mode="json")
    doc["metadata"]["schema_version"] = "9.0.0"
    # bypass strict semver-major guard via direct model tweak
    vu2 = VacancyUnderstanding.model_validate(doc)
    r = evaluate(vu2, evaluated_at=CREATED)
    assert isinstance(r, EvaluationError)
    assert "unsupported" in r.error


def test_no_numeric_scoring_in_runtime():
    pkg = REPO_ROOT / "job_intel" / "shadow_evaluator"
    for py in pkg.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert not re.search(r"\bscore\s*[+*]=", src), py.name
        assert "W_" not in src or py.name == "contract.py", py.name


def test_shadow_cannot_write_db_or_send_messages():
    pkg = REPO_ROOT / "job_intel" / "shadow_evaluator"
    forbidden = re.compile(
        r"sqlite3\.connect\((?!.*mode=ro)|INSERT INTO|UPDATE \w+ SET|"
        r"slack_sdk|chat_post|send_message|telegram|requests\.post|httpx", re.I)
    for py in pkg.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        for m in forbidden.finditer(src):
            # read-only sqlite (mode=ro) is permitted for replay snapshots
            pytest.fail(f"{py.name}: forbidden side-effect pattern {m.group(0)!r}")


def test_matrix_resolver_is_central_and_total():
    policy = load_policy()
    from job_intel.shadow_evaluator.contract import FitBand
    for m in FitBand:
        for c in FitBand:
            assert policy.resolve_matrix(m, c) is not None
