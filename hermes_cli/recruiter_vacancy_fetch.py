"""Read-only vacancy content fetch for recruiter decision support.

Given a vacancy URL, retrieves the actual posting content so decision
modules assess the real job description instead of just the link. Uses the
public read APIs of known ATSes (hh.ru, Greenhouse, Ashby) and falls back to
fetching the page and extracting the schema.org JobPosting JSON-LD or plain
text. Strictly read-only: a single GET per attempt, no auth, no browser.
"""

from __future__ import annotations

import html as _html
import json
import re
from typing import Any

_TIMEOUT = 20
_MAX_TEXT = 15_000
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; hermes-recruiter/1.0; read-only vacancy fetch)"}

_HH_RE = re.compile(r"https?://(?:[a-z]+\.)?hh\.(?:ru|kz)/vacancy/(\d+)")
_GREENHOUSE_RE = re.compile(r"https?://(?:job-boards|boards)\.greenhouse\.io/([\w-]+)/jobs/(\d+)")
_ASHBY_RE = re.compile(r"https?://jobs\.ashbyhq\.com/([\w-]+)/([0-9a-f-]{36})")


def fetch_vacancy_details(url: str) -> dict[str, Any]:
    """Return vacancy fields for *url*; always includes fetch_status."""
    url = str(url or "").strip()
    if not url:
        return {"fetch_status": "no_url"}
    specific = None
    match = _HH_RE.match(url)
    if match:
        specific = lambda: _fetch_hh(match.group(1))  # noqa: E731
    else:
        match = _GREENHOUSE_RE.match(url)
        if match:
            specific = lambda: _fetch_greenhouse(match.group(1), match.group(2))  # noqa: E731
        else:
            match = _ASHBY_RE.match(url)
            if match:
                specific = lambda: _fetch_ashby(match.group(1), match.group(2))  # noqa: E731

    if specific is not None:
        try:
            details = specific()
            if str(details.get("fetch_status") or "").startswith("ok"):
                return details
        except Exception:
            pass
    try:
        return _fetch_generic(url)
    except Exception as exc:
        return {"fetch_status": f"fetch_failed:{type(exc).__name__}"}


def _get(url: str) -> Any:
    import requests

    response = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    response.raise_for_status()
    return response


def _fetch_hh(vacancy_id: str) -> dict[str, Any]:
    payload = _get(f"https://api.hh.ru/vacancies/{vacancy_id}").json()
    return _details(
        title=payload.get("name"),
        company=(payload.get("employer") or {}).get("name"),
        location=(payload.get("area") or {}).get("name"),
        description_html=payload.get("description"),
        extra={
            "salary": payload.get("salary"),
            "employment": (payload.get("employment") or {}).get("name"),
            "experience": (payload.get("experience") or {}).get("name"),
            "remote": (payload.get("schedule") or {}).get("id") == "remote",
        },
    )


def _fetch_greenhouse(org: str, job_id: str) -> dict[str, Any]:
    payload = _get(f"https://boards-api.greenhouse.io/v1/boards/{org}/jobs/{job_id}").json()
    return _details(
        title=payload.get("title"),
        company=org,
        location=(payload.get("location") or {}).get("name"),
        description_html=payload.get("content"),
    )


def _fetch_ashby(org: str, job_id: str) -> dict[str, Any]:
    payload = _get(
        f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"
    ).json()
    for job in payload.get("jobs") or []:
        job_url = str(job.get("jobUrl") or job.get("applyUrl") or "")
        if job.get("id") == job_id or job_id in job_url:
            return _details(
                title=job.get("title"),
                company=org,
                location=job.get("location"),
                description_html=job.get("descriptionHtml") or job.get("descriptionPlain"),
                extra={"employment_type": job.get("employmentType"), "remote": job.get("isRemote")},
            )
    return {"fetch_status": "not_found_on_board"}


def _fetch_generic(url: str) -> dict[str, Any]:
    text = _get(url).text
    for jobposting in _jobposting_objects(text):
        return _details(
            title=jobposting.get("title"),
            company=(jobposting.get("hiringOrganization") or {}).get("name")
            if isinstance(jobposting.get("hiringOrganization"), dict)
            else jobposting.get("hiringOrganization"),
            location=_jobposting_location(jobposting),
            description_html=jobposting.get("description"),
        )
    stripped = _strip_html(text)
    if len(stripped) < 200:
        return {"fetch_status": "page_content_too_thin"}
    return {"fetch_status": "ok_page_text", "description_text": stripped[:_MAX_TEXT]}


def _jobposting_objects(html_text: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_text,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            payload = json.loads(match.group(1).strip())
        except Exception:
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                objects.append(item)
    return objects


def _jobposting_location(jobposting: dict[str, Any]) -> str | None:
    location = jobposting.get("jobLocation")
    if isinstance(location, list) and location:
        location = location[0]
    if isinstance(location, dict):
        address = location.get("address")
        if isinstance(address, dict):
            parts = [address.get("addressLocality"), address.get("addressCountry")]
            joined = ", ".join(str(part) for part in parts if part)
            return joined or None
    return None


def _details(
    *,
    title: Any,
    company: Any,
    location: Any,
    description_html: Any,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"fetch_status": "ok"}
    if title:
        result["title"] = str(title)
    if company:
        result["company"] = str(company)
    if location:
        result["location"] = str(location)
    text = _strip_html(str(description_html or ""))
    if text:
        result["description_text"] = text[:_MAX_TEXT]
    for key, value in (extra or {}).items():
        if value not in (None, ""):
            result[key] = value
    return result


def _strip_html(markup: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", markup, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()
