from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from .config import DEFAULT_CONFIG, load_config_bundle
from .models import Vacancy
from .runtime import retry_with_backoff, sha256_text
from .sources import BOARD_LABELS
from .store import JobIntelStore

EXECUTIVE_TITLE_HINTS = (
    "vp",
    "vice president",
    "director",
    "head of",
    "chief",
    "cpo",
    "gm",
    "general manager",
)

EXECUTIVE_EXPANSION_HINTS = (
    "product transformation",
    "monetization",
    "growth",
    "platform",
    "ecosystem",
    "subscription",
    "fintech",
    "b2c",
)

CAREER_LINK_HINTS = (
    "career",
    "careers",
    "jobs",
    "open roles",
    "openings",
    "positions",
    "join us",
    "join our team",
    "work with us",
)

HOMEPAGE_SIGNAL_HINTS = {
    "hiring_activity": ("hiring", "open roles", "join our team", "careers", "jobs"),
    "growth_signal": ("launch", "launched", "expansion", "expanded", "new market", "new country", "growth", "scale"),
    "funding_signal": ("funding", "raised", "series a", "series b", "series c", "seed round", "investment round", "backed by"),
    "leadership_change": ("appointed", "joined as", "hired as", "new ceo", "new cpo", "new cto", "leadership team"),
    "org_transformation": ("transformation", "restructure", "operating model", "platform", "monetization", "organization"),
}


@dataclass(frozen=True)
class TargetCompany:
    name: str
    category: str
    website: str
    career_paths: tuple[str, ...] = ()
    priority: str = "high"


@dataclass(frozen=True)
class CompanyMonitoringResult:
    vacancies: list[Vacancy] = field(default_factory=list)
    company_statuses: dict[str, dict[str, Any]] = field(default_factory=dict)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str, str]] = []
        self._capture_text = False
        self._current_href = ""
        self._current_text: list[str] = []
        self._title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.text_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag == "a" and attributes.get("href"):
            self._capture_text = True
            self._current_href = attributes["href"]
            self._current_text = []
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (attributes.get("name") or attributes.get("property") or "").lower()
            content = attributes.get("content") or ""
            if name and content:
                self.meta[name] = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_text:
            text = re.sub(r"\s+", " ", "".join(self._current_text)).strip()
            self.links.append((text, self._current_href, text.lower()))
            self._capture_text = False
            self._current_href = ""
            self._current_text = []
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._capture_text:
            self._current_text.append(data)
        if self._in_title:
            self._title += data
        stripped = data.strip()
        if stripped:
            self.text_chunks.append(stripped)

    @property
    def title(self) -> str:
        return re.sub(r"\s+", " ", self._title).strip()

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.text_chunks)).strip()


@dataclass
class _TargetCompanyRecord:
    name: str
    category: str
    website: str
    career_paths: list[str]
    priority: str = "high"

    @property
    def normalized_website(self) -> str:
        return self.website.rstrip("/")


def _cfg() -> dict[str, Any]:
    return load_config_bundle() or DEFAULT_CONFIG


def load_target_companies() -> list[_TargetCompanyRecord]:
    payload = _cfg().get("target_companies") or {}
    companies: list[_TargetCompanyRecord] = []
    for group_name, group in payload.items():
        if group_name != "high_priority":
            continue
        for item in group or []:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            companies.append(
                _TargetCompanyRecord(
                    name=name,
                    category=str(item.get("category") or "target company").strip(),
                    website=str(item.get("website") or "").strip(),
                    career_paths=[str(path).strip() for path in item.get("career_paths") or [] if str(path).strip()],
                    priority="high",
                )
            )
    return companies


def _fetch_html(url: str) -> str:
    def _request() -> requests.Response:
        return requests.get(
            url,
            timeout=12,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": url,
            },
        )

    response = retry_with_backoff(_request, attempts=1, base_delay=1.0, exceptions=(requests.RequestException,))
    response.raise_for_status()
    return response.text


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _parse_links(html: str, base_url: str) -> _LinkParser:
    parser = _LinkParser()
    parser.feed(html)
    parser.links = [(text, urljoin(base_url, href), lower) for text, href, lower in parser.links]
    return parser


def _extract_json_ld_objects(html: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, flags=re.I | re.S):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            objects.append(data)
        elif isinstance(data, list):
            objects.extend(item for item in data if isinstance(item, dict))
    return objects


def _extract_jobposting_data(html: str) -> dict[str, Any] | None:
    for obj in _extract_json_ld_objects(html):
        obj_type = obj.get("@type")
        if isinstance(obj_type, list):
            types = {str(item).lower() for item in obj_type}
        else:
            types = {str(obj_type).lower()}
        if "jobposting" in types:
            return obj
    return None


