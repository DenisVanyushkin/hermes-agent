from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import requests

from .models import Vacancy
from .runtime import retry_with_backoff, sha256_text


@dataclass
class AtsSourceResult:
    vacancies: list[Vacancy]
    errors: list[str]
    discovered_companies: int
    pages_fetched: int


_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}

def _int_env(name: str, default: int, *, min_value: int = 1, max_value: int = 10_000) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(min_value, min(max_value, value))


def _source_cap(source: str, key: str, default: int) -> int:
    src = (source or "").strip().upper().replace("-", "_")
    return _int_env(f"JOB_INTEL_ATS_{src}_{key}", _int_env(f"JOB_INTEL_ATS_{key}", default))

_ATS_ENV_SEEDS = {
    "greenhouse": "JOB_INTEL_ATS_SEEDS_GREENHOUSE",
    "lever": "JOB_INTEL_ATS_SEEDS_LEVER",
    "ashby": "JOB_INTEL_ATS_SEEDS_ASHBY",
    "teamtailor": "JOB_INTEL_ATS_SEEDS_TEAMTAILOR",
    "smartrecruiters": "JOB_INTEL_ATS_SEEDS_SMARTRECRUITERS",
    "personio": "JOB_INTEL_ATS_SEEDS_PERSONIO",
    "recruitee": "JOB_INTEL_ATS_SEEDS_RECRUITEE",
}


def env_ats_seeds(source: str) -> list[str]:
    """Temporary validation mechanism: operator-provided ATS tenant seeds.

    These are not a permanent discovery strategy.
    """
    var = _ATS_ENV_SEEDS.get((source or "").strip().lower())
    if not var:
        return []
    raw = os.getenv(var, "").strip()
    if not raw:
        return []
    # Allow comma / newline / space separation.
    parts = re.split(r"[,\n\r\t ]+", raw)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        token = (part or "").strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return out


def _http_get(url: str, *, timeout: int = 25, headers: dict[str, str] | None = None) -> requests.Response:
    merged = dict(_UA)
    if headers:
        merged.update(headers)
    return requests.get(url, timeout=timeout, headers=merged)


def _is_rate_limited(resp: requests.Response) -> bool:
    if resp.status_code == 429:
        return True
    return bool((resp.headers or {}).get("Retry-After"))


def _sleep_retry_after(resp: requests.Response) -> None:
    retry_after = (resp.headers or {}).get("Retry-After")
    if not retry_after:
        return
    try:
        seconds = int(retry_after)
    except ValueError:
        return
    if seconds <= 0:
        return
    # Keep it bounded; we prefer degraded over stalling the whole run.
    time.sleep(min(seconds, 5))


def status_from_hits_errors(hits: int, errors: list[str]) -> str:
    if hits and errors:
        return "degraded"
    if hits:
        return "ok"
    if errors:
        if any(
            "429" in e
            or "rate" in e.lower()
            or "temporarily" in e.lower()
            or "timeout" in e.lower()
            for e in errors
        ):
            return "degraded"
        return "error"
    return "empty"


