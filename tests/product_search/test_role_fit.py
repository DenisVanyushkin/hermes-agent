from __future__ import annotations

import json
import importlib
from pathlib import Path
import sqlite3
from typing import Any

import pytest


def evaluate_role_fit(*args: Any, **kwargs: Any) -> Any:
    try:
        module = importlib.import_module("job_intel.product_search.role_fit")
    except ModuleNotFoundError:
        pytest.fail("role_fit module is not implemented yet")
    return module.evaluate_role_fit(*args, **kwargs)


def test_accepts_product_leadership_in_software_without_scope_requirement() -> None:
    decision = evaluate_role_fit(
        "Chief Product Officer",
        "CloudWorks",
        "Remote",
        "CloudWorks is a B2B SaaS software company. Own the product strategy "
        "with the co-founder and lead product management across the business.",
    )

    assert decision.verdict == "accept"
    assert "software_product_leadership" in decision.rule_ids
    match = decision.match_for("software_product_leadership")
    assert "SaaS" in match.fragments or "software" in match.fragments
    assert "co-founder" in " ".join(match.fragments)


def test_rejects_advertising_platform_from_description() -> None:
    decision = evaluate_role_fit(
        "VP Product",
        "Aster",
        "London",
        "Lead product for an advertising technology platform using programmatic "
        "advertising and demand-side platform capabilities.",
    )

    assert decision.verdict == "reject"
    match = decision.match_for("domain_expertise_required")
    assert match.details["domain"] == "adtech_and_advertising_platforms"
    assert "advertising technology" in " ".join(match.fragments).lower()


def test_rejects_industry_banking_software_portfolio() -> None:
    decision = evaluate_role_fit(
        "Product Director",
        "FinCo",
        "Frankfurt",
        "Lead a portfolio of core banking software products and banking platforms "
        "for financial institutions.",
    )

    assert decision.verdict == "reject"
    match = decision.match_for("domain_expertise_required")
    assert match.details["domain"] == "banking_core_and_payment_infrastructure"
    assert match.details["legacy_rule_id"] == "banking_software_portfolio"
    assert match.fragments


def test_rejects_banking_credit_product_p_and_l() -> None:
    decision = evaluate_role_fit(
        "Product General Manager",
        "A bank",
        "Singapore",
        "Own the P&L and commercial performance for the bank's consumer credit "
        "and lending product.",
    )

    assert decision.verdict == "reject"
    match = decision.match_for("domain_expertise_required")
    assert match.details["domain"] == "credit_p_and_l"
    assert match.fragments


def test_rejects_commercial_ecommerce_assortment_leadership() -> None:
    decision = evaluate_role_fit(
        "Chief Commercial Officer",
        "MarketCo",
        "Berlin",
        "Lead commercial strategy, category management and assortment for our "
        "e-commerce marketplace.",
    )

    assert decision.verdict == "reject"
    assert decision.match_for("ecommerce_commercial_leadership").fragments


def test_blocks_required_language_outside_owner_languages() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Paris",
        "SaaS product leadership role. Fluent French is required; English is the "
        "company language.",
    )

    assert decision.verdict == "blocked"
    match = decision.match_for("required_language_unavailable")
    assert "French" in " ".join(match.fragments)
    assert "fr" in match.details["languages"]


def test_owner_language_list_is_configurable() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Paris",
        "SaaS product leadership role. Fluent French is required; English is the "
        "company language.",
        owner_languages=("en", "ru", "fr"),
    )

    assert decision.verdict == "accept"
    assert "required_language_unavailable" not in decision.rule_ids


def test_rejects_interim_role() -> None:
    decision = evaluate_role_fit(
        "VP Product",
        "SaaSCo",
        "Remote",
        "Lead product strategy in an interim capacity for a software company.",
    )

    assert decision.verdict == "reject"
    assert "interim_or_cover" in decision.rule_ids


def test_rejects_short_fixed_term_contract() -> None:
    decision = evaluate_role_fit(
        "Chief Product Officer",
        "SaaSCo",
        "Remote",
        "Software company seeking a product leader for a 12-month fixed-term "
        "contract.",
    )

    assert decision.verdict == "reject"
    match = decision.match_for("short_contract")
    assert "12-month" in " ".join(match.fragments)


