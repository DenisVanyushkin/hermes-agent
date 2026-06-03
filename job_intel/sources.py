from __future__ import annotations

import json
import os
import random
import re
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse, urlsplit, urlunsplit

import requests

from .browser_sourcing import (
    BrowserAcquisitionConfig,
    BrowserNativeUnavailable,
    BrowserSourceClient,
    browser_native_available,
    extract_company_career_vacancies_from_html,
    extract_headhunter_vacancies_from_html,
    extract_linkedin_vacancies_from_html,
    metrics_from_counts,
    resolve_browser_config,
    _ensure_required_browser_profile,
)
from .models import Vacancy
from .runtime import retry_with_backoff, sha256_text


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    source: str


class SourceFetchError(RuntimeError):
    pass


class _DuckDuckGoParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hits: list[tuple[str, str]] = []
        self._capture = False
        self._current_href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        class_name = attrs.get("class", "")
        if tag == "a" and "result__a" in class_name:
            self._capture = True
            self._current_href = attrs.get("href", "")
            self._text = []

    def handle_endtag(self, tag):
        if tag == "a" and self._capture:
            text = "".join(self._text).strip()
            if self._current_href and text:
                self.hits.append((text, self._current_href))
            self._capture = False
            self._current_href = ""
            self._text = []

    def handle_data(self, data):
        if self._capture:
            self._text.append(data)



BOARD_LABELS = ("RemoteOK", "LinkedIn", "Greenhouse", "Lever", "Ashby", "HeadHunter", "hh.ru")
EXECUTIVE_ROLE_HINTS = (
    "vp",
    "vice president",
    "director",
    "head of",
    "chief",
    "cpo",
    "gm",
    "general manager",
    "product",
    "monetization",
    "revenue",
    "growth",
    "platform",
    "ecosystem",
    "subscription",
    "fintech",
    "saas",
    "partnership",
)
EXECUTIVE_TITLE_HINTS = (
    "vp",
    "vice president",
    "director",
    "head of",
    "chief",
    "cpo",
    "gm",
    "general manager",
    "lead",
    "monetization",
    "growth",
)
LOW_SIGNAL_HINTS = (
    "sales development representative",
    "business development representative",
    "account executive",
    "support",
    "customer support",
    "recruiter",
    "talent acquisition",
    "designer",
    "copywriter",
    "developer",
    "engineer",
    "qa engineer",
    "project manager",
    "product manager",
    "senior product manager",
    "associate product manager",
)


def extract_duckduckgo_destination_url(href: str) -> str:
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urlsplit(href)
    if "duckduckgo.com" not in parsed.netloc:
        return href
    query = parse_qs(parsed.query)
    redirect = query.get("uddg", [""])[0]
    if redirect:
        return unquote(redirect)
    return href


def _strip_board_labels(title: str) -> str:
    cleaned = title.strip()
    for label in BOARD_LABELS:
        cleaned = re.sub(rf"\s*[-|·]\s*{re.escape(label)}\s*$", "", cleaned, flags=re.I)
    return re.sub(r"\s+", " ", cleaned).strip()


def _slug_to_company(slug: str) -> str:
    slug = slug.replace("_", "-").strip("/")
    if not slug:
        return "Unknown"
    return re.sub(r"\s+", " ", slug.replace("-", " ").title()).strip() or "Unknown"


def _company_from_url(dest_url: str) -> str:
    parsed = urlparse(dest_url)
    host = parsed.netloc.lower()
    parts = [part for part in parsed.path.split("/") if part]
    if "lever.co" in host and parts:
        return _slug_to_company(parts[0])
    if "greenhouse.io" in host and parts:
        return _slug_to_company(parts[0])
    if "ashbyhq.com" in host and parts:
        return _slug_to_company(parts[0])
    if "remoteok.com" in host and parts:
        return _slug_to_company(parts[0])
    if "linkedin.com" in host and parts:
        return "Unknown"
    if "hh.ru" in host:
        return "HeadHunter"
    return host.split(":")[0].replace("www.", "").title() or "Unknown"


