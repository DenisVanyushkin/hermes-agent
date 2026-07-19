"""Deterministic extraction baseline — pure, replayable, no network, no LLM.

Only facts with an explicit textual or structured basis are extracted here.
Semantic fields (scope_breadth, platform_as_business, revenue_proximity,
digital_business_ownership, mandate_summary, transferability, …) are
DELIBERATELY left unknown: they require semantic extraction, which is a
future, separately approved slice (see vacancy-understanding-extraction-plan).

The extractor must never:
- guess (missing data stays unknown, not false);
- set executive scope from a title alone with high confidence;
- resolve sanctioned/unstable from free text (delegated to country_groups).

A provider interface for semantic extraction may exist as an extension point
only (see SemanticExtractorProtocol) — disabled and unused in Step 2.
"""
from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime
from typing import Optional, Protocol

from pydantic import BaseModel, ConfigDict

from job_intel.vacancy_understanding.country_groups import (
    RESOLVER_VERSION,
    resolve_country_group,
)
from job_intel.vacancy_understanding.model import (
    SCHEMA_VERSION,
    BoolFact,
    CompanyFacts,
    Confidence,
    CountryGroup,
    Evidence,
    EvidenceSourceType,
    ExtractionDiagnostics,
    ExtractionMethod,
    Fact,
    FeasibilityFacts,
    IntFact,
    LanguageRequirement,
    ManagementLevel,
    Mandate,
    Metadata,
    RelocationSupport,
    Risk,
    RiskKind,
    RoleIdentity,
    SourceDocument,
    SponsorshipStated,
    StrFact,
    TitleFamily,
    TriState,
    VacancyUnderstanding,
    WorkFormat,
    Requirements,
)

EXTRACTOR_VERSION = "0.1.1"

_SRC_TEXT = "src_vacancy_text"
_SRC_STRUCT = "src_structured_fields"

MIN_MEANINGFUL_TEXT = 200  # below this the description is just a title echo


class RawVacancy(BaseModel):
    """Input snapshot — mirrors the stored vacancy row, no live DB access."""

    model_config = ConfigDict(extra="forbid")

    vacancy_key: str
    source_system: str
    source_record_id: Optional[str] = None
    company: str
    title: str
    location: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = None


class SemanticExtractorProtocol(Protocol):  # future extension point — UNUSED
    def extract_semantic(self, raw: RawVacancy, base: VacancyUnderstanding) -> VacancyUnderstanding: ...


def _clean(text: str) -> str:
    text = html.unescape(html.unescape(text))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _ev(source_id: str, excerpt: str, location: str, source_type=EvidenceSourceType.vacancy_text) -> Evidence:
    return Evidence(source_id=source_id, source_type=source_type, excerpt=excerpt[:400], location=location)


def _window(text: str, match: re.Match, radius: int = 80) -> str:
    return text[max(0, match.start() - radius): match.end() + radius].strip()


# --- title -----------------------------------------------------------------

_FAMILY_PATTERNS: list[tuple[TitleFamily, str]] = [
    (TitleFamily.sales, r"\bsales\b|account executive"),
    (TitleFamily.finance, r"fp&a|\bfinance\b|financial analyst|accounting|controller\b"),
    (TitleFamily.project_delivery, r"project manager|delivery manager|program(me)? manager|technical program"),
    (TitleFamily.engineering, r"\bengineer(ing)?\b|\bsre\b|site reliability|\bdevops\b"),
    (TitleFamily.growth, r"\bgrowth\b|\bexpansion\b"),
    (TitleFamily.general_management, r"general manager|\bgm\b|managing director|country manager"),
    (TitleFamily.commercial, r"commercial|monetization|monetisation|revenue\b"),
    (TitleFamily.strategy, r"\bstrategy\b|strategic planning"),
    (TitleFamily.operations, r"\boperations\b|\bops\b"),
    (TitleFamily.product, r"\bproduct\b|\bcpo\b"),
]

_LEVEL_PATTERNS: list[tuple[ManagementLevel, str]] = [
    (ManagementLevel.c_level, r"\bchief\b|\bcpo\b|\bcoo\b|\bceo\b|\bcfo\b"),
    (ManagementLevel.head_vp, r"\bhead of\b|\bvp\b|vice president"),
    (ManagementLevel.director, r"\bdirector\b"),
    (ManagementLevel.senior_manager, r"senior manager|group product manager|principal\b|staff\b|\blead\b"),
    (ManagementLevel.manager, r"\bmanager\b"),
]


def _title_families(title: str) -> list[TitleFamily]:
    found = [fam for fam, pat in _FAMILY_PATTERNS if re.search(pat, title, re.I)]
    return found or [TitleFamily.unknown]


