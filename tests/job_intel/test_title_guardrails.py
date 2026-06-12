from __future__ import annotations

from job_intel.digest import reject_reason_bucket
from job_intel.evaluator import score_vacancy_v3_shadow, score_vacancy_with_version
from job_intel.models import Vacancy


def _vacancy(title: str, description: str = "Owns roadmap, product strategy, monetization, and P&L.") -> Vacancy:
    return Vacancy(
        source="test",
        source_id=title.lower().replace(" ", "-"),
        company="Acme",
        title=title,
        location="Remote",
        url="https://example.com/job",
        description=description,
    )


def test_title_function_negative_controls_hard_reject() -> None:
    titles = [
        "Director of Product Design - Payments",
        "Director of Product Design - Business Accounts",
        "Director, Product Design",
        "Head of Product Design - Growth",
        "Head of Product Marketing",
        "Director, Product Marketing (Payment, Platform)",
        "Team Lead / Head of Product Marketing",
        "Product Counsel",
        "Product Finance",
    ]

    for title in titles:
        vacancy = _vacancy(title)
        shadow = score_vacancy_v3_shadow(vacancy)
        evaluation = score_vacancy_with_version(vacancy, "v3")
        assert shadow["recommendation"] == "reject", title
        assert shadow["gates"]["G0"]["status"] == "FAIL", title
        assert shadow["gates"]["G0"]["reason"] in {
            "product_design_function",
            "product_marketing_function",
            "product_legal_function",
            "product_finance_function",
        }, title
        assert evaluation.recommendation == "reject", title
        assert reject_reason_bucket(vacancy, evaluation, duplicate=False) == shadow["gates"]["G0"]["reason"], title


def test_title_function_positive_controls_remain_eligible() -> None:
    titles = [
        "Head of Product, Global Payments Network Infrastructure",
        "Product Director, Payments",
        "Product Lead, Embedded Finance",
        "Head of Product - Fintech",
        "VP Product Security",
        "Head of Growth Product",
        "Head of Monetization",
        "Head of Platform",
    ]

    for title in titles:
        shadow = score_vacancy_v3_shadow(_vacancy(title))
        assert shadow["gates"]["G0"]["status"] != "FAIL", title
        assert shadow["recommendation"] in {"strong_fit", "needs_review", "near_miss"}, title