def _infer_company_and_title(title: str, dest_url: str) -> tuple[str, str]:
    cleaned = _strip_board_labels(title)
    parsed = urlparse(dest_url)
    host = parsed.netloc.lower()

    patterns = [
        r"^(?P<title>.+?)\s+at\s+(?P<company>.+)$",
        r"^(?P<company>.+?)\s+hiring\s+(?P<title>.+)$",
        r"^(?P<title>.+?)\s+for\s+(?P<company>.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, cleaned, flags=re.I)
        if match:
            company = re.sub(r"\s+", " ", match.group("company")).strip()
            job_title = re.sub(r"\s+", " ", match.group("title")).strip()
            return company, job_title

    if "remoteok.com" in host:
        match = re.match(r"^(?P<title>.+?)\s+at\s+(?P<company>.+)$", cleaned, flags=re.I)
        if match:
            return match.group("company").strip(), match.group("title").strip()

    if "linkedin.com" in host:
        match = re.match(r"^(?P<company>.+?)\s+·\s+(?P<title>.+)$", cleaned)
        if match:
            return match.group("company").strip(), match.group("title").strip()

    if any(board in host for board in ("lever.co", "greenhouse.io", "ashbyhq.com")):
        company = _company_from_url(dest_url)
        job_title = cleaned or company
        if company.lower() in job_title.lower() and len(job_title) > len(company):
            job_title = cleaned
        return company, job_title

    if "hh.ru" in host:
        return "HeadHunter", cleaned or "Vacancy"

    return _company_from_url(dest_url), cleaned or title



def normalize_search_hit(hit: SearchHit) -> Vacancy:
    dest_url = extract_duckduckgo_destination_url(hit.url)
    company, title = _infer_company_and_title(hit.title, dest_url)
    location = "Unknown"
    if "remote" in hit.title.lower() or "remote" in hit.snippet.lower():
        location = "Remote"
    return Vacancy(
        source=hit.source,
        source_id=sha256_text(dest_url)[:16],
        company=company,
        title=title,
        location=location,
        url=dest_url,
        description=hit.snippet or hit.title,
    )


def _job_text(*parts: object) -> str:
    return " ".join(str(part or "") for part in parts).lower()


def _looks_strategic_role(*parts: object) -> bool:
    text = _job_text(*parts)
    if not text:
        return False
    if any(hint in text for hint in LOW_SIGNAL_HINTS):
        return False
    title = _job_text(parts[0] if parts else "")
    if any(hint in title for hint in EXECUTIVE_TITLE_HINTS):
        return True
    return False


def normalize_remoteok_job(job: dict[str, object]) -> Vacancy:
    title = str(job.get("position") or job.get("title") or "Vacancy")
    company = str(job.get("company") or "Unknown")
    location = str(job.get("location") or "Remote") or "Remote"
    url = str(job.get("url") or "")
    description = re.sub(r"<[^>]+>", " ", str(job.get("description") or ""))
    return Vacancy(
        source="remoteok",
        source_id=str(job.get("id") or sha256_text(url)[:16]),
        company=company,
        title=title,
        location=location,
        url=url,
        description=re.sub(r"\s+", " ", description).strip() or title,
        posted_at=str(job.get("date") or "") or None,
        salary=_format_salary({"from": job.get("salary_min"), "to": job.get("salary_max"), "currency": None}),
        metadata={"tags": job.get("tags") or [], "raw": job},
    )


def normalize_remotive_job(job: dict[str, object]) -> Vacancy:
    title = str(job.get("title") or "Vacancy")
    company = str(job.get("company_name") or "Unknown")
    location = str(job.get("candidate_required_location") or job.get("job_type") or "Remote") or "Remote"
    url = str(job.get("url") or "")
    description = re.sub(r"<[^>]+>", " ", str(job.get("description") or ""))
    return Vacancy(
        source="remotive",
        source_id=str(job.get("id") or sha256_text(url)[:16]),
        company=company,
        title=title,
        location=location,
        url=url,
        description=re.sub(r"\s+", " ", description).strip() or title,
        posted_at=str(job.get("publication_date") or "") or None,
        salary=str(job.get("salary") or None) or None,
        metadata={"tags": job.get("tags") or [], "category": job.get("category"), "raw": job},
    )


def search_duckduckgo(query: str, max_results: int = 10) -> list[SearchHit]:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

    def _request() -> requests.Response:
        return requests.get(
            url,
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
                "Referer": "https://duckduckgo.com/",
            },
        )

    resp = retry_with_backoff(_request, attempts=1, base_delay=1.0, exceptions=(requests.RequestException,))
    resp.raise_for_status()
    parser = _DuckDuckGoParser()
    parser.feed(resp.text)
    hits: list[SearchHit] = []
    for title, href in parser.hits[:max_results]:
        hits.append(SearchHit(title=title, url=extract_duckduckgo_destination_url(href), snippet="", source="duckduckgo"))
    return hits



