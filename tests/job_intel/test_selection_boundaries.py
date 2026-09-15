from __future__ import annotations

import json
from pathlib import Path

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
    boundary_rejection_evaluation,
    has_real_job_text,
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


def evidence(prefix: str) -> str:
    return prefix + " " + ("The role works with stakeholders and supports delivery. " * 5)


def test_each_owner_boundary_is_named_and_independent() -> None:
    cases = (
        ("gaming", {"company": "Tripledot Studios", "description": evidence("A mobile games studio building live game products.")}, REASON_GAMING),
        ("adtech", {"company": "Ogury", "description": evidence("Build a scalable CTV adtech business for advertisers and publishers.")}, REASON_ADTECH_CTV),
        ("technical", {"company": "CHAMP Cargosystems", "description": evidence("Own an IT product organisation, APIs, platform architecture, and technical service delivery.")}, REASON_TECHNICAL_PRODUCT),
        ("crypto", {"company": "OKX", "description": evidence("Lead product strategy for a crypto exchange and digital assets business.")}, REASON_CRYPTO),
        ("russia", {"company": "Panda Gifts", "location": "Moscow, Russia"}, REASON_RUSSIA),
        ("scope", {"title": "Product Lead", "description": evidence("Own a single product backlog and coordinate delivery.")}, REASON_BELOW_EXECUTIVE_SCOPE),
        ("authorization", {"company": "Hive", "location": "Canada (Remote); USA (Remote)", "description": evidence("You must have legal work authorization in the country where you reside and work; we cannot hire in Quebec.")}, REASON_WORK_AUTHORIZATION),
    )
    for label, fields, expected in cases:
        assessment = assess_selection_boundaries(vacancy(**fields))
        assert expected in assessment.rejection_reasons, label


def test_company_blacklist_is_a_separate_boundary() -> None:
    assessment = assess_selection_boundaries(
        vacancy(company="OKX"), blacklisted_company_keys={"okx"}
    )
    assert REASON_COMPANY_BLACKLIST in assessment.rejection_reasons


def test_company_blacklist_rejection_preserves_origin_and_count() -> None:
    explicit = assess_selection_boundaries(
        vacancy(company="OKX"),
        blacklisted_company_keys={
            "okx": {"origin": "explicit", "negative_event_count": 0}
        },
    )
    threshold = assess_selection_boundaries(
        vacancy(company="Coinbase"),
        blacklisted_company_keys={
            "coinbase": {"origin": "auto_threshold", "negative_event_count": 3}
        },
    )
    assert "company_blacklist:explicit" in explicit.rejection_reasons
    assert "company_blacklist:auto_threshold:3" in threshold.rejection_reasons


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


def test_company_blacklist_uses_explicit_list_and_threshold_three(tmp_path) -> None:
    store = JobIntelStore(tmp_path / "job-intel.sqlite3")
    store.bootstrap()
    for company in ("Coinbase", "Coinbase", "Coinbase", "Brex", "Maree"):
        store.create_feedback_event(
            slack_channel_id=None,
            slack_message_ts=None,
            user_id="owner",
            reaction_type="thumbsdown",
            company=company,
            polarity="negative",
            status="classified",
            applies_to_company=True,
        )

    effective = store.fetch_company_blacklist()
    assert effective["okx"] == {
        "origin": "explicit",
        "negative_event_count": 0,
    }
    assert effective["coinbase"] == {
        "origin": "auto_threshold",
        "negative_event_count": 3,
    }
    assert "brex" not in effective
    assert "maree" not in effective


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


def test_company_blacklist_origin_is_persisted_in_run_data(tmp_path) -> None:
    store = JobIntelStore(tmp_path / "job-intel.sqlite3")
    store.bootstrap()
    item = vacancy(company="OKX", description="crypto exchange product")
    assessment = assess_selection_boundaries(
        item,
        blacklisted_company_keys=store.fetch_company_blacklist(),
    )
    classification = {
        "classification": "vp_product",
        "executive_detected": True,
        "selection_boundary_reasons": list(assessment.rejection_reasons),
        "selection_boundary_unknowns": list(assessment.unknown_reasons),
    }
    evaluation = boundary_rejection_evaluation(item, assessment.rejection_reasons)
    run_id = store.start_run("test")
    record_daily_observability(store, run_id, [(item, evaluation, classification, 1, False)])
    with store.connect(read_only=True) as conn:
        stored = conn.execute(
            "SELECT selection_boundary_reasons_json FROM vacancy_observability"
        ).fetchone()[0]
    assert "company_blacklist:explicit" in json.loads(stored)


def test_title_only_description_is_unknown_for_scope_and_industry() -> None:
    listing = vacancy(
        company="Example",
        title="Product Lead - Adtech",
        description="Product Lead - Adtech",
    )
    assessment = assess_selection_boundaries(listing)
    assert REASON_BELOW_EXECUTIVE_SCOPE not in assessment.rejection_reasons
    assert REASON_ADTECH_CTV not in assessment.rejection_reasons
    assert "executive_scope_unknown" in assessment.unknown_reasons
    assert "industry_context_unknown" in assessment.unknown_reasons

    game_listing = vacancy(
        company="Voodoo",
        title="Product Lead - Portfolio Midcore Games",
        description="Product Lead - Portfolio Midcore Games",
    )
    game_assessment = assess_selection_boundaries(game_listing)
    assert REASON_BELOW_EXECUTIVE_SCOPE not in game_assessment.rejection_reasons
    assert REASON_GAMING not in game_assessment.rejection_reasons
    assert "executive_scope_unknown" in game_assessment.unknown_reasons
    assert "industry_context_unknown" in game_assessment.unknown_reasons


