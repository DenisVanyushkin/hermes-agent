from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
from hashlib import sha256
import re
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

from .models import Vacancy

ALIASES = {
    "vp product": "vice president product",
    "head of product": "product director",
    "director of product": "product director",
    "chief product officer": "product executive",
}


def _norm(text: str) -> str:
    text = text.lower().strip()
    for old, new in ALIASES.items():
        text = text.replace(old, new)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text




def canonical_job_url(url: str | None, source: str | None = None) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except Exception:
        return raw
    scheme = (parsed.scheme or "https").lower()
    host = parsed.netloc.lower()
    path = parsed.path or ""

    if "linkedin.com" in host:
        m = re.search(r"/jobs/view/([0-9]+)", path)
        if not m:
            return ""
        path = f"/jobs/view/{m.group(1)}"
        return urlunsplit((scheme, host, path, "", ""))

    if "hh.ru" in host:
        m = re.search(r"/vacancy/([0-9]+)", path)
        if m:
            path = f"/vacancy/{m.group(1)}"
            return urlunsplit((scheme, host, path, "", ""))

    path = path.rstrip("/")
    return urlunsplit((scheme, host, path, "", ""))


def canonical_vacancy_key(vacancy: Vacancy) -> str:
    source = _norm(getattr(vacancy, "source", "") or "unknown")
    canonical_url = canonical_job_url(getattr(vacancy, "url", ""), getattr(vacancy, "source", None))
    if canonical_url:
        payload = "|".join((source, canonical_url.lower()))
    else:
        payload = "|".join((source, _norm(vacancy.company), _norm(vacancy.title), _norm(vacancy.location)))
    return sha256(payload.encode("utf-8")).hexdigest()


def description_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def is_duplicate(
    candidate: Vacancy,
    existing: Vacancy,
    *,
    similarity_threshold: float = 0.82,
    repost_window_days: int = 45,
) -> bool:
    if canonical_vacancy_key(candidate) == canonical_vacancy_key(existing):
        return True

    similarity = description_similarity(candidate.description, existing.description)
    if similarity < similarity_threshold:
        return False

    candidate_dt = _parse_dt(candidate.posted_at)
    existing_dt = _parse_dt(existing.posted_at)
    if candidate_dt and existing_dt:
        delta_days = abs((candidate_dt - existing_dt).days)
        if delta_days <= repost_window_days:
            return True

    return _norm(candidate.company) == _norm(existing.company) and _norm(candidate.title) == _norm(existing.title)