def test_language_blocked_is_distinct_from_hard_reject() -> None:
    blocked = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Paris",
        "SaaS product leadership. French is required for the working language.",
    )
    rejected = evaluate_role_fit(
        "VP Product",
        "AdTechCo",
        "Remote",
        "Product leadership for an advertising technology platform handling ad "
        "serving and DSP operations.",
    )

    assert blocked.verdict == "blocked"
    assert rejected.verdict == "reject"


def test_rule_matches_include_exact_trigger_fragments() -> None:
    decision = evaluate_role_fit(
        "Product Director",
        "FinCo",
        "Remote",
        "Core banking software portfolio.",
    )

    match = decision.match_for("domain_expertise_required")
    assert all(fragment in decision.normalized_text for fragment in match.fragments)
    assert match.explanation


def test_accepts_payments_role_when_fintech_experience_is_preferred() -> None:
    decision = evaluate_role_fit(
        "Group Product Manager, APAC",
        "PaymentsCo",
        "Singapore",
        "Lead the payment engine and local payment methods across APAC. "
        "Experience in fintech or financial services is preferred, while strong "
        "product leadership and platform delivery are required.",
    )

    assert decision.verdict == "accept"
    assert "domain_expertise_required" not in decision.rule_ids


def test_rejects_role_requiring_fraud_and_antifraud_expertise() -> None:
    decision = evaluate_role_fit(
        "Head of Product (Fraud)",
        "FintechCo",
        "Sweden",
        "Own the fraud prevention and detection product. This role requires deep "
        "experience building fraud and anti-fraud systems and managing fraud risk "
        "at scale.",
    )

    assert decision.verdict == "reject"
    match = decision.match_for("domain_expertise_required")
    assert match.details["domain"] == "fraud_and_anti_fraud"
    assert "fraud" in match.explanation.lower()


def test_rejects_role_requiring_erp_and_manufacturing_expertise() -> None:
    decision = evaluate_role_fit(
        "Director of Product, Platform",
        "EnterpriseCo",
        "Pune",
        "Own the product strategy for an ERP platform serving manufacturing operations. "
        "Candidates must have deep experience with manufacturing systems and ERP "
        "workflows.",
    )

    assert decision.verdict == "reject"
    match = decision.match_for("domain_expertise_required")
    assert match.details["domain"] == "erp_and_manufacturing_systems"


def test_rejects_staffing_agency_role_using_client_placement_language() -> None:
    decision = evaluate_role_fit(
        "Senior Product Manager",
        "Talent Search Partners",
        "Remote",
        "Our client is hiring a product leader. You will work on behalf of our client "
        "and the recruitment team will coordinate the interview process.",
    )

    assert decision.verdict == "reject"
    match = decision.match_for("staffing_agency_or_aggregator")
    assert "client" in " ".join(match.fragments).lower()


def test_rejects_agency_role_with_client_leading_fintech_phrase() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "Search Partners",
        "Remote",
        "Our client, a leading fintech, is seeking a Head of Product to lead its platform."
        " Search Partners is recruiting for our client and will coordinate interviews.",
    )

    assert decision.verdict == "reject"
    assert "staffing_agency_or_aggregator" in decision.rule_ids


def test_rejects_explicit_executive_search_publisher_name() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "North Star Executive Search",
        "Remote",
        "Lead the software product roadmap and product management function.",
    )

    assert decision.verdict == "reject"
    assert "staffing_agency_or_aggregator" in decision.rule_ids


def test_does_not_reject_canva_like_role_for_product_client_reference() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "Canva",
        "Remote",
        "Our client base uses the design platform on mobile and web. Lead the software "
        "platform architecture and product delivery for hundreds of millions of users.",
    )

    assert decision.verdict == "accept"
    assert "staffing_agency_or_aggregator" not in decision.rule_ids


def test_does_not_reject_red_hat_like_recruitment_disclaimer() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "Red Hat",
        "Remote",
        "Lead the software product roadmap and product management function. Red Hat "
        "does not seek or accept unsolicited resumes or CVs from recruitment agencies.",
    )

    assert decision.verdict == "accept"
    assert "staffing_agency_or_aggregator" not in decision.rule_ids


def test_does_not_reject_legal_company_suffix_or_client_base_language() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "Acme Software Limited",
        "Remote",
        "Acme builds a SaaS platform for our client base of 300 enterprises across "
        "telco and fintech markets. Lead the product organisation and roadmap.",
    )

    assert decision.verdict == "accept"
    assert "staffing_agency_or_aggregator" not in decision.rule_ids


