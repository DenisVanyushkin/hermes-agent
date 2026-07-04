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
