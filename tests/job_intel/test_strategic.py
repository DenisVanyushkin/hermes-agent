from __future__ import annotations

from job_intel.strategic import build_strategic_report, update_strategic_layer
from job_intel.store import JobIntelStore


def test_update_strategic_layer_records_signals_and_predictions(tmp_path) -> None:
    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    store.upsert_company_intelligence(
        "Adapty",
        summary="Adapty | mobile subscription infrastructure | openings=0 | signals=hiring_activity, growth_signal, org_transformation",
        signals={
            "signals": ["hiring_activity", "growth_signal", "org_transformation"],
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
        title="New product leader",
        url="https://adapty.io",
        summary="Adapty appointed a new product leader",
        details={"impact": "product org reshaping"},
    )

    result = update_strategic_layer(store)

    assert result.predictions
    assert any(pred.prediction_type == "vp_product_hiring_3_6_months" for pred in result.predictions)
    assert any(signal.signal_type == "growth_signal" for signal in result.signals)
    assert store.fetch_strategic_signals()
    assert store.fetch_strategic_predictions()


def test_build_strategic_report_mentions_predictions(tmp_path) -> None:
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

    report = build_strategic_report(store)

    assert "Strategic opportunity report" in report
    assert "Miro" in report
    assert "likely future executive openings" in report.lower()
