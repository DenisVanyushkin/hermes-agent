from __future__ import annotations

import hashlib
import json
import os
import random
import re
import signal
import subprocess
import time
from contextlib import suppress


class _WallTimeout:
    """Best-effort wall-clock timeout for sync Playwright operations.

    Uses SIGALRM (main thread only). On timeout raises TimeoutError.
    """

    def __init__(self, seconds: float, message: str):
        self.seconds = max(0.0, float(seconds))
        self.message = message
        self._old = None

    def __enter__(self):
        if self.seconds <= 0:
            return self

        def _handler(signum, frame):
            raise TimeoutError(self.message)

        self._old = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, _handler)
        signal.setitimer(signal.ITIMER_REAL, self.seconds)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.seconds > 0:
            try:
                signal.setitimer(signal.ITIMER_REAL, 0)
            except Exception:
                pass
            if self._old is not None:
                try:
                    signal.signal(signal.SIGALRM, self._old)
                except Exception:
                    pass
        return False


from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import urlopen

from .models import Vacancy
from .runtime import resolve_browser_profile_base, sha256_text


_BROWSER_PROFILE_DEFAULT = resolve_browser_profile_base() / "company-career"
_BROWSER_PROFILE_DEFAULTS: dict[str, Path] = {
    "linkedin": resolve_browser_profile_base() / "linkedin",
    "company_career": _BROWSER_PROFILE_DEFAULT,
}


def build_linkedin_search_url(
    *,
    keywords: str,
    location: str | None = None,
    geo_id: str | None = None,
    start: int | None = None,
) -> str:
    params: list[tuple[str, str]] = [("keywords", keywords)]
    if location:
        params.append(("location", location))
    if geo_id:
        params.append(("geoId", geo_id))
    if start:
        params.append(("start", str(start)))
    return "https://www.linkedin.com/jobs/search/?" + urlencode(params)


@dataclass(frozen=True)
class BrowserAcquisitionConfig:
    source_name: str = ""
    user_data_dir: Path = _BROWSER_PROFILE_DEFAULT
    headless: bool = True
    slow_mo_ms: int = 150
    min_delay_ms: int = 700
    max_delay_ms: int = 1800
    scroll_pause_ms: int = 650
    navigation_timeout_ms: int = 45_000
    max_scrolls: int = 2
    noise_probability: float = 0.18
    linkedin_followup_page_probability: float = 0.35
    max_linkedin_pages: int = 2


@dataclass
class BrowserSessionHealth:
    source: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pages_fetched: int = 0
    login_walls: int = 0
    auth_redirects: int = 0
    extraction_failures: int = 0
    extraction_degradation: int = 0
    successful_extractions: int = 0
    failed_extractions: int = 0
    detail_pages_opened: int = 0
    pagination_depth_reached: int = 0
    total_page_load_seconds: float = 0.0
    last_url: str = ""
    last_successful_authenticated_request: str | None = None
    browser_profile: str = ""
    auth_attempted: bool = False
    session_state: str = "unknown"
    cookie_mismatch: bool = False
    page_unrecognised: bool = False
    critical_degradation: bool = False
    critical_degradation_reason: str | None = None
    status: str = "healthy"

    def snapshot(self) -> dict[str, Any]:
        age_seconds = max(0, int((datetime.now(timezone.utc) - self.started_at).total_seconds()))
        payload = asdict(self)
        payload["started_at"] = self.started_at.isoformat()
        payload["session_age_seconds"] = age_seconds
        payload["session_age_hours"] = round(age_seconds / 3600.0, 3)
        payload["avg_page_load_time_seconds"] = round(self.total_page_load_seconds / self.pages_fetched, 3) if self.pages_fetched else 0.0
        payload["anti_bot_events"] = max(self.login_walls, self.auth_redirects) + self.extraction_degradation
        return payload

    def update(self, *, url: str, html: str, vacancies_found: int, detail_page: bool = False, page_load_seconds: float | None = None, page_depth: int | None = None) -> None:
        self.pages_fetched += 1
        self.last_url = url
        if page_load_seconds is not None:
            self.total_page_load_seconds += max(0.0, page_load_seconds)
        if detail_page:
            self.detail_pages_opened += 1
        if page_depth is not None:
            self.pagination_depth_reached = max(self.pagination_depth_reached, page_depth)
        else:
            self.pagination_depth_reached = max(self.pagination_depth_reached, self.pages_fetched)
        auth_redirect = _looks_like_auth_redirect(url, html)
        login_wall = _looks_like_login_wall(url, html)
        if vacancies_found > 0 and _page_has_source_results(self.source, url, html):
            auth_redirect = False
            login_wall = False
        if auth_redirect:
            self.auth_redirects += 1
        if login_wall:
            self.login_walls += 1
        if vacancies_found > 0:
            self.successful_extractions += 1
            if not login_wall and not auth_redirect:
                self.last_successful_authenticated_request = url
        else:
            if _looks_like_extraction_failure(url, html):
                self.failed_extractions += 1
                self.extraction_failures += 1
            elif _looks_like_degradation(url, html):
                self.failed_extractions += 1
                self.extraction_degradation += 1
        self.status = _session_status(self)


@dataclass(frozen=True)
class AcquisitionMetrics:
    source: str
    vacancies_found: int
    executive_matches: int
    accepted: int
    rejected: int
    extraction_successes: int
    extraction_attempts: int
    anti_bot_failures: int
    executive_fit_ratio: float
    accepted_rejected_ratio: float
    extraction_success_rate: float
    anti_bot_failure_rate: float
    executive_density: float
    signal_noise_ratio: float
    normalization_quality: float
    acquisition_quality_score: float
    source_reliability: float
    status: str


class BrowserNativeUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserFetchResult:
    requested_url: str
    final_url: str
    html: str
    html_sha256: str
    page_offset: int
    planned_scroll_steps: int
    completed_scroll_steps: int
    scroll_trace: tuple[dict[str, Any], ...]
    dom_unique_job_ids: frozenset[str]
    artifact_ref: str | None
    scroll_checkpoints: tuple[dict[str, Any], ...] = ()
    scroll_stop_reason: str = "legacy"
    scroll_failure_reason: str | None = None


def _linkedin_job_id_from_url(url: str) -> str | None:
    match = re.search(r"/jobs/view/([^/?#]+)", urlparse(url).path, flags=re.I)
    return match.group(1) if match else None


