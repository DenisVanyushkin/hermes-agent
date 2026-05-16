from __future__ import annotations

from job_intel.company_intel import build_market_report, monitor_target_companies
from job_intel.evaluator import score_vacancy
from job_intel.models import Vacancy
from job_intel.store import JobIntelStore


class _Response:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


HOME_HTML = """
<html>
  <head><title>Adapty</title><meta name="description" content="We are hiring across product and growth." /></head>
  <body>
    <a href="/careers">Careers</a>
  </body>
</html>
"""

CAREERS_HTML = """
<html>
  <head><title>Adapty Careers</title></head>
  <body>
    <a href="https://careers.adapty.io/roles/vp-product">VP Product</a>
  </body>
</html>
"""

JOB_HTML = """
<html>
  <head><title>VP Product - Adapty</title></head>
  <body>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org/",
      "@type": "JobPosting",
      "title": "VP Product",
      "description": "Own monetization, product strategy, and P&L for a subscription platform.",
      "hiringOrganization": {"@type": "Organization", "name": "Adapty"},
      "jobLocation": {"@type": "Place", "address": {"addressCountry": "Remote"}}
    }
    </script>
  </body>
</html>
"""


def test_monitor_target_companies_discovers_executive_opening(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()

    def fake_get(url, *args, **kwargs):
        if url == "https://adapty.io":
            return _Response(HOME_HTML)
        if url == "https://adapty.io/careers":
            return _Response(CAREERS_HTML)
        if url == "https://careers.adapty.io/roles/vp-product":
            return _Response(JOB_HTML)
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr("job_intel.company_intel.requests.get", fake_get)

    result = monitor_target_companies(store)

    assert result.vacancies
    vacancy = result.vacancies[0]
    assert vacancy.company == "Adapty"
    assert vacancy.title == "VP Product"
    assert vacancy.metadata["target_company"] is True

    intel_rows = store.fetch_company_intelligence()
    assert intel_rows[0]["company"] == "Adapty"
    assert int(intel_rows[0]["opening_count"]) == 1
    assert "hiring_activity" in intel_rows[0]["signals_json"]


def test_market_report_renders_company_intel(tmp_path) -> None:
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()
    store.upsert_company_intelligence(
        "Adapty",
        summary="Adapty | mobile subscription infrastructure | openings=1 | signals=hiring_activity",
        signals={"signals": ["hiring_activity"], "risk_flags": [], "career_urls": ["https://adapty.io/careers"], "opening_count": 1},
        target_category="mobile subscription infrastructure",
        website="https://adapty.io",
        career_urls=["https://adapty.io/careers"],
        opening_count=1,
        source="target-company",
    )

    report = build_market_report(store)

    assert "Adapty" in report
    assert "hiring_activity" in report


def test_target_company_bonus_beats_generic_remote_noise() -> None:
    target = Vacancy(
        source="remoteok",
        source_id="1",
        company="Adapty",
        title="Director of Product",
        location="Remote",
        url="https://example.com/target",
        description="Own monetization, P&L, and product strategy for a subscription platform.",
        metadata={"target_company": True, "signals": {"signals": ["hiring_activity"], "opening_count": 1}},
    )
    noise = Vacancy(
        source="remoteok",
        source_id="2",
        company="Generic Co",
        title="Product Manager",
        location="Remote",
        url="https://example.com/noise",
        description="Remote product manager role for a support-heavy team.",
    )

    assert score_vacancy(target).score > score_vacancy(noise).score
