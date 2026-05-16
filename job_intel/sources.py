from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote_plus

import requests

from .models import Vacancy


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    source: str


class _DuckDuckGoParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hits: list[tuple[str, str]] = []
        self._capture = False
        self._current_href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("class", "").startswith("result__a"):
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


def search_duckduckgo(query: str, max_results: int = 10) -> list[SearchHit]:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    parser = _DuckDuckGoParser()
    parser.feed(resp.text)
    hits = []
    for title, href in parser.hits[:max_results]:
        hits.append(SearchHit(title=title, url=href, snippet="", source="duckduckgo"))
    return hits


def fetch_headhunter_vacancies(query: str, *, per_page: int = 20) -> list[Vacancy]:
    resp = requests.get(
        "https://api.hh.ru/vacancies",
        params={"text": query, "per_page": per_page, "page": 0, "order_by": "publication_time_desc"},
        timeout=30,
        headers={"User-Agent": "HermesJobIntel/1.0"},
    )
    resp.raise_for_status()
    data = resp.json()
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