def test_does_not_reject_hr_tech_product_that_mentions_talent() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "Safeguard Global",
        "Remote",
        "We build HR technology for workforce and talent management. Lead the software "
        "product roadmap and product organisation for our customers.",
    )

    assert decision.verdict == "accept"
    assert "staffing_agency_or_aggregator" not in decision.rule_ids


def test_does_not_reject_company_role_for_mentioning_a_recruiter() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "Acme Software",
        "Remote",
        "Lead the software product roadmap and product team. A recruiter will "
        "contact shortlisted candidates about the interview process.",
    )

    assert decision.verdict == "accept"
    assert "staffing_agency_or_aggregator" not in decision.rule_ids


def test_accepts_physical_product_role() -> None:
    decision = evaluate_role_fit(
        "VP Product Experience",
        "LearningCo",
        "Billund",
        "Lead product experience for physical learning kits and connected digital "
        "software learning products. Own the product roadmap and product organization.",
    )

    assert decision.verdict == "accept"
    assert "domain_expertise_required" not in decision.rule_ids


def test_does_not_block_language_marked_as_preferred() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Remote",
        "SaaS product leadership. French is preferred, while English is required.",
    )

    assert decision.verdict == "accept"
    assert "required_language_unavailable" not in decision.rule_ids


def test_does_not_reject_an_advertising_experience_preference() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Remote",
        "SaaS product leadership. Experience with advertising technology is a plus; "
        "this is not an advertising platform.",
    )

    assert decision.verdict == "accept"
    assert "advertising_platform" not in decision.rule_ids


def test_does_not_reject_product_leadership_in_ecommerce_without_commercial_scope() -> None:
    decision = evaluate_role_fit(
        "VP Product",
        "MarketCo",
        "Remote",
        "Lead the product for an e-commerce SaaS marketplace and improve the "
        "customer experience across the product.",
    )

    assert decision.verdict == "accept"
    assert "ecommerce_commercial_leadership" not in decision.rule_ids


def test_does_not_accept_a_non_product_role_that_mentions_product_management() -> None:
    decision = evaluate_role_fit(
        "Chief Operating Officer",
        "SaaSCo",
        "Remote",
        "Partner with product management while owning operations for a SaaS company.",
    )

    assert decision.verdict == "reject"
    assert "software_product_leadership" not in decision.rule_ids


def test_detects_required_language_proficiency_without_fluent_keyword() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Paris",
        "SaaS product leadership. French language proficiency is required for this role.",
    )

    assert decision.verdict == "blocked"
    assert "required_language_unavailable" in decision.rule_ids


def test_accepts_product_leadership_in_a_technology_platform_company() -> None:
    decision = evaluate_role_fit(
        "VP Product",
        "TechCo",
        "Remote",
        "Lead the product strategy for a technology platform used by businesses.",
    )

    assert decision.verdict == "accept"
    assert "software_product_leadership" in decision.rule_ids


def test_rejects_commercial_title_with_ecommerce_signal_in_another_sentence() -> None:
    decision = evaluate_role_fit(
        "Chief Commercial Officer",
        "MarketCo",
        "Remote",
        "The company operates an e-commerce marketplace. Own commercial growth, "
        "assortment and margin.",
    )

    assert decision.verdict == "reject"
    assert "ecommerce_commercial_leadership" in decision.rule_ids


def test_does_not_reject_product_commercial_growth_without_commercial_duties() -> None:
    decision = evaluate_role_fit(
        "Chief Product Officer",
        "TravelCo",
        "Remote",
        "Lead the product function for a travel e-commerce platform. Own product "
        "roadmap and commercial growth, without assortment, procurement or margin "
        "ownership.",
    )

    assert decision.verdict == "accept"
    assert "ecommerce_commercial_leadership" not in decision.rule_ids


def test_does_not_reject_adtech_platform_experience_as_a_preference() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Remote",
        "SaaS product leadership. Experience with an advertising technology platform "
        "is a plus.",
    )

    assert decision.verdict == "accept"
    assert "advertising_platform" not in decision.rule_ids


def test_does_not_reject_adform_adtech_preference_in_stand_out_section() -> None:
    decision = evaluate_role_fit(
        "Product Director, Shared Services & Settings",
        "Adform",
        "Remote",
        "Own the software platform roadmap and product organisation. Stand out by "
        "having: Experience within AdTech, MarTech, digital advertising, or other "
        "complex platform ecosystems. Continuous opportunities to learn, grow, and "
        "expand your expertise in Product Management, platform strategy, and AdTech.",
    )

    assert decision.verdict == "accept"
    assert "domain_expertise_required" not in decision.rule_ids


