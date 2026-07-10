from __future__ import annotations

import html
import pathlib
import re
import ssl
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib import error, parse, request


BASE_URL = "https://adilet.zan.kz"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AdiletLegalAnalyst/0.1; +https://adilet.zan.kz/rus/index/docs)",
    "Accept-Language": "ru,en;q=0.8",
}


class AdiletError(RuntimeError):
    """Base Adilet client error."""


class AdiletNetworkError(AdiletError):
    """Raised when network access to Adilet fails."""


class AdiletParseError(AdiletError):
    """Raised when a required HTML shape is missing."""


@dataclass
class FetchResult:
    url: str
    text: str
    warnings: list[str]


def _collapse_whitespace(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value, flags=re.S)
    return value.strip()


def _strip_html(fragment: str, *, keep_breaks: bool = False) -> str:
    fragment = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    if keep_breaks:
        fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
        fragment = re.sub(r"</(p|div|blockquote|li|tr|h[1-6])>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    if keep_breaks:
        fragment = re.sub(r"[ \t]+\n", "\n", fragment)
        fragment = re.sub(r"\n{3,}", "\n\n", fragment)
        return html.unescape(fragment).strip()
    return _collapse_whitespace(fragment)


def _extract_doc_id(url: str) -> str | None:
    match = re.search(r"/docs/([^/?#]+)", url)
    return match.group(1) if match else None


def _normalize_url(url: str) -> str:
    if not url:
        return url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return parse.urljoin(BASE_URL, url)


def _normalize_field_name(label: str) -> str:
    mapping = {
        "Дата принятия акта": "date_adopted",
        "Дата изменения акта": "date_modified",
        'Дата официальной публикации в ИПС "Әділет"': "date_published_in_adilet",
        "Официальная публикация": "official_publication",
        "Форма акта": "act_form",
        "Сфера правоотношений": "legal_scope",
        "Юридическая сила": "legal_force",
        "Орган, принявший акт": "issuing_body",
        "Территория действия": "territory",
        "Регистрационный номер акта в Государственном реестре нормативных правовых актов Республики Казахстан": "state_registry_number",
        "Регистрационный номер НПА, присвоенный нормотворческим органом": "issuer_registration_number",
        "Дата регистрации в МЮ": "date_registered_mju",
    }
    return mapping.get(label, re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_"))


def _parse_date(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def _extract_text_match(pattern: str, text: str, *, flags: int = re.S | re.I) -> str | None:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    return match.group(1)


def _extract_article(body_text: str, article: str) -> str | None:
    """Return the text of a single article ("77", "182-1") from an act body.

    Article headers sit on their own line ("Статья 77. Сверхурочная работа").
    A table of contents can produce a short duplicate match, so the longest
    match wins.
    """
    number = re.escape(article)
    pattern = rf"^\s*Статья\s+{number}[.\s].*?(?=^\s*Статья\s+\d|\Z)"
    matches = re.findall(pattern, body_text, flags=re.S | re.M)
    if not matches:
        return None
    return max(matches, key=len).strip()


def _parse_table_rows(table_html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_html in re.findall(r"<tr\b.*?>(.*?)</tr>", table_html, flags=re.S | re.I):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.S | re.I)
        if not cells:
            continue
        rows.append([_strip_html(cell, keep_breaks=True) for cell in cells])
    return rows


class AdiletClient:
    """Live-only scraper client for Adilet."""

    def __init__(self, *, timeout: float = 20.0, retries: int = 3, backoff_seconds: float = 1.2):
        self.timeout = timeout
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.ssl_context = ssl.create_default_context()
        # adilet.zan.kz serves an incomplete chain (leaf only, no GoGetSSL
        # intermediate); Linux OpenSSL does no AIA chasing, so verification
        # fails without the bundled intermediate. The bundle chains to
        # DigiCert Global Root G2 from the system store — verification stays
        # fully enabled.
        bundle = pathlib.Path(__file__).resolve().parent / "adilet_ca_bundle.pem"
        if bundle.exists():
            self.ssl_context.load_verify_locations(cafile=str(bundle))

    def fetch(self, url: str) -> FetchResult:
        warnings: list[str] = []
        final_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            req = request.Request(url, headers=DEFAULT_HEADERS)
            try:
                with request.urlopen(req, timeout=self.timeout, context=self.ssl_context) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    text = response.read().decode(charset, errors="replace")
                    if attempt > 1:
                        warnings.append(f"request_succeeded_after_retry_{attempt}")
                    return FetchResult(url=url, text=text, warnings=warnings)
            except (error.URLError, TimeoutError, ssl.SSLError, ConnectionError) as exc:
                final_error = exc
                if attempt < self.retries:
                    time.sleep(self.backoff_seconds * attempt)
        raise AdiletNetworkError(f"Failed to fetch {url}: {final_error}") from final_error

    def search_acts(self, query: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        filters = dict(filters or {})
        page = int(filters.get("page", 1) or 1)
        page_size = int(filters.get("page_size", 10) or 10)
        sort = filters.get("sort", "relevance") or "relevance"
        warnings: list[str] = []

        if page_size not in (10, 20, 50, 100):
            warnings.append("unsupported_page_size_requested_falling_back_to_10")
            page_size = 10

        params: dict[str, Any] = {"fulltext": query, "page": page, "pagesize": page_size}
        if sort == "legal_force":
            params["sort_field"] = "in"
            params["sort_desc"] = "false"
        elif sort == "adoption_date":
            params["sort_field"] = "dt"
        elif sort == "modification_date":
            params["sort_field"] = "dl"

        url = f"{BASE_URL}/rus/search/docs?{parse.urlencode(params, doseq=True)}"
        page_result = self.fetch(url)
        warnings.extend(page_result.warnings)
        html_text = page_result.text

        total_match = re.search(r"Найдено:\s*<strong>([\d\s]+)</strong>\s*документ", html_text, re.I)
        total_results = int(total_match.group(1).replace(" ", "")) if total_match else None
        if total_results is None:
            warnings.append("search_total_not_found")

        page_info_match = re.search(r"Страница\s+(\d+)\s+из\s+(\d+)", html_text, re.I)
        total_pages = int(page_info_match.group(2)) if page_info_match else None
        if total_pages is None:
            warnings.append("search_pagination_not_found")

        results = []
        header_matches = list(
            re.finditer(
                r'<h4 class="post_header">\s*(.*?)</h4>(.*?)(?=<h4 class="post_header">|<div id="footer"|$)',
                html_text,
                flags=re.S | re.I,
            )
        )
        for match in header_matches:
            header_html = match.group(1)
            body_html = match.group(2)
            href_match = re.search(r'<a href="(/rus/docs/[^"]+)">(.+?)</a>', header_html, flags=re.S | re.I)
            if not href_match:
                continue
            doc_url = _normalize_url(href_match.group(1))
            doc_id = _extract_doc_id(doc_url)
            if not doc_id:
                continue
            title = _strip_html(href_match.group(2))
            snippet = " ".join(
                _strip_html(item, keep_breaks=True)
                for item in re.findall(r"<blockquote>(.*?)</blockquote>", body_html, flags=re.S | re.I)
            ).strip()
            summary = _extract_text_match(r"<p>(.*?)</p>", body_html)
            status = _extract_text_match(r'<span class="status[^"]*">(.*?)</span>', body_html)
            number_match = re.search(r'<span class="post_number">\s*(\d+)\.', header_html)
            results.append(
                {
                    "rank": int(number_match.group(1)) if number_match else len(results) + 1,
                    "doc_id": doc_id,
                    "title": title,
                    "status": _collapse_whitespace(status or "") or None,
                    "url": doc_url,
                    "summary": _strip_html(summary, keep_breaks=True) if summary else None,
                    "snippet": snippet or None,
                }
            )

        if not results:
            if total_results == 0:
                warnings.append("no_results_for_query")
            else:
                raise AdiletParseError("Could not parse search results from Adilet response.")

        applied_structured_filters = any(
            filters.get(name)
            for name in ("date_from", "date_to", "act_form", "issuing_body", "legal_force", "scope")
        )
        if applied_structured_filters:
            warnings.append(
                "structured_filters_are_applied_client_side_on_the_requested_search_page_only"
            )
            filtered_results = []
            for item in results:
                info = self.get_act_info(item["doc_id"])
                if self._matches_filters(info, filters):
                    item["info_preview"] = {
                        key: info.get(key)
                        for key in ("date_adopted", "act_form", "issuing_body", "legal_force", "legal_scope")
                    }
                    filtered_results.append(item)
            results = filtered_results

        return {
            "query": query,
            "filters_applied": filters,
            "page": page,
            "page_size": page_size,
            "total_results": total_results,
            "total_pages": total_pages,
            "source_url": url,
            "results": results,
            "warnings": warnings,
        }

    DEFAULT_TEXT_MAX_CHARS = 50_000

    def get_act_text(
        self,
        doc_id: str,
        *,
        article: str | None = None,
        max_chars: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        url = f"{BASE_URL}/rus/docs/{doc_id}"
        fetched = self.fetch(url)
        html_text = fetched.text
        warnings = list(fetched.warnings)

        title = _strip_html(_extract_text_match(r"<h1>(.*?)</h1>", html_text) or "")
        if not title:
            raise AdiletParseError(f"Could not parse act title for {doc_id}.")

        status = _extract_text_match(r'<span class="status[^"]*">(.*?)</span>', html_text)
        summary = _extract_text_match(
            r'<div class="container_alpha slogan">.*?<p>(.*?)</p>',
            html_text,
        )

        body_html = _extract_text_match(r"<article>(.*?)</article>", html_text)
        if not body_html:
            body_html = _extract_text_match(
                r'<div class="container_gamma text[^"]*">\s*(.*?)\s*<div class="container_omega aftertext">',
                html_text,
            )
            if body_html:
                warnings.append("act_text_article_tag_missing_used_container_selector")
        if not body_html:
            body_html = _extract_text_match(
                r'<div class="container_alpha slogan">.*?</div>(.*?)<div class="container_omega aftertext">',
                html_text,
            )
            warnings.append("act_text_body_fallback_selector_used")
        if not body_html:
            raise AdiletParseError(f"Could not parse act text body for {doc_id}.")

        # Strip page chrome that survives inside broad selectors: nav tabs,
        # download toolbar and the metadata table.
        body_html = re.sub(r'<div id="tabs_container">.*?</div>\s*</div>', " ", body_html, flags=re.S | re.I)
        body_html = re.sub(r'<div class="container_gamma tabs[^"]*">.*?</div>', " ", body_html, flags=re.S | re.I)
        body_html = re.sub(r"<table\b[^>]*id=\"ethernatable\".*?</table>", " ", body_html, flags=re.S | re.I)
        body_text = _strip_html(body_html, keep_breaks=True)
        body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()
        if not body_text:
            raise AdiletParseError(f"Extracted empty act text body for {doc_id}.")

        requested_article = article.strip() if article else None
        if requested_article:
            extracted = _extract_article(body_text, requested_article)
            if extracted is None:
                warnings.append(f"article_not_found:{requested_article}")
            else:
                body_text = extracted

        total_chars = len(body_text)
        if offset:
            if offset >= total_chars:
                warnings.append("offset_beyond_text_end")
            body_text = body_text[offset:]
        limit = self.DEFAULT_TEXT_MAX_CHARS if max_chars is None else max_chars
        truncated = False
        if limit and limit > 0 and len(body_text) > limit:
            body_text = body_text[:limit]
            truncated = True
            warnings.append(
                "act_text_truncated: pass `article` to fetch a single article, "
                "or use `offset`/`max_chars` to page through the text"
            )

        downloads = self.get_act_downloads(doc_id, html_text=html_text)

        return {
            "doc_id": doc_id,
            "title": title,
            "status": _collapse_whitespace(status or "") or None,
            "summary": _strip_html(summary, keep_breaks=True) if summary else None,
            "article": requested_article,
            "text": body_text,
            "total_chars": total_chars,
            "offset": offset,
            "truncated": truncated,
            "download_urls": downloads,
            "source_url": url,
            "warnings": warnings,
        }

    def get_act_info(self, doc_id: str) -> dict[str, Any]:
        url = f"{BASE_URL}/rus/docs/{doc_id}/info"
        fetched = self.fetch(url)
        html_text = fetched.text
        warnings = list(fetched.warnings)

        title = _strip_html(_extract_text_match(r"<h1>(.*?)</h1>", html_text) or "")
        if not title:
            raise AdiletParseError(f"Could not parse info page title for {doc_id}.")
        status = _extract_text_match(r'<span class="status[^"]*">(.*?)</span>', html_text)

        table_html = _extract_text_match(r'<table[^>]*id="ethernatable"[^>]*>(.*?)</table>', html_text)
        if not table_html:
            raise AdiletParseError(f"Could not parse info table for {doc_id}.")
        rows = _parse_table_rows(table_html)
        metadata: dict[str, Any] = {
            "doc_id": doc_id,
            "title": title,
            "status": _collapse_whitespace(status or "") or None,
            "language": "rus",
            "source_urls": {
                "text": f"{BASE_URL}/rus/docs/{doc_id}",
                "info": url,
                "history": f"{BASE_URL}/rus/docs/{doc_id}/history",
                "links": f"{BASE_URL}/rus/docs/{doc_id}/links",
            },
            "warnings": warnings,
        }
        for row in rows:
            if len(row) < 2:
                continue
            label = row[0].strip()
            value = row[1].strip()
            key = _normalize_field_name(label)
            if not key:
                warnings.append(f"info_row_with_empty_label_skipped:{value[:40]}")
                continue
            metadata[key] = value or None

        required = ("date_adopted", "act_form", "legal_force", "issuing_body")
        for field in required:
            if field not in metadata:
                warnings.append(f"missing_expected_info_field:{field}")
                metadata.setdefault(field, None)

        for field in (
            "date_modified",
            "date_published_in_adilet",
            "official_publication",
            "legal_scope",
            "territory",
            "state_registry_number",
            "issuer_registration_number",
            "date_registered_mju",
        ):
            metadata.setdefault(field, None)

        return metadata

    def get_act_history(self, doc_id: str) -> dict[str, Any]:
        url = f"{BASE_URL}/rus/docs/{doc_id}/history"
        fetched = self.fetch(url)
        html_text = fetched.text
        warnings = list(fetched.warnings)

        title = _strip_html(_extract_text_match(r"<h1>(.*?)</h1>", html_text) or "")
        table_html = _extract_text_match(r'<table[^>]*id="ethernatable"[^>]*>(.*?)</table>', html_text)
        if not table_html:
            raise AdiletParseError(f"Could not parse history table for {doc_id}.")
        rows = _parse_table_rows(table_html)
        headers = rows[0]
        entries = []
        for row in rows[1:]:
            if len(row) != len(headers):
                warnings.append("history_row_column_mismatch")
                continue
            item = {headers[idx]: row[idx] for idx in range(len(headers))}
            entries.append(
                {
                    "index": item.get("№"),
                    "title": item.get("Заголовок") or item.get("Документ"),
                    "act_form_and_body": item.get("Форма НПА и орган, принявший акт"),
                    "details": item.get("Дополнительная информация"),
                    "modified_date": item.get("Дата изменения"),
                    "status": item.get("Статус НПА"),
                }
            )

        return {
            "doc_id": doc_id,
            "title": title or None,
            "entries": entries,
            "source_url": url,
            "warnings": warnings,
        }

    def get_act_links(self, doc_id: str) -> dict[str, Any]:
        url = f"{BASE_URL}/rus/docs/{doc_id}/links"
        fetched = self.fetch(url)
        html_text = fetched.text
        warnings = list(fetched.warnings)
        title = _strip_html(_extract_text_match(r"<h1>(.*?)</h1>", html_text) or "")

        def parse_section(section_id: str) -> list[dict[str, Any]]:
            section_match = re.search(
                rf'<div id="{section_id}">(.*?)</table>',
                html_text,
                re.S | re.I,
            )
            if not section_match:
                warnings.append(f"links_section_not_found:{section_id}")
                return []
            section_html = section_match.group(1)
            row_blocks = re.findall(r"<tr\b.*?>(.*?)</tr>", section_html, flags=re.S | re.I)
            entries = []
            for row_html in row_blocks[1:]:
                cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.S | re.I)
                if len(cells) < 3:
                    continue
                link_match = re.search(r'<a href="(/rus/docs/[^"#?]+)', cells[1], flags=re.I)
                entries.append(
                    {
                        "index": _strip_html(cells[0]),
                        "document": _strip_html(cells[1], keep_breaks=True),
                        "context": _strip_html(cells[2], keep_breaks=True),
                        "url": _normalize_url(link_match.group(1)) if link_match else None,
                    }
                )
            return entries

        return {
            "doc_id": doc_id,
            "title": title or None,
            "from_document": parse_section("from"),
            "to_document": parse_section("to"),
            "source_url": url,
            "warnings": warnings,
        }

    def get_act_downloads(self, doc_id: str, *, html_text: str | None = None) -> dict[str, Any]:
        url = f"{BASE_URL}/rus/docs/{doc_id}"
        warnings: list[str] = []
        if html_text is None:
            fetched = self.fetch(url)
            html_text = fetched.text
            warnings.extend(fetched.warnings)
        pdf_path = _extract_text_match(rf'<a href="(/rus/docs/{re.escape(doc_id)}/download)"[^>]*>\s*PDF\s*</a>', html_text)
        docx_path = _extract_text_match(rf'<a href="(/rus/docs/{re.escape(doc_id)}/download/docx)"[^>]*>\s*DOCX\s*</a>', html_text)
        ekb_url = _extract_text_match(r'<a href="(http://law\.gov\.kz/client/#!/doc/[^"]+)"[^>]*>\s*Версия из ЭКБ\s*</a>', html_text)
        return {
            "doc_id": doc_id,
            "pdf_url": _normalize_url(pdf_path) if pdf_path else None,
            "docx_url": _normalize_url(docx_path) if docx_path else None,
            "ekb_url": ekb_url,
            "source_url": url,
            "warnings": warnings,
        }

    def healthcheck_source(self) -> dict[str, Any]:
        url = f"{BASE_URL}/rus/index/docs"
        fetched = self.fetch(url)
        html_text = fetched.text
        warnings = list(fetched.warnings)
        search_form_present = bool(re.search(r'<form action="/rus/search/docs"', html_text))
        advanced_search_present = "Расширенный поиск" in html_text
        if not search_form_present:
            raise AdiletParseError("Could not find the search form on Adilet index page.")
        if not advanced_search_present:
            warnings.append("advanced_search_link_not_found")
        return {
            "ok": True,
            "source_url": url,
            "search_form_present": search_form_present,
            "advanced_search_present": advanced_search_present,
            "warnings": warnings,
        }

    def _matches_filters(self, info: dict[str, Any], filters: dict[str, Any]) -> bool:
        def contains_any(field_name: str, values: list[str] | None) -> bool:
            if not values:
                return True
            field_value = (info.get(field_name) or "").lower()
            return any(value.lower() in field_value for value in values)

        if not contains_any("act_form", filters.get("act_form")):
            return False
        if not contains_any("issuing_body", filters.get("issuing_body")):
            return False
        if not contains_any("legal_force", filters.get("legal_force")):
            return False
        if not contains_any("legal_scope", filters.get("scope")):
            return False

        adopted = _parse_date(info.get("date_adopted") or "")
        if filters.get("date_from") and adopted:
            date_from = datetime.strptime(filters["date_from"], "%Y-%m-%d")
            if adopted < date_from:
                return False
        if filters.get("date_to") and adopted:
            date_to = datetime.strptime(filters["date_to"], "%Y-%m-%d")
            if adopted > date_to:
                return False
        return True

    def run_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = arguments or {}
        dispatch = {
            "search_acts": lambda: self.search_acts(
                query=arguments["query"],
                filters=arguments.get("filters"),
            ),
            "get_act_text": lambda: self.get_act_text(
                arguments["doc_id"],
                article=arguments.get("article"),
                max_chars=arguments.get("max_chars"),
                offset=int(arguments.get("offset") or 0),
            ),
            "get_act_info": lambda: self.get_act_info(arguments["doc_id"]),
            "get_act_history": lambda: self.get_act_history(arguments["doc_id"]),
            "get_act_links": lambda: self.get_act_links(arguments["doc_id"]),
            "get_act_downloads": lambda: self.get_act_downloads(arguments["doc_id"]),
            "healthcheck_source": self.healthcheck_source,
        }
        if name not in dispatch:
            raise KeyError(f"Unknown tool: {name}")
        return dispatch[name]()

    @staticmethod
    def tool_schemas() -> list[dict[str, Any]]:
        search_filters = {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                "act_form": {"type": "array", "items": {"type": "string"}},
                "issuing_body": {"type": "array", "items": {"type": "string"}},
                "legal_force": {"type": "array", "items": {"type": "string"}},
                "scope": {"type": "array", "items": {"type": "string"}},
                "page": {"type": "integer", "minimum": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                "sort": {
                    "type": "string",
                    "enum": ["relevance", "legal_force", "adoption_date", "modification_date"]
                }
            },
            "additionalProperties": False
        }
        return [
            {
                "name": "search_acts",
                "description": "Search Adilet acts by free-text query with optional structured filters.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "filters": search_filters
                    },
                    "required": ["query"],
                    "additionalProperties": False
                }
            },
            {
                "name": "get_act_text",
                "description": (
                    "Fetch the text of an Adilet act by doc_id. Large acts (codes) are "
                    "truncated to max_chars; pass `article` (e.g. \"77\" or \"182-1\") to "
                    "fetch a single article, or page with offset/max_chars."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "doc_id": {"type": "string"},
                        "article": {
                            "type": "string",
                            "description": "Article number to extract, e.g. \"77\" or \"182-1\""
                        },
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Max characters of text to return (default 50000)"
                        },
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Skip this many characters before applying max_chars"
                        }
                    },
                    "required": ["doc_id"],
                    "additionalProperties": False
                }
            },
            {
                "name": "get_act_info",
                "description": "Fetch normalized legal metadata for an Adilet act by doc_id.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"doc_id": {"type": "string"}},
                    "required": ["doc_id"],
                    "additionalProperties": False
                }
            },
            {
                "name": "get_act_history",
                "description": "Fetch the change-history table for an Adilet act by doc_id.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"doc_id": {"type": "string"}},
                    "required": ["doc_id"],
                    "additionalProperties": False
                }
            },
            {
                "name": "get_act_links",
                "description": "Fetch inbound and outbound act references for an Adilet act by doc_id.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"doc_id": {"type": "string"}},
                    "required": ["doc_id"],
                    "additionalProperties": False
                }
            },
            {
                "name": "get_act_downloads",
                "description": "Return canonical download links for an Adilet act by doc_id.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"doc_id": {"type": "string"}},
                    "required": ["doc_id"],
                    "additionalProperties": False
                }
            },
            {
                "name": "healthcheck_source",
                "description": "Check Adilet reachability and core selector availability.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False
                }
            }
        ]
