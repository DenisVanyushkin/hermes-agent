from __future__ import annotations

import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urlparse, urlsplit, urlunsplit

import requests

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
        return _slug_to_company(parts[0])
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
    return any(hint in text for hint in EXECUTIVE_ROLE_HINTS)


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


def _request_json(url: str, *, params: dict[str, object], headers: dict[str, str]) -> dict:
    response = requests.get(url, params=params, timeout=30, headers=headers)
    if response.status_code == 403:
        raise SourceFetchError(
            "HeadHunter API returned 403 Forbidden. Set JOB_INTEL_HH_ACCESS_TOKEN (Bearer token) if access is required for your account/app."
        )
    response.raise_for_status()
    return response.json()


def fetch_headhunter_vacancies(query: str, *, per_page: int = 20) -> list[Vacancy]:
    headers = {
        "User-Agent": os.getenv(
            "JOB_INTEL_HH_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        "Referer": "https://hh.ru/",
        "Origin": "https://hh.ru",
    }
    token = os.getenv("JOB_INTEL_HH_ACCESS_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = retry_with_backoff(
        lambda: _request_json(
            "https://api.hh.ru/vacancies",
            params={"text": query, "per_page": per_page, "page": 0, "order_by": "publication_time_desc"},
            headers=headers,
        ),
        attempts=3,
        base_delay=1.5,
        exceptions=(requests.RequestException,),
    )
    items = []
    for item in data.get("items", []):
        items.append(
            Vacancy(
                source="headhunter",
                source_id=str(item.get("id", "")),
                company=(item.get("employer") or {}).get("name", ""),
                title=item.get("name", ""),
                location=(item.get("area") or {}).get("name", "Remote"),
                url=item.get("alternate_url", ""),
                description=re.sub(
                    r"\s+",
                    " ",
                    ((item.get("snippet") or {}).get("responsibility", "") + " " + (item.get("snippet") or {}).get("requirement", "")),
                ).strip(),
                posted_at=item.get("published_at"),
                salary=_format_salary(item.get("salary")),
                company_url=(item.get("employer") or {}).get("alternate_url"),
                metadata={"raw": item},
            )
        )
    return items


def _format_salary(salary: dict | None) -> str | None:
    if not salary:
        return None
    parts = [salary.get("from"), salary.get("to"), salary.get("currency")]
    if not any(parts):
        return None
    return " ".join(str(part) for part in parts if part)


def discovery_queries() -> list[tuple[str, str]]:
    return [
        ("linkedin", 'site:linkedin.com/jobs/view (VP Product OR Head of Product OR Chief Product Officer) (monetization OR platform OR ecosystem) (remote OR Europe OR UAE)'),
        ("wellfound", 'site:wellfound.com/jobs (superapp OR subscription OR fintech OR product)'),
        ("greenhouse", 'site:boards.greenhouse.io (VP Product OR Director of Product OR Head of Monetization)'),
        ("lever", 'site:jobs.lever.co (VP Product OR Director of Product OR Head of Product)'),
        ("ashby", 'site:jobs.ashbyhq.com (product director OR head of product OR chief product officer)'),
        ("remoteok", 'site:remoteok.com remote VP Product monetization'),
        ("company", '(VP Product OR Head of Product) (monetization OR ecosystem OR platform) (company careers)'),
    ]
