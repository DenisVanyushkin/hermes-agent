"""Owner-authorized selection boundaries for the legacy daily path.

This module is deliberately a gate, not a score adjustment.  A boundary is
only a rejection when the vacancy carries the corresponding evidence; absent
industry or eligibility evidence is returned as an explicit unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from html import unescape
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .models import Evaluation, Vacancy

REASON_GAMING = "gaming_experience_mismatch"
REASON_ADTECH_CTV = "adtech_ctv_experience_mismatch"
REASON_TECHNICAL_PRODUCT = "technical_product_scope_mismatch"
REASON_CRYPTO = "crypto_industry_mismatch"
REASON_RUSSIA = "russia_employer_country"
REASON_BELOW_EXECUTIVE_SCOPE = "below_minimum_executive_scope"
REASON_COMPANY_BLACKLIST = "company_blacklist"
REASON_WORK_AUTHORIZATION = "work_authorization_mismatch"

UNKNOWN_INDUSTRY_CONTEXT = "industry_context_unknown"
UNKNOWN_WORK_AUTHORIZATION = "work_authorization_unknown"
UNKNOWN_EXECUTIVE_SCOPE = "executive_scope_unknown"

AUTHORITY_PATH = (
    Path(__file__).resolve().parents[1]
    / "config/product_search/work_authorization.v1.yaml"
)
MIN_REAL_DESCRIPTION_CHARS = 200

# Independently supported identity evidence comes from retained control
# captures for the four control companies and from domain-level identity for
# major crypto exchanges.  This set is not populated from run-489 replay
# candidates; other title-only companies remain industry-unknown.
_INDEPENDENT_GAMING_COMPANY_KEYS = frozenset(
    {"tripledotstudios", "sonyinteractiveentertainment"}
)
_INDEPENDENT_ADTECH_COMPANY_KEYS = frozenset({"ogury"})
_INDEPENDENT_CRYPTO_COMPANY_KEYS = frozenset(
    {"okx", "coinbase", "binance", "kraken", "bybit", "cryptocom"}
)
_INDEPENDENT_INDUSTRY_COMPANY_KEYS = frozenset(
    {
        *_INDEPENDENT_GAMING_COMPANY_KEYS,
        *_INDEPENDENT_ADTECH_COMPANY_KEYS,
        *_INDEPENDENT_CRYPTO_COMPANY_KEYS,
    }
)


@dataclass(frozen=True)
class BoundaryAssessment:
    rejection_reasons: tuple[str, ...] = ()
    unknown_reasons: tuple[str, ...] = ()


def _normalized(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").casefold()).strip()


def _decoded_job_text(value: str | None) -> str:
    """Normalize stored HTML-escaped descriptions for matching only."""
    decoded = value or ""
    for _ in range(3):
        unescaped = unescape(decoded)
        if unescaped == decoded:
            break
        decoded = unescaped
    decoded = re.sub(r"<[^>]+>", " ", decoded)
    return _normalized(decoded)


def canonical_company_key(value: str | None) -> str:
    """Match the store's company identity rule without importing store.py."""
    return "".join(char for char in _normalized(value) if char.isalnum())


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    return any(needle in text for needle in needles)


def _industry_context(vacancy: Vacancy) -> str:
    # Title is intentionally excluded.  A title such as "VP Product, Sports"
    # cannot make an industry boundary fire without page/company context.
    return _normalized(" ".join((vacancy.company or "", _decoded_job_text(vacancy.description))))


def has_real_job_text(vacancy: Vacancy) -> bool:
    """Return whether description evidence is more than a listing title.

    Source adapters commonly use the title as a placeholder when detail text
    was not fetched.  A conservative length floor separates that placeholder
    from a usable description; persisted LinkedIn detail provenance is an
    explicit exception because it records a successful detail observation.
    Short or title-derived text is therefore insufficient evidence, not a
    rejection signal.
    """
    metadata = vacancy.metadata if isinstance(vacancy.metadata, dict) else {}
    if isinstance(metadata.get("linkedin_detail_enrichment"), dict):
        return True
    description = _decoded_job_text(vacancy.description)
    title = _normalized(vacancy.title)
    return bool(
        len(description) >= MIN_REAL_DESCRIPTION_CHARS
        and description not in title
    )


def _industry_context_is_available(vacancy: Vacancy) -> bool:
    if has_real_job_text(vacancy):
        return True
    # A company identity can itself be evidence for a narrow industry, but an
    # arbitrary company name must not turn title-only text into a rejection.
    company = canonical_company_key(vacancy.company)
    return company in _INDEPENDENT_INDUSTRY_COMPANY_KEYS


