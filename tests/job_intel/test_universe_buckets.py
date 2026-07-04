from job_intel.universe.models import CandidateCompany, REASONS, normalize_slug


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
