#!/usr/bin/env python3
"""Public 2GIS page scraper.

Practical fallback workflow for public 2GIS pages when an official API key is
not available yet. It uses only stdlib, fetches normal 2gis.kz HTML pages, and
extracts structured data that is already present in the public response.

Commands:
  search  - find candidate firm pages from a city search page
  firm    - extract one firm card/page into structured JSON
  lookup  - search first, then enrich the top results with firm extraction
"""

from __future__ import annotations

import argparse
import html
from html.parser import HTMLParser
import json
import math
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE_HOST = "https://2gis.kz"
USER_AGENT = "Mozilla/5.0 (compatible; HermesAgent/1.0; +https://hermes-agent.nousresearch.com)"
REQUEST_TIMEOUT = 25
MAX_RETRIES = 3
BASE_DELAY = 0.8


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def error_exit(message: str, code: int = 1) -> None:
    print_json({"status": "error", "error": message})
    sys.exit(code)


def rate_sleep(multiplier: float = 1.0) -> None:
    delay = BASE_DELAY * multiplier + random.uniform(0.0, 0.35)
    time.sleep(delay)


def fetch_text(url: str, *, retries: int = MAX_RETRIES) -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.8",
    }
    req = urllib.request.Request(url, headers=headers)
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.reason} for {url}"
            if exc.code in (429, 500, 502, 503, 504):
                time.sleep(attempt)
                continue
            raise RuntimeError(last_error) from exc
        except urllib.error.URLError as exc:
            last_error = f"URL error: {exc.reason} for {url}"
            time.sleep(attempt)
        except TimeoutError as exc:
            last_error = f"Timeout while requesting {url}"
            time.sleep(attempt)
    raise RuntimeError(last_error or f"Failed to fetch {url}")


class FirmSearchParser(HTMLParser):
    def __init__(self, city: str):
        super().__init__(convert_charrefs=True)
        self.city = city.strip("/")
        self.in_firm_anchor = False
        self.current_href = ""
        self.current_text: list[str] = []
        self.results: list[dict[str, Any]] = []
        self._seen_ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attr_map = dict(attrs)
        href = attr_map.get("href") or ""
        m = re.search(rf"/(?:{re.escape(self.city)}/)?firm/(\d+)", href)
        if not m:
            return
        firm_id = m.group(1)
        if firm_id in self._seen_ids:
            return
        self.in_firm_anchor = True
        self.current_href = href
        self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.in_firm_anchor:
            self.current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self.in_firm_anchor:
            return
        firm_id_match = re.search(r"/firm/(\d+)", self.current_href)
        firm_id = firm_id_match.group(1) if firm_id_match else None
        raw_name = " ".join(part.strip() for part in self.current_text if part.strip())
        raw_name = re.sub(r"\s+", " ", raw_name).strip()
        name = _clean_anchor_name(raw_name)
        if firm_id and name:
            self._seen_ids.add(firm_id)
            self.results.append(
                {
                    "firm_id": firm_id,
                    "name": name,
                    "url": absolute_2gis_url(self.current_href, city=self.city),
                }
            )
        self.in_firm_anchor = False
        self.current_href = ""
        self.current_text = []


