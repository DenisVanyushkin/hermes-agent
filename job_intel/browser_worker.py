from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import subprocess
import sys
import time
from time import perf_counter
import traceback
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from .browser_sourcing import (
    BrowserNativeUnavailable,
    BrowserSourceClient,
    resolve_browser_config,
    _ensure_required_browser_profile,
)
from .models import Vacancy


_CDP_TARGETS = {
    "linkedin": {
        "profile": "linkedin",
        "start_url": "https://www.linkedin.com/",
        "cdp_url": "http://169.254.77.2:19222",
        "allowed_prefixes": ("https://www.linkedin.com/", "chrome://newtab/", "about:blank"),
        "max_page_targets": 8,
    },
}


@dataclass
class _DispatchCounters:
    market_query_dispatch_count: int = 0
    sudo_dispatch_count: int = 0

    def reset(self) -> None:
        self.market_query_dispatch_count = 0
        self.sudo_dispatch_count = 0


_DISPATCH_COUNTERS = _DispatchCounters()


def _run_process(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    command = args[0] if args else kwargs.get("args")
    if isinstance(command, (list, tuple)) and command and command[0] == "sudo":
        _DISPATCH_COUNTERS.sudo_dispatch_count += 1
    return subprocess.run(*args, **kwargs)


def _browser_runtime_dir() -> Path:
    configured = os.getenv("JOB_INTEL_BROWSER_RUNTIME_DIR", "").strip() or os.getenv("BROWSER_DESKTOP_BASE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    browser_python = os.getenv("JOB_INTEL_BROWSER_PYTHON", "").strip()
    if browser_python:
        path = Path(browser_python).expanduser()
        if path.name == "python" and path.parent.name == "bin" and path.parent.parent.name == "playwright-venv":
            return path.parent.parent.parent
    return Path("/var/lib/browser-desktop")


def _prepare_browser_runtime_env() -> None:
    base_dir = _browser_runtime_dir()
    cache_dir = Path(os.getenv("XDG_CACHE_HOME", "").strip() or (base_dir / ".cache"))
    browsers_path = Path(os.getenv("PLAYWRIGHT_BROWSERS_PATH", "").strip() or (cache_dir / "ms-playwright"))
    os.environ["JOB_INTEL_BROWSER_RUNTIME_DIR"] = str(base_dir)
    os.environ["BROWSER_DESKTOP_BASE_DIR"] = str(base_dir)
    os.environ["HOME"] = str(base_dir)
    os.environ["XDG_RUNTIME_DIR"] = str(base_dir / "runtime")
    os.environ["XDG_CONFIG_HOME"] = str(base_dir / ".config")
    os.environ["XDG_CACHE_HOME"] = str(cache_dir)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
    os.environ.setdefault("JOB_INTEL_BROWSER_DIAGNOSTICS_DIR", "/var/lib/job-intel/state/browser-diagnostics")
    os.environ.pop("JOB_INTEL_BROWSER_EXECUTABLE", None)
    os.environ.pop("JOB_INTEL_BROWSER_CHANNEL", None)
    os.environ["JOB_INTEL_BROWSER_HEADLESS"] = "0"


def _cdp_ready(cdp_url: str, *, attempts: int = 1, delay_seconds: float = 1.0) -> bool:
    version_url = f"{cdp_url.rstrip('/')}/json/version"
    for attempt in range(attempts):
        try:
            with urlopen(version_url, timeout=5) as response:
                if response.status == 200:
                    return True
        except URLError:
            pass
        except Exception:
            pass
        if attempt + 1 < attempts:
            time.sleep(delay_seconds)
    return False


def _cdp_json(cdp_url: str, suffix: str) -> Any:
    url = f"{cdp_url.rstrip('/')}/{suffix.lstrip('/')}"
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8", errors="ignore"))


def _allowed_page_url(source: str, url: str) -> bool:
    target = _CDP_TARGETS.get(source)
    if not target:
        return True
    return any(url.startswith(prefix) for prefix in target.get("allowed_prefixes", ()))


def _page_cleanup_key(source: str, url: str) -> str:
    if not url:
        return "empty"
    if url == "about:blank":
        return "about:blank"
    if source == "linkedin" and url.startswith("https://www.linkedin.com/"):
        return "linkedin-main"
    return url


def _close_foreign_pages(source: str, cdp_url: str) -> dict[str, int]:
    try:
        items = _cdp_json(cdp_url, "/json/list")
    except Exception:
        return {"pages": 0, "foreign": 0, "closed": 0, "remaining_foreign": 0}
    page_targets = [item for item in items if item.get("type") == "page"]
    foreign_targets = []
    closable = []
    keep_seen: set[str] = set()
    for item in page_targets:
        url = item.get("url", "")
        if not _allowed_page_url(source, url):
            foreign_targets.append(item)
            closable.append(item)
            continue
        key = _page_cleanup_key(source, url)
        if key in keep_seen:
            closable.append(item)
            continue
        keep_seen.add(key)
    closed = 0
    for item in closable:
        target_id = item.get("id")
        if not target_id:
            continue
        try:
            _cdp_json(cdp_url, f"/json/close/{target_id}")
            closed += 1
        except Exception:
            continue
    if closed:
        time.sleep(1.5)
    try:
        remaining = _cdp_json(cdp_url, "/json/list")
    except Exception:
        remaining = []
    remaining_pages = [item for item in remaining if item.get("type") == "page"]
    remaining_foreign = [item for item in remaining_pages if not _allowed_page_url(source, item.get("url", ""))]
    return {
        "pages": len(page_targets),
        "foreign": len(foreign_targets),
        "closed": closed,
        "remaining_foreign": len(remaining_foreign),
    }


def _endpoint_dirty(source: str, cdp_url: str) -> bool:
    target = _CDP_TARGETS.get(source)
    if not target:
        return False
    try:
        items = _cdp_json(cdp_url, "/json/list")
    except Exception:
        return False
    page_count = sum(1 for item in items if item.get("type") == "page")
    foreign = sum(1 for item in items if item.get("type") == "page" and not _allowed_page_url(source, item.get("url", "")))
    return foreign > 0 or page_count > int(target.get("max_page_targets", 8))


def _browser_process_age_seconds(
    source: str, *, cdp_url: str | None = None, profile: Path | None = None
) -> int:
    target = _CDP_TARGETS.get(source)
    if not target:
        return 0
    endpoint = cdp_url or str(target["cdp_url"])
    try:
        port = urlsplit(endpoint).port
    except ValueError:
        return 0
    if port is None:
        return 0
    profile_marker = (
        f"--user-data-dir={profile}"
        if profile is not None
        else f"profiles/{target['profile']}"
    )
    proc = _run_process(
        ["ps", "-eo", "etimes=,args="],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    for line in (proc.stdout or "").splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2:
            continue
        if f"remote-debugging-port={port}" not in fields[1] or profile_marker not in fields[1]:
            continue
        try:
            return int(fields[0])
        except ValueError:
            return 0
    return 0


def _browser_process_stale(
    source: str, *, cdp_url: str | None = None, profile: Path | None = None
) -> bool:
    max_age = int(os.getenv("JOB_INTEL_BROWSER_MAX_PROCESS_AGE_SECONDS", "14400"))
    age = _browser_process_age_seconds(source, cdp_url=cdp_url, profile=profile)
    return age > max_age if age > 0 else False


def _should_retry_attach(exc: Exception) -> bool:
    text = str(exc)
    retry_markers = (
        "connect_over_cdp",
        "browser attach failed",
        "ECONNREFUSED",
        "BrowserContext.new_page",
        "Target page, context or browser has been closed",
        "Target closed",
    )
    return any(marker in text for marker in retry_markers)


def _ensure_browser_desktop(
    source: str,
    *,
    cdp_url: str | None = None,
    profile: Path | None = None,
    force_recycle: bool = False,
) -> str:
    target = _CDP_TARGETS.get(source)
    if not target:
        return ""
    endpoint = cdp_url or os.getenv("JOB_INTEL_BROWSER_CDP_URL", "").strip() or str(
        target["cdp_url"]
    )
    if _cdp_ready(endpoint):
        cleanup = _close_foreign_pages(source, endpoint)
        if cleanup["remaining_foreign"] == 0 and not _endpoint_dirty(source, endpoint) and not _browser_process_stale(source, cdp_url=endpoint, profile=profile):
            return endpoint
        raise BrowserNativeUnavailable(
            f"browser CDP endpoint is dirty or stale; bootstrap must recycle it for {source}"
        )
    raise BrowserNativeUnavailable(
        f"browser CDP endpoint is unavailable; bootstrap must be active for {source}: {endpoint}"
    )


def _payload(
    *,
    ok: bool,
    vacancies: list[Vacancy] | None = None,
    session_health: dict[str, Any] | None = None,
    search_trace: dict[str, Any] | None = None,
    error: str | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "vacancies": [vacancy.model_dump(mode="json") for vacancy in (vacancies or [])],
        "session_health": session_health,
        "search_trace": search_trace,
        "error": error,
        "error_type": error_type,
        "market_query_dispatch_count": _DISPATCH_COUNTERS.market_query_dispatch_count,
        "sudo_dispatch_count": _DISPATCH_COUNTERS.sudo_dispatch_count,
    }


def _with_browser_source(source: str, fn):
    _prepare_browser_runtime_env()
    config = resolve_browser_config(source)
    _ensure_required_browser_profile(source, config)
    cdp_override = os.getenv("JOB_INTEL_BROWSER_CDP_URL", "").strip() or None
    last_exc: Exception | None = None
    browser_start_ms = 0
    for attempt in range(2):
        started = perf_counter()
        cdp_url = _ensure_browser_desktop(
            source,
            cdp_url=cdp_override,
            profile=config.user_data_dir,
            force_recycle=attempt > 0,
        )
        browser_start_ms += int(round((perf_counter() - started) * 1000))
        if cdp_url:
            os.environ["JOB_INTEL_BROWSER_CDP_URL"] = cdp_url
        try:
            with BrowserSourceClient(config) as client:
                vacancies, session_health = fn(client)
                search_trace = client.last_search_trace_snapshot()
                search_trace["browser_start_ms"] = int(search_trace.get("browser_start_ms") or 0) + browser_start_ms
                search_trace["browser_attach_retry_count"] = attempt
                return vacancies, session_health, search_trace
        except Exception as exc:
            last_exc = exc
            if source in _CDP_TARGETS and attempt == 0 and _should_retry_attach(exc):
                time.sleep(2.0)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise BrowserNativeUnavailable(f"browser worker failed for {source}")


def _run_linkedin(
    query: str,
    *,
    max_pages: int,
    location: str | None = None,
    geo_id: str | None = None,
    execution_plan: dict[str, Any] | None = None,
    run_id: str | None = None,
    query_id: str | None = None,
    cell_id: str | None = None,
    allow_unauthenticated: bool = False,
) -> tuple[list[Vacancy], dict[str, Any], dict[str, Any]]:
    _DISPATCH_COUNTERS.market_query_dispatch_count += 1

    def _run(client: BrowserSourceClient) -> tuple[list[Vacancy], dict[str, Any]]:
        vacancies = client.search_linkedin(
            query,
            max_pages=max_pages,
            geography_location=location,
            geography_geo_id=geo_id,
            execution_plan=execution_plan,
            run_id=run_id,
            query_id=query_id,
            cell_id=cell_id,
            allow_unauthenticated=allow_unauthenticated,
        )
        return vacancies, client.session_health_snapshot()
    return _with_browser_source("linkedin", _run)


def _probe(source: str) -> tuple[list[Vacancy], dict[str, Any], dict[str, Any]]:
    def _run(client: BrowserSourceClient) -> tuple[list[Vacancy], dict[str, Any]]:
        if os.getenv("JOB_INTEL_BROWSER_CAPTURE_EXISTING_PAGES", "").strip():
            client.capture_existing_pages(label=f"{source}-probe-state")
        return [], client.session_health_snapshot()
    return _with_browser_source(source, _run)


def _fetch_page(url: str, *, source: str) -> str:
    """Render one page and return its HTML for browser-backed sources."""
    _prepare_browser_runtime_env()
    config = resolve_browser_config(source)
    with BrowserSourceClient(config) as client:
        return client.fetch_html(url)


def main(argv: list[str] | None = None) -> int:
    _DISPATCH_COUNTERS.reset()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    linkedin = sub.add_parser("linkedin")
    linkedin.add_argument("query")
    linkedin.add_argument("max_pages", type=int)
    linkedin.add_argument("--location")
    linkedin.add_argument("--geo-id", dest="geo_id")
    linkedin.add_argument("--execution-plan-json")
    linkedin.add_argument("--run-id")
    linkedin.add_argument("--query-id")
    linkedin.add_argument("--cell-id")
    linkedin.add_argument("--allow-unauthenticated", action="store_true")

    probe = sub.add_parser("probe")
    probe.add_argument("source", choices=("linkedin",))

    fetch = sub.add_parser("fetch")
    fetch.add_argument("url")
    fetch.add_argument("--source", default="company_career")

    args = parser.parse_args(argv)
    if args.cmd == "fetch":
        try:
            html = _fetch_page(args.url, source=args.source)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc), "error_type": type(exc).__name__}))
            traceback.print_exc(file=sys.stderr)
            return 1
        print(json.dumps({"ok": True, "html": html, "html_len": len(html)}))
        return 0
    try:
        if args.cmd == "linkedin":
            execution_plan = (
                json.loads(args.execution_plan_json)
                if args.execution_plan_json
                else None
            )
            vacancies, session_health, search_trace = _run_linkedin(
                args.query,
                max_pages=args.max_pages,
                location=args.location,
                geo_id=args.geo_id,
                execution_plan=execution_plan,
                run_id=args.run_id,
                query_id=args.query_id,
                cell_id=args.cell_id,
                allow_unauthenticated=args.allow_unauthenticated,
            )
        else:
            vacancies, session_health, search_trace = _probe(args.source)
    except Exception as exc:
        print(json.dumps(_payload(ok=False, error=str(exc), error_type=type(exc).__name__, session_health=None, search_trace=None)))
        traceback.print_exc(file=sys.stderr)
        return 1

    print(json.dumps(_payload(ok=True, vacancies=vacancies, session_health=session_health, search_trace=search_trace)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
