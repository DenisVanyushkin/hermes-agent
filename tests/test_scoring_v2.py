import os

import pytest

from job_intel.evaluator import score_vacancy_with_version, score_vacancy
from job_intel.models import Vacancy


def V(**kw):
    base = dict(
        source="test",
        source_id="1",
        company="TestCo",
        title="",
        location="Remote",
        url="https://example.com/1",
        description="",
    )
    base.update(kw)
    return Vacancy(**base)


def test_v2_rejects_sales_executive_titles():
    v = V(
        title="Sales Executive",
        location="Remote",
        description="Fintech sales role. Drive pipeline and business development.",
    )
    e = score_vacancy_with_version(v, "v2")
    assert e.recommendation == "reject"


def test_v2_head_of_product_fintech_remote_is_review_positive():
    v = V(
        title="Head of Product - Fintech",
        location="Remote",
        description="Remote fintech product leadership. Own product strategy and roadmap.",
    )
    e = score_vacancy_with_version(v, "v2")
    assert e.score >= 40
    assert e.recommendation in {"near_miss", "potential_fit", "strong_fit"}


def test_v2_head_of_product_ai_remote_is_review_positive():
    v = V(
        title="Head of Product (AI Product) - Full remote",
        location="Remote",
        description="AI product leadership. Build AI products, own product strategy.",
    )
    e = score_vacancy_with_version(v, "v2")
    assert e.score >= 40
    assert e.recommendation in {"near_miss", "potential_fit", "strong_fit"}


def test_v2_growth_product_lead_requires_product_growth_ownership():
    v = V(
        title="Growth Product Lead",
        location="Remote",
        description="Growth product role: activation, retention, lifecycle, monetization. Own growth product roadmap.",
    )
    e = score_vacancy_with_version(v, "v2")
    assert e.score >= 40

    v_bad = V(
        title="Senior Growth Manager (Performance & Analytics)",
        location="Remote",
        description="Performance marketing and analytics. Drive growth via paid channels.",
        url="https://example.com/2",
    )
    e_bad = score_vacancy_with_version(v_bad, "v2")
    assert e_bad.recommendation == "reject"


def test_v2_product_lead_without_growth_or_ownership_stays_near_miss_or_reject():
    v = V(
        title="Product Lead (Афиша)",
        location="Almaty",
        description="Lead the product. Collaborate with stakeholders.",
    )
    e = score_vacancy_with_version(v, "v2")
    assert e.score < 40
    assert e.recommendation == "reject"


def test_env_dispatch_scoring_model_version():
    v = V(
        title="Head of Product - Fintech",
        location="Remote",
        description="Remote fintech product leadership. Own product strategy and roadmap.",
    )

    old = os.environ.get("SCORING_MODEL_VERSION")
    try:
        os.environ["SCORING_MODEL_VERSION"] = "v2"
        e = score_vacancy(v)
        assert "head_of_product_title_bonus" in e.raw_breakdown

        os.environ["SCORING_MODEL_VERSION"] = "v1"
        e1 = score_vacancy(v)
        assert "head_of_product_title_bonus" not in e1.raw_breakdown
    finally:
        if old is None:
            os.environ.pop("SCORING_MODEL_VERSION", None)
        else:
            os.environ["SCORING_MODEL_VERSION"] = old