def test_accepts_product_role_in_travel_ecommerce_without_commercial_duties() -> None:
    decision = evaluate_role_fit(
        "Chief Product Officer",
        "TravelCo",
        "Remote",
        "Build the software platform for online travel e-commerce. Own the product "
        "roadmap, user experience and product management, not assortment, procurement "
        "or margin.",
    )

    assert decision.verdict == "accept"
    assert "ecommerce_commercial_leadership" not in decision.rule_ids


def test_accepts_privacy_product_when_advertising_platform_is_only_market_context() -> None:
    decision = evaluate_role_fit(
        "Director of Product Management",
        "PrivacyCo",
        "Remote",
        "Lead the privacy product management function for a software platform. "
        "The market includes advertising platform competitors, but this role owns "
        "privacy workflows, not ad serving.",
    )

    assert decision.verdict == "accept"
    assert "advertising_platform" not in decision.rule_ids


@pytest.mark.parametrize(
    ("language_text", "language_code"),
    [
        ("Excellente communication en français et en anglais, à l'oral comme à l'écrit.", "fr"),
        ("Goede beheersing van het Nederlands, zowel mondeling als schriftelijk.", "nl"),
        ("Sehr gute Deutschkenntnisse in Wort und Schrift erforderlich.", "de"),
        ("Se requiere dominio del español, oral y escrito.", "es"),
    ],
)
def test_blocks_required_language_in_localized_wording(language_text: str, language_code: str) -> None:
    decision = evaluate_role_fit(
        "Director of Product",
        "SaaSCo",
        "Remote",
        f"SaaS software product leadership. {language_text}",
    )

    assert decision.verdict == "blocked"
    assert decision.match_for("required_language_unavailable").details["languages"] == (language_code,)


def test_rejects_short_parental_leave_replacement_without_contract_wording() -> None:
    decision = evaluate_role_fit(
        "Chief Product Officer",
        "SaaSCo",
        "Remote",
        "Software product leadership while our current Head of Product is on "
        "parental leave (:12 months).",
    )

    assert decision.verdict == "reject"
    assert "short_contract" in decision.rule_ids


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer",
        "Senior Engineering Lead — Business Onboarding",
        "Product Manager",
    ],
)
def test_does_not_accept_non_executive_or_engineering_product_mentions(title: str) -> None:
    decision = evaluate_role_fit(
        title,
        "SaaSCo",
        "Remote",
        "Build software and collaborate with product management on product delivery.",
    )

    assert decision.verdict == "reject"
    assert "software_product_leadership" not in decision.rule_ids


def test_accepts_explicit_product_lead_title() -> None:
    decision = evaluate_role_fit(
        "Product Lead",
        "SaaSCo",
        "Remote",
        "Lead the software product roadmap and product management function.",
    )

    assert decision.verdict == "accept"


def test_accepts_digital_product_company_signal() -> None:
    decision = evaluate_role_fit(
        "Director of Product",
        "DigitalCo",
        "Remote",
        "Own the digital product portfolio for a technology business.",
    )

    assert decision.verdict == "accept"


def test_accepts_senior_product_title_with_punctuation() -> None:
    decision = evaluate_role_fit(
        "Director, Product Management",
        "SaaSCo",
        "Remote",
        "Own the software product portfolio and product strategy.",
    )

    assert decision.verdict == "accept"


def test_accepts_senior_product_title_with_em_dash() -> None:
    decision = evaluate_role_fit(
        "Director — Product Management",
        "SaaSCo",
        "Remote",
        "Own the software product portfolio and product strategy.",
    )

    assert decision.verdict == "accept"


def test_accepts_product_function_title_with_level_after_product() -> None:
    decision = evaluate_role_fit(
        "Product Marketing Director",
        "SaaSCo",
        "Remote",
        "Own the software product portfolio and product strategy.",
    )

    assert decision.verdict == "accept"


def test_accepts_ai_technology_product_company_signal() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "AICo",
        "Remote",
        "Own the AI product portfolio for an artificial intelligence business.",
    )

    assert decision.verdict == "accept"


def test_rejects_banking_credit_p_and_l_across_sentences() -> None:
    decision = evaluate_role_fit(
        "Director of Product",
        "BankCo",
        "Remote",
        "Own the P&L. The banking business includes a consumer credit product.",
    )

    assert decision.verdict == "reject"
    assert "domain_expertise_required" in decision.rule_ids


