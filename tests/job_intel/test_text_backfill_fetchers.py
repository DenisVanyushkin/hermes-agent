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


SR_API_URL = "https://api.smartrecruiters.com/v1/companies/Wise/postings/744000142223879"


def test_smartrecruiters_returns_none_without_a_job_ad(monkeypatch):
    """URL must be an addressable api.smartrecruiters.com posting: the fetcher
    refuses anything else before calling _detail_json, so a placeholder URL here
    would make this pass without exercising the stub at all."""
    monkeypatch.setattr("job_intel.ats_sources._detail_json", lambda url, **kw: {"id": "1"})
    assert fetch_smartrecruiters_detail(SR_API_URL) is None


def test_smartrecruiters_returns_none_when_the_posting_is_permanently_absent(monkeypatch):
    """None from _detail_json means 404/410 -- the posting is gone. It maps to
    the TERMINAL `unavailable` state. A transport failure is a different thing
    and returns DETAIL_TRANSIENT instead; see
    tests/job_intel/test_text_backfill_transport_taxonomy.py. This test was
    once named "..._on_transport_failure", which conflated the two."""
    monkeypatch.setattr("job_intel.ats_sources._detail_json", lambda url, **kw: None)
    assert fetch_smartrecruiters_detail(SR_API_URL) is None

from job_intel.ats_sources import fetch_teamtailor_detail


def test_teamtailor_reads_the_jobposting_description(monkeypatch):
    html = (FIXTURES / "teamtailor_detail.html").read_text()
    monkeypatch.setattr("job_intel.ats_sources._detail_html", lambda url, **kw: html)
    text = fetch_teamtailor_detail("https://acme.teamtailor.com/jobs/1")
    assert "own the product roadmap" in text
    assert "<p>" not in text


def test_teamtailor_returns_none_without_a_jobposting(monkeypatch):
    monkeypatch.setattr("job_intel.ats_sources._detail_html",
                        lambda url, **kw: "<html><body>nothing here</body></html>")
    assert fetch_teamtailor_detail("https://acme.teamtailor.com/jobs/1") is None


def test_teamtailor_decodes_the_ampersand_entity(monkeypatch):
    """P&amp;L in the JobPosting description must come back as P&L, not
    leak the raw entity into the stored text."""
    html_fixture = (FIXTURES / "teamtailor_detail.html").read_text()
    monkeypatch.setattr("job_intel.ats_sources._detail_html", lambda url, **kw: html_fixture)
    text = fetch_teamtailor_detail("https://acme.teamtailor.com/jobs/1")
    assert "P&L" in text
    assert "&amp;" not in text


from job_intel.ats_sources import _clean_html_text, _json_ld_objects


def test_clean_html_text_decodes_named_and_numeric_entities():
    assert _clean_html_text("<p>P&amp;L</p>") == "P&L"
    assert _clean_html_text("It&#39;s here") == "It's here"
    assert _clean_html_text("&quot;great&quot; role") == '"great" role'


def test_clean_html_text_nbsp_is_collapsed_to_a_plain_space():
    """&nbsp; decodes to U+00A0 (non-breaking space). Python's \\s+ *does*
    match U+00A0 for str patterns (unlike some other regex flavours), so the
    real, verified behaviour is that it gets collapsed into a normal ASCII
    space by the whitespace-collapse step that runs after unescaping -- not
    that it survives as a lingering U+00A0."""
    result = _clean_html_text("a&nbsp;b")
    assert result == "a b"
    assert "\u00a0" not in result


def test_json_ld_objects_matches_a_real_ld_json_script_tag():
    html_doc = (
        '<html><head><script type="application/ld+json">'
        '{"@type": "JobPosting", "title": "X"}'
        '</script></head><body></body></html>'
    )
    objects = _json_ld_objects(html_doc)
    assert objects == [{"@type": "JobPosting", "title": "X"}]



# One literal backslash character, spelled without a backslash-escaped
# string literal so nothing here is at risk of the same double-escaping
# mistake the regex bug itself was.
BACKSLASH = chr(92)


def test_json_ld_objects_does_not_match_a_backslash_typo_in_the_type_attr():
    """Pins the regex bug this task fixed. The old source read
    r'...application/ld\\+json...' -- two literal backslashes in the raw
    string compiled to a regex requiring one-or-more literal backslash
    characters before "json" (no "+" involved at all), which a real
    `type="application/ld+json"` attribute never contains. The fixture
    below is built with chr(92) rather than a backslash-escaped string
    literal, so the byte count is unambiguous: exactly one backslash,
    no plus sign -- text that the *old* buggy pattern would have matched
    (1+ backslashes then "json") but the *fixed* pattern (needs a literal
    "+") must not."""
    typo_html = (
        '<html><head><script type="application/ld' + BACKSLASH + 'json">'
        '{"@type": "JobPosting", "title": "X"}'
        '</script></head><body></body></html>'
    )
    assert typo_html.count(BACKSLASH) == 1  # sanity: exactly one backslash byte
    assert "+json" not in typo_html  # sanity: no plus sign present
    assert _json_ld_objects(typo_html) == []
