from job_intel.universe.models import CandidateCompany, REASONS, normalize_slug
from job_intel.universe.anchors import (
    BEHAVIORAL_ANCHORS, NEGATIVE_TITLE_RE, load_editorial_anchors,
    load_anchor_similar, EXPLORATION_QUOTA,
)


def test_reason_codes_closed_set():
    assert REASONS == {
        "thesis_fit", "fintech_payments_fit", "geo_fit", "supported_ats",
        "senior_product_titles", "positive_anchor_similarity",
        "no_endpoint", "browser_required", "low_relevance", "reputation_risk",
    }


def test_candidate_defaults_and_slug():
    c = CandidateCompany(name="Checkout.com")
    assert c.slug == "checkout-com"
    assert c.sources == [] and c.reasons == [] and c.bucket is None


def test_normalize_slug():
    assert normalize_slug("  Adyen N.V. ") == "adyen-n-v"


def test_behavioral_anchors_and_quota():
    assert BEHAVIORAL_ANCHORS == ["wise", "airwallex"]
    assert EXPLORATION_QUOTA == 0.30


def test_negative_titles_suppressed():
    for t in ["Head of Product Design", "Product Marketing Manager",
              "Senior PMM", "Product Designer"]:
        assert NEGATIVE_TITLE_RE.search(t), t
    assert not NEGATIVE_TITLE_RE.search("VP Product, Payments")


def test_anchor_files_load():
    assert isinstance(load_editorial_anchors(), list)
    similar = load_anchor_similar()
    assert isinstance(similar, dict)
    assert all(isinstance(v, list) for v in similar.values())


from job_intel.universe.buckets import classify


def _c(*reasons):
    c = CandidateCompany(name="X")
    for r in reasons:
        c.add_reason(r)
    return c


def test_reputation_risk_always_rejects():
    assert classify(_c("reputation_risk", "supported_ats", "thesis_fit",
                       "geo_fit", "senior_product_titles")) == "reject"


def test_no_endpoint_holds():
    assert classify(_c("thesis_fit", "geo_fit", "no_endpoint")) == "hold"
    assert classify(_c("thesis_fit", "browser_required")) == "hold"


def test_strong_candidate_requires_full_deterministic_stack():
    assert classify(_c("supported_ats", "geo_fit", "fintech_payments_fit",
                       "senior_product_titles")) == "strong_candidate"


def test_anchor_similarity_alone_caps_at_maybe():
    assert classify(_c("positive_anchor_similarity")) == "maybe"
    assert classify(_c("positive_anchor_similarity", "supported_ats")) == "maybe"


def test_candidate_needs_ats_plus_two_signals():
    assert classify(_c("supported_ats", "thesis_fit", "geo_fit")) == "candidate"


def test_empty_rejects_with_low_relevance():
    c = CandidateCompany(name="Y")
    assert classify(c) == "reject"
    assert "low_relevance" in c.reasons


def test_candidate_requires_thesis_or_domain_signal():
    # udacity-like case: ATS + geo + senior titles but no thesis/domain -> maybe
    assert classify(_c("supported_ats", "geo_fit", "senior_product_titles")) == "maybe"
    assert classify(_c("supported_ats", "senior_product_titles",
                       "fintech_payments_fit")) == "candidate"
