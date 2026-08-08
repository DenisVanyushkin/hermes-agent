"""Detail fetchers: one API shape each, and nothing else."""
import json
from pathlib import Path

import pytest

from job_intel.ats_sources import fetch_smartrecruiters_detail

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "job_intel"


def _payload(name):
    return json.loads((FIXTURES / name).read_text())


def test_smartrecruiters_joins_the_job_sections(monkeypatch):
    monkeypatch.setattr("job_intel.ats_sources._detail_json",
                        lambda url, **kw: _payload("smartrecruiters_detail.json"))
    text = fetch_smartrecruiters_detail("https://api.smartrecruiters.com/v1/companies/wise/postings/1")
    assert "own the pricing and incentive structure" in text
    assert "10+ years in product" in text
    assert "We offer equity" in text


def test_smartrecruiters_excludes_company_boilerplate(monkeypatch):
    """companyDescription is company prose: the duty filter drops it anyway and
    it only adds false-positive surface."""
    monkeypatch.setattr("job_intel.ats_sources._detail_json",
                        lambda url, **kw: _payload("smartrecruiters_detail.json"))
    text = fetch_smartrecruiters_detail("https://api.smartrecruiters.com/v1/companies/wise/postings/1")
    assert "global technology company" not in text


def test_smartrecruiters_strips_html(monkeypatch):
    monkeypatch.setattr("job_intel.ats_sources._detail_json",
                        lambda url, **kw: _payload("smartrecruiters_detail.json"))
    text = fetch_smartrecruiters_detail("https://api.smartrecruiters.com/v1/companies/wise/postings/1")
    assert "<p>" not in text


def test_smartrecruiters_returns_none_without_a_job_ad(monkeypatch):
    monkeypatch.setattr("job_intel.ats_sources._detail_json", lambda url, **kw: {"id": "1"})
    assert fetch_smartrecruiters_detail("https://x/1") is None


def test_smartrecruiters_returns_none_on_transport_failure(monkeypatch):
    monkeypatch.setattr("job_intel.ats_sources._detail_json", lambda url, **kw: None)
    assert fetch_smartrecruiters_detail("https://x/1") is None