def _clean_anchor_name(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    # Search result anchors often include rating / UI crumbs after the title.
    parts = [p.strip() for p in text.split(" · ") if p.strip()]
    if parts:
        text = parts[0]
    text = re.sub(r"\s+[0-9]+(?:[.,][0-9]+)?$", "", text).strip()
    return text


def absolute_2gis_url(target: str, *, city: str | None = None) -> str:
    if target.startswith("http://") or target.startswith("https://"):
        return target
    if target.startswith("/"):
        return urllib.parse.urljoin(BASE_HOST, target)
    if re.fullmatch(r"\d+", target):
        resolved_city = city or "almaty"
        return f"{BASE_HOST}/{resolved_city}/firm/{target}"
    raise ValueError(f"Cannot normalize target: {target}")


def parse_origins(values: list[str] | None) -> list[dict[str, Any]]:
    origins = []
    for raw in values or []:
        if ":" not in raw:
            raise ValueError(f"Origin must look like label:lat,lon ; got {raw!r}")
        label, coords = raw.split(":", 1)
        if "," not in coords:
            raise ValueError(f"Origin must look like label:lat,lon ; got {raw!r}")
        lat_s, lon_s = coords.split(",", 1)
        origins.append({
            "label": label.strip() or "origin",
            "lat": float(lat_s.strip()),
            "lon": float(lon_s.strip()),
        })
    return origins


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_m * c


def attach_distances(payload: dict[str, Any], origins: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not origins:
        return payload
    coords = payload.get("coordinates")
    if not isinstance(coords, dict) or coords.get("lat") is None or coords.get("lon") is None:
        payload["distances"] = []
        return payload
    lat = float(coords["lat"])
    lon = float(coords["lon"])
    distances = []
    for origin in origins:
        meters = haversine_m(origin["lat"], origin["lon"], lat, lon)
        distances.append({
            "label": origin["label"],
            "straight_line_m": round(meters, 1),
            "straight_line_km": round(meters / 1000.0, 3),
            "origin": {"lat": origin["lat"], "lon": origin["lon"]},
        })
    payload["distances"] = distances
    return payload


def extract_title(html_text: str) -> str | None:
    m = re.search(r"<title>(.*?)</title>", html_text, re.I | re.S)
    if not m:
        return None
    title = html.unescape(m.group(1)).replace("\xa0", " ").strip()
    return re.sub(r"\s+", " ", title)


def extract_meta(html_text: str, attr_name: str, attr_value: str, content_name: str = "content") -> str | None:
    pattern = rf'<meta[^>]+{re.escape(attr_name)}="{re.escape(attr_value)}"[^>]+{re.escape(content_name)}="([^"]*)"'
    m = re.search(pattern, html_text, re.I)
    if not m:
        return None
    return html.unescape(m.group(1)).replace("\xa0", " ").strip()


def extract_json_value(html_text: str, key: str, *, start: int = 0) -> tuple[Any, int] | tuple[None, int]:
    marker = f'"{key}":'
    idx = html_text.find(marker, start)
    if idx == -1:
        return None, -1
    decoder = json.JSONDecoder()
    payload = html_text[idx + len(marker) :]
    try:
        value, consumed = decoder.raw_decode(payload)
        return value, idx + len(marker) + consumed
    except json.JSONDecodeError:
        return None, -1


def normalize_schedule(schedule: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(schedule, dict):
        return None
    out: dict[str, Any] = {
        "is_24x7": bool(schedule.get("is_24x7", False)),
        "description": schedule.get("description"),
        "days": {},
    }
    day_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for day in day_order:
        day_payload = schedule.get(day)
        if not isinstance(day_payload, dict):
            continue
        wh = day_payload.get("working_hours") or []
        windows = []
        for item in wh:
            if isinstance(item, dict) and item.get("from") and item.get("to"):
                windows.append({"from": item["from"], "to": item["to"]})
        out["days"][day] = windows
    if not out["days"] and not out.get("description") and not out.get("is_24x7"):
        return None
    return out


def normalize_contacts(contact_groups: list[Any] | None, html_text: str) -> dict[str, Any]:
    contacts: dict[str, list[str]] = {
        "phones": [],
        "whatsapp": [],
        "websites": [],
        "instagram": [],
        "telegram": [],
        "email": [],
        "other_urls": [],
    }

    def add(kind: str, value: str | None) -> None:
        if not value:
            return
        value = value.strip()
        if not value:
            return
        bucket = contacts.setdefault(kind, [])
        if value not in bucket:
            bucket.append(value)

    for group in contact_groups or []:
        if not isinstance(group, dict):
            continue
        for item in group.get("contacts", []) or []:
            if not isinstance(item, dict):
                continue
            ctype = str(item.get("type") or "").lower()
            value = item.get("print_text") or item.get("text") or item.get("value") or item.get("url")
            url = item.get("url")
            if ctype == "phone":
                add("phones", value)
            elif ctype == "whatsapp":
                add("whatsapp", url or value)
            elif ctype in ("website", "site"):
                add("websites", url or value)
            elif ctype == "instagram":
                add("instagram", url or value)
            elif ctype == "telegram":
                add("telegram", url or value)
            elif ctype == "email":
                add("email", value)
            elif url:
                add("other_urls", url)

    for m in re.findall(r'href="(https://wa\.me/[^"]+)"', html_text):
        add("whatsapp", html.unescape(m))
    for m in re.findall(r'href="(https?://(?:www\.)?instagram\.com/[^"]+)"', html_text):
        add("instagram", html.unescape(m))
    for m in re.findall(r'href="mailto:([^"]+)"', html_text):
        add("email", html.unescape(m))
    for m in re.findall(r'href="tel:([^"]+)"', html_text):
        add("phones", html.unescape(m))

    phones = contacts.get("phones") or []
    if phones:
        full_numbers = [p for p in phones if "*" not in p]
        if full_numbers:
            masked = []
            for p in phones:
                if "*" in p:
                    tail = re.sub(r"\D", "", p).replace("*", "")[-4:]
                    if tail and any(re.sub(r"\D", "", fp).endswith(tail) for fp in full_numbers):
                        continue
                masked.append(p)
            contacts["phones"] = []
            for p in full_numbers + masked:
                if p not in contacts["phones"]:
                    contacts["phones"].append(p)

    for key in list(contacts.keys()):
        if not contacts[key]:
            contacts.pop(key)
    return contacts


def extract_firm(html_text: str, url: str) -> dict[str, Any]:
    firm_id_match = re.search(r"/firm/(\d+)", url)
    firm_id = firm_id_match.group(1) if firm_id_match else None
    title = extract_title(html_text)
    start_idx = 0
    if firm_id:
        located = html_text.find(f'"id":"{firm_id}"')
        if located != -1:
            start_idx = max(0, located - 120000)

    address_name, _ = extract_json_value(html_text, "address_name", start=start_idx)
    point, _ = extract_json_value(html_text, "point", start=start_idx)
    rubrics, _ = extract_json_value(html_text, "rubrics", start=start_idx)
    schedule, _ = extract_json_value(html_text, "schedule", start=start_idx)
    contact_groups, _ = extract_json_value(html_text, "contact_groups", start=start_idx)
    dates, _ = extract_json_value(html_text, "dates", start=start_idx)
    city_alias, _ = extract_json_value(html_text, "city_alias", start=start_idx)
    name_ex, _ = extract_json_value(html_text, "name_ex", start=start_idx)
    org, _ = extract_json_value(html_text, "org", start=start_idx)

    primary_name = None
    extension = None
    if isinstance(name_ex, dict):
        primary_name = name_ex.get("primary")
        extension = name_ex.get("extension")

    description = extract_meta(html_text, "name", "description")
    og_title = extract_meta(html_text, "property", "og:title")
    og_description = extract_meta(html_text, "property", "og:description")

    result: dict[str, Any] = {
        "status": "ok",
        "source": "2GIS public page HTML",
        "url": url,
        "firm_id": firm_id,
        "name": primary_name or (org.get("primary") if isinstance(org, dict) else None) or title,
        "title": title,
        "subtitle": extension,
        "address": address_name,
        "city_alias": city_alias,
        "description": description,
        "og_title": og_title,
        "og_description": og_description,
        "coordinates": point if isinstance(point, dict) else None,
        "rubrics": [item.get("name") for item in (rubrics or []) if isinstance(item, dict) and item.get("name")],
        "schedule": normalize_schedule(schedule if isinstance(schedule, dict) else None),
        "contacts": normalize_contacts(contact_groups if isinstance(contact_groups, list) else None, html_text),
        "updated_at": dates.get("updated_at") if isinstance(dates, dict) else None,
        "limitations": [
            "Public 2GIS pages may expose masked phone hrefs while showing fuller phone text in embedded data.",
            "Markup and embedded JSON shape can change without notice; treat this as a fallback workflow, not a hard contract.",
        ],
    }

    if isinstance(result.get("coordinates"), dict):
        coords = result["coordinates"]
        if coords.get("lat") is None or coords.get("lon") is None:
            result["coordinates"] = None

    return result


def search_firms(city: str, query: str, limit: int = 10) -> dict[str, Any]:
    city = city.strip("/")
    encoded_query = urllib.parse.quote(query, safe="")
    url = f"{BASE_HOST}/{city}/search/{encoded_query}"
    html_text = fetch_text(url)
    parser = FirmSearchParser(city=city)
    parser.feed(html_text)
    results = parser.results[:limit]
    return {
        "status": "ok",
        "source": "2GIS public search HTML",
        "city": city,
        "query": query,
        "search_url": url,
        "count": len(results),
        "results": results,
    }


def lookup(city: str, query: str, search_limit: int = 10, firm_limit: int = 5, origins: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    search_payload = search_firms(city=city, query=query, limit=search_limit)
    enriched = []
    for row in search_payload["results"][:firm_limit]:
        rate_sleep(multiplier=1.0)
        html_text = fetch_text(row["url"])
        firm_data = extract_firm(html_text, row["url"])
        # Preserve the search-side display name if extraction title is noisy.
        if row.get("name") and not firm_data.get("name"):
            firm_data["name"] = row["name"]
        attach_distances(firm_data, origins)
        enriched.append(firm_data)
    return {
        "status": "ok",
        "workflow": "search_then_extract_firm_pages",
        "city": city,
        "query": query,
        "search": search_payload,
        "firms": enriched,
    }


def cmd_search(args: argparse.Namespace) -> None:
    print_json(search_firms(city=args.city, query=args.query, limit=args.limit))


def cmd_firm(args: argparse.Namespace) -> None:
    url = absolute_2gis_url(args.target, city=args.city)
    html_text = fetch_text(url)
    payload = extract_firm(html_text, url)
    attach_distances(payload, parse_origins(args.origin))
    print_json(payload)


def cmd_lookup(args: argparse.Namespace) -> None:
    print_json(
        lookup(
            city=args.city,
            query=args.query,
            search_limit=args.search_limit,
            firm_limit=args.firm_limit,
            origins=parse_origins(args.origin),
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Public 2GIS page scraper for fallback local business lookup"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="Search public 2GIS results and return candidate firm URLs")
    p_search.add_argument("query", help="Search text, e.g. 'coffee almaty' or 'barber' ")
    p_search.add_argument("--city", default="almaty", help="2GIS city alias, e.g. almaty, astana")
    p_search.add_argument("--limit", type=int, default=10, help="Max candidate firm links to return")
    p_search.set_defaults(func=cmd_search)

    p_firm = sub.add_parser("firm", help="Extract one public 2GIS firm page into structured JSON")
    p_firm.add_argument("target", help="Full URL, /city/firm/<id>, or bare numeric firm id")
    p_firm.add_argument("--city", default="almaty", help="Used only when target is a bare numeric id")
    p_firm.add_argument(
        "--origin",
        action="append",
        default=[],
        help="Optional origin in label:lat,lon form, e.g. home:43.21,76.91 ; can repeat",
    )
    p_firm.set_defaults(func=cmd_firm)

    p_lookup = sub.add_parser("lookup", help="Search then enrich top firm pages with detailed extraction")
    p_lookup.add_argument("query", help="Search text, e.g. 'coffee', 'визовый центр', 'стоматология'")
    p_lookup.add_argument("--city", default="almaty", help="2GIS city alias")
    p_lookup.add_argument("--search-limit", type=int, default=10, help="How many search hits to collect")
    p_lookup.add_argument("--firm-limit", type=int, default=5, help="How many firm pages to enrich")
    p_lookup.add_argument(
        "--origin",
        action="append",
        default=[],
        help="Optional origin in label:lat,lon form, e.g. office:43.24,76.93 ; can repeat",
    )
    p_lookup.set_defaults(func=cmd_lookup)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        error_exit("Interrupted by user", code=130)
    except Exception as exc:  # noqa: BLE001
        error_exit(str(exc))


if __name__ == "__main__":
    main()
