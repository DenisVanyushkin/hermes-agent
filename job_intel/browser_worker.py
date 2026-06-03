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
from urllib.parse import urlparse
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
        "allowed_prefixes": ("https://www.linkedin.com/", "chrome://newtab/", "about:blank"),
        "max_page_targets": 8,
    },
    "headhunter": {
        "profile": "hh",
        "start_url": "https://hh.ru/",
        "cdp_url": "http://127.0.0.1:9223",
        "allowed_prefixes": ("https://hh.ru/", "chrome://newtab/", "about:blank"),
        "max_page_targets": 3,
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
    if source == "headhunter" and url.startswith("https://hh.ru/search/vacancy"):
        return "hh-search"
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


def _recycle_browser_desktop(source: str) -> None:
    target = _CDP_TARGETS.get(source)
    if not target:
        return
    cdp_url = str(target["cdp_url"])
    port = urlparse(cdp_url).port or 0
    profile = str(target["profile"])
    kill_script = """
set -e
pattern="remote-debugging-port={port}.*profiles/{profile}"
# Find matching top-level chrome PIDs.
pids="$(pgrep -f "$pattern" || true)"
if [ -n "$pids" ]; then
  # First try graceful termination.
  for pid in $pids; do
    pkill -TERM -P "$pid" 2>/dev/null || true
    kill -TERM "$pid" 2>/dev/null || true
  done
  sleep 2
  # Then force kill if still present.
  for pid in $pids; do
    pkill -KILL -P "$pid" 2>/dev/null || true
    kill -KILL "$pid" 2>/dev/null || true
  done
fi
""".format(port=port, profile=profile)

    subprocess.run(["sudo", "-n", "bash", "-lc", kill_script], capture_output=True, text=True, timeout=30, check=False)
    for _ in range(20):
        if not _cdp_ready(cdp_url):
            break
        time.sleep(1.0)

    # If the CDP endpoint is still up, fall back to killing the listener by port.
    if _cdp_ready(cdp_url):
        fallback = """
set -e
port={port}
# Best-effort: kill whatever is listening on the CDP port.
ss -ltnp 2>/dev/null | awk -v p=":{port}" '$4 ~ p {{print $NF}}' | sed -E 's/.*pid=([0-9]+).*/\1/' | sort -u | while read -r pid; do
  [ -n \"$pid\" ] || continue
  pkill -TERM -P \"$pid\" 2>/dev/null || true
  kill -TERM \"$pid\" 2>/dev/null || true
done
sleep 2
ss -ltnp 2>/dev/null | awk -v p=":{port}" '$4 ~ p {{print $NF}}' | sed -E 's/.*pid=([0-9]+).*/\1/' | sort -u | while read -r pid; do
  [ -n \"$pid\" ] || continue
  pkill -KILL -P \"$pid\" 2>/dev/null || true
  kill -KILL \"$pid\" 2>/dev/null || true
done
""".format(port=port)
        subprocess.run(["sudo", "-n", "bash", "-lc", fallback], capture_output=True, text=True, timeout=30, check=False)
        for _ in range(10):
            if not _cdp_ready(cdp_url):
                break
            time.sleep(0.5)


def _browser_process_age_seconds(source: str) -> int:
    target = _CDP_TARGETS.get(source)
    if not target:
        return 0
    cdp_url = str(target["cdp_url"])
    port = urlparse(cdp_url).port or 0
    profile = str(target["profile"])
    cmd = f"ps -eo etimes=,args= | grep 'remote-debugging-port={port}' | grep 'profiles/{profile}' | grep -v grep | awk 'NR==1 {{print $1}}'"
    proc = subprocess.run(["sudo", "-n", "bash", "-lc", cmd], capture_output=True, text=True, timeout=15, check=False)
    out = (proc.stdout or "").strip()
    try:
        return int(out)
    except Exception:
        return 0


def _browser_process_stale(source: str) -> bool:
    max_age = int(os.getenv("JOB_INTEL_BROWSER_MAX_PROCESS_AGE_SECONDS", "14400"))
    age = _browser_process_age_seconds(source)
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


def _ensure_browser_desktop(source: str, *, force_recycle: bool = False) -> str:
    target = _CDP_TARGETS.get(source)
    if not target:
        return ""
    cdp_url = str(target["cdp_url"])
    if force_recycle:
        _recycle_browser_desktop(source)
    if _cdp_ready(cdp_url):
        cleanup = _close_foreign_pages(source, cdp_url)
        if cleanup["remaining_foreign"] == 0 and not _endpoint_dirty(source, cdp_url) and not _browser_process_stale(source):
            return cdp_url
        _recycle_browser_desktop(source)
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
    if not _cdp_ready(cdp_url, attempts=45, delay_seconds=1.0):
        raise BrowserNativeUnavailable(f"browser-desktop CDP endpoint did not become ready for {source}: {cdp_url}")
    _close_foreign_pages(source, cdp_url)
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
    config = resolve_browser_config(source)
    _ensure_required_browser_profile(source, config)
    last_exc: Exception | None = None
    for attempt in range(2):
        cdp_url = _ensure_browser_desktop(source, force_recycle=attempt > 0)
        if cdp_url:
            os.environ["JOB_INTEL_BROWSER_CDP_URL"] = cdp_url
        try:
            with BrowserSourceClient(config) as client:
                return fn(client)
        except Exception as exc:
            last_exc = exc
            if source in _CDP_TARGETS and attempt == 0 and _should_retry_attach(exc):
                _recycle_browser_desktop(source)
                time.sleep(2.0)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise BrowserNativeUnavailable(f"browser worker failed for {source}")


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
        if os.getenv("JOB_INTEL_BROWSER_CAPTURE_EXISTING_PAGES", "").strip():
            client.capture_existing_pages(label=f"{source}-probe-state")
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
