"""Read-only external web research for company decision support.

Runs a small fixed set of web searches about the target company through the
agent's configured web-search backend and returns result metadata (title,
url, snippet). The results are fed to the provider-backed company_research
extraction as *candidate sources*; the claim validator and the company
research quality gate remain the arbiters of what counts. Strictly
read-only, capped query budget, fails soft to posting-only research.
"""

from __future__ import annotations

import json
import re
from typing import Any

_MAX_QUERIES = 4
_RESULTS_PER_QUERY = 3
_MAX_RESULTS_TOTAL = 10

_QUERY_TEMPLATES = (
    "{company} company news",
    "{company} layoffs OR restructuring",
    "{company} funding valuation revenue",
    "{company} glassdoor employee reviews culture",
)


def gather_company_web_research(company: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (search results, warnings) for *company*; never raises."""
    company = str(company or "").strip()
    if not company:
        return [], ["company web research skipped: no company name"]

    try:
        from hermes_cli.env_loader import load_hermes_dotenv

        load_hermes_dotenv()
    except Exception:
        pass

    try:
        from tools.web_tools import web_search_tool
    except Exception as exc:
        return [], [f"web search backend unavailable ({type(exc).__name__}); research limited to the posting"]

    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_urls: set[str] = set()
    for template in _QUERY_TEMPLATES[:_MAX_QUERIES]:
        query = template.format(company=company)
        try:
            raw = web_search_tool(query, limit=_RESULTS_PER_QUERY)
        except Exception as exc:
            warnings.append(f"web search failed for one query ({type(exc).__name__})")
            continue
        for item in _parse_search_results(raw):
            url = str(item.get("url") or "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append(
                {
                    "title": _clean(item.get("title")),
                    "url": url,
                    "snippet": _clean(item.get("description")),
                    "query": query,
                }
            )
        if len(results) >= _MAX_RESULTS_TOTAL:
            break

    if not results and not warnings:
        warnings.append("web search returned no results; research limited to the posting")
    return results[:_MAX_RESULTS_TOTAL], warnings


def _parse_search_results(raw: Any) -> list[dict[str, Any]]:
    payload = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    web = data.get("web") if isinstance(data, dict) else None
    return [item for item in (web or []) if isinstance(item, dict)]


def _clean(value: Any) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:400]