def _has_gaming_context(vacancy: Vacancy) -> bool:
    context = _industry_context(vacancy)
    company = canonical_company_key(vacancy.company)
    if company in _INDEPENDENT_GAMING_COMPANY_KEYS:
        return True
    if not _contains_any(
        context,
        (
            "video game",
            "video games",
            "game studio",
            "game development",
            "game industry",
            "game publisher",
            "live service game",
            "mobile games",
            "gaming business",
            "gaming and esports",
            "gaming & esports",
        ),
    ):
        return False
    # Sports, betting and a generic mention of gaming in a broader media
    # company are not enough.  Require a product/development/business sense.
    return bool(
        re.search(
            r"\b(?:game|games|gaming)\b.{0,90}\b(?:product|studio|development|industry|publisher|players?|title|business|esports)\b"
            r"|\b(?:product|studio|development|industry|publisher|players?|title|business|esports)\b.{0,90}\b(?:game|games|gaming)\b",
            context,
        )
    )


def _has_adtech_or_ctv_context(vacancy: Vacancy) -> bool:
    context = _industry_context(vacancy)
    company = canonical_company_key(vacancy.company)
    if company in _INDEPENDENT_ADTECH_COMPANY_KEYS:
        return True
    return bool(
        re.search(
            r"\b(?:connected tv|connected television|ctv)\b|"
            r"\b(?:ott|over[- ]the[- ]top)[ -](?:advertising|ads?|ad inventory)\b|"
            r"\bvideo (?:advertising|ads?|inventory)\b",
            context,
        )
    )


def _has_crypto_context(vacancy: Vacancy) -> bool:
    context = _industry_context(vacancy)
    company = canonical_company_key(vacancy.company)
    return company in _INDEPENDENT_CRYPTO_COMPANY_KEYS or bool(
        re.search(r"\bcrypto(?:currency)?\b|\bblockchain\b|\bweb3\b|\bdigital assets?\b|\bdefi\b", context)
    )


def _has_technical_product_context(vacancy: Vacancy) -> bool:
    context = _industry_context(vacancy)
    if _contains_any(
        context,
        (
            "technical product manager",
            "platform engineering",
            "engineering platform",
            "developer platform",
            "internal tools",
            "internal infrastructure",
            "cloud infrastructure",
            "it product/service organisation",
            "it product/service organization",
            "api-first",
        ),
    ):
        return True
    return bool(re.search(r"\bit product(?:/service)? organisations?\b", context))


def _has_material_executive_scope(vacancy: Vacancy) -> bool:
    description = _decoded_job_text(vacancy.description)
    return bool(
        re.search(
            r"\b(?:p&l|profit and loss|business line|business unit|portfolio|multi-team|"
            r"product organisation|product organization|product teams?|direct reports?|"
            r"revenue|margin|commercial (?:success|outcomes?)|end-to-end business)\b",
            description,
        )
    )


def _is_below_minimum_scope(vacancy: Vacancy) -> bool | None:
    title = _normalized(vacancy.title)
    if re.search(r"\b(?:chief|cpo|vp|vice president|director|head|gm|general manager)\b", title):
        return False
    if not re.search(r"\b(?:growth )?product lead\b|\blead product manager\b", title):
        return False
    if not has_real_job_text(vacancy):
        return None
    return not _has_material_executive_scope(vacancy)


def _country_from_location(location: str | None) -> str | None:
    text = _normalized(location)
    patterns = (
        ("kazakhstan", (r"\bkazakhstan\b", "алматы", "астана", "нур-султан")),
        ("russia", (r"\brussia\b", r"\brussian federation\b", "москва", r"\bmoscow\b", "санкт-петербург")),
        ("united states", (r"\bunited states\b", r"\bu\.s\.\b", r"\busa\b", r"\bus remote\b")),
        ("canada", (r"\bcanada\b", r"\btoronto\b", r"\bmontreal\b", r"\bquebec\b")),
        ("new zealand", (r"\bnew zealand\b", r"\btauranga\b", r"\bauckland\b")),
        ("australia", (r"\baustralia\b", r"\bsydney\b", r"\bmelbourne\b", r"\bnsw\b")),
        ("united kingdom", (r"\bunited kingdom\b", r"\buk\b", r"\blondon\b", r"\bmanchester\b")),
    )
    for country, needles in patterns:
        if any(
            re.search(needle, text) if needle.startswith("\\b") else needle in text
            for needle in needles
        ):
            return country
    return None


@lru_cache(maxsize=4)
def _authorized_without_sponsorship(path: str) -> frozenset[str]:
    payload: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    countries = payload.get("authorized_without_sponsorship_countries")
    if not isinstance(countries, list) or not all(isinstance(item, str) for item in countries):
        raise ValueError("work authorization authority must list countries")
    return frozenset(_normalized(item) for item in countries)