def test_blocks_short_bilingual_language_code_form() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Remote",
        "Bilingual FR/EN communication required.",
    )

    assert decision.verdict == "blocked"
    assert decision.match_for("required_language_unavailable").details["languages"] == ("fr",)


def test_blocks_bilingual_language_code_form_in_title() -> None:
    decision = evaluate_role_fit(
        "Product Director | FR/EN",
        "SaaSCo",
        "Remote",
        "Bilingual communication required.",
    )

    assert decision.verdict == "blocked"
    assert decision.match_for("required_language_unavailable").details["languages"] == ("fr",)


def test_rejects_unsupported_french_description_before_other_rules() -> None:
    decision = evaluate_role_fit(
        "Directeur Produit | Product Director",
        "Booxi",
        "Montreal, Quebec, Canada",
        "Description du poste en français pour diriger le produit.",
    )

    assert decision.verdict == "reject"
    assert decision.rule_ids == ("description_language_not_supported",)
    match = decision.match_for("description_language_not_supported")
    assert "français" in " ".join(match.fragments)
    assert match.details["detected_languages"] == ("fr",)


UNSUPPORTED_LANGUAGE_POOL_FIXTURES = (
    pytest.param(
        "Head of Product, WFM",
        "Visma",
        "Helsinki, Uusimaa, Finland",
        (
            "Head of Product, WFM Etsimme kokenutta ja ihmisläheistä tuoteammattilaista "
            "vahvistamaan työvoimahallinnan (WFM) tuotealuettamme sekä toimimaan veturina "
            "koko HR Tech -yksikkömme tuotehallinnan toimintatapojen ja AI-työkalujen "
            "kehittämisessä. Tässä roolissa pääset viemään niin suomalaisen julkisen "
            "sektorin kuin yksityispuolenkin kriittisiä järjestelmiä kohti modernia, "
            "AI-natiivia aikaa osana Solveon-alustaa. Tavoitteenamme on sujuvoittaa "
            "satojen tuhansien ihmisten arkea – emme tee työtä suorittamalla pitkiä "
            "vaatimuslistoja, vaan poistamalla manuaalityötä, ratkomalla asiakkaiden "
            "todellisia ongelmia ja rakentamalla fiksumpia toimintamalleja."
        ),
        id="visma-finnish",
    ),
    pytest.param(
        "Head of Product",
        "Inact",
        "Copenhagen, Capital Region of Denmark, Denmark",
        (
            "Head of Product Om Inact Inact er en B2B SaaS-virksomhed med ét klart mål: "
            "at hjælpe virksomheder med at bygge en kundedrevet supply chain — og rent "
            "faktisk handle på den. Vores platform, Inact Now, omsætter supply chain-data "
            "til klar indsigt og konkrete handlinger — deraf navnet: Insights + Actions. "
            "Vi arbejder med virksomheder, der stiller høje krav til leveringspræcision "
            "og lagerstyring, og vi hjælper dem med at reducere spild, forbedre "
            "beslutningsgrundlaget og bygge en supply chain, der rent faktisk driver vækst."
        ),
        id="inact-danish",
    ),
    pytest.param(
        "Head of Product",
        "Lemontech",
        "Santiago, Santiago Metropolitan Region, Chile",
        (
            "En LemonTech🍋, somos una empresa SaaS líder en LATAM en Legaltech con más "
            "de 19 años impulsando procesos legales más eficientes y digitales. Contamos "
            "con tres softwares, más de 12.000 usuarios activos y una base de 1.700 "
            "clientes en toda la región. Desde 2019 formamos parte de Accel-KKR, un fondo "
            "de inversiones de Silicon Valley enfocado en empresas tecnológicas. Nuestro "
            "objetivo es reducir la burocracia y transformar el sistema judicial para "
            "hacerlo más moderno y justo. ¿Te apasiona construir productos SaaS B2B que "
            "transforman industrias completas y escalan sin fronteras? En LemonTech "
            "buscamos a nuestro/a próximo/a Head of Product: un/a líder estratega, "
            "analítico/a y profundamente obsesionado/a por resolver problemas reales."
        ),
        id="lemontech-spanish",
    ),
)


@pytest.mark.parametrize(
    ("title", "company", "location", "description"),
    UNSUPPORTED_LANGUAGE_POOL_FIXTURES,
)
def test_rejects_real_weekly_pool_non_supported_language_fixtures(
    title: str,
    company: str,
    location: str,
    description: str,
) -> None:
    decision = evaluate_role_fit(title, company, location, description)

    assert decision.verdict == "reject"
    assert "description_language_not_supported" in decision.rule_ids