def _page_metadata(html: str) -> dict[str, str]:
    parser = _parse_links(html, "https://example.com")
    return {
        "title": parser.title,
        "description": parser.meta.get("description") or parser.meta.get("og:description") or "",
        "site_name": parser.meta.get("og:site_name") or "",
    }


def _pick_location(jobposting: dict[str, Any], body_text: str) -> str:
    location = jobposting.get("jobLocation")
    if isinstance(location, list) and location:
        location = location[0]
    if isinstance(location, dict):
        address = location.get("address") or {}
        if isinstance(address, dict):
            parts = [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")]
            rendered = ", ".join(str(part) for part in parts if part)
            if rendered:
                return rendered
    if "remote" in body_text.lower():
        return "Remote"
    return "Unknown"


def _looks_exec_role(title: str, description: str = "") -> bool:
    text = f"{title} {description}".lower()
    if any(term in text for term in ("product manager", "project manager", "delivery manager", "support", "engineer", "developer", "recruiter", "designer")):
        return False
    title_text = title.lower()
    if any(term in title_text for term in EXECUTIVE_TITLE_HINTS):
        return True
    if "lead" in title_text and any(term in title_text for term in ("product", "growth", "transformation", "monetization", "ecosystem")):
        return True
    return False


def _derive_signals(text: str) -> list[str]:
    lowered = text.lower()
    signals: list[str] = []
    for signal, terms in HOMEPAGE_SIGNAL_HINTS.items():
        if any(term in lowered for term in terms):
            signals.append(signal)
    return signals


def _discover_career_urls(company: _TargetCompanyRecord, homepage_url: str, html: str) -> list[str]:
    parser = _parse_links(html, homepage_url)
    discovered: list[str] = []
    for text, href, lower_text in parser.links:
        if any(hint in lower_text or hint in href.lower() for hint in CAREER_LINK_HINTS):
            discovered.append(href)
    for path in company.career_paths:
        discovered.append(urljoin(homepage_url, path))
    unique: list[str] = []
    seen: set[str] = set()
    for href in discovered:
        href = href.rstrip("/")
        if href and href not in seen:
            unique.append(href)
            seen.add(href)
    return unique


def _normalize_job_page(url: str, html: str, *, company: _TargetCompanyRecord, source: str) -> Vacancy | None:
    body_text = _normalize_text(_strip_tags(html))
    jobposting = _extract_jobposting_data(html)
    metadata = {
        "target_company": True,
        "target_category": company.category,
        "career_page_url": url,
        "homepage_url": company.normalized_website,
        "page_title": _page_metadata(html)["title"],
        "signals": _derive_signals(body_text),
        "source_type": source,
    }
    if jobposting:
        title = _normalize_text(str(jobposting.get("title") or metadata["page_title"] or ""))
        description = _normalize_text(_strip_tags(str(jobposting.get("description") or metadata["page_title"] or body_text)))
        if not _looks_exec_role(title, description):
            return None
        company_name = _normalize_text(
            str(
                ((jobposting.get("hiringOrganization") or {}).get("name") if isinstance(jobposting.get("hiringOrganization"), dict) else None)
                or company.name
            )
        )
        return Vacancy(
            source=source,
            source_id=sha256_text(url)[:16],
            company=company_name,
            title=title,
            location=_pick_location(jobposting, body_text),
            url=url,
            description=description or body_text[:4000],
            salary=None,
            metadata=metadata,
        )

    title = metadata["page_title"] or company.name
    if not title:
        return None
    if company.name.lower() in title.lower():
        title = re.sub(fr"\s*[-|·]\s*{re.escape(company.name)}\s*$", "", title, flags=re.I).strip() or title
    description = _normalize_text(_page_metadata(html)["description"] or body_text)
    if not _looks_exec_role(title, description):
        return None
    return Vacancy(
        source=source,
        source_id=sha256_text(url)[:16],
        company=company.name,
        title=title,
        location="Remote" if "remote" in body_text.lower() else "Unknown",
        url=url,
        description=description or body_text[:4000],
        salary=None,
        metadata=metadata,
    )


def _record_company_snapshot(
    store: JobIntelStore,
    company: _TargetCompanyRecord,
    *,
    website: str,
    career_urls: list[str],
    signals: list[str],
    opening_count: int,
    source: str,
    latest_body: str,
) -> None:
    risk_flags: list[str] = []
    red_flags = (_cfg().get("company_red_flags") or {})
    all_red_flags = []
    for group in red_flags.values():
        all_red_flags.extend(group or [])
    lowered = latest_body.lower()
    for flag in all_red_flags:
        if flag.lower() in lowered:
            risk_flags.append(flag)
    summary_parts = [company.name, company.category, f"openings={opening_count}"]
    if signals:
        summary_parts.append(f"signals={', '.join(signals)}")
    if risk_flags:
        summary_parts.append(f"risk_flags={', '.join(risk_flags[:3])}")
    store.upsert_company_intelligence(
        company.name,
        summary=" | ".join(summary_parts),
        signals={"signals": signals, "risk_flags": risk_flags, "homepage_url": website, "career_urls": career_urls, "source": source, "opening_count": opening_count},
        target_category=company.category,
        website=website,
        career_urls=career_urls,
        opening_count=opening_count,
        source=source,
        risk_flags=risk_flags,
        last_signal_at=datetime.now(timezone.utc).isoformat() if signals else None,
    )
    for signal in signals:
        store.append_company_event(
            company.name,
            signal,
            source=source,
            title=f"{company.name} {signal.replace('_', ' ')}",
            url=website,
            summary=f"{company.name} emitted {signal}",
            details={"career_urls": career_urls, "opening_count": opening_count, "category": company.category},
        )


def monitor_target_companies(store: JobIntelStore) -> CompanyMonitoringResult:
    companies = load_target_companies()
    vacancies: list[Vacancy] = []
    company_statuses: dict[str, dict[str, Any]] = {}
    for company in companies:
        company_key = company.name.lower()
        try:
            homepage = _fetch_html(company.website)
            homepage_signals = _derive_signals(homepage)
            career_urls = _discover_career_urls(company, company.website, homepage)
            source = "target-company"
            company_vacancies: list[Vacancy] = []
            company_body_samples = [_normalize_text(_strip_tags(homepage))[:2000]]
            for career_url in career_urls[:2]:
                try:
                    page_html = _fetch_html(career_url)
                    company_body_samples.append(_normalize_text(_strip_tags(page_html))[:2000])
                    vacancy = _normalize_job_page(career_url, page_html, company=company, source=source)
                    if vacancy:
                        company_vacancies.append(vacancy)
                    parser = _parse_links(page_html, career_url)
                    for text, href, lower_text in parser.links[:6]:
                        if not _looks_exec_role(text, text):
                            continue
                        try:
                            job_html = _fetch_html(href)
                            company_body_samples.append(_normalize_text(_strip_tags(job_html))[:1500])
                            vacancy = _normalize_job_page(href, job_html, company=company, source=source)
                            if vacancy:
                                company_vacancies.append(vacancy)
                        except Exception:
                            continue
                except Exception:
                    continue
            unique_vacancies: list[Vacancy] = []
            seen_urls: set[str] = set()
            for vacancy in company_vacancies:
                if vacancy.url in seen_urls:
                    continue
                seen_urls.add(vacancy.url)
                unique_vacancies.append(vacancy)
            signals = set(homepage_signals)
            for body in company_body_samples:
                signals.update(_derive_signals(body))
            openings = len(unique_vacancies)
            _record_company_snapshot(
                store,
                company,
                website=company.website,
                career_urls=career_urls,
                signals=sorted(signals),
                opening_count=openings,
                source=source,
                latest_body="\n".join(company_body_samples),
            )
            vacancies.extend(unique_vacancies)
            company_statuses[company_key] = {
                "source": source,
                "status": "ok" if openings or career_urls or signals else "empty",
                "website": company.website,
                "career_urls": career_urls,
                "openings": openings,
                "signals": sorted(signals),
            }
        except Exception as exc:
            company_statuses[company_key] = {
                "source": "target-company",
                "status": "error",
                "website": company.website,
                "errors": [str(exc)],
            }
    return CompanyMonitoringResult(vacancies=vacancies, company_statuses=company_statuses)


def build_market_report(store: JobIntelStore, *, limit: int = 10) -> str:
    rows = store.fetch_company_intelligence(limit=50)
    if not rows:
        return "[SILENT]"
    lines = ["*Target company intelligence report*", ""]
    interesting = sorted(
        rows,
        key=lambda row: (
            int(row.get("opening_count") or 0),
            row.get("last_signal_at") or row.get("updated_at") or "",
        ),
        reverse=True,
    )
    for idx, row in enumerate(interesting[:limit], 1):
        signals = row.get("signals_json") or "{}"
        try:
            payload = json.loads(signals)
        except json.JSONDecodeError:
            payload = {}
        signal_list = payload.get("signals") or []
        risk_flags = payload.get("risk_flags") or []
        career_urls = payload.get("career_urls") or []
        summary = row.get("summary") or row.get("company")
        lines.append(f"{idx}. *{row.get('company')}* — {summary}")
        if signal_list:
            lines.append(f"   Signals: {', '.join(signal_list)}")
        if risk_flags:
            lines.append(f"   Risks: {', '.join(risk_flags)}")
        if career_urls:
            lines.append(f"   Career pages: {', '.join(career_urls[:3])}")
        lines.append("")
    return "\n".join(lines).rstrip()
