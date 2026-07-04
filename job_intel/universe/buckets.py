"""Explainable bucket classifier: deterministic evidence dominates.

Priority rules (no weighted scores): risk vetoes first, endpoint gates second,
then deterministic-positive combinations. Anchor similarity alone can never
lift a candidate above `maybe`.
"""
from __future__ import annotations

from .models import Bucket, CandidateCompany

_DETERMINISTIC_POSITIVE = ("thesis_fit", "fintech_payments_fit", "geo_fit",
                           "senior_product_titles")


def classify(c: CandidateCompany) -> Bucket:
    r = set(c.reasons)
    if "reputation_risk" in r or "low_relevance" in r:
        c.bucket = "reject"
        return c.bucket
    if "browser_required" in r or "no_endpoint" in r:
        c.bucket = "hold"
        return c.bucket
    det = [x for x in _DETERMINISTIC_POSITIVE if x in r]
    if "supported_ats" in r:
        if ("geo_fit" in r and "senior_product_titles" in r
                and ("thesis_fit" in r or "fintech_payments_fit" in r)):
            c.bucket = "strong_candidate"
            return c.bucket
        if len(det) >= 2:
            c.bucket = "candidate"
            return c.bucket
    if det or "positive_anchor_similarity" in r or "supported_ats" in r:
        c.bucket = "maybe"
        return c.bucket
    c.add_reason("low_relevance")
    c.bucket = "reject"
    return c.bucket