def _management_level(title: str) -> Fact[ManagementLevel]:
    for level, pat in _LEVEL_PATTERNS:
        m = re.search(pat, title, re.I)
        if m:
            # Title is evidence, not truth: confidence is capped at medium.
            return Fact[ManagementLevel](
                value=level,
                confidence=Confidence.medium,
                method=ExtractionMethod.deterministic_derivation,
                evidence=[_ev(_SRC_STRUCT, title, "title", EvidenceSourceType.structured_source_field)],
            )
    return Fact[ManagementLevel](value=ManagementLevel.unknown)


# --- feasibility -----------------------------------------------------------

_REMOTE_LOC = re.compile(r"\bremote\b", re.I)
_ONSITE_LOC = re.compile(r"on-?site", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)
_OFFICE_DAYS = re.compile(r"(in|at)\s+the\s+office\s+at\s+least\s+\d+\s+days?", re.I)
_REMOTE_FIRST = re.compile(r"remote-first", re.I)

_SPONSOR_NO = re.compile(
    r"(visa\s+)?sponsorship\s+is\s+not\s+available|not\s+able\s+to\s+sponsor|"
    r"cannot\s+sponsor|unable\s+to\s+sponsor|do(es)?\s+not\s+(currently\s+)?"
    r"(offer|provide)\s+(visa\s+)?sponsorship|without\s+.{0,20}sponsorship\s+of\s+a\s+visa|"
    r"do\s+not\s+require\s+.{0,30}sponsorship\s+of\s+a\s+visa",
    re.I,
)
_SPONSOR_YES = re.compile(
    r"we\s+(can|do|will)\s+sponsor|visa\s+sponsorship\s+(is\s+)?(available|provided|offered)|"
    r"sponsorship\s+(and|&)?\s*relocation\s+(support|package|assistance)|provide\s+visa\s+sponsorship",
    re.I,
)
_RELOC = re.compile(
    r"relocat(e|ion)\s+(to|package|support|assistance|bonus)|help\s+you\s+relocate|"
    r"relocation\s+is\s+(provided|supported)",
    re.I,
)
_ALREADY_AUTH = re.compile(
    r"current\s+right\s+to\s+work|must\s+(already\s+)?(be|have)\s+(authorized|authorised|the\s+right)\s+to\s+work|"
    r"work\s+authorization\s+required",
    re.I,
)

_LANG = re.compile(
    r"fluen(t|cy)\s+in\s+(both\s+)?(?P<langs>[A-Za-z]+(?:(?:\s+and\s+|\s*,\s*|\s+или\s+)[A-Za-z]+)*)"
    r"(\s+chinese)?",
    re.I,
)

_YEARS = re.compile(r"(?P<years>\d{1,2})\s*\+?\s*years?\s+(of\s+)?(relevant\s+|professional\s+)?experience", re.I)
_PNL_EXPLICIT = re.compile(r"(own(ership)?\s+(of\s+)?(the\s+)?p&l|full\s+p&l|p&l\s+ownership)", re.I)
_TEAM_OF = re.compile(r"team\s+of\s+(?P<n>\d{1,4})", re.I)
_REPORTS_TO = re.compile(r"report(s|ing)?\s+(directly\s+)?to\s+(the\s+)?(?P<who>[A-Z][A-Za-z &/]{2,40})")

_KZ_CITIES = {"almaty", "astana", "shymkent", "karaganda"}


def _split_location(location: str) -> tuple[Optional[str], Optional[str]]:
    """Best-effort deterministic (city, country) from a location string."""
    loc = re.sub(r"\(.*?\)", "", location).strip()
    loc = re.sub(r"\bremote\b[\s:-]*", "", loc, flags=re.I).strip(" ,-")
    if not loc:
        return None, None
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    if len(parts) == 1:
        token = parts[0]
        # "SG - Singapore" / "US - San Francisco" style
        m = re.match(r"^[A-Z]{2}\s*-\s*(.+)$", token)
        if m:
            token = m.group(1).strip()
        if resolve_country_group(token).group != CountryGroup.other or token.lower() in {
            "singapore", "united states", "usa", "kazakhstan",
        }:
            return None, token
        return token, None
    return parts[0], parts[-1]


_US_STATE_HINT = re.compile(
    r"\b(CA|NY|WA|TX|FL|IN|OH|OK|California|New York|Washington|Seattle|San Francisco)\b"
)


def _country_from_location(location: str) -> Optional[str]:
    _, country = _split_location(location)
    if country:
        norm = country.strip()
        if norm.upper() in {"US", "USA"} or "united states" in norm.lower():
            return "United States"
        return norm
    if _US_STATE_HINT.search(location) or re.search(r"\bUSA?\b", location):
        return "United States"
    return None


