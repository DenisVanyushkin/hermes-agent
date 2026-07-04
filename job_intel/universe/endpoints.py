"""D3: probe supported ATS tenant endpoints for a candidate slug.

Personio is deliberately excluded (endpoint blocked from the VPS). The caller
enforces the total probe budget; this module only rate-limits per company.
"""
from __future__ import annotations

import requests

from .models import CandidateCompany

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"}
_TIMEOUT = 20

_PATTERNS: list[tuple[str, str, str]] = [
    # (ats_type, url_template, validation: "json" | text marker)
    ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", "json"),
    ("lever", "https://api.lever.co/v0/postings/{slug}?mode=json", "json"),
    ("ashby", "https://api.ashbyhq.com/posting-api/job-board/{slug}", "json"),
    # SR answers 200 + valid JSON with totalFound=0 for ANY slug, so an empty
    # tenant must not count as a hit.
    ("smartrecruiters", "https://api.smartrecruiters.com/v1/companies/{slug}/postings", "sr_nonempty"),
    ("recruitee", "https://{slug}.recruitee.com/api/offers/", "json"),
    ("teamtailor", "https://{slug}.teamtailor.com/jobs", "teamtailor"),
]


def probe_ats(slug: str, *, session: requests.Session | None = None) -> tuple[str, str] | None:
    s = session or requests.Session()
    for ats_type, template, validation in _PATTERNS:
        url = template.format(slug=slug)
        try:
            resp = s.get(url, headers=_UA, timeout=_TIMEOUT)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        if validation == "json":
            try:
                resp.json()
            except ValueError:
                continue
        elif validation == "sr_nonempty":
            try:
                payload = resp.json()
            except ValueError:
                continue
            if not (isinstance(payload, dict) and payload.get("totalFound", 0) >= 1):
                continue
        elif validation not in resp.text.lower():
            continue
        return ats_type, url
    return None


def apply_probe(c: CandidateCompany, *, session: requests.Session | None = None) -> None:
    hit = probe_ats(c.slug, session=session)
    if hit is None:
        c.add_reason("no_endpoint", "no supported ATS endpoint responded")
        return
    c.ats_type, c.endpoint_url = hit
    c.add_reason("supported_ats", f"{c.ats_type}: {c.endpoint_url}")
