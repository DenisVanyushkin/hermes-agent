"""Small authenticated client for the official HeadHunter API.

This module owns the HTTP contract only. Mapping API payloads into Vacancy is
kept in ``job_intel.sources`` so the acquisition boundary stays explicit.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import requests


HH_BASE_URL = "https://api.hh.ru"
HH_TOKEN_URL = f"{HH_BASE_URL}/token"
HH_USER_AGENT = "hermes-job-intel/1.0 (denis@vanyushk.in)"
MINT_COOLDOWN_SECONDS = 300
MAX_PER_PAGE = 100
PAGINATION_DEPTH_CAP = 2000
MAX_REQUEST_ATTEMPTS = 3
DEFAULT_REQUEST_DELAY_SECONDS = 0.5

_sleep = time.sleep
_last_request_at: float | None = None


class HHError(RuntimeError):
    """Base class for expected HeadHunter transport failures."""


class HHTokenCooldown(HHError):
    """The application-token endpoint must not be called again yet."""


class HHAuthError(HHError):
    """The cached application token was rejected by hh."""


class HHRateLimited(HHError):
    """hh returned 429 after the bounded retry budget."""


class HHNotFound(HHError):
    """The requested vacancy is positively absent (404/410)."""


class HHArgumentDropped(HHError):
    """The API silently omitted one of the requested search arguments."""


class _HTTPStatus(HHError):
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"HeadHunter HTTP {status_code}: {payload}")


@dataclass(frozen=True)
class SearchResult:
    items: list[dict[str, Any]]
    found: int
    truncated: bool
    pages_requested: int = 0


def _token_cache_path() -> Path:
    configured = os.getenv("JOB_INTEL_HH_TOKEN_CACHE", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path("~/.hermes/cache/hh_app_token.json").expanduser()


def _read_token_cache(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_token_cache(path: Path, payload: dict[str, Any]) -> None:
    # mkdir(mode=...) only applies to directories created by this call. Never
    # chmod an existing state directory: it may carry ACL grants for exporters
    # and monitoring consumers.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)
    os.chmod(path, 0o600)


@contextmanager
def _token_cache_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _post_token(client_id: str, client_secret: str) -> dict[str, Any]:
    response = requests.post(
        HH_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"User-Agent": HH_USER_AGENT, "Accept": "application/json"},
        timeout=30,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        payload = response.text
        raise HHError(f"HeadHunter token response was not JSON: {payload[:200]}") from exc
    if response.status_code >= 400:
        raise _HTTPStatus(response.status_code, payload)
    if not isinstance(payload, dict):
        raise HHError("HeadHunter token response was not an object")
    return payload


def get_app_token(*, force_refresh: bool = False) -> str:
    """Read the persistent app token, minting only when strictly necessary."""
    path = _token_cache_path()
    cached = _read_token_cache(path)
    token = str(cached.get("access_token") or "").strip()
    if token and not force_refresh:
        return token

    with _token_cache_lock(path):
        cached = _read_token_cache(path)
        token = str(cached.get("access_token") or "").strip()
        if token and not force_refresh:
            return token
        minted_at = float(cached.get("minted_at") or 0)
        age = time.time() - minted_at if minted_at else None
        if force_refresh and age is not None and age < MINT_COOLDOWN_SECONDS:
            remaining = max(0, int(MINT_COOLDOWN_SECONDS - age))
            raise HHTokenCooldown(f"HeadHunter app token cooldown active for {remaining}s")

        client_id = os.getenv("JOB_INTEL_HH_CLIENT_ID", "").strip()
        client_secret = os.getenv("JOB_INTEL_HH_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise HHError("JOB_INTEL_HH_CLIENT_ID and JOB_INTEL_HH_CLIENT_SECRET are required")
        payload = _post_token(client_id, client_secret)
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise HHError("HeadHunter token response did not contain access_token")
        _write_token_cache(
            path,
            {
                "access_token": token,
                "token_type": payload.get("token_type", "bearer"),
                "minted_at": time.time(),
            },
        )
        return token


def _get(path: str, params: dict[str, Any], token: str) -> Any:
    response = requests.get(
        f"{HH_BASE_URL}{path}",
        params=params,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": HH_USER_AGENT,
            "Accept": "application/json",
        },
        timeout=30,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    if response.status_code >= 400:
        raise _HTTPStatus(response.status_code, payload)
    return payload


def _is_oauth_failure(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return False
    oauth_values = {"token_expired", "bad_authorization", "token_revoked"}
    return any(
        isinstance(error, dict)
        and (error.get("type") == "oauth" or error.get("value") in oauth_values)
        for error in errors
    )


def _request_delay_seconds() -> float:
    raw = os.getenv("JOB_INTEL_HH_DELAY_SECONDS", str(DEFAULT_REQUEST_DELAY_SECONDS)).strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_REQUEST_DELAY_SECONDS


def _wait_for_request_slot() -> None:
    global _last_request_at
    now = time.monotonic()
    if _last_request_at is not None:
        remaining = _request_delay_seconds() - (now - _last_request_at)
        if remaining > 0:
            _sleep(remaining)
    _last_request_at = time.monotonic()


def _request_json(path: str, params: dict[str, Any]) -> Any:
    token = get_app_token()
    auth_retried = False
    rate_attempt = 0
    while True:
        try:
            _wait_for_request_slot()
            return _get(path, params, token)
        except _HTTPStatus as exc:
            if exc.status_code == 403 and _is_oauth_failure(exc.payload):
                if auth_retried:
                    raise HHAuthError(str(exc)) from exc
                auth_retried = True
                try:
                    token = get_app_token(force_refresh=True)
                except (HHTokenCooldown, HHError) as refresh_exc:
                    raise HHAuthError(f"HeadHunter token refresh failed: {refresh_exc}") from refresh_exc
                continue
            if exc.status_code in (404, 410):
                raise HHNotFound(str(exc)) from exc
            if exc.status_code == 429:
                if rate_attempt >= MAX_REQUEST_ATTEMPTS - 1:
                    raise HHRateLimited(str(exc)) from exc
                _sleep(0.5 * (2**rate_attempt))
                rate_attempt += 1
                continue
            raise HHError(str(exc)) from exc


def _argument_names(arguments: Any) -> set[str]:
    names: set[str] = set()
    if not isinstance(arguments, list):
        return names
    for argument in arguments:
        if isinstance(argument, str):
            names.add(argument)
        elif isinstance(argument, dict) and argument.get("argument"):
            names.add(str(argument["argument"]))
    return names


def _assert_arguments(payload: Any, requested: set[str]) -> None:
    if not isinstance(payload, dict):
        return
    actual = _argument_names(payload.get("arguments"))
    if not actual:
        return
    expected = requested - {"page", "per_page", "no_magic", "describe_arguments"}
    dropped = sorted(expected - actual)
    if dropped:
        raise HHArgumentDropped(f"HeadHunter silently dropped search arguments: {', '.join(dropped)}")


def search_vacancies(**params: Any) -> dict[str, Any]:
    requested = {key for key, value in params.items() if value is not None}
    query = {key: value for key, value in params.items() if value is not None}
    per_page = int(query.get("per_page", MAX_PER_PAGE))
    if per_page < 1 or per_page > MAX_PER_PAGE:
        raise ValueError(f"HeadHunter per_page must be between 1 and {MAX_PER_PAGE}")
    query["per_page"] = per_page
    query["no_magic"] = "true"
    query["describe_arguments"] = "true"
    payload = _request_json("/vacancies", query)
    _assert_arguments(payload, requested)
    if not isinstance(payload, dict):
        raise HHError("HeadHunter search response was not an object")
    return payload


def fetch_vacancy_detail(vacancy_id: str | int) -> dict[str, Any]:
    value = str(vacancy_id).strip()
    if not value:
        raise ValueError("vacancy_id is required")
    payload = _request_json(f"/vacancies/{value}", {})
    if not isinstance(payload, dict):
        raise HHError("HeadHunter detail response was not an object")
    return payload


def _collect_pages(max_items: int | None, params: dict[str, Any]) -> SearchResult:
    limit = PAGINATION_DEPTH_CAP if max_items is None else min(max(int(max_items), 0), PAGINATION_DEPTH_CAP)
    per_page = min(max(int(params.get("per_page", MAX_PER_PAGE)), 1), MAX_PER_PAGE)
    base = {key: value for key, value in params.items() if key != "page"}
    base["per_page"] = per_page
    first = search_vacancies(**base, page=0)
    found = int(first.get("found") or 0)
    pages = int(first.get("pages") or 0)
    expected_items = min(found, limit)
    page_count = min(
        1 if expected_items == 0 else (expected_items + per_page - 1) // per_page,
        max(pages, 1),
        (PAGINATION_DEPTH_CAP + per_page - 1) // per_page,
    )
    items: list[dict[str, Any]] = []
    pages_requested = 0
    for page in range(page_count):
        payload = first if page == 0 else search_vacancies(**base, page=page)
        pages_requested += 1
        page_items = payload.get("items") or []
        if not isinstance(page_items, list):
            page_items = []
        items.extend(item for item in page_items if isinstance(item, dict))
        if len(items) >= limit or len(page_items) < per_page:
            break
    items = items[:limit]
    return SearchResult(items=items, found=found, truncated=found > len(items), pages_requested=pages_requested)


def iter_search_results(max_items: int | None = None, **params: Any) -> Iterator[dict[str, Any]]:
    result = _collect_pages(max_items, params)
    yield from result.items


def collect_search_results(max_items: int | None = None, **params: Any) -> SearchResult:
    return _collect_pages(max_items, params)