def _ddg_search_html(query: str) -> str:
    url = "https://html.duckduckgo.com/html/?q=" + requests.utils.quote(query)

    def _request() -> requests.Response:
        return _http_get(url, timeout=15, headers={"Referer": "https://duckduckgo.com/"})

    resp = retry_with_backoff(_request, attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
    resp.raise_for_status()
    return resp.text


def _ddg_extract_links(html: str, *, limit: int) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<href>[^"]+)"',
        html,
        flags=re.I,
    ):
        href = match.group("href")
        if not href:
            continue
        urls.append(href)
        if len(urls) >= limit:
            break
    return urls


def _ddg_unwrap(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" not in parsed.netloc:
        return href
    m = re.search(r"(?:^|&)uddg=([^&]+)", parsed.query)
    if not m:
        return href
    try:
        return requests.utils.unquote(m.group(1))
    except Exception:
        return href


def discover_companies(
    queries: list[str],
    *,
    site_filter: str,
    company_from_url: Callable[[str], str | None],
    max_hits_per_query: int = 5,
    max_companies: int = 35,
) -> list[str]:
    companies: list[str] = []
    seen: set[str] = set()
    for q in queries:
        query = f"site:{site_filter} {q}".strip()
        try:
            html = _ddg_search_html(query)
        except Exception:
            continue
        for raw in _ddg_extract_links(html, limit=max_hits_per_query):
            dest = _ddg_unwrap(raw)
            company = company_from_url(dest)
            if not company:
                continue
            key = company.lower()
            if key in seen:
                continue
            seen.add(key)
            companies.append(company)
            if len(companies) >= max_companies:
                return companies
    return companies


def discover_ats_seeds_from_career_urls(career_urls: list[str], *, max_pages: int = 40) -> dict[str, list[str]]:
    """Best-effort extraction of ATS tenant slugs from a set of career pages.

    This avoids reliance on public web search (DDG HTML results are unstable / often blocked from servers).
    """
    seeds: dict[str, set[str]] = {
        "greenhouse": set(),
        "lever": set(),
        "ashby": set(),
        "teamtailor": set(),
        "smartrecruiters": set(),
        "personio": set(),
        "recruitee": set(),
    }
    urls = [u for u in (career_urls or []) if isinstance(u, str) and u.strip()]
    seen: set[str] = set()
    pages = 0

    for url in urls:
        if pages >= max_pages:
            break
        url = url.strip()
        if url in seen:
            continue
        seen.add(url)
        try:
            resp = _http_get(url, timeout=20)
            pages += 1
            if resp.status_code != 200:
                continue
            html = resp.text or ""
        except Exception:
            continue

        # Greenhouse boards
        for m in re.finditer(r"https?://boards\\.greenhouse\\.io/([^/\\s\"'#?]+)", html, flags=re.I):
            seeds["greenhouse"].add(m.group(1))
        for m in re.finditer(r"https?://(?:www\\.)?greenhouse\\.io/([^/\\s\"'#?]+)", html, flags=re.I):
            seeds["greenhouse"].add(m.group(1))

        # Lever
        for m in re.finditer(r"https?://jobs\\.lever\\.co/([^/\\s\"'#?]+)", html, flags=re.I):
            seeds["lever"].add(m.group(1))

        # Ashby
        for m in re.finditer(r"https?://jobs\\.ashbyhq\\.com/([^/\\s\"'#?]+)", html, flags=re.I):
            seeds["ashby"].add(m.group(1))

        # Teamtailor
        for m in re.finditer(r"https?://([^./\\s\"'#?]+)\\.teamtailor\\.com/jobs", html, flags=re.I):
            seeds["teamtailor"].add(m.group(1))

        # SmartRecruiters
        for m in re.finditer(r"https?://jobs\\.smartrecruiters\\.com/([^/\\s\"'#?]+)", html, flags=re.I):
            seeds["smartrecruiters"].add(m.group(1))

        # Personio
        for m in re.finditer(r"https?://([^./\\s\"'#?]+)\\.jobs\\.personio\\.com", html, flags=re.I):
            seeds["personio"].add(m.group(1))

        # Recruitee
        for m in re.finditer(r"https?://([^./\\s\"'#?]+)\\.recruitee\\.com", html, flags=re.I):
            seeds["recruitee"].add(m.group(1))

    return {k: sorted(v) for k, v in seeds.items() if v}


def _vacancy(
    source: str,
    *,
    url: str,
    title: str,
    company: str,
    location: str = "Unknown",
    description: str = "",
    posted_at: str | None = None,
    salary: str | None = None,
    metadata: dict[str, Any] | None = None,
    ) -> Vacancy:
    title = (title or "Vacancy").strip() or "Vacancy"
    url = (url or "").strip()
    return Vacancy(
        source=source,
        source_id=sha256_text(f"{source}:{url}:{title}")[:16],
        company=(company or "Unknown").strip() or "Unknown",
        title=title,
        location=(location or "Unknown").strip() or "Unknown",
        url=url,
        description=(description or title).strip() or title,
        posted_at=posted_at,
        salary=salary,
        metadata=metadata or {},
    )


def _clean_html_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _json_ld_objects(html: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for match in re.finditer(
        r"<script[^>]+type=[\"']application/ld\\+json[\"'][^>]*>(.*?)</script>",
        html or "",
        flags=re.I | re.S,
    ):
        raw = (match.group(1) or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if isinstance(payload, dict):
            objects.append(payload)
        elif isinstance(payload, list):
            objects.extend(item for item in payload if isinstance(item, dict))
    return objects


def _jobposting_objects(html: str) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for obj in _json_ld_objects(html):
        obj_type = obj.get("@type")
        types = {str(item).lower() for item in obj_type} if isinstance(obj_type, list) else {str(obj_type).lower()}
        if "jobposting" in types:
            jobs.append(obj)
    return jobs


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
                return rendered.split(",", 1)[0].strip() or rendered.strip()
    if "remote" in (body_text or "").lower():
        return "Remote"
    return "Unknown"


def extract_jobposting_vacancies_from_html(
    html: str,
    *,
    source: str,
    page_url: str,
    company_override: str | None = None,
) -> list[Vacancy]:
    vacancies: list[Vacancy] = []
    for jp in _jobposting_objects(html):
        title = str(jp.get("title") or jp.get("name") or "Vacancy").strip() or "Vacancy"
        description = _clean_html_text(str(jp.get("description") or ""))
        url = str(jp.get("url") or page_url or "").strip() or page_url
        company = company_override
        org = jp.get("hiringOrganization")
        if not company and isinstance(org, dict):
            company = str(org.get("name") or "").strip() or None
        if not company and isinstance(org, str):
            company = org.strip() or None
        company = company or company_override or "Unknown"
        location = _location_from_jobposting(jp, description)
        posted_at = str(jp.get("datePosted") or jp.get("published_at") or "") or None
        vacancies.append(
            _vacancy(
                source,
                url=url,
                title=title,
                company=company,
                location=location,
                description=description,
                posted_at=posted_at,
                metadata={"raw": jp, "source_url": page_url},
            )
        )
    return vacancies

def _company_from_subdomain(url: str, *, suffix: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if not host.endswith(suffix):
        return None
    sub = host[: -len(suffix)].strip(".")
    if not sub:
        return None
    return sub.split(".")[0]


def _company_from_path_segment(url: str, *, hosts: tuple[str, ...]) -> str | None:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if not any(h in host for h in hosts):
        return None
    parts = [p for p in (parsed.path or "").split("/") if p]
    if not parts:
        return None
    return parts[0]


def fetch_greenhouse(
    queries: list[str],
    *,
    max_companies: int = 0,
    max_jobs_per_company: int = 0,
    companies: list[str] | None = None,
) -> AtsSourceResult:
    max_companies = max_companies or _source_cap("greenhouse", "MAX_COMPANIES", 8)
    max_jobs_per_company = max_jobs_per_company or _source_cap("greenhouse", "MAX_JOBS_PER_COMPANY", 200)
    def _company(url: str) -> str | None:
        return _company_from_path_segment(url, hosts=("boards.greenhouse.io", "greenhouse.io"))

    tokens = list(companies or []) or env_ats_seeds("greenhouse") or discover_companies(queries, site_filter="boards.greenhouse.io", company_from_url=_company, max_companies=max_companies)
    vacancies: list[Vacancy] = []
    errors: list[str] = []
    pages = 0

    for token in tokens:
        api_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"

        def _request() -> requests.Response:
            return _http_get(api_url, timeout=20, headers={"Accept": "application/json"})

        try:
            resp = retry_with_backoff(_request, attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
            pages += 1
            if _is_rate_limited(resp):
                _sleep_retry_after(resp)
                errors.append(f"429 rate_limited greenhouse board={token}")
                continue
            if resp.status_code >= 500:
                errors.append(f"{resp.status_code} upstream greenhouse board={token}")
                continue
            if resp.status_code != 200:
                errors.append(f"{resp.status_code} greenhouse board={token}")
                continue
            payload = resp.json() if resp.content else {}
            jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
            for job in jobs[:max_jobs_per_company]:
                if not isinstance(job, dict):
                    continue
                title = str(job.get("title") or "Vacancy")
                company = str((job.get("company") or {}).get("name") or token)
                location = str((job.get("location") or {}).get("name") or "Unknown")
                url = str(job.get("absolute_url") or "")
                content = str(job.get("content") or "")
                description = re.sub(r"<[^>]+>", " ", content)
                description = re.sub(r"\s+", " ", description).strip()
                posted_at = str(job.get("updated_at") or job.get("created_at") or "") or None
                vacancies.append(
                    _vacancy(
                        "greenhouse",
                        url=url,
                        title=title,
                        company=company,
                        location=location,
                        description=description,
                        posted_at=posted_at,
                        metadata={"raw": job, "board": token},
                    )
                )
        except Exception as exc:
            errors.append(f"greenhouse board={token}: {exc}")

    return AtsSourceResult(vacancies=vacancies, errors=errors, discovered_companies=len(tokens), pages_fetched=pages)


def fetch_lever(
    queries: list[str],
    *,
    max_companies: int = 0,
    max_jobs_per_company: int = 0,
    companies: list[str] | None = None,
) -> AtsSourceResult:
    max_companies = max_companies or _source_cap("lever", "MAX_COMPANIES", 10)
    max_jobs_per_company = max_jobs_per_company or _source_cap("lever", "MAX_JOBS_PER_COMPANY", 200)
    def _company(url: str) -> str | None:
        return _company_from_path_segment(url, hosts=("jobs.lever.co",))

    slugs = list(companies or []) or env_ats_seeds("lever") or discover_companies(queries, site_filter="jobs.lever.co", company_from_url=_company, max_companies=max_companies)
    vacancies: list[Vacancy] = []
    errors: list[str] = []
    pages = 0

    for slug in slugs:
        api_url = f"https://api.lever.co/v0/postings/{slug}?mode=json"

        def _request() -> requests.Response:
            return _http_get(api_url, timeout=20, headers={"Accept": "application/json"})

        try:
            resp = retry_with_backoff(_request, attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
            pages += 1
            if _is_rate_limited(resp):
                _sleep_retry_after(resp)
                errors.append(f"429 rate_limited lever site={slug}")
                continue
            if resp.status_code >= 500:
                errors.append(f"{resp.status_code} upstream lever site={slug}")
                continue
            if resp.status_code != 200:
                errors.append(f"{resp.status_code} lever site={slug}")
                continue
            payload = resp.json() if resp.content else []
            if not isinstance(payload, list):
                errors.append(f"lever site={slug}: unexpected_payload")
                continue
            for item in payload[:max_jobs_per_company]:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("text") or item.get("title") or "Vacancy")
                categories = item.get("categories")
                location = "Unknown"
                if isinstance(categories, dict):
                    location = str(categories.get("location") or "Unknown")
                url = str(item.get("hostedUrl") or item.get("applyUrl") or "")
                desc = str(item.get("descriptionPlain") or item.get("description") or "")
                description = re.sub(r"<[^>]+>", " ", desc)
                description = re.sub(r"\s+", " ", description).strip()
                posted_at = str(item.get("createdAt") or "") or None
                vacancies.append(
                    _vacancy(
                        "lever",
                        url=url,
                        title=title,
                        company=slug,
                        location=location,
                        description=description,
                        posted_at=posted_at,
                        metadata={"raw": item, "site": slug},
                    )
                )
        except Exception as exc:
            errors.append(f"lever site={slug}: {exc}")

    return AtsSourceResult(vacancies=vacancies, errors=errors, discovered_companies=len(slugs), pages_fetched=pages)


def fetch_ashby(
    queries: list[str],
    *,
    max_companies: int = 0,
    max_jobs_per_company: int = 0,
    companies: list[str] | None = None,
) -> AtsSourceResult:
    max_companies = max_companies or _source_cap("ashby", "MAX_COMPANIES", 10)
    max_jobs_per_company = max_jobs_per_company or _source_cap("ashby", "MAX_JOBS_PER_COMPANY", 300)
    def _company(url: str) -> str | None:
        return _company_from_path_segment(url, hosts=("jobs.ashbyhq.com", "ashbyhq.com"))

    boards = list(companies or []) or env_ats_seeds("ashby") or discover_companies(queries, site_filter="jobs.ashbyhq.com", company_from_url=_company, max_companies=max_companies)
    vacancies: list[Vacancy] = []
    errors: list[str] = []
    pages = 0

    for board in boards:
        api_url = f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=false"

        def _request() -> requests.Response:
            return _http_get(api_url, timeout=20, headers={"Accept": "application/json"})

        try:
            resp = retry_with_backoff(_request, attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
            pages += 1
            if _is_rate_limited(resp):
                _sleep_retry_after(resp)
                errors.append(f"429 rate_limited ashby board={board}")
                continue
            if resp.status_code >= 500:
                errors.append(f"{resp.status_code} upstream ashby board={board}")
                continue
            if resp.status_code != 200:
                errors.append(f"{resp.status_code} ashby board={board}")
                continue
            payload = resp.json() if resp.content else {}
            jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
            for job in jobs[:max_jobs_per_company]:
                if not isinstance(job, dict):
                    continue
                title = str(job.get("title") or "Vacancy")
                location = str(job.get("location") or "Unknown")
                url = str(job.get("jobUrl") or job.get("applyUrl") or "")
                desc = str(job.get("descriptionPlain") or job.get("descriptionHtml") or "")
                description = re.sub(r"<[^>]+>", " ", desc)
                description = re.sub(r"\s+", " ", description).strip()
                posted_at = str(job.get("publishedAt") or "") or None
                vacancies.append(
                    _vacancy(
                        "ashby",
                        url=url,
                        title=title,
                        company=board,
                        location=location,
                        description=description,
                        posted_at=posted_at,
                        metadata={"raw": job, "board": board},
                    )
                )
        except Exception as exc:
            errors.append(f"ashby board={board}: {exc}")

    return AtsSourceResult(vacancies=vacancies, errors=errors, discovered_companies=len(boards), pages_fetched=pages)


def fetch_smartrecruiters(
    queries: list[str],
    *,
    max_companies: int = 0,
    max_jobs_per_company: int = 0,
    companies: list[str] | None = None,
) -> AtsSourceResult:
    max_companies = max_companies or _source_cap("smartrecruiters", "MAX_COMPANIES", 8)
    max_jobs_per_company = max_jobs_per_company or _source_cap("smartrecruiters", "MAX_JOBS_PER_COMPANY", 250)
    def _company(url: str) -> str | None:
        return _company_from_path_segment(url, hosts=("jobs.smartrecruiters.com",))

    companies = list(companies or []) or env_ats_seeds("smartrecruiters") or discover_companies(queries, site_filter="jobs.smartrecruiters.com", company_from_url=_company, max_companies=max_companies)
    vacancies: list[Vacancy] = []
    errors: list[str] = []
    pages = 0

    for company in companies:
        offset = 0
        limit = 100
        fetched_any = False
        while True:
            api_url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit={limit}&offset={offset}"

            def _request() -> requests.Response:
                return _http_get(api_url, timeout=20, headers={"Accept": "application/json"})

            try:
                resp = retry_with_backoff(_request, attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
                pages += 1
                if resp.status_code in (401, 403):
                    errors.append(f"{resp.status_code} smartrecruiters api_auth company={company}")
                    break
                if _is_rate_limited(resp):
                    _sleep_retry_after(resp)
                    errors.append(f"429 rate_limited smartrecruiters company={company}")
                    break
                if resp.status_code >= 500:
                    errors.append(f"{resp.status_code} upstream smartrecruiters company={company}")
                    break
                if resp.status_code != 200:
                    errors.append(f"{resp.status_code} smartrecruiters company={company}")
                    break
                payload = resp.json() if resp.content else {}
                if not isinstance(payload, dict):
                    errors.append(f"smartrecruiters company={company}: unexpected_payload")
                    break
                postings = payload.get("content")
                if not isinstance(postings, list):
                    postings = []
                fetched_any = True
                if not postings:
                    break

                for post in postings:
                    if not isinstance(post, dict):
                        continue
                    title = str(post.get("name") or "Vacancy")
                    loc = post.get("location")
                    location = "Unknown"
                    if isinstance(loc, dict):
                        location = str(loc.get("city") or loc.get("region") or loc.get("country") or "Unknown")
                    url = str(post.get("ref") or "")
                    if not url:
                        pid = str(post.get("id") or "")
                        if pid:
                            url = f"https://jobs.smartrecruiters.com/{company}/{pid}"
                    posted_at = str(post.get("releasedDate") or "") or None
                    vacancies.append(
                        _vacancy(
                            "smartrecruiters",
                            url=url,
                            title=title,
                            company=company,
                            location=location,
                            posted_at=posted_at,
                            metadata={"raw": post, "company": company},
                        )
                    )

                total = int(payload.get("totalFound") or 0)
                offset = int(payload.get("offset") or offset)
                limit = int(payload.get("limit") or limit)
                next_offset = offset + limit
                if next_offset <= 0 or next_offset >= total:
                    break
                offset = next_offset
                if len(vacancies) >= max_jobs_per_company:
                    break
            except Exception as exc:
                errors.append(f"smartrecruiters company={company}: {exc}")
                break

        if fetched_any:
            continue

        # Fallback scrape: fetch a handful of posting pages from DDG for this company.
        try:
            html = _ddg_search_html(f"site:jobs.smartrecruiters.com/{company} VP Product OR Head of Product OR Director Product")
            hits = [_ddg_unwrap(u) for u in _ddg_extract_links(html, limit=8)]
            for url in hits:
                try:
                    page = _http_get(url, timeout=20)
                    pages += 1
                    if page.status_code != 200:
                        continue
                    vacancies.extend(extract_jobposting_vacancies_from_html(page.text, source="smartrecruiters", page_url=url))
                except Exception:
                    continue
        except Exception:
            pass

    return AtsSourceResult(vacancies=vacancies, errors=errors, discovered_companies=len(companies), pages_fetched=pages)


def extract_teamtailor_job_urls(html: str, base_url: str, *, limit: int = 80) -> list[str]:
    """Extract job posting URLs from a Teamtailor listing page.

    Handles both relative hrefs (`/jobs/123-title`) and absolute URLs used by
    tenants on custom career domains (e.g. career.instabee.com). Links pointing
    to other hosts are ignored so external boards never masquerade as jobs.
    """
    base_host = urlparse(base_url).netloc.lower()
    href_re = re.compile(r"href=['\"](?P<href>[^'\"#?]*/jobs/[^'\"#?]+)['\"]", flags=re.I)
    urls: list[str] = []
    seen: set[str] = set()
    for match in href_re.finditer(html or ""):
        absolute = urljoin(base_url, match.group("href"))
        parsed = urlparse(absolute)
        if parsed.netloc.lower() != base_host:
            continue
        if not re.search(r"(^|/)jobs/[^/]", parsed.path):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        urls.append(absolute)
        if len(urls) >= limit:
            break
    return urls


def fetch_teamtailor(
    queries: list[str],
    *,
    max_companies: int = 0,
    max_pages_per_company: int = 0,
    max_jobs_per_company: int = 0,
    companies: list[str] | None = None,
) -> AtsSourceResult:
    max_companies = max_companies or _source_cap("teamtailor", "MAX_COMPANIES", 6)
    max_pages_per_company = max_pages_per_company or _source_cap("teamtailor", "MAX_PAGES_PER_COMPANY", 2)
    max_jobs_per_company = max_jobs_per_company or _source_cap("teamtailor", "MAX_JOBS_PER_COMPANY", 80)
    def _company(url: str) -> str | None:
        return _company_from_subdomain(url, suffix=".teamtailor.com")

    companies = list(companies or []) or env_ats_seeds("teamtailor") or discover_companies(queries, site_filter="teamtailor.com/jobs", company_from_url=_company, max_companies=max_companies)
    vacancies: list[Vacancy] = []
    errors: list[str] = []
    pages = 0

    for company in companies:
        base = f"https://{company}.teamtailor.com/jobs"
        job_urls: list[str] = []
        seen_jobs: set[str] = set()

        for page_idx in range(1, max_pages_per_company + 1):
            url = base if page_idx == 1 else f"{base}?page={page_idx}"
            try:
                resp = _http_get(url, timeout=20)
                pages += 1
                if _is_rate_limited(resp):
                    _sleep_retry_after(resp)
                    errors.append(f"429 rate_limited teamtailor company={company}")
                    break
                if resp.status_code >= 500:
                    errors.append(f"{resp.status_code} upstream teamtailor company={company}")
                    break
                if resp.status_code != 200:
                    break
                found = 0
                # resp.url reflects redirects to tenant custom career domains.
                for absolute in extract_teamtailor_job_urls(resp.text or "", str(resp.url or url), limit=max_jobs_per_company):
                    if absolute in seen_jobs:
                        continue
                    seen_jobs.add(absolute)
                    job_urls.append(absolute)
                    found += 1
                    if len(job_urls) >= max_jobs_per_company:
                        break
                if found == 0:
                    break
                if len(job_urls) >= max_jobs_per_company:
                    break
            except Exception as exc:
                errors.append(f"teamtailor company={company}: {exc}")
                break

        for job_url in job_urls:
            try:
                resp = _http_get(job_url, timeout=20)
                pages += 1
                if resp.status_code != 200:
                    continue
                extracted = extract_jobposting_vacancies_from_html(resp.text, source="teamtailor", page_url=job_url)
                if extracted:
                    vacancies.extend(extracted)
                    continue
                vacancies.append(_vacancy("teamtailor", url=job_url, title="Vacancy", company=company))
            except Exception:
                continue

    return AtsSourceResult(vacancies=vacancies, errors=errors, discovered_companies=len(companies), pages_fetched=pages)


def _parse_personio_xml(xml_text: str, *, company: str) -> list[Vacancy]:
    vacancies: list[Vacancy] = []
    root = ET.fromstring(xml_text)
    for position in root.findall(".//position"):
        title = (position.findtext("name") or "Vacancy").strip() or "Vacancy"
        office = (position.findtext("office") or "").strip()
        location = office or (position.findtext("recruitingCategory") or "Unknown")
        url = (position.findtext("jobAdUrl") or "").strip()
        if not url:
            pid = (position.findtext("id") or "").strip()
            if pid:
                url = f"https://{company}.jobs.personio.com/job/{pid}"
        description = (position.findtext("jobDescription") or "").strip()
        posted_at = (position.findtext("createdAt") or "").strip() or None
        vacancies.append(
            _vacancy(
                "personio",
                url=url,
                title=title,
                company=company,
                location=location or "Unknown",
                description=description,
                posted_at=posted_at,
            )
        )
    return vacancies


def fetch_personio(
    queries: list[str],
    *,
    max_companies: int = 0,
    max_jobs_per_company: int = 0,
    companies: list[str] | None = None,
) -> AtsSourceResult:
    max_companies = max_companies or _source_cap("personio", "MAX_COMPANIES", 10)
    max_jobs_per_company = max_jobs_per_company or _source_cap("personio", "MAX_JOBS_PER_COMPANY", 300)
    def _company(url: str) -> str | None:
        return _company_from_subdomain(url, suffix=".jobs.personio.com")

    accounts = list(companies or []) or env_ats_seeds("personio") or discover_companies(queries, site_filter="jobs.personio.com", company_from_url=_company, max_companies=max_companies)
    vacancies: list[Vacancy] = []
    errors: list[str] = []
    pages = 0

    for account in accounts:
        xml_url = f"https://{account}.jobs.personio.com/xml"

        def _request() -> requests.Response:
            return _http_get(xml_url, timeout=25, headers={"Accept": "application/xml,text/xml,*/*;q=0.8"})

        try:
            resp = retry_with_backoff(_request, attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
            pages += 1
            if _is_rate_limited(resp):
                _sleep_retry_after(resp)
                errors.append(f"429 rate_limited personio account={account}")
                continue
            if resp.status_code >= 500:
                errors.append(f"{resp.status_code} upstream personio account={account}")
                continue
            if resp.status_code != 200:
                continue
            parsed = _parse_personio_xml(resp.text or "", company=account)
            vacancies.extend(parsed[:max_jobs_per_company])
        except Exception as exc:
            errors.append(f"personio account={account}: {exc}")

    return AtsSourceResult(vacancies=vacancies, errors=errors, discovered_companies=len(accounts), pages_fetched=pages)


def _parse_recruitee_offers_xml(xml_text: str, *, company: str) -> list[Vacancy]:
    vacancies: list[Vacancy] = []
    root = ET.fromstring(xml_text)
    for offer in root.findall(".//offer"):
        title = (offer.findtext("title") or offer.findtext("name") or "Vacancy").strip() or "Vacancy"
        location = (offer.findtext("location") or "Unknown").strip() or "Unknown"
        url = (offer.findtext("url") or offer.findtext("job_url") or "").strip()
        description = (offer.findtext("description") or "").strip()
        posted_at = (offer.findtext("created_at") or offer.findtext("published_at") or "").strip() or None
        vacancies.append(
            _vacancy(
                "recruitee",
                url=url,
                title=title,
                company=company,
                location=location,
                description=description,
                posted_at=posted_at,
            )
        )
    return vacancies


def fetch_recruitee(
    queries: list[str],
    *,
    max_companies: int = 0,
    max_jobs_per_company: int = 0,
    companies: list[str] | None = None,
) -> AtsSourceResult:
    max_companies = max_companies or _source_cap("recruitee", "MAX_COMPANIES", 10)
    max_jobs_per_company = max_jobs_per_company or _source_cap("recruitee", "MAX_JOBS_PER_COMPANY", 300)
    def _company(url: str) -> str | None:
        return _company_from_subdomain(url, suffix=".recruitee.com")

    accounts = list(companies or []) or env_ats_seeds("recruitee") or discover_companies(queries, site_filter="recruitee.com", company_from_url=_company, max_companies=max_companies)
    vacancies: list[Vacancy] = []
    errors: list[str] = []
    pages = 0

    for account in accounts:
        feed_url = f"https://{account}.recruitee.com/api/feeds/offers.xml"

        def _request() -> requests.Response:
            return _http_get(feed_url, timeout=25, headers={"Accept": "application/xml,text/xml,*/*;q=0.8"})

        try:
            resp = retry_with_backoff(_request, attempts=2, base_delay=1.0, exceptions=(requests.RequestException,))
            pages += 1
            if _is_rate_limited(resp):
                _sleep_retry_after(resp)
                errors.append(f"429 rate_limited recruitee account={account}")
                continue
            if resp.status_code >= 500:
                errors.append(f"{resp.status_code} upstream recruitee account={account}")
                continue
            if resp.status_code != 200:
                continue
            parsed = _parse_recruitee_offers_xml(resp.text or "", company=account)
            vacancies.extend(parsed[:max_jobs_per_company])
        except Exception as exc:
            errors.append(f"recruitee account={account}: {exc}")

    return AtsSourceResult(vacancies=vacancies, errors=errors, discovered_companies=len(accounts), pages_fetched=pages)