class _LinkCapture(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._capture = False
        self._href = ""
        self._text: list[str] = []
        self._in_title = False
        self._title = ""
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key: value or "" for key, value in attrs}
        if tag == "a" and attrs_map.get("href"):
            self._capture = True
            self._href = attrs_map["href"]
            self._text = []
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (attrs_map.get("name") or attrs_map.get("property") or "").lower()
            content = attrs_map.get("content") or ""
            if key and content:
                self.meta[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture:
            text = _normalize_whitespace("".join(self._text))
            if text or self._href:
                self.links.append((text, self._href))
            self._capture = False
            self._href = ""
            self._text = []
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._text.append(data)
        if self._in_title:
            self._title += data

    @property
    def title(self) -> str:
        return _normalize_whitespace(self._title)


_EXECUTIVE_ROLE_HINTS = (
    "vp",
    "vice president",
    "director",
    "head of",
    "chief",
    "cpo",
    "gm",
    "general manager",
    "monetization",
    "growth",
    "platform",
    "ecosystem",
    "subscription",
    "fintech",
    "product",
)

_LOW_SIGNAL_HINTS = (
    "support",
    "customer success",
    "customer support",
    "recruiter",
    "talent acquisition",
    "project manager",
    "product manager",
    "scrum master",
    "developer",
    "engineer",
    "designer",
    "qa engineer",
)


def resolve_browser_config(source: str | None = None) -> BrowserAcquisitionConfig:
    source_key = (source or "").strip().lower().replace(" ", "_")
    overrides = []
    if source_key:
        env_suffix = source_key.upper().replace("-", "_")
        overrides.append(os.getenv(f"JOB_INTEL_BROWSER_PROFILE_DIR_{env_suffix}", "").strip())
        if source_key == "linkedin":
            overrides.append(os.getenv("JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN", "").strip())
    overrides.append(os.getenv("JOB_INTEL_BROWSER_PROFILE_DIR", "").strip())
    default_dir = _BROWSER_PROFILE_DEFAULTS.get(source_key, BrowserAcquisitionConfig().user_data_dir)
    user_data_dir = Path(next((override for override in overrides if override), default_dir)).expanduser()
    headless = os.getenv("JOB_INTEL_BROWSER_HEADLESS", "1").strip().lower() not in {"0", "false", "no"}
    slow_mo_ms = int(os.getenv("JOB_INTEL_BROWSER_SLOW_MO_MS", str(BrowserAcquisitionConfig().slow_mo_ms)))
    min_delay_ms = int(os.getenv("JOB_INTEL_BROWSER_MIN_DELAY_MS", str(BrowserAcquisitionConfig().min_delay_ms)))
    max_delay_ms = int(os.getenv("JOB_INTEL_BROWSER_MAX_DELAY_MS", str(BrowserAcquisitionConfig().max_delay_ms)))
    scroll_pause_ms = int(os.getenv("JOB_INTEL_BROWSER_SCROLL_PAUSE_MS", str(BrowserAcquisitionConfig().scroll_pause_ms)))
    navigation_timeout_ms = int(os.getenv("JOB_INTEL_BROWSER_NAV_TIMEOUT_MS", str(BrowserAcquisitionConfig().navigation_timeout_ms)))
    max_scrolls = int(os.getenv("JOB_INTEL_BROWSER_MAX_SCROLLS", str(BrowserAcquisitionConfig().max_scrolls)))
    return BrowserAcquisitionConfig(
        source_name=source_key,
        user_data_dir=user_data_dir,
        headless=headless,
        slow_mo_ms=slow_mo_ms,
        min_delay_ms=min_delay_ms,
        max_delay_ms=max_delay_ms,
        scroll_pause_ms=scroll_pause_ms,
        navigation_timeout_ms=navigation_timeout_ms,
        max_scrolls=max_scrolls,
    )


def _browser_profile_is_populated(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    try:
        return any(path.iterdir())
    except OSError:
        return False


def _ensure_required_browser_profile(source: str, config: BrowserAcquisitionConfig) -> None:
    if source not in {"linkedin", "company_career"}:
        return
    if not _browser_profile_is_populated(config.user_data_dir):
        raise BrowserNativeUnavailable(
            f"{source} browser profile directory {config.user_data_dir} is missing or empty; refusing to fall back to a shared or blank profile."
        )


def _browser_runtime_python() -> Path:
    configured = os.getenv("JOB_INTEL_BROWSER_PYTHON", "").strip()
    if configured:
        return Path(configured).expanduser()
    base_dir = os.getenv("JOB_INTEL_BROWSER_RUNTIME_DIR", "").strip() or os.getenv("BROWSER_DESKTOP_BASE_DIR", "").strip()
    if base_dir:
        return Path(base_dir).expanduser() / "playwright-venv" / "bin" / "python"
    return Path("/var/lib/browser-desktop/playwright-venv/bin/python")


def _browser_python_has_playwright() -> bool:
    browser_python_path = _browser_runtime_python()
    if not browser_python_path.exists():
        return False
    probe = [str(browser_python_path), "-c", "from importlib.util import find_spec; raise SystemExit(0 if find_spec('playwright.sync_api') else 1)"]
    try:
        result = subprocess.run(
            probe,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0


def browser_native_available() -> bool:
    try:
        if find_spec("playwright.sync_api") is not None:
            return True
    except ModuleNotFoundError:
        pass
    return _browser_python_has_playwright()


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


def _clean_html_text(value: str) -> str:
    return _normalize_whitespace(unescape(_strip_html(value or "")))


def _looks_like_linkedin_results_page(url: str, html: str) -> bool:
    lowered_url = url.lower()
    lowered_html = html.lower()
    return "linkedin.com/jobs/search" in lowered_url and (
        "job-card-container__link" in lowered_html
        or "job-card-list__title--link" in lowered_html
        or "artdeco-entity-lockup__subtitle" in lowered_html
    )


def _page_has_source_results(source: str, url: str, html: str) -> bool:
    source_key = (source or "").strip().lower()
    if source_key == "linkedin":
        return _looks_like_linkedin_results_page(url, html)
    return False


def _json_ld_objects(html: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, flags=re.I | re.S):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            objects.append(payload)
        elif isinstance(payload, list):
            objects.extend(item for item in payload if isinstance(item, dict))
    return objects


def _jobposting_objects(html: str) -> list[dict[str, Any]]:
    jobpostings: list[dict[str, Any]] = []
    for obj in _json_ld_objects(html):
        obj_type = obj.get("@type")
        types = {str(item).lower() for item in obj_type} if isinstance(obj_type, list) else {str(obj_type).lower()}
        if "jobposting" in types:
            jobpostings.append(obj)
    return jobpostings


def _company_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path_parts = [part for part in parsed.path.split("/") if part]
    host_parts = [part for part in host.split(".") if part]
    if "linkedin.com" in host:
        return "Unknown"
    if host.startswith(("careers.", "jobs.", "work.", "hiring.", "join.")) and len(host_parts) >= 3:
        brand = host_parts[1]
        return _normalize_whitespace(brand.replace("-", " ").replace("_", " ").title()) or "Unknown"
    if ("greenhouse.io" in host or "boards.greenhouse.io" in host or "lever.co" in host or "ashbyhq.com" in host) and path_parts:
        return _normalize_whitespace(path_parts[0].replace("-", " ").replace("_", " ").title())
    if path_parts and path_parts[0] not in {"jobs", "vacancy", "careers", "roles", "positions", "openings"}:
        return _normalize_whitespace(path_parts[0].replace("-", " ").replace("_", " ").title())
    return _normalize_whitespace(host.replace("www.", "").split(":")[0].split(".")[0].title()) or "Unknown"


def _normalize_location_text(value: str) -> str:
    normalized = _clean_html_text(value)
    if not normalized:
        return "Unknown"
    lowered = normalized.lower()
    if "remote" in lowered:
        return "Remote"
    return normalized


def _location_from_jobposting(jobposting: dict[str, Any], body_text: str) -> str:
    location = jobposting.get("jobLocation")
    if isinstance(location, list) and location:
        location = location[0]
    if isinstance(location, dict):
        address = location.get("address") or {}
        if isinstance(address, dict):
            parts = [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")]
            rendered = ", ".join(str(part) for part in parts if part)
            if rendered:
                return _normalize_location_text(rendered)
    text = body_text.lower()
    if "remote" in text:
        return "Remote"
    return "Unknown"


def _salary_from_jobposting(jobposting: dict[str, Any]) -> str | None:
    salary = jobposting.get("baseSalary")
    if not salary:
        return None
    if isinstance(salary, list) and salary:
        salary = salary[0]
    if isinstance(salary, dict):
        value = salary.get("value") or {}
        if isinstance(value, dict):
            parts = [value.get("minValue"), value.get("maxValue"), value.get("unitText")]
            rendered = " ".join(str(part) for part in parts if part)
            return rendered or None
    return None


def _looks_executive(title: str, description: str = "") -> bool:
    text = f"{title} {description}".lower()
    if any(hint in text for hint in _LOW_SIGNAL_HINTS):
        return False
    return any(hint in text for hint in _EXECUTIVE_ROLE_HINTS)


def _looks_like_login_wall(url: str, html: str) -> bool:
    text = f"{url} {html}".lower()
    return any(
        phrase in text
        for phrase in (
            "sign in to view more jobs",
            "sign in",
            "log in",
            "login",
            "checkpoint",
            "authentication required",
            "to view more jobs",
            "continue to linkedin",
        )
    )


def _looks_like_auth_redirect(url: str, html: str) -> bool:
    lowered_url = url.lower()
    return any(token in lowered_url for token in ("/login", "/checkpoint", "/signin", "/auth")) or _looks_like_login_wall(url, html)


def _looks_like_extraction_failure(url: str, html: str) -> bool:
    lowered = f"{url} {html}".lower()
    return any(
        phrase in lowered
        for phrase in (
            "no vacancies found",
            "no results found",
            "nothing found",
            "something went wrong",
            "access denied",
            "forbidden",
            "captcha",
            "verify you are human",
            "service unavailable",
            "temporarily unavailable",
        )
    )


def _looks_like_degradation(url: str, html: str) -> bool:
    lowered = f"{url} {html}".lower()
    return any(
        phrase in lowered
        for phrase in (
            "partial results",
            "limited results",
            "results may be incomplete",
            "slow down",
            "rate limit",
            "blocked",
        )
    )


def apply_linkedin_verdict(health: BrowserSessionHealth, verdict: Any) -> None:
    """Записать вердикт в здоровье сессии.

    Счётчики login_walls и auth_redirects остаются нетронутыми: они сохранены
    ради совместимости дашбордов, но перестают быть основанием для вывода.
    Решение принимается по session_state, который получен из свидетельств
    присутствия, а не из подстроки в футере.
    """
    health.session_state = verdict.state
    health.cookie_mismatch = verdict.cookie_mismatch
    health.page_unrecognised = getattr(verdict, "page_unrecognised", False)


def _session_status(health: BrowserSessionHealth) -> str:
    if health.critical_degradation:
        return "blocked"
    if health.login_walls >= 2 or health.auth_redirects >= 3:
        return "blocked"
    if health.login_walls >= 1 or health.auth_redirects >= 1:
        return "degraded"
    if health.extraction_failures >= 2 or health.extraction_degradation >= 3:
        return "degraded"
    return "healthy"


def _vacancy_source_id(source: str, url: str, title: str) -> str:
    return sha256_text(f"{source}:{url}:{title}")[:16]


def _vacancy_from_jobposting(jobposting: dict[str, Any], *, source: str, page_url: str, company_override: str | None = None) -> Vacancy:
    title = _normalize_whitespace(str(jobposting.get("title") or jobposting.get("name") or "")) or "Vacancy"
    description = _normalize_whitespace(_strip_html(str(jobposting.get("description") or "")))
    url = str(jobposting.get("url") or page_url or "").strip()
    company = company_override
    hiring_org = jobposting.get("hiringOrganization") or {}
    if not company and isinstance(hiring_org, dict):
        company = str(hiring_org.get("name") or "").strip() or None
    if not company and isinstance(hiring_org, str):
        company = hiring_org.strip() or None
    company = company or _company_from_url(url or page_url)
    location = _location_from_jobposting(jobposting, description)
    return Vacancy(
        source=source,
        source_id=_vacancy_source_id(source, url or page_url, title),
        company=company,
        title=title,
        location=location,
        url=url or page_url,
        description=description or title,
        posted_at=str(jobposting.get("datePosted") or jobposting.get("published_at") or "") or None,
        salary=_salary_from_jobposting(jobposting),
        metadata={"raw": jobposting, "source_url": page_url},
    )


def _dedupe_vacancies(vacancies: list[Vacancy]) -> list[Vacancy]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[Vacancy] = []
    for vacancy in vacancies:
        key = (vacancy.company.lower(), vacancy.title.lower(), vacancy.url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(vacancy)
    return deduped


def _vacancy_identity(vacancy: Vacancy) -> tuple[str, str]:
    return (vacancy.url, vacancy.title.lower())


def _merge_vacancy_lists(primary: list[Vacancy], secondary: list[Vacancy]) -> list[Vacancy]:
    merged: dict[tuple[str, str], Vacancy] = {_vacancy_identity(v): v for v in primary}
    for vacancy in secondary:
        key = _vacancy_identity(vacancy)
        existing = merged.get(key)
        if existing is None:
            merged[key] = vacancy
            continue
        if existing.company == "Unknown" and vacancy.company != "Unknown":
            existing.company = vacancy.company
        if existing.location == "Unknown" and vacancy.location != "Unknown":
            existing.location = vacancy.location
        if len(vacancy.title) < len(existing.title) and vacancy.title:
            existing.title = vacancy.title
        if (not existing.description or existing.description == existing.title) and vacancy.description:
            existing.description = vacancy.description
    return _dedupe_vacancies(list(merged.values()))


def _linkedin_card_vacancies_from_html(html: str, *, page_url: str, apply_role_filter: bool = True) -> list[Vacancy]:
    vacancies: list[Vacancy] = []
    pattern = re.compile(
        r'<a[^>]+href="(?P<href>/jobs/view/[^"]+)"[^>]*class="[^"]*job-card-container__link[^"]*"[^>]*>.*?<strong><!---->(?P<title>.*?)<!----></strong>.*?</a>(?P<tail>.{0,2500}?)job-card-list__footer-wrapper',
        flags=re.S,
    )
    for match in pattern.finditer(html):
        title = _clean_html_text(match.group("title"))
        if not title or (apply_role_filter and not _looks_executive(title)):
            continue
        tail = match.group("tail")
        company_match = re.search(r'artdeco-entity-lockup__subtitle[^>]*>.*?<span[^>]*>\s*<!---->(?P<company>.*?)<!---->\s*</span>', tail, flags=re.S)
        location_match = re.search(r'job-card-container__metadata-wrapper.*?<li[^>]*>\s*<span[^>]*>\s*<!---->(?P<location>.*?)<!---->\s*</span>', tail, flags=re.S)
        company = _clean_html_text(company_match.group("company")) if company_match else "Unknown"
        location = _normalize_location_text(location_match.group("location")) if location_match else "Unknown"
        absolute = urljoin(page_url, match.group("href"))
        vacancies.append(Vacancy(
            source="linkedin",
            source_id=_vacancy_source_id("linkedin", absolute, title),
            company=company or "Unknown",
            title=title,
            location=location or "Unknown",
            url=absolute,
            description=title,
            metadata={"source_url": page_url, "href": match.group("href")},
        ))
    return _dedupe_vacancies(vacancies)


def _link_vacancies_from_html(html: str, *, source: str, page_url: str, company_override: str | None = None) -> list[Vacancy]:
    parser = _LinkCapture()
    parser.feed(html)
    vacancies: list[Vacancy] = []
    page_host = urlparse(page_url).netloc.lower()
    for text, href in parser.links:
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        normalized = absolute.lower()
        path_tokens = {token for token in parsed.path.lower().split("/") if token}
        if source == "linkedin":
            if "linkedin.com/jobs/view/" not in normalized:
                continue
        elif not (
            any(domain in normalized for domain in ("greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "teamtailor.com", "personio", "recruitee"))
            or any(token in path_tokens for token in {"careers", "jobs", "vacancy", "vacancies", "positions", "openings", "roles", "job"})
            or any(token in page_host for token in ("careers", "jobs", "vacancies", "openings"))
        ):
            continue
        title = _normalize_whitespace(text)
        if not title:
            continue
        if not _looks_executive(title):
            continue
        company = company_override
        if not company and source != "linkedin":
            company = _company_from_url(absolute)
        company = company or "Unknown"
        vacancies.append(
            Vacancy(
                source=source,
                source_id=_vacancy_source_id(source, absolute, title),
                company=company,
                title=title,
                location="Unknown",
                url=absolute,
                description=title,
                metadata={"source_url": page_url, "href": href},
            )
        )
    return _dedupe_vacancies(vacancies)




def _extract_linkedin_company_hint(html: str) -> str | None:
    patterns = (
        r'jobs-unified-top-card__company-name[^>]*>\s*<a[^>]*>(?P<company>.*?)</a>',
        r'jobs-unified-top-card__company-name[^>]*>\s*<span[^>]*>(?P<company>.*?)</span>',
        r'topcard__org-name-link[^>]*>\s*(?P<company>.*?)\s*</a>',
        r'\"companyName\"\s*:\s*\"(?P<company>[^\"]+)\"',
        r'\"hiringOrganization\"\s*:\s*\{[^}]*\"name\"\s*:\s*\"(?P<company>[^\"]+)\"',
    )
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.S | re.I)
        if not match:
            continue
        company = _clean_html_text(match.group("company"))
        if company and company.lower() != "unknown":
            return company
    return None

def extract_linkedin_vacancies_from_html(html: str, *, page_url: str) -> list[Vacancy]:
    card_vacancies = _linkedin_card_vacancies_from_html(html, page_url=page_url)
    structured = [_vacancy_from_jobposting(jobposting, source="linkedin", page_url=page_url) for jobposting in _jobposting_objects(html)]
    # Do not ingest generic link-based LinkedIn rows here: they are a major source of
    # collection/search artifacts and Unknown-company duplicates.
    merged = _merge_vacancy_lists(card_vacancies or structured, structured)
    company_hint = _extract_linkedin_company_hint(html)
    if company_hint:
        for vacancy in merged:
            if (vacancy.company or "").strip().lower() in {"", "unknown"}:
                vacancy.company = company_hint
    return merged


def extract_company_career_vacancies_from_html(html: str, *, page_url: str) -> list[Vacancy]:
    structured = [_vacancy_from_jobposting(jobposting, source="company_career", page_url=page_url) for jobposting in _jobposting_objects(html)]
    link_vacancies = _link_vacancies_from_html(html, source="company_career", page_url=page_url)
    return _dedupe_vacancies(structured + link_vacancies)



def extract_jobposting_vacancies_from_html(html: str, *, source: str, page_url: str) -> list[Vacancy]:
    """Extract JSON-LD JobPosting objects into Vacancy rows with a caller-provided source name.

    Used for ATS boards where the page is public but does not warrant a full browser-native flow.
    """
    structured = [_vacancy_from_jobposting(jobposting, source=source, page_url=page_url) for jobposting in _jobposting_objects(html)]
    return _dedupe_vacancies(structured)

class BrowserSourceClient:
    def __init__(self, config: BrowserAcquisitionConfig | None = None):
        self.config = config or BrowserAcquisitionConfig()
        self._playwright = None
        self._browser = None
        self._context = None
        self._cdp_attached = False
        self._cdp_url = ""
        self._health = BrowserSessionHealth(source="browser")
        self._health.browser_profile = str(self.config.user_data_dir)
        self._last_search_trace: dict[str, Any] = {}
        diagnostics_root = os.getenv("JOB_INTEL_BROWSER_DIAGNOSTICS_DIR", "").strip()
        self._diagnostics_dir = Path(diagnostics_root).expanduser() if diagnostics_root else None

    def __enter__(self) -> "BrowserSourceClient":
        if not browser_native_available():
            raise BrowserNativeUnavailable("Playwright is not installed. Install playwright to enable browser-native acquisition.")
        from playwright.sync_api import sync_playwright  # type: ignore

        profile_name = self.config.source_name.strip().lower().replace("-", "_")
        if profile_name in {"linkedin", "company_career"}:
            _ensure_required_browser_profile(profile_name, self.config)

        cdp_url = os.getenv("JOB_INTEL_BROWSER_CDP_URL", "").strip()
        self._cdp_url = cdp_url
        try:
            self._playwright = sync_playwright().start()
            if cdp_url:
                self._write_attach_diagnostics(label="cdp-before-connect", extra={"cdp_url": cdp_url})
                self._browser = self._playwright.chromium.connect_over_cdp(cdp_url)
                self._write_attach_diagnostics(label="cdp-after-connect", extra={"cdp_url": cdp_url})
                contexts = list(getattr(self._browser, "contexts", []) or [])
                self._write_attach_diagnostics(label="cdp-after-contexts", extra={"context_count": len(contexts)})
                if not contexts:
                    raise BrowserNativeUnavailable(f"Playwright CDP attach at {cdp_url} did not expose a persistent browser context.")
                self._context = contexts[0]
                self._write_attach_diagnostics(label="cdp-after-context-selected", extra={"cdp_url": cdp_url})
                self._cdp_attached = True
                return self

            self.config.user_data_dir.mkdir(parents=True, exist_ok=True)
            launch_kwargs: dict[str, Any] = {
                "user_data_dir": str(self.config.user_data_dir),
                "headless": self.config.headless,
                "slow_mo": self.config.slow_mo_ms,
                "viewport": {"width": 1440, "height": 1600},
            }
            browser_executable = os.getenv("JOB_INTEL_BROWSER_EXECUTABLE", "").strip()
            if browser_executable:
                launch_kwargs["executable_path"] = browser_executable
            browser_channel = os.getenv("JOB_INTEL_BROWSER_CHANNEL", "").strip()
            if browser_channel:
                launch_kwargs["channel"] = browser_channel
            self._context = self._playwright.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as exc:
            try:
                if self._playwright is not None:
                    self._playwright.stop()
            except Exception:
                pass
            self._browser = None
            self._context = None
            self._playwright = None
            self._cdp_attached = False
            mode = "attach" if cdp_url else "launch"
            raise BrowserNativeUnavailable(f"Playwright browser {mode} failed: {exc}") from exc
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._context is not None and not self._cdp_attached:
            self._context.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._browser = None
        self._context = None
        self._playwright = None
        self._cdp_attached = False

    def _page_contains_any(self, html: str, markers: tuple[str, ...]) -> bool:
        lowered = html.lower()
        return any(marker in lowered for marker in markers)

    def _diagnostic_slug(self, label: str) -> str:
        slug = re.sub(r"[^a-z0-9._-]+", "-", (label or "page").lower()).strip("-")
        return slug or "page"

    def _write_attach_diagnostics(self, *, label: str, extra: dict[str, Any] | None = None) -> None:
        if self._diagnostics_dir is None:
            return
        try:
            self._diagnostics_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        slug = self._diagnostic_slug(label)
        meta_path = self._diagnostics_dir / f"{timestamp}-{slug}.json"
        payload: dict[str, Any] = {
            "label": label,
            "source": self.config.source_name or self._health.source,
            "requested_profile": str(self.config.user_data_dir),
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "diagnostics_dir": str(self._diagnostics_dir),
        }
        if extra:
            payload["extra"] = extra
        with suppress(Exception):
            meta_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2))

    def _capture_page_diagnostics(self, *, page: Any, label: str, html: str | None = None, extra: dict[str, Any] | None = None) -> str | None:
        if self._diagnostics_dir is None:
            return None
        self._diagnostics_dir.mkdir(parents=True, exist_ok=True)
        self._diagnostics_dir.chmod(0o700)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        slug = self._diagnostic_slug(label)
        base = self._diagnostics_dir / f"{timestamp}-{slug}"
        screenshot_path = base.with_name(base.name + ".png")
        html_path = base.with_name(base.name + ".html")
        meta_path = base.with_name(base.name + ".json")
        page.screenshot(path=str(screenshot_path), full_page=True)
        screenshot_path.chmod(0o600)
        if html is None:
            html = page.content()
        html_path.write_text(html, encoding="utf-8")
        html_path.chmod(0o600)
        payload: dict[str, Any] = {
            "label": label,
            "source": self.config.source_name or self._health.source,
            "requested_profile": str(self.config.user_data_dir),
            "page_url": getattr(page, "url", ""),
            "page_title": "",
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "screenshot_ref": screenshot_path.name,
            "html_ref": html_path.name,
            "html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        }
        with suppress(Exception):
            payload["page_title"] = page.title()
        if html is not None:
            lowered = html.lower()
            payload["markers"] = {
                "sign_in": "sign in" in lowered,
                "log_in": "log in" in lowered,
                "join_linkedin": "join linkedin" in lowered,
                "verification_code": "verification code" in lowered,
                "otp": "otp" in lowered,
                "captcha": "captcha" in lowered,
                "gmail": "gmail" in lowered,
                "hermes_at_vanyushk": "hermes@vanyushk.in" in lowered,
            }
        if extra:
            payload["extra"] = extra
        meta_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        meta_path.chmod(0o600)
        return meta_path.name

    def _browser_state_snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "cdp_url": self._cdp_url or "",
            "cdp_attached": self._cdp_attached,
            "browser_present": self._browser is not None,
            "context_present": self._context is not None,
            "context_page_count": 0,
            "context_page_urls": [],
            "browser_context_count": 0,
            "browser_page_urls": [],
            "cdp_targets": [],
        }
        with suppress(Exception):
            if self._context is not None:
                pages = list(getattr(self._context, "pages", []) or [])
                snapshot["context_page_count"] = len(pages)
                snapshot["context_page_urls"] = [getattr(page, "url", "") for page in pages[:10]]
        with suppress(Exception):
            if self._browser is not None:
                contexts = list(getattr(self._browser, "contexts", []) or [])
                snapshot["browser_context_count"] = len(contexts)
                browser_urls: list[str] = []
                for ctx in contexts[:3]:
                    for page in list(getattr(ctx, "pages", []) or [])[:5]:
                        browser_urls.append(getattr(page, "url", ""))
                snapshot["browser_page_urls"] = browser_urls[:15]
        if self._cdp_url:
            with suppress(Exception):
                with urlopen(self._cdp_url.rstrip("/") + "/json/list", timeout=5) as resp:
                    targets = json.loads(resp.read().decode("utf-8", errors="replace"))
                snapshot["cdp_targets"] = [
                    {
                        "id": item.get("id", ""),
                        "type": item.get("type", ""),
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                    }
                    for item in targets[:25]
                ]
        return snapshot

    def _write_browser_failure_diagnostics(
        self,
        *,
        label: str,
        requested_url: str,
        error: str,
        page: Any | None = None,
        html: str | None = None,
    ) -> None:
        extra = {
            "requested_url": requested_url,
            "error": error,
            "browser_state": self._browser_state_snapshot(),
        }
        self._write_attach_diagnostics(label=label, extra=extra)
        if page is not None:
            with suppress(Exception):
                self._capture_page_diagnostics(page=page, label=label, html=html, extra=extra)

    def _validate_authenticated_html(
        self,
        *,
        source: str,
        url: str,
        html: str,
        required_markers: tuple[str, ...],
        login_markers: tuple[str, ...],
    ) -> None:
        has_required_markers = self._page_contains_any(html, required_markers)
        if has_required_markers:
            self._health.last_successful_authenticated_request = url
            return
        if _looks_like_login_wall(url, html) or _looks_like_auth_redirect(url, html):
            self._health.update(url=url, html=html, vacancies_found=0)
            raise BrowserNativeUnavailable(f"{source} authentication validation landed on a login wall or redirect at {url}")
        if login_markers and self._page_contains_any(html, login_markers):
            self._health.update(url=url, html=html, vacancies_found=0)
            raise BrowserNativeUnavailable(f"{source} authentication validation found sign-in markers at {url}")
        if source == "linkedin":
            raise BrowserNativeUnavailable(f"{source} authenticated feed/profile markers were not visible at {url}")
        self._health.last_successful_authenticated_request = url

    def _validate_linkedin_auth(self) -> None:
        from job_intel.linkedin_session import (
            SESSION_MISSING,
            SESSION_OK,
            classify_auth_page,
            read_cookie_inventory,
            resolve_profile_dir,
            resolve_session_state,
            session_state_from_cookies,
        )

        url = "https://www.linkedin.com/feed/"
        self._health.auth_attempted = True
        html = self.fetch_html(url, scrolls=0, capture_label="linkedin-auth-validate")
        page_state = classify_auth_page(url, html)

        cookie_db = resolve_profile_dir(Path(self.config.user_data_dir)) / "Cookies"
        try:
            cookie_state = session_state_from_cookies(
                read_cookie_inventory(cookie_db), now=datetime.now(timezone.utc)
            )
        except Exception:
            # Профиль может быть недоступен для чтения из-под другого
            # пользователя. Это неизвестность, а не отсутствие сессии:
            # подменять её на SESSION_MISSING значило бы объявить сессию
            # мёртвой по причине прав доступа.
            cookie_state = SESSION_OK if page_state == SESSION_OK else SESSION_MISSING

        verdict = resolve_session_state(cookie_state=cookie_state, page_state=page_state)
        apply_linkedin_verdict(self._health, verdict)

        if verdict.state == SESSION_OK:
            self._health.last_successful_authenticated_request = url
            if verdict.page_unrecognised:
                # Сессия жива по куке, но разметку опознать не удалось.
                # Улику сохраняем: следующий редизайн иначе заметят только
                # тогда, когда проверка окончательно перестанет работать.
                self._write_attach_diagnostics(
                    label="linkedin-auth-page-unrecognised",
                    extra={"requested_url": url, "cookie_state": cookie_state},
                )
            return

        self._write_attach_diagnostics(
            label=f"linkedin-auth-{verdict.state}",
            extra={
                "requested_url": url,
                "cookie_state": cookie_state,
                "page_state": page_state,
            },
        )
        # Подъём исключения — не деталь реализации, а предохранитель: без него
        # неавторизованный клиент продолжит ходить по страницам поиска, то есть
        # ровно то поведение, которого мы больше всего избегаем. Раньше это
        # свойство обеспечивал _validate_authenticated_html.
        raise BrowserNativeUnavailable(
            f"linkedin authentication validation: {verdict.state} at {url}"
        )

    def session_health_snapshot(self) -> dict[str, Any]:
        return self._health.snapshot()

    def last_search_trace_snapshot(self) -> dict[str, Any]:
        return dict(self._last_search_trace or {})

    def capture_existing_pages(self, *, label: str) -> None:
        snapshot = self._browser_state_snapshot()
        self._write_attach_diagnostics(label=label, extra={"browser_state": snapshot})
        if self._context is None:
            return
        with suppress(Exception):
            pages = list(getattr(self._context, "pages", []) or [])
            for idx, page in enumerate(pages[:5]):
                self._capture_page_diagnostics(
                    page=page,
                    label=f"{label}-page-{idx}",
                    extra={"browser_state": snapshot, "page_index": idx},
                )

    def _reuse_or_prepare_linkedin_page(self, *, requested_url: str, label: str) -> Any | None:
        if self._context is None:
            return None
        pages = list(getattr(self._context, "pages", []) or [])
        if not pages:
            return None
        keep = None
        closed = 0
        page_urls: list[str] = []
        for page in pages:
            url = ""
            with suppress(Exception):
                url = getattr(page, "url", "") or ""
            page_urls.append(url)
            normalized = url.lower()
            should_keep = (
                normalized.startswith("https://www.linkedin.com/")
                or normalized == "https://www.linkedin.com"
                or normalized == "about:blank"
            )
            if keep is None and should_keep:
                keep = page
                continue
            with suppress(Exception):
                page.close()
                closed += 1
        if keep is None:
            return None
        with suppress(Exception):
            keep.bring_to_front()
        self._write_attach_diagnostics(
            label=f"{label}-reuse-page",
            extra={
                "requested_url": requested_url,
                "closed_pages": closed,
                "remaining_url": getattr(keep, "url", ""),
                "seen_urls": page_urls[:15],
            },
        )
        return keep

    def _mark_critical_degradation(self, reason: str) -> None:
        self._health.critical_degradation = True
        self._health.critical_degradation_reason = reason
        self._health.status = "blocked"

    def _linkedin_dom_unique_job_ids(self, page: Any) -> frozenset[str]:
        locator = page.locator("a[href*='/jobs/view/']")
        hrefs = locator.evaluate_all(
            "(anchors) => anchors.map(anchor => anchor.href || anchor.getAttribute('href'))"
        )
        return frozenset(
            job_id
            for href in hrefs
            if isinstance(href, str)
            for job_id in [_linkedin_job_id_from_url(href)]
            if job_id
        )

    def _coerce_linkedin_execution_plan(
        self, plan: Mapping[str, Any] | Any | None
    ) -> Any | None:
        if plan is None:
            return None
        from .product_search.acquisition_probe import LinkedInExecutionPlan

        if isinstance(plan, LinkedInExecutionPlan):
            return plan
        return LinkedInExecutionPlan.model_validate(plan)

    def _execute_linkedin_scroll_plan(
        self, *, page: Any, plan: Any
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, str | None]:
        checkpoints: list[dict[str, Any]] = []
        scroll_trace: list[dict[str, Any]] = []
        consecutive_without_new = 0
        mode = "results_container"
        failure_reason: str | None = None
        try:
            locator = page.locator(plan.results_selector)
            count = locator.count() if callable(getattr(locator, "count", None)) else 1
            if count < 1:
                mode = "page_fallback"
                failure_reason = "results_container_unavailable"
                self._health.status = "degraded"
                container = None
            else:
                container = getattr(locator, "first", locator)
        except Exception as exc:  # noqa: BLE001 - fallback is explicitly traced
            mode = "page_fallback"
            failure_reason = f"results_container_unavailable: {exc}"
            self._health.status = "degraded"
            container = None

        for step in range(1, plan.max_scroll_checkpoints + 1):
            before_ids = self._linkedin_dom_unique_job_ids(page)
            try:
                if container is None:
                    page.mouse.wheel(0, 1800)
                else:
                    moved = container.evaluate(
                        "(element) => { element.scrollTop += element.clientHeight; return true; }"
                    )
                    if moved is False:
                        raise RuntimeError("results container did not execute scroll")
                page.wait_for_timeout(plan.settle_timeout_ms)
                after_ids = self._linkedin_dom_unique_job_ids(page)
            except Exception as exc:  # noqa: BLE001 - incomplete plan is critical
                reason = f"scroll plan failed at checkpoint {step}: {exc}"
                self._mark_critical_degradation(reason)
                raise BrowserNativeUnavailable(reason) from exc

            new_ids = sorted(after_ids - before_ids)
            consecutive_without_new = (
                consecutive_without_new + 1 if not new_ids else 0
            )
            checkpoint = {
                "step": step,
                "mode": mode,
                "before_unique_dom_ids": sorted(before_ids),
                "after_unique_dom_ids": sorted(after_ids),
                "before_unique_dom_id_count": len(before_ids),
                "after_unique_dom_id_count": len(after_ids),
                "new_unique_dom_ids": new_ids,
                "completed": True,
            }
            checkpoints.append(checkpoint)
            scroll_trace.append(checkpoint)
            if consecutive_without_new >= plan.saturation_checkpoints:
                return scroll_trace, checkpoints, "saturation", failure_reason

        return scroll_trace, checkpoints, "max_steps", failure_reason

    def fetch_page(
        self,
        url: str,
        *,
        scrolls: int | None = None,
        page_offset: int = 0,
        capture_label: str | None = None,
        execution_plan: Mapping[str, Any] | Any | None = None,
    ) -> BrowserFetchResult:
        if self._context is None:
            raise BrowserNativeUnavailable("BrowserSourceClient must be entered as a context manager first.")
        scroll_count = self.config.max_scrolls if scrolls is None else max(0, scrolls)
        start = time.perf_counter()
        source_key = (self.config.source_name or "").strip().lower()
        fetch_label = capture_label or "fetch"
        plan = self._coerce_linkedin_execution_plan(execution_plan)

        def _attempt_fetch() -> BrowserFetchResult:
            page = None
            reuse_existing = False
            scroll_trace: list[dict[str, Any]] = []
            scroll_checkpoints: list[dict[str, Any]] = []
            scroll_stop_reason = "legacy"
            scroll_failure_reason: str | None = None
            try:
                if source_key == "linkedin":
                    # For LinkedIn search pages we prefer a fresh tab. Reusing the feed tab sometimes
                    # yields Page.goto net::ERR_ABORTED / frame-detached.
                    if fetch_label.startswith("linkedin-search"):
                        page = None
                        reuse_existing = False
                    else:
                        page = self._reuse_or_prepare_linkedin_page(requested_url=url, label=fetch_label)
                        reuse_existing = page is not None
                if page is None:
                    self._write_attach_diagnostics(label=f"{fetch_label}-new-page-start", extra={"requested_url": url})
                    page = self._context.new_page()
                    self._write_attach_diagnostics(label=f"{fetch_label}-new-page-opened", extra={"requested_url": url})
                else:
                    self._write_attach_diagnostics(label=f"{fetch_label}-reuse-page-opened", extra={"requested_url": url, "current_url": getattr(page, "url", "")})
                self._sleep(source=self.config.source_name)
                self._write_attach_diagnostics(label=f"{fetch_label}-goto-start", extra={"requested_url": url, "timeout_ms": self.config.navigation_timeout_ms, "reused_page": reuse_existing})
                def _do_goto() -> None:
                    if source_key == "linkedin" and fetch_label.startswith("linkedin-search"):
                        wall = float(os.getenv("JOB_INTEL_BROWSER_GOTO_WALL_TIMEOUT_SECONDS", "70"))
                        with _WallTimeout(wall, f"Page.goto wall-timeout after {wall}s"):
                            page.goto(url, wait_until="domcontentloaded", timeout=self.config.navigation_timeout_ms)
                    else:
                        page.goto(url, wait_until="domcontentloaded", timeout=self.config.navigation_timeout_ms)
                try:
                    _do_goto()
                except Exception as exc:
                    msg = str(exc)
                    transient = source_key == "linkedin" and ("ERR_NETWORK_CHANGED" in msg or "ERR_ABORTED" in msg or "frame was detached" in msg)
                    if not transient:
                        raise
                    self._write_attach_diagnostics(label=f"{fetch_label}-goto-retry", extra={"requested_url": url, "error": msg[:300]})
                    page.wait_for_timeout(1200)
                    _do_goto()
                self._write_attach_diagnostics(label=f"{fetch_label}-goto-done", extra={"requested_url": url, "reused_page": reuse_existing})
                page.wait_for_timeout(self.config.scroll_pause_ms)
                self._humanize_page(page, url=url, deterministic_measurement=plan is not None)
                if plan is not None and source_key == "linkedin":
                    (
                        scroll_trace,
                        scroll_checkpoints,
                        scroll_stop_reason,
                        scroll_failure_reason,
                    ) = self._execute_linkedin_scroll_plan(page=page, plan=plan)
                else:
                    for _ in range(scroll_count):
                        page.mouse.wheel(0, 1800)
                        page.wait_for_timeout(self.config.scroll_pause_ms)
                        scroll_trace.append({"step": len(scroll_trace) + 1, "completed": True})
                html = page.content()
                self._write_attach_diagnostics(label=f"{fetch_label}-content-read", extra={"requested_url": url, "html_length": len(html), "reused_page": reuse_existing})
                final_url = str(getattr(page, "url", "") or url)
                dom_ids = self._linkedin_dom_unique_job_ids(page) if source_key == "linkedin" else frozenset()
                artifact_ref = None
                if capture_label:
                    try:
                        artifact_ref = self._capture_page_diagnostics(
                            page=page,
                            label=capture_label,
                            html=html,
                            extra={
                                "requested_url": url,
                                "final_url": final_url,
                                "page_offset": page_offset,
                                "planned_scroll_steps": (
                                    plan.max_scroll_checkpoints if plan is not None else scroll_count
                                ),
                                "completed_scroll_steps": len(scroll_trace),
                                "reused_page": reuse_existing,
                            },
                        )
                    except Exception as exc:
                        reason = f"diagnostic artifact unavailable: {exc}"
                        self._mark_critical_degradation(reason)
                        raise BrowserNativeUnavailable(reason) from exc
                    if artifact_ref is None:
                        reason = "diagnostic artifact unavailable: no diagnostics directory"
                        self._mark_critical_degradation(reason)
                        raise BrowserNativeUnavailable(reason)
                return BrowserFetchResult(
                    requested_url=url,
                    final_url=final_url,
                    html=html,
                    html_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
                    page_offset=page_offset,
                    planned_scroll_steps=(
                        plan.max_scroll_checkpoints if plan is not None else scroll_count
                    ),
                    completed_scroll_steps=len(scroll_trace),
                    scroll_trace=tuple(scroll_trace),
                    dom_unique_job_ids=dom_ids,
                    artifact_ref=artifact_ref,
                    scroll_checkpoints=tuple(scroll_checkpoints),
                    scroll_stop_reason=scroll_stop_reason,
                    scroll_failure_reason=scroll_failure_reason,
                )
            finally:
                if page is not None and not reuse_existing:
                    with suppress(Exception):
                        page.close()

        try:
            return _attempt_fetch()
        except BrowserNativeUnavailable:
            raise
        except Exception as exc:
            if plan is not None:
                self._mark_critical_degradation(f"page plan failed: {exc}")
            page_obj = locals().get("page")
            html_obj = locals().get("html")
            self._write_browser_failure_diagnostics(
                label=f"{fetch_label}-failure-state",
                requested_url=url,
                error=str(exc),
                page=page_obj,
                html=html_obj,
            )
            raise BrowserNativeUnavailable(f"Playwright browser fetch failed: {exc}") from exc
        finally:
            self._last_fetch_seconds = max(0.0, time.perf_counter() - start)

    def fetch_html(self, url: str, *, scrolls: int | None = None, capture_label: str | None = None) -> str:
        result = self.fetch_page(url, scrolls=scrolls, capture_label=capture_label)
        self._last_fetch_result = result
        return result.html


    def _sleep(self, *, source: str | None = None, extra_bias_ms: tuple[int, int] | None = None) -> None:
        source_key = (source or self.config.source_name or "").strip().lower()
        min_delay = self.config.min_delay_ms
        max_delay = self.config.max_delay_ms
        if source_key == "linkedin":
            min_delay += 200
            max_delay += 900
        if extra_bias_ms:
            min_delay += extra_bias_ms[0]
            max_delay += extra_bias_ms[1]
        delay = random.uniform(min_delay, max_delay) / 1000.0
        time.sleep(delay)

    def _humanize_page(
        self, page: Any, *, url: str, deterministic_measurement: bool = False
    ) -> None:
        source_key = (self.config.source_name or "").strip().lower()
        lowered_url = url.lower()
        try:
            if source_key == "linkedin" and "linkedin.com/jobs" in lowered_url:
                if deterministic_measurement:
                    page.wait_for_timeout(random.randint(700, 2400))
                    return
                if random.random() < 0.75:
                    page.wait_for_timeout(random.randint(700, 2400))
                if random.random() < 0.50:
                    page.mouse.wheel(0, random.randint(220, 1100))
                    page.wait_for_timeout(random.randint(300, 1100))
                if random.random() < 0.22:
                    page.mouse.wheel(0, -random.randint(120, 360))
                    page.wait_for_timeout(random.randint(180, 800))
                return
            if source_key == "linkedin" and "linkedin.com/feed" in lowered_url:
                if random.random() < 0.60:
                    page.wait_for_timeout(random.randint(500, 1800))
                return
        except Exception:
            return

    def _observe_page(self, url: str, html: str, vacancies_found: int, *, detail_page: bool = False, page_depth: int | None = None) -> None:
        self._health.update(
            url=url,
            html=html,
            vacancies_found=vacancies_found,
            detail_page=detail_page,
            page_load_seconds=getattr(self, "_last_fetch_seconds", None),
            page_depth=page_depth,
        )

    def _detail_candidates(self, html: str, *, page_url: str, source: str) -> list[str]:
        parser = _LinkCapture()
        parser.feed(html)
        candidates: list[str] = []
        for text, href in parser.links:
            absolute = urljoin(page_url, href)
            normalized = absolute.lower()
            if source == "linkedin" and "linkedin.com/jobs/view" in normalized:
                candidates.append(absolute)
            elif source == "company_career" and any(domain in normalized for domain in ("greenhouse.io", "lever.co", "ashbyhq.com", "careers", "jobs", "vacancy", "openings")):
                candidates.append(absolute)
        return list(dict.fromkeys(candidates))

    def _maybe_open_noise_page(self, *, page_url: str, html: str, source: str) -> list[Vacancy]:
        if random.random() > self.config.noise_probability:
            return []
        candidates = self._detail_candidates(html, page_url=page_url, source=source)
        if not candidates:
            return []
        detail_url = random.choice(candidates)
        detail_html = self.fetch_html(detail_url, scrolls=0)
        detail_vacancies = {
            "linkedin": extract_linkedin_vacancies_from_html,
            "company_career": extract_company_career_vacancies_from_html,
        }[source](detail_html, page_url=detail_url)
        self._observe_page(detail_url, detail_html, len(detail_vacancies), detail_page=True)
        return detail_vacancies

    def _maybe_open_detail_vacancy(self, *, source: str, vacancies: list[Vacancy]) -> list[Vacancy]:
        if not vacancies or random.random() > max(0.25, self.config.noise_probability):
            return []
        candidates = [vacancy for vacancy in vacancies if vacancy.url]
        if not candidates:
            return []
        candidate = random.choice(candidates)
        detail_url = candidate.url
        detail_html = self.fetch_html(detail_url, scrolls=0)
        detail_vacancies = {
            "linkedin": extract_linkedin_vacancies_from_html,
        }.get(source, extract_company_career_vacancies_from_html)(detail_html, page_url=detail_url)
        self._observe_page(detail_url, detail_html, len(detail_vacancies), detail_page=True)
        return detail_vacancies

    def _linkedin_page_plan(self, max_pages: int, *, execution_plan: Any | None = None) -> list[int]:
        if execution_plan is not None:
            return list(execution_plan.page_offsets)
        allowed_pages = max(1, min(max_pages, self.config.max_linkedin_pages))
        plan = [0]
        if allowed_pages > 1 and random.random() < self.config.linkedin_followup_page_probability:
            followups = list(range(1, allowed_pages))
            random.shuffle(followups)
            plan.extend(followups[:1])
        return plan

    def search_linkedin(
        self,
        query: str,
        *,
        max_pages: int = 1,
        run_id: str | None = None,
        query_id: str | None = None,
        cell_id: str | None = None,
        geography_location: str | None = None,
        geography_geo_id: str | None = None,
        execution_plan: Mapping[str, Any] | Any | None = None,
    ) -> list[Vacancy]:
        if not (geography_location or geography_geo_id):
            raise BrowserNativeUnavailable(
                "blocked_unsupported_geography: LinkedIn search requires a confirmed location or geoId"
            )
        url = build_linkedin_search_url(
            keywords=query,
            location=geography_location,
            geo_id=geography_geo_id,
        )
        plan = self._coerce_linkedin_execution_plan(execution_plan)
        vacancies: list[Vacancy] = []
        self._health.source = "linkedin"
        trace: dict[str, Any] = {
            "browser_start_ms": 0,
            "search_pages_ms": 0,
            "detail_pages_ms": 0,
            "extract_ms": 0,
            "normalize_ms": 0,
            "filter_ms": 0,
            "session_health_ms": 0,
            "login_wall_check_ms": 0,
            "pages_fetched": 0,
            "detail_pages_opened": 0,
            "vacancies_extracted": 0,
            "login_wall_hits": 0,
            "auth_redirects": 0,
            "anti_bot_events": 0,
            "pages": [],
            "zero_result_reason": "",
            "planned_page_offsets": [],
            "completed_page_offsets": [],
            "scroll_checkpoints": [],
            "stop_reason": "",
            "failure_reason": None,
            "execution_plan_version": plan.version if plan is not None else None,
        }
        started = time.perf_counter()
        try:
            self._validate_linkedin_auth()
        except Exception as exc:
            if plan is not None:
                self._mark_critical_degradation(f"linkedin authentication failed: {exc}")
                trace["failure_reason"] = str(exc)
                trace["stop_reason"] = "critical_degradation"
                self._last_search_trace = trace
            raise
        trace["login_wall_check_ms"] = int(round((time.perf_counter() - started) * 1000))
        page_plan = self._linkedin_page_plan(max_pages, execution_plan=plan)
        trace["planned_page_offsets"] = list(page_plan)
        for page_index in page_plan:
            self._sleep(source="linkedin", extra_bias_ms=(250, 1300))
            page_url = (
                build_linkedin_search_url(
                    keywords=query,
                    location=geography_location,
                    geo_id=geography_geo_id,
                    start=page_index if plan is not None else page_index * 25,
                )
                if page_index
                else url
            )
            started = time.perf_counter()
            capture_label = f"{run_id or 'run-unknown'}-{query_id or 'query-unknown'}-cell-{cell_id or 'unknown'}-page-{page_index}"
            try:
                if plan is not None:
                    page_result = self.fetch_page(
                        page_url,
                        page_offset=page_index,
                        capture_label=capture_label,
                        execution_plan=plan,
                    )
                    fetched = page_result.html
                    self._last_fetch_result = page_result
                else:
                    fetched = self.fetch_html(page_url, capture_label=capture_label)
                    page_result = getattr(self, "_last_fetch_result", None)
            except Exception as exc:
                if plan is not None:
                    self._mark_critical_degradation(f"page offset {page_index} failed: {exc}")
                    trace["failure_reason"] = str(exc)
                    trace["stop_reason"] = "critical_degradation"
                    self._last_search_trace = trace
                raise
            if not isinstance(page_result, BrowserFetchResult) or page_result.requested_url != page_url or page_result.html != fetched:
                page_result = BrowserFetchResult(
                    requested_url=page_url,
                    final_url=page_url,
                    html=fetched,
                    html_sha256=hashlib.sha256(fetched.encode("utf-8")).hexdigest(),
                    page_offset=page_index,
                    planned_scroll_steps=0,
                    completed_scroll_steps=0,
                    scroll_trace=(),
                    dom_unique_job_ids=frozenset(),
                    artifact_ref=None,
                )
            trace["completed_page_offsets"].append(page_index)
            trace["scroll_checkpoints"].extend(page_result.scroll_checkpoints)
            if page_result.scroll_failure_reason and plan is not None:
                trace["failure_reason"] = page_result.scroll_failure_reason
            trace["stop_reason"] = page_result.scroll_stop_reason
            html = page_result.html
            trace["search_pages_ms"] += int(round((time.perf_counter() - started) * 1000))
            started = time.perf_counter()
            page_vacancies = extract_linkedin_vacancies_from_html(html, page_url=page_url)
            trace["extract_ms"] += int(round((time.perf_counter() - started) * 1000))
            pre_filter_vacancies = _linkedin_card_vacancies_from_html(
                html, page_url=page_url, apply_role_filter=False
            )
            pre_filter_vacancies.extend(
                _vacancy_from_jobposting(jobposting, source="linkedin", page_url=page_url)
                for jobposting in _jobposting_objects(html)
            )
            dom_ids = page_result.dom_unique_job_ids
            returned_ids = {
                job_id
                for vacancy in page_vacancies
                for job_id in [_linkedin_job_id_from_url(vacancy.url)]
                if job_id
            }
            parsed_ids = sorted(
                {
                    job_id
                    for vacancy in pre_filter_vacancies
                    for job_id in [_linkedin_job_id_from_url(vacancy.url)]
                    if job_id
                } & dom_ids
            )
            returned_ids &= dom_ids
            excluded_by_reason = {
                job_id: "role_filter"
                for job_id in sorted(set(parsed_ids) - returned_ids)
            }
            unexplained_ids = sorted(dom_ids - set(parsed_ids) - set(excluded_by_reason))
            trace["pages"].append(
                {
                    "requested_url": page_result.requested_url,
                    "final_url": page_result.final_url,
                    "html_sha256": page_result.html_sha256,
                    "dom_unique_job_ids": sorted(page_result.dom_unique_job_ids),
                    "parsed_unique_job_ids_before_role_filter": parsed_ids,
                    "returned_unique_job_ids": sorted(returned_ids),
                    "excluded_job_ids_by_reason": excluded_by_reason,
                    "unexplained_dom_job_ids": unexplained_ids,
                    "planned_scroll_steps": page_result.planned_scroll_steps,
                    "completed_scroll_steps": page_result.completed_scroll_steps,
                    "scroll_trace": list(page_result.scroll_trace),
                    "artifact_ref": page_result.artifact_ref,
                }
            )
            self._observe_page(page_url, html, len(page_vacancies))
            trace["vacancies_extracted"] += len(page_vacancies)
            vacancies.extend(page_vacancies)
            started = time.perf_counter()
            detail_rows = (
                []
                if plan is not None
                else self._maybe_open_detail_vacancy(source="linkedin", vacancies=page_vacancies)
            )
            vacancies.extend(detail_rows)
            if self._health.login_walls or self._health.auth_redirects:
                if plan is not None:
                    reason = "login_wall_or_auth_redirect"
                    self._mark_critical_degradation(reason)
                    trace["failure_reason"] = reason
                    trace["stop_reason"] = "critical_degradation"
                trace["detail_pages_ms"] += int(round((time.perf_counter() - started) * 1000))
                break
            noise_rows = (
                []
                if plan is not None
                else self._maybe_open_noise_page(page_url=page_url, html=html, source="linkedin")
            )
            vacancies.extend(noise_rows)
            trace["detail_pages_ms"] += int(round((time.perf_counter() - started) * 1000))
        if plan is not None and trace["completed_page_offsets"] != trace["planned_page_offsets"]:
            reason = "planned page offsets were not all completed"
            self._mark_critical_degradation(reason)
            trace["failure_reason"] = trace["failure_reason"] or reason
            trace["stop_reason"] = "critical_degradation"
        started = time.perf_counter()
        deduped = _dedupe_vacancies(vacancies)
        trace["filter_ms"] = int(round((time.perf_counter() - started) * 1000))
        started = time.perf_counter()
        snapshot = self.session_health_snapshot()
        trace["session_health_ms"] = int(round((time.perf_counter() - started) * 1000))
        trace["pages_fetched"] = int(snapshot.get("pages_fetched") or 0)
        trace["detail_pages_opened"] = int(snapshot.get("detail_pages_opened") or 0)
        trace["login_wall_hits"] = int(snapshot.get("login_walls") or 0)
        trace["auth_redirects"] = int(snapshot.get("auth_redirects") or 0)
        trace["anti_bot_events"] = int(snapshot.get("anti_bot_events") or 0)
        if deduped:
            trace["zero_result_reason"] = ""
        elif trace["login_wall_hits"]:
            trace["zero_result_reason"] = "login_wall"
        elif trace["auth_redirects"]:
            trace["zero_result_reason"] = "auth_redirect"
        elif trace["pages_fetched"] > 0:
            trace["zero_result_reason"] = "search_returned_no_actionable_results"
        else:
            trace["zero_result_reason"] = "no_pages_fetched"
        trace["normalize_ms"] = 0
        trace["planned_search_pages"] = len(page_plan)
        self._last_search_trace = trace
        return deduped

    def crawl_company_page(self, url: str) -> list[Vacancy]:
        self._health.source = "company_career"
        html = self.fetch_html(url)
        vacancies = extract_company_career_vacancies_from_html(html, page_url=url)
        self._observe_page(url, html, len(vacancies))
        return vacancies


def metrics_from_counts(
    *,
    source: str,
    found: int,
    executive_matches: int,
    accepted: int,
    rejected: int,
    extraction_successes: int,
    extraction_attempts: int,
    anti_bot_failures: int = 0,
    normalization_quality: float = 1.0,
    detail_pages_opened: int = 0,
    target_company_hits: int = 0,
) -> AcquisitionMetrics:
    executive_fit_ratio = (executive_matches / found) if found else 0.0
    accepted_rejected_ratio = (accepted / rejected) if rejected else float(accepted) if accepted else 0.0
    extraction_success_rate = (extraction_successes / extraction_attempts) if extraction_attempts else 0.0
    anti_bot_failure_rate = (anti_bot_failures / extraction_attempts) if extraction_attempts else 0.0
    executive_density = executive_fit_ratio
    signal_noise_ratio = (accepted / rejected) if rejected else float(accepted) if accepted else 0.0
    normalization_quality = max(0.0, min(1.0, normalization_quality))
    anti_bot_resilience = max(0.0, 1.0 - anti_bot_failure_rate)
    acceptance_rate = accepted / (accepted + rejected) if (accepted + rejected) else 0.0
    signal_noise_quality = max(0.0, min(1.0, signal_noise_ratio))
    behavioral_browsing_quality = min(1.0, detail_pages_opened / max(extraction_attempts, 1))
    target_company_signal = min(1.0, target_company_hits / max(found, 1)) if found else 0.0
    auth_session_health = max(0.0, min(1.0, (anti_bot_resilience * 0.7) + (extraction_success_rate * 0.3)))
    acquisition_quality_score = round(
        (executive_density * 0.28)
        + (acceptance_rate * 0.16)
        + (signal_noise_quality * 0.10)
        + (auth_session_health * 0.22)
        + (behavioral_browsing_quality * 0.08)
        + (target_company_signal * 0.10)
        + (extraction_success_rate * 0.03)
        + (normalization_quality * 0.03),
        4,
    )
    source_reliability = round(max(0.0, extraction_success_rate * anti_bot_resilience * (0.45 + executive_fit_ratio / 2.0 + target_company_signal / 4.0)), 4)
    if found <= 0 and extraction_attempts > 0 and anti_bot_failures >= extraction_attempts / 2:
        status = "blocked"
    elif acquisition_quality_score >= 0.68 and extraction_success_rate >= 0.75 and anti_bot_resilience >= 0.75 and behavioral_browsing_quality >= 0.15:
        status = "operational"
    elif extraction_success_rate >= 0.35 or executive_fit_ratio >= 0.2 or acquisition_quality_score >= 0.4 or anti_bot_resilience < 0.75:
        status = "degraded"
    else:
        status = "low-signal"
    return AcquisitionMetrics(
        source=source,
        vacancies_found=found,
        executive_matches=executive_matches,
        accepted=accepted,
        rejected=rejected,
        extraction_successes=extraction_successes,
        extraction_attempts=extraction_attempts,
        anti_bot_failures=anti_bot_failures,
        executive_fit_ratio=executive_fit_ratio,
        accepted_rejected_ratio=accepted_rejected_ratio,
        extraction_success_rate=extraction_success_rate,
        anti_bot_failure_rate=anti_bot_failure_rate,
        executive_density=executive_density,
        signal_noise_ratio=signal_noise_ratio,
        normalization_quality=normalization_quality,
        acquisition_quality_score=acquisition_quality_score,
        source_reliability=source_reliability,
        status=status,
    )
