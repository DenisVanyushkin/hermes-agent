from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.error import URLError
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
        "cdp_url": "http://127.0.0.1:9222",
    },
    "headhunter": {
        "profile": "hh",
        "start_url": "https://hh.ru/",
        "cdp_url": "http://127.0.0.1:9223",
    },
}


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
    os.environ.pop("JOB_INTEL_BROWSER_CDP_URL", None)
    os.environ["JOB_INTEL_BROWSER_HEADLESS"] = "0"


def _bootstrap_script() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "browser-desktop-bootstrap.sh"


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


def _ensure_browser_desktop(source: str) -> str:
    target = _CDP_TARGETS.get(source)
    if not target:
        return ""
    cdp_url = str(target["cdp_url"])
    if _cdp_ready(cdp_url):
        return cdp_url
    script = _bootstrap_script()
    if not script.exists():
        raise BrowserNativeUnavailable(f"browser-desktop bootstrap script is missing: {script}")
    proc = subprocess.run(
        ["sudo", "-n", "bash", str(script), "--profile", str(target["profile"]), "--url", str(target["start_url"])],
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    if proc.returncode != 0 and not _cdp_ready(cdp_url, attempts=5, delay_seconds=1.0):
        detail = (proc.stderr or proc.stdout or f"browser-desktop bootstrap failed for {source}").strip()
        raise BrowserNativeUnavailable(detail)
    if not _cdp_ready(cdp_url, attempts=30, delay_seconds=1.0):
        raise BrowserNativeUnavailable(f"browser-desktop CDP endpoint did not become ready for {source}: {cdp_url}")
    return cdp_url


def _payload(*, ok: bool, vacancies: list[Vacancy] | None = None, session_health: dict[str, Any] | None = None, error: str | None = None, error_type: str | None = None) -> dict[str, Any]:
    return {
        "ok": ok,
        "vacancies": [vacancy.model_dump(mode="json") for vacancy in (vacancies or [])],
        "session_health": session_health,
        "error": error,
        "error_type": error_type,
    }


def _with_browser_source(source: str, fn):
    _prepare_browser_runtime_env()
    cdp_url = _ensure_browser_desktop(source)
    if cdp_url:
        os.environ["JOB_INTEL_BROWSER_CDP_URL"] = cdp_url
    config = resolve_browser_config(source)
    _ensure_required_browser_profile(source, config)
    with BrowserSourceClient(config) as client:
        return fn(client)


def _run_linkedin(query: str, *, max_pages: int) -> tuple[list[Vacancy], dict[str, Any]]:
    def _run(client: BrowserSourceClient) -> tuple[list[Vacancy], dict[str, Any]]:
        vacancies = client.search_linkedin(query, max_pages=max_pages)
        return vacancies, client.session_health_snapshot()
    return _with_browser_source("linkedin", _run)


def _run_headhunter(query: str, *, per_page: int) -> tuple[list[Vacancy], dict[str, Any]]:
    max_pages = max(1, (per_page + 24) // 25)
    def _run(client: BrowserSourceClient) -> tuple[list[Vacancy], dict[str, Any]]:
        vacancies = client.search_headhunter(query, max_pages=max_pages)[:per_page]
        return vacancies, client.session_health_snapshot()
    return _with_browser_source("headhunter", _run)


def _probe(source: str) -> tuple[list[Vacancy], dict[str, Any]]:
    def _run(client: BrowserSourceClient) -> tuple[list[Vacancy], dict[str, Any]]:
        return [], client.session_health_snapshot()
    return _with_browser_source(source, _run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    linkedin = sub.add_parser("linkedin")
    linkedin.add_argument("query")
    linkedin.add_argument("max_pages", type=int)

    hh = sub.add_parser("headhunter")
    hh.add_argument("query")
    hh.add_argument("per_page", type=int)

    probe = sub.add_parser("probe")
    probe.add_argument("source", choices=("linkedin", "headhunter"))

    args = parser.parse_args(argv)
    try:
        if args.cmd == "linkedin":
            vacancies, session_health = _run_linkedin(args.query, max_pages=args.max_pages)
        elif args.cmd == "headhunter":
            vacancies, session_health = _run_headhunter(args.query, per_page=args.per_page)
        else:
            vacancies, session_health = _probe(args.source)
    except Exception as exc:
        print(json.dumps(_payload(ok=False, error=str(exc), error_type=type(exc).__name__, session_health=None)))
        traceback.print_exc(file=sys.stderr)
        return 1

    print(json.dumps(_payload(ok=True, vacancies=vacancies, session_health=session_health)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