def search_remoteok_jobs(max_results: int = 25) -> list[Vacancy]:
    response = requests.get(
        "https://remoteok.com/api",
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    response.raise_for_status()
    items = response.json()[1:]
    vacancies: list[Vacancy] = []
    for item in items:
        title = str(item.get("position") or item.get("title") or "")
        description = re.sub(r"<[^>]+>", " ", str(item.get("description") or ""))
        if not _looks_strategic_role(title, description, item.get("company"), item.get("tags")):
            continue
        vacancies.append(normalize_remoteok_job(item))
        if len(vacancies) >= max_results:
            break
    return vacancies


def search_remotive_jobs(max_results: int = 25) -> list[Vacancy]:
    response = requests.get(
        "https://remotive.com/api/remote-jobs",
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("jobs", []) if isinstance(payload, dict) else []
    vacancies: list[Vacancy] = []
    for item in items:
        title = str(item.get("title") or "")
        description = re.sub(r"<[^>]+>", " ", str(item.get("description") or ""))
        if not _looks_strategic_role(title, description, item.get("company_name"), item.get("tags"), item.get("category")):
            continue
        vacancies.append(normalize_remotive_job(item))
        if len(vacancies) >= max_results:
            break
    return vacancies


def _browser_config(source: str | None = None) -> BrowserAcquisitionConfig:
    return resolve_browser_config(source)


def _browser_runtime_base_dir() -> Path:
    configured = os.getenv("JOB_INTEL_BROWSER_RUNTIME_DIR", "").strip() or os.getenv("BROWSER_DESKTOP_BASE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    browser_python = os.getenv("JOB_INTEL_BROWSER_PYTHON", "").strip()
    if browser_python:
        path = Path(browser_python).expanduser()
        if path.name == "python" and path.parent.name == "bin" and path.parent.parent.name == "playwright-venv":
            return path.parent.parent.parent
    return Path("/var/lib/browser-desktop")


def _browser_worker_env() -> dict[str, str]:
    env = os.environ.copy()
    base_dir = _browser_runtime_base_dir()
    cache_dir = Path(env.get("XDG_CACHE_HOME", "").strip() or (base_dir / ".cache"))
    browsers_path = Path(env.get("PLAYWRIGHT_BROWSERS_PATH", "").strip() or (cache_dir / "ms-playwright"))
    env["JOB_INTEL_BROWSER_RUNTIME_DIR"] = str(base_dir)
    env["BROWSER_DESKTOP_BASE_DIR"] = str(base_dir)
    env["HOME"] = str(base_dir)
    env["XDG_CACHE_HOME"] = str(cache_dir)
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
    return env


_BROWSER_TIMEOUT_TARGETS = {
    "linkedin": {
        "source": "linkedin",
        "cdp_url": "http://127.0.0.1:9222/json/list",
        "display": ":99",
        "profile": "linkedin",
    },
    "headhunter": {
        "source": "headhunter",
        "cdp_url": "http://127.0.0.1:9223/json/list",
        "display": ":100",
        "profile": "hh",
    },
}


def _browser_diagnostics_dir() -> Path | None:
    configured = os.getenv("JOB_INTEL_BROWSER_DIAGNOSTICS_DIR", "").strip()
    if not configured:
        return None
    path = Path(configured).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _capture_browser_worker_timeout(command: str, browser_python: Path, *, timeout: int, args: tuple[str, ...]) -> None:
    target = _BROWSER_TIMEOUT_TARGETS.get(command)
    diagnostics_dir = _browser_diagnostics_dir()
    if not target or diagnostics_dir is None:
        return
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    base = diagnostics_dir / f"{timestamp}-{target['source']}-worker-timeout"
    payload: dict[str, Any] = {
        "label": f"{target['source']}-worker-timeout",
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "args": list(args),
        "timeout_seconds": timeout,
        "browser_python": str(browser_python),
        "cdp_url": target["cdp_url"],
        "display": target["display"],
        "profile": target["profile"],
    }
    with suppress(Exception):
        response = requests.get(target["cdp_url"], timeout=5)
        payload["cdp_status"] = response.status_code
        payload["cdp_targets"] = response.json()[:25]
    pattern_port = target["cdp_url"].split(":")[-1].split("/")[0]
    ps_cmd = (
        "ps -eo pid=,ppid=,etimes=,%cpu=,%mem=,args= "
        f"| grep 'remote-debugging-port={pattern_port}' "
        f"| grep 'profiles/{target['profile']}' | grep -v grep"
    )
    with suppress(Exception):
        ps_proc = subprocess.run(["bash", "-lc", ps_cmd], capture_output=True, text=True, timeout=10, check=False)
        payload["browser_processes"] = [line for line in (ps_proc.stdout or "").splitlines() if line.strip()]
    screenshot_path = base.with_suffix('.xwd')
    temp_screenshot = Path(f"/tmp/{timestamp}-{target['source']}-worker-timeout.xwd")
    with suppress(Exception):
        shot_proc = subprocess.run(
            [
                "sudo", "-n", "runuser", "-u", "browser", "--", "env",
                f"DISPLAY={target['display']}",
                "xwd", "-root", "-silent", "-out", str(temp_screenshot),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        payload["xwd_returncode"] = shot_proc.returncode
        payload["xwd_stderr"] = (shot_proc.stderr or "").strip()[:1000]
        if temp_screenshot.exists():
            temp_screenshot.replace(screenshot_path)
            payload["screenshot_path"] = str(screenshot_path)
            payload["screenshot_size"] = screenshot_path.stat().st_size
    with suppress(Exception):
        base.with_suffix('.json').write_text(json.dumps(payload, ensure_ascii=True, indent=2))


def _browser_worker_payload(command: str, *args: str, timeout: int = 240) -> dict[str, Any]:
    browser_python = Path(os.getenv("JOB_INTEL_BROWSER_PYTHON", "").strip() or "/var/lib/browser-desktop/playwright-venv/bin/python").expanduser()
    if not browser_python.exists():
        raise SourceFetchError(f"browser worker python missing: {browser_python}")
    try:
        proc = subprocess.run(
            [str(browser_python), "-m", "job_intel.browser_worker", command, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_browser_worker_env(),
        )
    except subprocess.TimeoutExpired as exc:
        _capture_browser_worker_timeout(command, browser_python, timeout=timeout, args=args)
        raise SourceFetchError(f"browser worker timed out after {timeout} seconds") from exc
    payload = None
    stdout = (proc.stdout or "").strip()
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(payload, dict):
        detail = (proc.stderr or stdout or f"browser worker failed with exit code {proc.returncode}").strip()
        raise SourceFetchError(detail)
    if not payload.get("ok"):
        detail = str(payload.get("error") or proc.stderr or stdout or "browser worker failed")
        if payload.get("error_type") == "BrowserNativeUnavailable":
            raise BrowserNativeUnavailable(detail)
        raise SourceFetchError(detail)
    return payload


def fetch_linkedin_vacancies(query: str, *, max_pages: int = 1) -> list[Vacancy]:
    fetch_linkedin_vacancies.last_health = None  # type: ignore[attr-defined]
    if not browser_native_available():
        raise SourceFetchError("Playwright is not installed, so LinkedIn browser-native acquisition is unavailable.")
    config = _browser_config("linkedin")
    _ensure_required_browser_profile("linkedin", config)
    try:
        payload = _browser_worker_payload("linkedin", query, str(max_pages))
        fetch_linkedin_vacancies.last_health = payload.get("session_health")  # type: ignore[attr-defined]
        return [Vacancy.model_validate(item) for item in payload.get("vacancies", [])]
    except BrowserNativeUnavailable as exc:
        raise SourceFetchError(str(exc)) from exc


def fetch_company_career_vacancies(url: str) -> list[Vacancy]:
    fetch_company_career_vacancies.last_health = None  # type: ignore[attr-defined]
    if browser_native_available():
        config = _browser_config("company_career")
        try:
            with BrowserSourceClient(config) as client:
                vacancies = client.crawl_company_page(url)
                fetch_company_career_vacancies.last_health = client.session_health_snapshot()  # type: ignore[attr-defined]
                return vacancies
        except BrowserNativeUnavailable:
            pass
    response = requests.get(
        url,
        timeout=20,
        headers={
            "User-Agent": os.getenv(
                "JOB_INTEL_COMPANY_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    response.raise_for_status()
    return extract_company_career_vacancies_from_html(response.text, page_url=url)


def _request_json(url: str, *, params: dict[str, object], headers: dict[str, str]) -> dict:
    response = requests.get(url, params=params, timeout=30, headers=headers)
    if response.status_code == 403:
        raise SourceFetchError(
            "HeadHunter API returned 403 Forbidden. Browser-native HH acquisition is preferred and does not require JOB_INTEL_HH_ACCESS_TOKEN."
        )
    response.raise_for_status()
    return response.json()


def fetch_headhunter_vacancies(query: str, *, per_page: int = 20) -> list[Vacancy]:
    fetch_headhunter_vacancies.last_health = None  # type: ignore[attr-defined]
    if not browser_native_available():
        raise SourceFetchError("Playwright is not installed, so HeadHunter browser-native acquisition is unavailable.")
    config = _browser_config("headhunter")
    _ensure_required_browser_profile("headhunter", config)
    try:
        payload = _browser_worker_payload("headhunter", query, str(per_page))
        fetch_headhunter_vacancies.last_health = payload.get("session_health")  # type: ignore[attr-defined]
        return [Vacancy.model_validate(item) for item in payload.get("vacancies", [])]
    except BrowserNativeUnavailable as exc:
        raise SourceFetchError(str(exc)) from exc


def _format_salary(salary: dict | None) -> str | None:
    if not salary:
        return None
    parts = [salary.get("from"), salary.get("to"), salary.get("currency")]
    if not any(parts):
        return None
    return " ".join(str(part) for part in parts if part)


ROLE_FAMILIES: list[tuple[str, tuple[str, ...]]] = [
    ("core_executive_product", ("VP Product", "Head of Product", "Director Product", "Chief Product Officer", "Product Lead")),
    ("growth_monetization", ("Growth Product Lead", "Monetization Product Lead", "Product Strategy Lead", "Product Growth Lead")),
    ("platform_ecosystem", ("Platform Product Lead", "Ecosystem Product Lead", "Digital Products Lead", "Consumer Product Lead")),
    ("consumer_digital", ("VP Product", "Head of Product", "Director Product", "Product Lead")),
]

CONTEXT_FAMILIES: list[tuple[str, tuple[str, ...]]] = [
    ("fintech_telecom", ("fintech", "telecom", "payments", "wallet", "banking")),
    ("consumer_platform", ("consumer", "B2C", "platform", "superapp", "digital products")),
    ("growth_revenue", ("growth", "monetization", "subscriptions", "marketplace", "engagement")),
    ("ai_products", ("AI products", "artificial intelligence", "machine learning")),
]

GEO_FAMILIES: list[tuple[str, tuple[str, ...]]] = [
    ("global_remote", ("remote", "Europe")),
    ("emea", ("UAE", "MENA", "Germany", "UK")),
    ("eu_apac", ("Netherlands", "Poland", "Singapore", "Kazakhstan")),
]


def _query_rng(source: str) -> random.Random:
    source_seed = sum(ord(ch) for ch in source.lower())
    date_seed = int(datetime.now(timezone.utc).strftime("%Y%m%d%H"))
    entropy = random.SystemRandom().randint(0, 2**31 - 1)
    return random.Random(source_seed ^ date_seed ^ entropy)


def _join_group(terms: tuple[str, ...]) -> str:
    if len(terms) == 1:
        return terms[0]
    return " OR ".join(terms)


def rotating_source_queries(source: str, *, limit: int = 6) -> list[str]:
    rng = _query_rng(source)
    combos = [(role, context, geo) for role in ROLE_FAMILIES for context in CONTEXT_FAMILIES for geo in GEO_FAMILIES]
    rng.shuffle(combos)
    queries: list[str] = []
    for role, context, geo in combos:
        role_expr = f"({_join_group(role[1])})"
        context_expr = f"({_join_group(context[1])})"
        geo_expr = f"({_join_group(geo[1])})"
        query = f"{role_expr} {context_expr} {geo_expr}".strip()
        if query not in queries:
            queries.append(query)
        if len(queries) >= limit:
            break
    return queries


def discovery_queries() -> list[tuple[str, str]]:
    rng = _query_rng("duckduckgo")
    source_templates = [
        ("linkedin", 'site:linkedin.com/jobs/view'),
        ("wellfound", 'site:wellfound.com/jobs'),
        ("greenhouse", 'site:boards.greenhouse.io'),
        ("lever", 'site:jobs.lever.co'),
        ("ashby", 'site:jobs.ashbyhq.com'),
        ("remoteok", 'site:remoteok.com'),
        ("company", 'company careers'),
    ]
    role_groups = [group for _, group in ROLE_FAMILIES]
    context_groups = [group for _, group in CONTEXT_FAMILIES]
    geo_groups = [group for _, group in GEO_FAMILIES]
    combos = [(site, role, context, geo) for site in source_templates for role in role_groups for context in context_groups for geo in geo_groups]
    rng.shuffle(combos)
    queries: list[tuple[str, str]] = []
    for source_label, site_prefix in source_templates:
        queries.append((source_label, f"{site_prefix} ({_join_group(role_groups[0])}) ({_join_group(context_groups[0])}) ({_join_group(geo_groups[0])})"))
    for site_label, role, context, geo in combos:
        query = f"{site_label[1]} ({_join_group(role)}) ({_join_group(context)}) ({_join_group(geo)})"
        if (site_label[0], query) not in queries:
            queries.append((site_label[0], query))
        if len(queries) >= 7:
            break
    return queries
