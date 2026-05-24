from __future__ import annotations

import imaplib
import json
import os
import random
import re
import time
from email import message_from_bytes
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from importlib.util import find_spec
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

from .models import Vacancy
from .runtime import resolve_browser_profile_base, sha256_text


_BROWSER_PROFILE_DEFAULT = resolve_browser_profile_base() / "company-career"
_BROWSER_PROFILE_DEFAULTS: dict[str, Path] = {
    "linkedin": resolve_browser_profile_base() / "linkedin",
    "headhunter": resolve_browser_profile_base() / "hh",
    "hh": resolve_browser_profile_base() / "hh",
    "company_career": _BROWSER_PROFILE_DEFAULT,
}


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
    max_headhunter_pages: int = 2


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
    email_challenge_attempted: bool = False
    email_challenge_resolved: bool = False
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
        if _looks_like_auth_redirect(url, html):
            self.auth_redirects += 1
        if _looks_like_login_wall(url, html):
            self.login_walls += 1
        if vacancies_found > 0:
            self.successful_extractions += 1
            if not _looks_like_login_wall(url, html) and not _looks_like_auth_redirect(url, html):
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
        if source_key == "headhunter":
            overrides.append(os.getenv("JOB_INTEL_BROWSER_PROFILE_DIR_HH", "").strip())
        elif source_key == "hh":
            overrides.append(os.getenv("JOB_INTEL_BROWSER_PROFILE_DIR_HH", "").strip())
        elif source_key == "linkedin":
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
    if source not in {"linkedin", "headhunter", "hh", "company_career"}:
        return
    if not _browser_profile_is_populated(config.user_data_dir):
        raise BrowserNativeUnavailable(
            f"{source} browser profile directory {config.user_data_dir} is missing or empty; refusing to fall back to a shared or blank profile."
        )


def browser_native_available() -> bool:
    try:
        return find_spec("playwright.sync_api") is not None
    except ModuleNotFoundError:
        return False


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


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
    if "hh.ru" in host:
        return "HeadHunter"
    if path_parts and path_parts[0] not in {"jobs", "vacancy", "careers", "roles", "positions", "openings"}:
        return _normalize_whitespace(path_parts[0].replace("-", " ").replace("_", " ").title())
    return _normalize_whitespace(host.replace("www.", "").split(":")[0].split(".")[0].title()) or "Unknown"


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
                return rendered
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


