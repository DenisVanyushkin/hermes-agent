"""Dry-run validation through the production ATS fetchers (read-only).

Fetchers only read HTTP and return vacancies; nothing is stored, scored, or
delivered here.
"""
from __future__ import annotations

import logging
import re

from ..ats_sources import (fetch_ashby, fetch_greenhouse, fetch_lever,
                           fetch_recruitee, fetch_smartrecruiters, fetch_teamtailor)
from .models import CandidateCompany

log = logging.getLogger(__name__)

_DEFAULT_FETCHERS = {
    "greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters, "recruitee": fetch_recruitee,
    "teamtailor": fetch_teamtailor,
}
_MAX_JOBS = 25  # dry-run cap: enough to sample, cheap on the tenant

# Prefer thesis-like samples over the first returned vacancy: a title counts
# as product-leadership when it carries both a seniority and a product/platform marker.
_SENIORITY_RE = re.compile(r"\b(head|director|vp|vice president|chief|lead|principal)\b", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"\b(product|platform)\b", re.IGNORECASE)


def _is_product_leadership(title: str) -> bool:
    return bool(_SENIORITY_RE.search(title) and _DOMAIN_RE.search(title))


def dry_run_candidate(c: CandidateCompany, *, fetchers=None) -> None:
    table = _DEFAULT_FETCHERS if fetchers is None else fetchers
    fetcher = table.get(c.ats_type or "")
    if fetcher is None:
        return  # dry_run_vacancies stays -1 (not attempted)
    try:
        result = fetcher([], companies=[c.slug], max_jobs_per_company=_MAX_JOBS)
    except Exception as exc:  # noqa: BLE001 — probe must never break the report run
        log.warning("universe dry-run failed for %s: %s", c.slug, exc)
        c.dry_run_vacancies = 0
        return
    c.dry_run_vacancies = len(result.vacancies)
    titles = [v.title for v in result.vacancies]
    product_titles = [t for t in titles if _is_product_leadership(t)]
    c.dry_run_product_sample = bool(product_titles)
    c.dry_run_sample_titles = (product_titles or titles)[:3]