# --- main entry ------------------------------------------------------------

def extract(raw: RawVacancy, *, created_at: datetime) -> VacancyUnderstanding:
    """Pure deterministic extraction. Same input → same output.

    ``created_at`` is REQUIRED and must come from the caller (e.g. the source
    row's observation timestamp): the extractor contains no wall-clock reads,
    so identical input — including created_at — always yields an identical
    canonical record (replay/cache-key guarantee).
    """
    text = _clean(raw.description or "")
    location = raw.location or ""
    loc_ev = _ev(_SRC_STRUCT, location, "location", EvidenceSourceType.structured_source_field)

    registry = [
        SourceDocument(
            id=_SRC_TEXT,
            source_type=EvidenceSourceType.vacancy_text,
            description="normalized vacancy description text",
            content_hash=hashlib.sha256(text.encode()).hexdigest()[:16],
        ),
        SourceDocument(
            id=_SRC_STRUCT,
            source_type=EvidenceSourceType.structured_source_field,
            description="structured source row fields (title/location/company)",
        ),
    ]

    risks: list[Risk] = []
    warnings: list[str] = []

    # role identity ---------------------------------------------------------
    families = _title_families(raw.title)
    role = RoleIdentity(
        raw_title=raw.title,
        normalized_title=re.sub(r"\s+", " ", raw.title).strip(),
        title_families=families,
        function_families=families,
        management_level_observed=_management_level(raw.title),
    )

    # feasibility -----------------------------------------------------------
    feas = FeasibilityFacts()

    country = _country_from_location(location) if location else None
    city, _ = _split_location(location) if location else (None, None)
    if country:
        feas.country = StrFact(
            value=country, confidence=Confidence.high,
            method=ExtractionMethod.deterministic_derivation, evidence=[loc_ev],
        )
    if city:
        feas.city = StrFact(
            value=city, confidence=Confidence.medium,
            method=ExtractionMethod.deterministic_derivation, evidence=[loc_ev],
        )
    resolved = resolve_country_group(country)
    if resolved.group != CountryGroup.unknown:
        feas.country_group = Fact[CountryGroup](
            value=resolved.group, confidence=Confidence.high,
            method=ExtractionMethod.deterministic_derivation, evidence=[loc_ev],
        )
    feas.country_group_resolver_version = RESOLVER_VERSION

    # work format
    wf: Optional[tuple[WorkFormat, Evidence, Confidence]] = None
    if _REMOTE_LOC.search(location):
        wf = (WorkFormat.remote, loc_ev, Confidence.high)
    elif _ONSITE_LOC.search(location):
        wf = (WorkFormat.onsite, loc_ev, Confidence.high)
    if text:
        m = _HYBRID.search(text) or _OFFICE_DAYS.search(text)
        if m:
            wf = (WorkFormat.hybrid, _ev(_SRC_TEXT, _window(text, m), "description"), Confidence.high)
        elif wf is None:
            m2 = _REMOTE_FIRST.search(text)
            if m2:
                wf = (WorkFormat.remote, _ev(_SRC_TEXT, _window(text, m2), "description"), Confidence.medium)
    if wf:
        feas.work_format = Fact[WorkFormat](
            value=wf[0], confidence=wf[2],
            method=ExtractionMethod.explicit_statement, evidence=[wf[1]],
        )

    # sponsorship / relocation / authorization
    if text:
        m = _SPONSOR_NO.search(text)
        if m:
            feas.sponsorship_stated = Fact[SponsorshipStated](
                value=SponsorshipStated.no, confidence=Confidence.high,
                method=ExtractionMethod.explicit_statement,
                evidence=[_ev(_SRC_TEXT, _window(text, m), "description")],
            )
        else:
            m = _SPONSOR_YES.search(text)
            if m:
                feas.sponsorship_stated = Fact[SponsorshipStated](
                    value=SponsorshipStated.yes, confidence=Confidence.high,
                    method=ExtractionMethod.explicit_statement,
                    evidence=[_ev(_SRC_TEXT, _window(text, m), "description")],
                )
        m = _ALREADY_AUTH.search(text)
        if m:
            feas.must_be_already_authorized = BoolFact(
                value=TriState.true, confidence=Confidence.high,
                method=ExtractionMethod.explicit_statement,
                evidence=[_ev(_SRC_TEXT, _window(text, m), "description")],
            )
        for m in _LANG.finditer(text):
            langs = re.split(r"\s+and\s+|\s*,\s*", m.group("langs"), flags=re.I)
            for lang in langs:
                lang = lang.strip().title()
                if lang.lower() in {"both", "either"} or len(lang) < 3:
                    continue
                if any(l.language == lang for l in feas.language_requirements):
                    continue
                feas.language_requirements.append(
                    LanguageRequirement(
                        language=lang, mandatory=TriState.true,
                        evidence=[_ev(_SRC_TEXT, _window(text, m), "description")],
                    )
                )

    # relocation — checked in description AND title (title works even for
    # title-only snapshots); provenance names the real origin field.
    reloc_ev: Optional[Evidence] = None
    m = _RELOC.search(text) if text else None
    if m:
        reloc_ev = _ev(_SRC_TEXT, _window(text, m), "description")
    else:
        mt = _RELOC.search(raw.title)
        if mt:
            reloc_ev = _ev(_SRC_STRUCT, raw.title, "title",
                           EvidenceSourceType.structured_source_field)
    if reloc_ev:
        feas.relocation_support = Fact[RelocationSupport](
            value=RelocationSupport.explicit, confidence=Confidence.high,
            method=ExtractionMethod.explicit_statement,
            evidence=[reloc_ev],
        )

    # KZ local indicator — a factual combination; sponsorship may stay
    # unknown, that is valid (no visa needed to work locally).
    if resolved.group == CountryGroup.kazakhstan or (
        city and city.lower() in _KZ_CITIES
    ):
        feas.local_market_indicator = BoolFact(
            value=TriState.true, confidence=Confidence.medium,
            method=ExtractionMethod.deterministic_derivation, evidence=[loc_ev],
        )

    # mandate (deterministic slice only) ------------------------------------
    mandate = Mandate()
    if text:
        m = _PNL_EXPLICIT.search(text)
        if m:
            mandate.pnl_ownership = BoolFact(
                value=TriState.true, confidence=Confidence.high,
                method=ExtractionMethod.explicit_statement,
                evidence=[_ev(_SRC_TEXT, _window(text, m), "description")],
            )

    # requirements ----------------------------------------------------------
    reqs = Requirements()
    if text:
        m = _YEARS.search(text)
        if m:
            reqs.years_experience_min = IntFact(
                value=int(m.group("years")), confidence=Confidence.high,
                method=ExtractionMethod.explicit_statement,
                evidence=[_ev(_SRC_TEXT, _window(text, m), "description")],
            )

    # organization ----------------------------------------------------------
    from job_intel.vacancy_understanding.model import Organization
    org = Organization()
    if text:
        m = _TEAM_OF.search(text)
        if m:
            org.direct_reports_estimate = IntFact(
                value=int(m.group("n")), confidence=Confidence.medium,
                method=ExtractionMethod.explicit_statement,
                evidence=[_ev(_SRC_TEXT, _window(text, m), "description")],
            )
        m = _REPORTS_TO.search(text)
        if m:
            org.reports_to_title = StrFact(
                value=m.group("who").strip(), confidence=Confidence.medium,
                method=ExtractionMethod.explicit_statement,
                evidence=[_ev(_SRC_TEXT, _window(text, m), "description")],
            )

    # diagnostics / risks ---------------------------------------------------
    if len(text) < MIN_MEANINGFUL_TEXT:
        risks.append(Risk(
            kind=RiskKind.source_text_incomplete,
            note=f"description length {len(text)} < {MIN_MEANINGFUL_TEXT}; "
                 "semantic facts cannot be extracted from this snapshot",
        ))
        warnings.append("source_text_incomplete")
    if (
        feas.country_group.value == CountryGroup.usa
        and feas.work_format.value in (WorkFormat.onsite, WorkFormat.hybrid, WorkFormat.unknown)
        and feas.sponsorship_stated.value == SponsorshipStated.unknown
    ):
        risks.append(Risk(kind=RiskKind.relocation_unclear,
                          note="USA location without any sponsorship statement (factual note only)"))

    understanding = VacancyUnderstanding(
        metadata=Metadata(
            schema_version=SCHEMA_VERSION,
            extractor_version=EXTRACTOR_VERSION,
            created_at=created_at,
            vacancy_key=raw.vacancy_key,
            source_system=raw.source_system,
            source_record_id=raw.source_record_id,
            source_content_hash=hashlib.sha256((raw.description or "").encode()).hexdigest()[:16],
            language=raw.language,
        ),
        role_identity=role,
        mandate=mandate,
        organization=org,
        company=CompanyFacts(name=raw.company),
        feasibility_facts=feas,
        requirements=reqs,
        risks=risks,
        evidence_registry=registry,
        extraction_diagnostics=ExtractionDiagnostics(
            warnings=warnings,
            source_text_length=len(text),
        ),
    )
    return understanding
