from __future__ import annotations

import json

from job_intel.models import Evaluation, Vacancy
from job_intel.observability import record_daily_observability
from job_intel.selection_boundaries import (
    REASON_ADTECH_CTV,
    REASON_BELOW_EXECUTIVE_SCOPE,
    REASON_COMPANY_BLACKLIST,
    REASON_CRYPTO,
    REASON_GAMING,
    REASON_RUSSIA,
    REASON_TECHNICAL_PRODUCT,
    REASON_WORK_AUTHORIZATION,
    assess_selection_boundaries,
)
from job_intel.store import JobIntelStore


def vacancy(**overrides: object) -> Vacancy:
    values: dict[str, object] = {
        "source": "linkedin",
        "source_id": "fixture-1",
        "company": "Example",
        "title": "VP Product",
        "location": "London, United Kingdom",
        "url": "https://example.test/jobs/1",
        "description": "Own product strategy, roadmap, and measurable commercial outcomes.",
    }
    values.update(overrides)
    return Vacancy(**values)


def test_each_owner_boundary_is_named_and_independent() -> None:
    cases = (
        ("gaming", {"company": "Tripledot Studios", "description": "A mobile games studio building live game products."}, REASON_GAMING),
        ("adtech", {"company": "Ogury", "description": "Build a scalable CTV adtech business for advertisers and publishers."}, REASON_ADTECH_CTV),
        ("technical", {"company": "CHAMP Cargosystems", "description": "Own an IT product organisation, APIs, platform architecture, and technical service delivery."}, REASON_TECHNICAL_PRODUCT),
        ("crypto", {"company": "OKX", "description": "Lead product strategy for a crypto exchange and digital assets business."}, REASON_CRYPTO),
        ("russia", {"company": "Panda Gifts", "location": "Moscow, Russia"}, REASON_RUSSIA),
        ("scope", {"title": "Product Lead", "description": "Own a single product backlog and coordinate delivery."}, REASON_BELOW_EXECUTIVE_SCOPE),
        ("authorization", {"company": "Hive", "location": "Canada (Remote); USA (Remote)", "description": "You must have legal work authorization in the country where you reside and work; we cannot hire in Quebec."}, REASON_WORK_AUTHORIZATION),
    )
    for label, fields, expected in cases:
        assessment = assess_selection_boundaries(vacancy(**fields))
        assert expected in assessment.rejection_reasons, label


def test_company_blacklist_is_a_separate_boundary() -> None:
    assessment = assess_selection_boundaries(
        vacancy(company="OKX"), blacklisted_company_keys={"okx"}
    )
    assert REASON_COMPANY_BLACKLIST in assessment.rejection_reasons


def test_industry_is_not_rejected_from_title_without_context() -> None:
    assessment = assess_selection_boundaries(
        vacancy(company="Example", title="VP Product, Sports", description="")
    )
    assert REASON_GAMING not in assessment.rejection_reasons
    assert "industry_context_unknown" in assessment.unknown_reasons


def test_vodafone_cloud_security_and_sports_are_not_technical_or_gaming() -> None:
    assert not assess_selection_boundaries(
        vacancy(
            company="VodafoneThree / Vodafone Business UK",
            title="Head of Product, Cloud & Security Portfolio",
            description=(
                "Own the product roadmap for a commercial cloud and security portfolio, "
                "including revenue, margin, customers, and growth."
            ),
        )
    ).rejection_reasons
    assert not assess_selection_boundaries(
        vacancy(
            company="Legend",
            title="VP Product, Sports",
            description="Lead product and design across a global sports portfolio and sports media products.",
        )
    ).rejection_reasons