def test_html_entities_are_decoded_for_boundary_matching_without_rewriting_storage() -> None:
    encoded = "&lt;p&gt;" + ("P&amp;amp;L ownership accountability. " * 8) + "&lt;/p&gt;"
    listing = vacancy(title="Product Lead", description=encoded)
    assessment = assess_selection_boundaries(listing)
    assert REASON_BELOW_EXECUTIVE_SCOPE not in assessment.rejection_reasons
    assert has_real_job_text(listing)
    assert listing.description == encoded


def test_html_tags_do_not_inflate_real_text_threshold() -> None:
    encoded = "".join("&lt;span&gt;x&lt;/span&gt;" for _ in range(60))
    listing = vacancy(title="Product Lead", description=encoded)
    assessment = assess_selection_boundaries(listing)
    assert not has_real_job_text(listing)
    assert REASON_BELOW_EXECUTIVE_SCOPE not in assessment.rejection_reasons
    assert "executive_scope_unknown" in assessment.unknown_reasons


def test_n26_core_banking_control_is_technical_product() -> None:
    # Public text captured from the live read-only vacancy row
    # https://n26.com/en-eu/careers/positions/8111591.
    description = (
        "&lt;p&gt;&lt;strong&gt;N26 is looking for a Head of Product - Core Banking Systems "
        "to partner with our Engineering, Treasury, Risk, and Banking Operations teams. "
        "You will own the Core Banking Systems product roadmap and lead a team of product "
        "&amp;amp; business managers delivering foundational capabilities that power N26 — "
        "from rapid product configuration to balance sheet infrastructure and regulatory reporting.&lt;/strong&gt;&lt;/p&gt; "
        "&lt;p&gt;You will work at the intersection of Core Banking architecture, financial data integrity, "
        "and compliance. Your mission is to transform Core Banking Systems into a composable platform "
        "that enables product managers to configure and launch products in days.&lt;/p&gt; "
        "&lt;li&gt;5-7+ years of product management experience focused on core banking systems, ledger infrastructure, "
        "payments, or core platform products.&lt;/li&gt; "
        "&lt;li&gt;Proven experience partnering with Core Banking and Platform Engineering experts.&lt;/li&gt; "
        "&lt;li&gt;Strong technical literacy, backend platform trade-offs, multi-currency ledgers, and data pipelines.&lt;/li&gt;"
    )
    assessment = assess_selection_boundaries(
        vacancy(
            company="n26",
            title="Head of Product, Core Banking Systems",
            location="Berlin",
            url="https://n26.com/en-eu/careers/positions/8111591",
            description=description,
        )
    )
    assert REASON_TECHNICAL_PRODUCT in assessment.rejection_reasons


def test_detail_enrichment_makes_short_listing_text_real() -> None:
    item = vacancy(
        company="Example",
        title="Product Lead",
        description="Product Lead",
        metadata={
            "linkedin_detail_enrichment": {
                "source": "public_linkedin_job_detail",
                "observed_at": "2026-09-14T10:00:00+00:00",
            }
        },
    )
    assert has_real_job_text(item)
    assessment = assess_selection_boundaries(item)
    assert "executive_scope_unknown" not in assessment.unknown_reasons


def test_title_only_industry_outcome_does_not_depend_on_replay_company_name() -> None:
    assessments = [
        assess_selection_boundaries(
            vacancy(
                company=company,
                title="Product Lead - Portfolio Midcore Games",
                description="Product Lead - Portfolio Midcore Games",
            )
        )
        for company in ("Scopely", "Voodoo")
    ]
    assert [assessment.rejection_reasons for assessment in assessments] == [(), ()]
    assert all("gaming_experience_mismatch" not in assessment.rejection_reasons for assessment in assessments)
    assert all("industry_context_unknown" in assessment.unknown_reasons for assessment in assessments)


def test_adtech_boundary_is_limited_to_ctv_and_video_advertising() -> None:
    # Owner decision 2026-09-14: only CTV / video advertising is out of scope.
    # Performance marketing, programmatic and advertiser/publisher marketplaces
    # are adjacent growth work and must not be rejected by this boundary.
    general_adtech = [
        "A performance marketing marketplace where advertisers and publishers transact efficiently.",
        "Lead our programmatic advertising platform and grow the ad tech revenue line.",
        "Own the SSP and DSP roadmap for our advertising technology products.",
    ]
    for text in general_adtech:
        assessment = assess_selection_boundaries(
            vacancy(company="Example", title="Head of Product", description=evidence(text))
        )
        assert REASON_ADTECH_CTV not in assessment.rejection_reasons, text

    ctv = [
        "Build a scalable connected TV advertising business for brands.",
        "Grow our CTV proposition across streaming inventory.",
        "Own the video advertising product line for publishers.",
    ]
    for text in ctv:
        assessment = assess_selection_boundaries(
            vacancy(company="Example", title="Head of Product", description=evidence(text))
        )
        assert REASON_ADTECH_CTV in assessment.rejection_reasons, text


def test_selection_boundaries_source_does_not_name_replay_companies() -> None:
    source = Path(__file__).resolve().parents[2] / "job_intel" / "selection_boundaries.py"
    source_text = source.read_text(encoding="utf-8").casefold()
    for company_name in (
        "scopely", "amanotes", "brawl stars", "brawlstars", "supercell",
        "almedia", "vondel", "publicis",
    ):
        assert company_name not in source_text, company_name