def test_real_german_fixture_is_rejected_by_supported_language_density() -> None:
    decision = evaluate_role_fit(
        "Head of Product Management (m/w/d) Finance Software",
        "Infoniqa Deutschland GmbH",
        "Germany",
        (
            "Infoniqa steht für moderne HR- und Finance-Lösungen und für die Menschen, "
            "die sie möglich machen. Mit rund 1.200 Mitarbeitenden begleiten wir "
            "Unternehmen im DACH-Raum dabei, ihre Arbeitswelt einfacher, effizienter "
            "und verlässlicher zu gestalten. Du möchtest die Zukunft eines umfangreichen "
            "ERP-Portfolios aktiv gestalten und Produktmanagement neu denken? Als Head "
            "of Product Management Finance Software verantwortest du die strategische "
            "und operative Weiterentwicklung unserer ERP-Produkte und führst eine "
            "Organisation auf dem Weg zu einer vollständig cloudbasierten Produktwelt."
        ),
    )

    assert decision.verdict == "reject"
    assert decision.rule_ids == ("description_language_not_supported",)


def test_does_not_drop_full_english_description_with_dutch_tail() -> None:
    decision = evaluate_role_fit(
        "Head of Product",
        "SaaSCo",
        "Amsterdam",
        "We are looking for a Head of Product to lead the software product roadmap "
        "and manage the product team. Wij werken met klanten in Nederland.",
    )

    assert decision.verdict == "accept"
    assert "description_language_not_supported" not in decision.rule_ids


def test_russian_description_reaches_industry_rules_without_language_rejection() -> None:
    decision = evaluate_role_fit(
        "Руководитель продукта",
        "TechCo",
        "Удалённо",
        "Мы развиваем программную платформу для бизнеса и ищем руководителя продукта.",
    )

    assert "description_language_not_supported" not in decision.rule_ids


def test_short_description_does_not_trigger_unsupported_language() -> None:
    decision = evaluate_role_fit(
        "Product Director",
        "SaaSCo",
        "Remote",
        "Bonjour",
    )

    assert "description_language_not_supported" not in decision.rule_ids


def test_supported_language_list_is_configurable() -> None:
    decision = evaluate_role_fit(
        "Directeur Produit",
        "FrenchCo",
        "Paris",
        "Description du poste en français pour diriger le produit.",
        supported_languages=("en", "ru", "fr"),
    )

    assert "description_language_not_supported" not in decision.rule_ids


def test_corpus_bilingual_english_dutch_description_is_not_dropped() -> None:
    corpus_path = Path(__file__).parents[2] / "scripts" / "gate_b_readiness_corpus.v1.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    candidates = [
        item["raw"]
        for item in corpus
        if "Fluency in Dutch and English is necessary" in item["raw"].get("description", "")
    ]
    assert candidates, "the immutable corpus must contain an English/Dutch bilingual example"

    candidate = candidates[0]
    decision = evaluate_role_fit(
        candidate.get("title", ""),
        candidate.get("company", ""),
        candidate.get("location", ""),
        candidate.get("description", ""),
    )
    assert "description_language_not_supported" not in decision.rule_ids


def test_owner_labels_match_live_corpus_20_of_20() -> None:
    labels_path = Path("/home/hermes/.hermes/job_intel/manual-shortlist/labels/owner-labels-2026-09.json")
    database = Path("/var/lib/job-intel/state/job_intel.sqlite3")
    if not labels_path.exists() or not database.exists():
        pytest.skip("live owner labels or Job Intel database is not available")
    payload = json.loads(labels_path.read_text(encoding="utf-8"))
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    expected_verdicts = {
        "yes": "accept",
        "no": "reject",
        "yes_blocked_language": "blocked",
        "accept": "accept",
        "reject": "reject",
        "blocked": "blocked",
    }
    mismatches = []
    for label in payload["labels"]:
        row = connection.execute(
            """
            SELECT title, company, location, description
            FROM vacancies
            WHERE vacancy_key = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (label["vacancy_key"],),
        ).fetchone()
        assert row is not None, label["vacancy_key"]
        decision = evaluate_role_fit(*row)
        if decision.verdict != expected_verdicts[label["verdict"]]:
            mismatches.append((label["vacancy_key"], label["verdict"], decision.verdict, decision.rule_ids))

    assert len(payload["labels"]) == 20
    assert mismatches == []