def test_fourteen_owner_reference_controls_survive_new_boundaries() -> None:
    controls = (
        ("Legend", "VP Product, Sports", "Lead product and design across a global sports portfolio."),
        ("RTINGS.com", "Head of Digital Product", "Own the consumer digital product, vision, strategy, roadmap, and growth."),
        ("Sotheby's", "VP, Product Management", "Own websites, mobile app, and marketplace product transformation."),
        ("Checkatrade", "VP Product", "Own product strategy for a two-sided marketplace and lead the product team."),
        ("VodafoneThree", "Head of Product, Cloud & Security Portfolio", "Own the commercial cloud and security portfolio, revenue, margin, and customer growth."),
        ("LEGO Education", "VP Product Experience", "Own the product strategy for a global K-8 physical and digital learning portfolio."),
        ("GPC Asia Pacific", "Head of Product", "Own the vision, strategy, and roadmap for B2B ecommerce platforms across APAC."),
        ("Coupa", "Product Management Director", "Lead a two-sided B2B supplier marketplace and its monetization strategy."),
        ("Eucalyptus", "Director of Product", "Own engagement, retention, and patient outcomes for a digital health product."),
        ("Ruby Labs", "Head of Product", "Own monetization, retention, ARPU, and subscription lifetime value."),
        ("DeepL", "Head of Product Growth", "Drive product-led growth, adoption, and revenue for a global translation platform."),
        ("Hostinger", "Head of Product", "Define product vision, strategy, and roadmap for a core digital consumer product."),
        ("Trainline", "Head of Product - B2B", "Own product vision and P&L accountability for travel distribution."),
        ("Kiss My Apps", "Head of Product", "Own product strategy and growth channels for a consumer subscription product."),
    )
    for company, title, description in controls:
        assert not assess_selection_boundaries(
            vacancy(company=company, title=title, description=description)
        ).rejection_reasons, company


def test_work_authorization_silence_is_unknown_not_rejection() -> None:
    assessment = assess_selection_boundaries(
        vacancy(company="DeepL", location="London, United Kingdom")
    )
    assert REASON_WORK_AUTHORIZATION not in assessment.rejection_reasons
    assert "work_authorization_unknown" in assessment.unknown_reasons


def test_work_authorization_question_is_not_a_hard_mismatch() -> None:
    assessment = assess_selection_boundaries(
        vacancy(
            company="Eucalyptus",
            location="AU - HQ - NSW",
            description=(
                "Do you currently have the right to work in Australia? "
                "Do you now, or will you in the future, require sponsorship?"
            ),
        )
    )
    assert REASON_WORK_AUTHORIZATION not in assessment.rejection_reasons


def test_possible_sponsorship_path_is_not_a_hard_mismatch() -> None:
    assessment = assess_selection_boundaries(
        vacancy(
            company="Sony Interactive Entertainment",
            location="London, United Kingdom",
            description=(
                "Not everyone has an automatic right to work in the UK; "
                "we may consider eligibility to be sponsored for a work visa."
            ),
        )
    )
    assert REASON_WORK_AUTHORIZATION not in assessment.rejection_reasons


def test_feedback_company_blacklist_is_consumed_read_only(tmp_path) -> None:
    store = JobIntelStore(tmp_path / "job-intel.sqlite3")
    store.bootstrap()
    store.create_feedback_event(
        slack_channel_id=None,
        slack_message_ts=None,
        user_id="owner",
        reaction_type="thumbsdown",
        company="OKX",
        polarity="negative",
        status="classified",
        applies_to_company=True,
    )
    assert "okx" in store.fetch_company_blacklist()


def test_boundary_reasons_are_persisted_as_rejection_events(tmp_path) -> None:
    store = JobIntelStore(tmp_path / "job-intel.sqlite3")
    store.bootstrap()
    item = vacancy(company="OKX", description="crypto exchange product")
    classification = {
        "classification": "vp_product",
        "executive_detected": True,
        "selection_boundary_reasons": [REASON_CRYPTO],
        "selection_boundary_unknowns": [],
    }
    evaluation = Evaluation(
        score=0,
        tier="reject",
        recommendation="reject",
        reasons=[REASON_CRYPTO],
    )
    run_id = store.start_run("test")
    record_daily_observability(
        store,
        run_id,
        [(item, evaluation, classification, 1, False)],
    )
    with store.connect(read_only=True) as conn:
        observability = conn.execute(
            "SELECT selection_boundary_reasons_json FROM vacancy_observability"
        ).fetchone()
        reasons = [
            row[0]
            for row in conn.execute(
                "SELECT rejection_reason FROM vacancy_rejection_events"
            )
        ]
    assert REASON_CRYPTO in reasons
    assert json.loads(observability[0]) == [REASON_CRYPTO]