def _work_authorization_state(vacancy: Vacancy) -> str:
    country = _country_from_location(vacancy.location)
    if country is None:
        return "unknown"
    authorized = _authorized_without_sponsorship(str(AUTHORITY_PATH))
    if country in authorized:
        return "authorized"
    if not has_real_job_text(vacancy):
        return "unknown"
    text = _normalized(" ".join((vacancy.location or "", _decoded_job_text(vacancy.description))))
    explicit_local_rights = bool(
        re.search(
            r"\b(?:must|required|requirement|requires)\b.{0,90}\b(?:legal )?(?:right|rights|work authorization|work authorisation|authorized to work|authorised to work)\b"
            r"|\bask that you have\b.{0,90}\b(?:legal )?(?:right|rights|work authorization|work authorisation|authorized to work|authorised to work)\b"
            r"|\b(?:legal )?(?:right|rights)\b.{0,70}\b(?:live and work|reside|work authorization|work authorisation)\b",
            text,
        )
    )
    no_path = bool(
        re.search(
            r"\b(?:unable|cannot|can't|do not|don't|no)\b.{0,80}\b(?:sponsor|sponsorship|visa)\b|"
            r"\bwithout (?:visa )?sponsorship\b|\bno visa sponsorship\b",
            text,
        )
    )
    # IPSY-style state-limited US remote hiring is an explicit country-
    # eligibility gate even when the page phrases it as a list of states.
    us_state_limited = country == "united states" and bool(
        re.search(r"\b(?:remote|hire|eligible)\b.{0,100}\b(?:states?|authorized to hire)\b", text)
    )
    if explicit_local_rights or no_path or us_state_limited:
        return "explicit_mismatch"
    return "unknown"


def assess_selection_boundaries(
    vacancy: Vacancy,
    *,
    blacklisted_company_keys: Iterable[str] = (),
) -> BoundaryAssessment:
    reasons: list[str] = []
    unknowns: list[str] = []
    company_key = canonical_company_key(vacancy.company)
    blacklist = {
        canonical_company_key(item)
        for item in blacklisted_company_keys
    }
    if company_key and company_key in blacklist:
        reasons.append(REASON_COMPANY_BLACKLIST)
        entry = (
            blacklisted_company_keys.get(company_key)
            if isinstance(blacklisted_company_keys, Mapping)
            else None
        )
        if isinstance(entry, Mapping):
            origin = str(entry.get("origin") or "").strip()
            if origin == "auto_threshold":
                count = entry.get("negative_event_count")
                reasons.append(f"{REASON_COMPANY_BLACKLIST}:auto_threshold:{count}")
            elif origin == "explicit":
                reasons.append(f"{REASON_COMPANY_BLACKLIST}:explicit")

    scope = _is_below_minimum_scope(vacancy)
    if scope is True:
        reasons.append(REASON_BELOW_EXECUTIVE_SCOPE)
    elif scope is None:
        unknowns.append(UNKNOWN_EXECUTIVE_SCOPE)

    if _country_from_location(vacancy.location) == "russia":
        reasons.append(REASON_RUSSIA)

    context = _industry_context(vacancy)
    if _industry_context_is_available(vacancy):
        if _has_gaming_context(vacancy):
            reasons.append(REASON_GAMING)
        if _has_adtech_or_ctv_context(vacancy):
            reasons.append(REASON_ADTECH_CTV)
        if _has_technical_product_context(vacancy):
            reasons.append(REASON_TECHNICAL_PRODUCT)
        if _has_crypto_context(vacancy):
            reasons.append(REASON_CRYPTO)
    else:
        unknowns.append(UNKNOWN_INDUSTRY_CONTEXT)

    auth_state = _work_authorization_state(vacancy)
    if auth_state == "explicit_mismatch":
        reasons.append(REASON_WORK_AUTHORIZATION)
    elif auth_state == "unknown":
        unknowns.append(UNKNOWN_WORK_AUTHORIZATION)

    return BoundaryAssessment(
        rejection_reasons=tuple(dict.fromkeys(reasons)),
        unknown_reasons=tuple(dict.fromkeys(unknowns)),
    )


def boundary_rejection_evaluation(vacancy: Vacancy, reasons: Iterable[str]) -> Evaluation:
    named_reasons = list(dict.fromkeys(reasons))
    return Evaluation(
        score=0,
        tier="reject",
        recommendation="reject",
        reasons=named_reasons,
        concerns=named_reasons,
        raw_breakdown={reason: -100 for reason in named_reasons},
    )