def _session_status(health: BrowserSessionHealth) -> str:
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
        if not (
            any(domain in normalized for domain in ("linkedin.com/jobs/view", "hh.ru/vacancy", "greenhouse.io", "lever.co", "ashbyhq.com"))
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


def extract_linkedin_vacancies_from_html(html: str, *, page_url: str) -> list[Vacancy]:
    vacancies = [_vacancy_from_jobposting(jobposting, source="linkedin", page_url=page_url) for jobposting in _jobposting_objects(html)]
    if not vacancies:
        vacancies = _link_vacancies_from_html(html, source="linkedin", page_url=page_url)
    return _dedupe_vacancies(vacancies)


def extract_headhunter_vacancies_from_html(html: str, *, page_url: str) -> list[Vacancy]:
    vacancies = [_vacancy_from_jobposting(jobposting, source="headhunter", page_url=page_url) for jobposting in _jobposting_objects(html)]
    if not vacancies:
        vacancies = _link_vacancies_from_html(html, source="headhunter", page_url=page_url)
    return _dedupe_vacancies(vacancies)


def extract_company_career_vacancies_from_html(html: str, *, page_url: str) -> list[Vacancy]:
    structured = [_vacancy_from_jobposting(jobposting, source="company_career", page_url=page_url) for jobposting in _jobposting_objects(html)]
    link_vacancies = _link_vacancies_from_html(html, source="company_career", page_url=page_url)
    return _dedupe_vacancies(structured + link_vacancies)


class BrowserSourceClient:
    def __init__(self, config: BrowserAcquisitionConfig | None = None):
        self.config = config or BrowserAcquisitionConfig()
        self._playwright = None
        self._context = None
        self._health = BrowserSessionHealth(source="browser")
        self._health.browser_profile = str(self.config.user_data_dir)

    def __enter__(self) -> "BrowserSourceClient":
        if not browser_native_available():
            raise BrowserNativeUnavailable("Playwright is not installed. Install playwright to enable browser-native acquisition.")
        from playwright.sync_api import sync_playwright  # type: ignore

        profile_name = self.config.source_name.strip().lower().replace("-", "_")
        if profile_name in {"linkedin", "headhunter", "hh", "company_career"}:
            _ensure_required_browser_profile(profile_name, self.config)

        self.config.user_data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.config.user_data_dir),
                headless=self.config.headless,
                slow_mo=self.config.slow_mo_ms,
                viewport={"width": 1440, "height": 1600},
            )
        except Exception as exc:
            self._context = None
            self._playwright = None
            raise BrowserNativeUnavailable(f"Playwright browser launch failed: {exc}") from exc
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._context is not None:
            self._context.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._context = None
        self._playwright = None

    def _page_contains_any(self, html: str, markers: tuple[str, ...]) -> bool:
        lowered = html.lower()
        return any(marker in lowered for marker in markers)

    def _validate_authenticated_html(
        self,
        *,
        source: str,
        url: str,
        html: str,
        required_markers: tuple[str, ...],
        login_markers: tuple[str, ...],
    ) -> None:
        if _looks_like_login_wall(url, html) or _looks_like_auth_redirect(url, html):
            self._health.update(url=url, html=html, vacancies_found=0)
            raise BrowserNativeUnavailable(f"{source} authentication validation landed on a login wall or redirect at {url}")
        if not self._page_contains_any(html, required_markers):
            if login_markers and self._page_contains_any(html, login_markers):
                self._health.update(url=url, html=html, vacancies_found=0)
                raise BrowserNativeUnavailable(f"{source} authentication validation found sign-in markers at {url}")
            if source == "linkedin":
                raise BrowserNativeUnavailable(f"{source} authenticated feed/profile markers were not visible at {url}")
        self._health.last_successful_authenticated_request = url

    @staticmethod
    def _extract_message_text(message: Any) -> str:
        parts: list[str] = []
        if getattr(message, "is_multipart", lambda: False)():
            for part in message.walk():
                if part.get_content_maintype() != "text":
                    continue
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="ignore"))
                except Exception:
                    continue
        else:
            try:
                payload = message.get_payload(decode=True)
                if payload:
                    parts.append(payload.decode(message.get_content_charset() or "utf-8", errors="ignore"))
            except Exception:
                body = message.get_payload()
                if isinstance(body, str):
                    parts.append(body)
        try:
            subject = message.get("Subject") or ""
        except Exception:
            subject = ""
        if subject:
            parts.append(str(subject))
        return "\n".join(parts)

    def _read_headhunter_otp_from_gmail(self) -> str | None:
        gmail_account = os.getenv("JOB_INTEL_GMAIL_ADDRESS", "").strip() or os.getenv("JOB_INTEL_GMAIL_USERNAME", "").strip()
        gmail_password = os.getenv("JOB_INTEL_GMAIL_APP_PASSWORD", "").strip() or os.getenv("JOB_INTEL_GMAIL_PASSWORD", "").strip()
        if not gmail_account or not gmail_password:
            return None
        host = os.getenv("JOB_INTEL_GMAIL_IMAP_HOST", "imap.gmail.com").strip()
        port = int(os.getenv("JOB_INTEL_GMAIL_IMAP_PORT", "993"))
        sender_hint = os.getenv("JOB_INTEL_HH_OTP_FROM", "hh.ru").strip() or "hh.ru"
        subject_hint = os.getenv("JOB_INTEL_HH_OTP_SUBJECT_HINT", "code").strip() or "code"
        try:
            with imaplib.IMAP4_SSL(host, port) as client:
                client.login(gmail_account, gmail_password)
                client.select("INBOX")
                status, data = client.search(None, f'(FROM "{sender_hint}" SUBJECT "{subject_hint}")')
                if status != "OK":
                    status, data = client.search(None, 'ALL')
                ids = [item for item in (data[0].split() if data and data[0] else []) if item]
                for message_id in reversed(ids[-10:]):
                    fetch_status, payload = client.fetch(message_id, "(RFC822)")
                    if fetch_status != "OK" or not payload or not payload[0]:
                        continue
                    raw_bytes = payload[0][1]
                    if not raw_bytes:
                        continue
                    message = message_from_bytes(raw_bytes)
                    body = self._extract_message_text(message)
                    match = re.search(r"\b(\d{4,8})\b", body)
                    if match:
                        return match.group(1)
        except Exception:
            return None
        return None

    def _attempt_headhunter_otp_recovery(self, login_url: str) -> bool:
        if self._context is None:
            raise BrowserNativeUnavailable("BrowserSourceClient must be entered as a context manager first.")
        self._health.email_challenge_attempted = True
        page = self._context.new_page()
        try:
            page.goto(login_url, wait_until="domcontentloaded", timeout=self.config.navigation_timeout_ms)
            page.wait_for_timeout(self.config.scroll_pause_ms)
            login_email = os.getenv("JOB_INTEL_HH_LOGIN_EMAIL", "").strip() or os.getenv("JOB_INTEL_GMAIL_ADDRESS", "").strip()
            if login_email:
                for selector in (
                    "input[type='email']",
                    "input[name*='email']",
                    "input[name='login']",
                    "input[name*='login']",
                ):
                    locator = page.locator(selector)
                    if locator.count():
                        locator.first.fill(login_email)
                        break
            otp_code = self._read_headhunter_otp_from_gmail()
            if not otp_code:
                return False
            for selector in (
                "input[name*='code']",
                "input[name*='otp']",
                "input[type='tel']",
                "input[inputmode='numeric']",
                "input[autocomplete='one-time-code']",
            ):
                locator = page.locator(selector)
                if locator.count():
                    locator.first.fill(otp_code)
                    break
            for selector in (
                "button:has-text('Verify')",
                "button:has-text('Continue')",
                "button:has-text('Submit')",
                "button:has-text('Sign in')",
                "button[type='submit']",
            ):
                locator = page.locator(selector)
                if locator.count():
                    locator.first.click()
                    break
            else:
                page.keyboard.press("Enter")
            page.wait_for_timeout(self.config.scroll_pause_ms)
            self._health.email_challenge_resolved = True
            return True
        except Exception:
            return False
        finally:
            page.close()

    def _validate_linkedin_auth(self) -> None:
        url = "https://www.linkedin.com/feed/"
        self._health.auth_attempted = True
        html = self.fetch_html(url, scrolls=0)
        self._validate_authenticated_html(
            source="linkedin",
            url=url,
            html=html,
            required_markers=(
                "global-nav__me",
                "feed-identity-module",
                "nav-item__profile-member-photo",
                "profile-photo",
                "my network",
                "feed",
            ),
            login_markers=("sign in", "log in", "join linkedin", "create a new account"),
        )

    def _validate_headhunter_auth(self) -> None:
        url = "https://hh.ru/"
        self._health.auth_attempted = True
        html = self.fetch_html(url, scrolls=0)
        if _looks_like_login_wall(url, html) or _looks_like_auth_redirect(url, html):
            if self._attempt_headhunter_otp_recovery("https://hh.ru/account/login?backurl=https%3A%2F%2Fhh.ru%2Fsearch%2Fvacancy"):
                html = self.fetch_html(url, scrolls=0)
        self._validate_authenticated_html(
            source="headhunter",
            url=url,
            html=html,
            required_markers=(
                "profile",
                "avatar",
                "logout",
                "vacancy search",
                "search",
                "resume",
            ),
            login_markers=("sign in", "log in", "authorize", "verification code", "otp", "captcha"),
        )

    def session_health_snapshot(self) -> dict[str, Any]:
        return self._health.snapshot()

    def fetch_html(self, url: str, *, scrolls: int | None = None) -> str:
        if self._context is None:
            raise BrowserNativeUnavailable("BrowserSourceClient must be entered as a context manager first.")
        scroll_count = self.config.max_scrolls if scrolls is None else max(0, scrolls)
        start = time.perf_counter()
        try:
            page = self._context.new_page()
            try:
                self._sleep()
                page.goto(url, wait_until="domcontentloaded", timeout=self.config.navigation_timeout_ms)
                page.wait_for_timeout(self.config.scroll_pause_ms)
                for _ in range(scroll_count):
                    page.mouse.wheel(0, 1800)
                    page.wait_for_timeout(self.config.scroll_pause_ms)
                return page.content()
            finally:
                page.close()
        except Exception as exc:
            raise BrowserNativeUnavailable(f"Playwright browser fetch failed: {exc}") from exc
        finally:
            self._last_fetch_seconds = max(0.0, time.perf_counter() - start)

    def _sleep(self) -> None:
        delay = random.uniform(self.config.min_delay_ms, self.config.max_delay_ms) / 1000.0
        time.sleep(delay)

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
            elif source == "headhunter" and "hh.ru/vacancy" in normalized:
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
            "headhunter": extract_headhunter_vacancies_from_html,
            "company_career": extract_company_career_vacancies_from_html,
        }[source](detail_html, page_url=detail_url)
        self._observe_page(detail_url, detail_html, len(detail_vacancies), detail_page=True)
        return detail_vacancies

    def _maybe_open_detail_vacancy(self, *, source: str, vacancies: list[Vacancy]) -> list[Vacancy]:
        if not vacancies or random.random() > max(0.25, self.config.noise_probability):
            return []
        candidate = next((vacancy for vacancy in vacancies if vacancy.url), None)
        if candidate is None:
            return []
        detail_url = candidate.url
        detail_html = self.fetch_html(detail_url, scrolls=0)
        detail_vacancies = {
            "linkedin": extract_linkedin_vacancies_from_html,
            "headhunter": extract_headhunter_vacancies_from_html,
        }.get(source, extract_company_career_vacancies_from_html)(detail_html, page_url=detail_url)
        self._observe_page(detail_url, detail_html, len(detail_vacancies), detail_page=True)
        return detail_vacancies

    def _linkedin_page_plan(self, max_pages: int) -> list[int]:
        allowed_pages = max(1, min(max_pages, self.config.max_linkedin_pages))
        plan = [0]
        if allowed_pages > 1 and random.random() < self.config.linkedin_followup_page_probability:
            followups = list(range(1, allowed_pages))
            random.shuffle(followups)
            plan.extend(followups[:1])
        return plan

    def _headhunter_page_plan(self, max_pages: int) -> list[int]:
        allowed_pages = max(1, min(max_pages, self.config.max_headhunter_pages))
        plan = [0]
        if allowed_pages > 1 and random.random() < 0.25:
            followups = list(range(1, allowed_pages))
            random.shuffle(followups)
            plan.extend(followups[:1])
        return plan

    def search_linkedin(self, query: str, *, max_pages: int = 1) -> list[Vacancy]:
        url = f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(query)}"
        vacancies: list[Vacancy] = []
        self._health.source = "linkedin"
        self._validate_linkedin_auth()
        for page_index in self._linkedin_page_plan(max_pages):
            page_url = f"{url}&start={page_index * 25}" if page_index else url
            html = self.fetch_html(page_url)
            page_vacancies = extract_linkedin_vacancies_from_html(html, page_url=page_url)
            self._observe_page(page_url, html, len(page_vacancies))
            vacancies.extend(page_vacancies)
            vacancies.extend(self._maybe_open_detail_vacancy(source="linkedin", vacancies=page_vacancies))
            if self._health.login_walls or self._health.auth_redirects:
                break
            vacancies.extend(self._maybe_open_noise_page(page_url=page_url, html=html, source="linkedin"))
        return _dedupe_vacancies(vacancies)

    def search_headhunter(self, query: str, *, max_pages: int = 1) -> list[Vacancy]:
        url = f"https://hh.ru/search/vacancy?text={quote_plus(query)}"
        vacancies: list[Vacancy] = []
        self._health.source = "headhunter"
        self._validate_headhunter_auth()
        for page_index in self._headhunter_page_plan(max_pages):
            page_url = f"{url}&page={page_index}" if page_index else url
            html = self.fetch_html(page_url)
            page_vacancies = extract_headhunter_vacancies_from_html(html, page_url=page_url)
            self._observe_page(page_url, html, len(page_vacancies))
            vacancies.extend(page_vacancies)
            vacancies.extend(self._maybe_open_detail_vacancy(source="headhunter", vacancies=page_vacancies))
            if self._health.login_walls or self._health.auth_redirects:
                break
        return _dedupe_vacancies(vacancies)

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
