from __future__ import annotations

from job_intel.strategic import build_strategic_report, update_strategic_layer
from job_intel.store import JobIntelStore


def test_update_strategic_layer_separates_actionable_and_watchlist(tmp_path) -> None:
    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    store.upsert_company_intelligence(
        "Miro",
        summary="Miro | SaaS platform | openings=2 | signals=funding_signal, growth_signal, hiring_activity",
        signals={
            "signals": ["funding_signal", "growth_signal", "hiring_activity"],
            "risk_flags": [],
            "career_urls": ["https://miro.com/careers"],
            "opening_count": 2,
        },
        target_category="SaaS platform",
        website="https://miro.com",
        career_urls=["https://miro.com/careers"],
        opening_count=2,
        source="target-company",
    )
    store.append_company_event(
        "Miro",
        "leadership_change",
        source="target-company",
        title="New product leader",
        url="https://miro.com",
        summary="Miro appointed a new product leader",
        details={"impact": "product org reshaping"},
    )
    store.upsert_company_intelligence(
        "Adapty",
        summary="Adapty | mobile subscription infrastructure | openings=0 | signals=org_transformation, growth_signal",
        signals={
            "signals": ["growth_signal", "org_transformation"],
            "risk_flags": [],
            "career_urls": ["https://adapty.io/careers"],
            "opening_count": 0,
        },
        target_category="mobile subscription infrastructure",
        website="https://adapty.io",
        career_urls=["https://adapty.io/careers"],
        opening_count=0,
        source="target-company",
    )
    store.append_company_event(
        "Adapty",
        "leadership_change",
        source="target-company",
        title="Product org reshaping",
        url="https://adapty.io",
        summary="Adapty changed product leadership while reworking the org",
        details={"impact": "watchlist-worthy but no openings yet"},
    )

    result = update_strategic_layer(store)

    assert result.actionable_opportunities
    assert any(item["bucket"] == "actionable opportunity" for item in result.actionable_opportunities)
    assert any(item["bucket"] == "interesting company" for item in result.watchlist_companies)
    assert any(signal.signal_strength in {"moderate_signal", "strong_signal"} for signal in result.signals)
    assert store.fetch_strategic_signals()
    assert store.fetch_strategic_predictions()


def test_build_strategic_report_is_concise_and_tier_based(tmp_path) -> None:
    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    store.upsert_company_intelligence(
        "Miro",
        summary="Miro | SaaS platform | openings=2 | signals=funding_signal, growth_signal, hiring_activity",
        signals={
            "signals": ["funding_signal", "growth_signal", "hiring_activity"],
            "risk_flags": [],
            "career_urls": ["https://miro.com/careers"],
            "opening_count": 2,
        },
        target_category="SaaS platform",
        website="https://miro.com",
        career_urls=["https://miro.com/careers"],
        opening_count=2,
        source="target-company",
    )
    store.upsert_company_intelligence(
        "Canva",
        summary="Canva | design platform | openings=0 | signals=leadership_change, org_transformation",
        signals={
            "signals": ["leadership_change", "org_transformation"],
            "risk_flags": [],
            "career_urls": ["https://www.canva.com/careers"],
            "opening_count": 0,
        },
        target_category="platform",
        website="https://www.canva.com",
        career_urls=["https://www.canva.com/careers"],
        opening_count=0,
        source="target-company",
    )

    report = build_strategic_report(store)

    assert "Executive strategic intelligence brief" in report
    assert "Actionable opportunities" in report
    assert "Interesting companies to watch" in report
    assert "signal_strength=" in report
    assert "probability=" not in report
    assert "likely future executive openings" not in report.lower()
