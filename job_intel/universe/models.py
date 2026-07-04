"""Candidate model and closed reason-code set for the company universe (MVP-0).

MVP-0 is read-only and deterministic by design (scope choice, not an
architectural limitation): buckets with reason codes, no 0-100 scores.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

Bucket = Literal["strong_candidate", "candidate", "maybe", "hold", "reject"]

REASONS: frozenset[str] = frozenset({
    "thesis_fit", "fintech_payments_fit", "geo_fit", "supported_ats",
    "senior_product_titles", "positive_anchor_similarity",
    "no_endpoint", "browser_required", "low_relevance", "reputation_risk",
})


def normalize_slug(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.strip().lower())).strip("-")


@dataclass
class CandidateCompany:
    name: str
    slug: str = ""
    domain: Optional[str] = None
    sources: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    senior_titles: list[str] = field(default_factory=list)
    ats_type: Optional[str] = None
    endpoint_url: Optional[str] = None
    dry_run_vacancies: int = -1  # -1 = not attempted
    dry_run_sample_titles: list[str] = field(default_factory=list)
    bucket: Optional[Bucket] = None

    def __post_init__(self) -> None:
        if not self.slug:
            self.slug = normalize_slug(self.name)

    def add_reason(self, reason: str, evidence: str = "") -> None:
        if reason not in REASONS:
            raise ValueError(f"unknown reason code: {reason}")
        if reason not in self.reasons:
            self.reasons.append(reason)
        if evidence:
            self.evidence.append(evidence)
