from job_intel.evaluator import score_vacancy, tier_for_score
from job_intel.models import Vacancy


def test_product_monetization_vp_role_scores_strongly() -> None:
    vacancy = Vacancy(
        source="hh",
        source_id="42",
        company="Adapty",
        title="Director of Product",
        location="Remote, Europe",
        url="https://example.com/job",
        description=(
            "Own product strategy, monetization, retention, and P&L for a B2C subscription platform "
            "with a high-autonomy international team."
        ),
    )

    evaluation = score_vacancy(vacancy)

    assert evaluation.score >= 75
    assert evaluation.recommendation in {"strong_fit", "exceptional_fit"}
    assert tier_for_score(evaluation.score) in {"strong_fit", "exceptional_fit"}


def test_outsourcing_delivery_only_roles_are_rejected() -> None:
    vacancy = Vacancy(
        source="linkedin",
        source_id="99",
        company="Bodyshop Inc",
        title="Delivery Manager",
        location="Remote",
        url="https://example.com/job2",
        description="Delivery-only role for outsourcing clients with ticket processing and support operations.",
    )

    evaluation = score_vacancy(vacancy)

    assert evaluation.score < 40
    assert evaluation.recommendation == "reject"
