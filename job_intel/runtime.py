from __future__ import annotations

import getpass
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

T = TypeVar("T")

DEFAULT_RUNTIME_BASE = Path("/var/lib/job-intel")
DEFAULT_STATE_DIR = DEFAULT_RUNTIME_BASE / "state"
DEFAULT_DB_PATH = DEFAULT_STATE_DIR / "job_intel.sqlite3"
DEFAULT_WORKDIR_CANDIDATES = (
    Path("/workspace/live-hermes"),
    Path("/home/hermes/.hermes/hermes-agent"),
    Path.cwd(),
)
REPO_SCRIPTS_CANDIDATE = Path(__file__).resolve().parents[1] / "scripts"
DEFAULT_SCRIPTS_CANDIDATES = (
    Path("/root/.hermes/scripts"),
    REPO_SCRIPTS_CANDIDATE,
)
BROWSER_PROFILE_BASE = Path("/var/lib/browser-desktop/profiles")
BROWSER_PROFILE_DEFAULTS = {
    "linkedin": BROWSER_PROFILE_BASE / "linkedin",
    "headhunter": BROWSER_PROFILE_BASE / "hh",
    "hh": BROWSER_PROFILE_BASE / "hh",
    "company_career": BROWSER_PROFILE_BASE / "company-career",
}


@dataclass(frozen=True)
class RuntimePaths:
    user: str
    home: Path
    workdir: Path
    db_path: Path
    environment: str
    scripts_dir: Path | None


def runtime_user() -> str:
    return os.getenv("JOB_INTEL_RUNTIME_USER") or getpass.getuser()


def runtime_home() -> Path:
    return Path.home()


def resolve_environment_name() -> str:
    return os.getenv("JOB_INTEL_ENVIRONMENT", "production").strip() or "production"


def resolve_db_path() -> Path:
    override = os.getenv("JOB_INTEL_DB_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_DB_PATH


def resolve_workdir() -> Path:
    override = os.getenv("JOB_INTEL_WORKDIR", "").strip()
    if override:
        return Path(override).expanduser()
    for candidate in DEFAULT_WORKDIR_CANDIDATES:
        try:
            if (candidate / "job_intel").is_dir():
                return candidate
        except OSError:
            continue
    return Path.cwd()


def _safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def resolve_scripts_dir() -> Path | None:
    override = os.getenv("JOB_INTEL_SCRIPTS_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
        return path if _safe_exists(path) else path
    for candidate in DEFAULT_SCRIPTS_CANDIDATES:
        if _safe_exists(candidate):
            return candidate
    return None


def resolve_state_dir() -> Path:
    override = os.getenv("JOB_INTEL_STATE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_STATE_DIR


def resolve_browser_profile_base() -> Path:
    override = os.getenv("JOB_INTEL_BROWSER_PROFILE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return BROWSER_PROFILE_BASE


def _module_origin(module_name: str) -> str | None:
    module = sys.modules.get(module_name)
    origin = getattr(module, "__file__", None)
    if origin:
        try:
            return str(Path(origin).resolve())
        except OSError:
            return str(origin)
    try:
        spec = importlib.util.find_spec(module_name)
    except ModuleNotFoundError:
        return None
    if spec and spec.origin:
        try:
            return str(Path(spec.origin).resolve())
        except OSError:
            return str(spec.origin)
    return None


def _redact_env_value(name: str, value: str) -> str:
    lowered = name.lower()
    if any(token in lowered for token in ("password", "passwd", "secret", "token", "credential", "auth", "cookie", "key")):
        return "[REDACTED]" if value else value
    return value


def _collect_env_overrides() -> dict[str, str]:
    captured: dict[str, str] = {}
    prefixes = ("JOB_INTEL_", "PYTHONPATH", "VIRTUAL_ENV", "PATH", "HOME", "PWD", "XDG_CACHE_HOME", "TERMINAL_CWD", "TZ")
    for key, value in os.environ.items():
        if key.startswith("JOB_INTEL_") or key in prefixes:
            captured[key] = _redact_env_value(key, value)
    return dict(sorted(captured.items()))


def _git_commit_hash(*candidates: Path) -> str | None:
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        try:
            if not candidate.exists():
                continue
        except OSError:
            continue
        try:
            completed = subprocess.run(
                ["git", "-C", str(candidate), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        commit = completed.stdout.strip()
        if commit:
            return commit
    return None


def _browser_profile_paths() -> dict[str, str]:
    base_dir = resolve_browser_profile_base()
    resolved: dict[str, str] = {}
    for source, default in BROWSER_PROFILE_DEFAULTS.items():
        env_name = f"JOB_INTEL_BROWSER_PROFILE_DIR_{source.upper()}"
        if source == "hh":
            env_name = "JOB_INTEL_BROWSER_PROFILE_DIR_HH"
        override = os.getenv(env_name, "").strip()
        if not override and source in {"headhunter", "hh"}:
            override = os.getenv("JOB_INTEL_BROWSER_PROFILE_DIR_HH", "").strip()
        if not override:
            override = os.getenv("JOB_INTEL_BROWSER_PROFILE_DIR", "").strip()
        if override:
            path = Path(override).expanduser()
        elif source == "company_career":
            path = base_dir / "company-career"
        else:
            path = default
        resolved[source] = str(path)
    return resolved


def _runtime_mirror_paths() -> dict[str, str | list[str]]:
    resolved_scripts = resolve_scripts_dir()
    return {
        "resolved_scripts_dir": str(resolved_scripts) if resolved_scripts else "",
        "repo_scripts_dir": str(REPO_SCRIPTS_CANDIDATE),
        "default_scripts_candidates": [str(candidate) for candidate in DEFAULT_SCRIPTS_CANDIDATES],
    }


def _path_within(path: Path, root: Path) -> bool:
    try:
        path_resolved = path.resolve()
        root_resolved = root.resolve()
    except OSError:
        return False
    try:
        return path_resolved == root_resolved or root_resolved in path_resolved.parents
    except Exception:
        return False


def build_runtime_contract() -> dict[str, Any]:
    workdir = resolve_workdir()
    state_dir = resolve_state_dir()
    db_path = resolve_db_path()
    browser_profile_dir = resolve_browser_profile_base()
    browser_profile_paths = _browser_profile_paths()
    required_browser_profile_names = ("linkedin", "headhunter", "hh")
    required_browser_profile_paths = {
        name: path_str
        for name, path_str in browser_profile_paths.items()
        if name in required_browser_profile_names
    }
    optional_browser_profile_paths = {
        name: path_str
        for name, path_str in browser_profile_paths.items()
        if name not in required_browser_profile_names
    }
    expected_git_commit = os.getenv("JOB_INTEL_EXPECTED_GIT_COMMIT", "").strip()
    module_origins = {
        module_name: origin
        for module_name in (
            "job_intel.runtime",
            "job_intel.store",
            "job_intel.browser_sourcing",
            "job_intel.cli",
        )
        if (origin := _module_origin(module_name))
    }
    contract: dict[str, Any] = {
        "workdir": str(workdir),
        "state_dir": str(state_dir),
        "db_path": str(db_path),
        "browser_profile_dir": str(browser_profile_dir),
        "browser_profile_paths": browser_profile_paths,
        "required_browser_profile_paths": required_browser_profile_paths,
        "optional_browser_profile_paths": optional_browser_profile_paths,
        "browser_python": os.getenv("JOB_INTEL_BROWSER_PYTHON", "").strip() or str(Path("/var/lib/browser-desktop/playwright-venv/bin/python")),
        "expected_git_commit": expected_git_commit,
        "actual_git_commit": _git_commit_hash(workdir),
        "module_origins": module_origins,
        "state_dir_flags": file_access_flags(state_dir),
        "state_dir_parent_flags": file_access_flags(state_dir.parent),
        "db_path_flags": file_access_flags(db_path),
        "db_parent_flags": file_access_flags(db_path.parent),
    }
    issues: list[str] = []
    if not _safe_exists(workdir):
        issues.append(f"workdir missing: {workdir}")
    if _safe_exists(workdir) and not workdir.is_dir():
        issues.append(f"workdir is not a directory: {workdir}")
    if not _safe_exists(workdir / "job_intel"):
        issues.append(f"job_intel package missing from workdir: {workdir / 'job_intel'}")
    for name, path_str in required_browser_profile_paths.items():
        path = Path(path_str)
        if not _safe_exists(path):
            issues.append(f"browser profile missing: {name}={path}")
    if not expected_git_commit:
        issues.append("JOB_INTEL_EXPECTED_GIT_COMMIT is not set")
    actual_commit = contract["actual_git_commit"]
    if expected_git_commit and actual_commit != expected_git_commit:
        issues.append(f"git commit mismatch: expected {expected_git_commit}, got {actual_commit or 'n/a'}")
    state_dir_exists = _safe_exists(state_dir)
    state_dir_writable = contract["state_dir_flags"]["writable"]
    state_dir_parent_writable = contract["state_dir_parent_flags"]["writable"]
    if not state_dir_exists and not state_dir_parent_writable:
        issues.append(f"state dir parent not writable: {state_dir.parent}")
    elif state_dir_exists and not state_dir_writable:
        issues.append(f"state dir not writable: {state_dir}")
    db_exists = db_path.exists()
    db_writable = contract["db_path_flags"]["writable"]
    db_parent_writable = contract["db_parent_flags"]["writable"]
    if not db_exists and not db_parent_writable:
        issues.append(f"DB path parent not writable: {db_path.parent}")
    elif db_exists and not db_writable:
        issues.append(f"DB file not writable: {db_path}")
    for module_name, origin in module_origins.items():
        if origin is None:
            issues.append(f"module origin missing: {module_name}")
            continue
        if not _path_within(Path(origin), workdir):
            issues.append(f"module outside workdir: {module_name} -> {origin}")
    contract["issues"] = issues
    return contract


def assert_runtime_contract() -> dict[str, Any]:
    contract = build_runtime_contract()
    issues = contract.get("issues") or []
    if issues:
        raise RuntimeError("Job-intel runtime contract violated: " + "; ".join(issues))
    return contract


def capture_runtime_provenance(
    *,
    db_path: Path | str | None = None,
    state_dir: Path | str | None = None,
    workdir: Path | str | None = None,
) -> dict[str, Any]:
    resolved_db_path = Path(db_path).expanduser() if db_path is not None else resolve_db_path()
    resolved_state_dir = Path(state_dir).expanduser() if state_dir is not None else resolve_state_dir()
    resolved_workdir = Path(workdir).expanduser() if workdir is not None else resolve_workdir()
    contract = build_runtime_contract()
    provenance = {
        "whoami": runtime_user(),
        "hostname": socket.gethostname(),
        "pwd": str(Path.cwd()),
        "effective_workdir": str(resolved_workdir),
        "git_commit_hash": _git_commit_hash(resolved_workdir, Path(__file__).resolve().parents[1], Path.cwd()),
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "sys_path": list(sys.path),
        "imported_module_locations": {
            module_name: origin
            for module_name in (
                "job_intel.runtime",
                "job_intel.store",
                "job_intel.browser_sourcing",
                "job_intel.cli",
                "requests",
                "yaml",
                "playwright.sync_api",
                "sqlite3",
            )
            if (origin := _module_origin(module_name))
        },
        "db_path": str(resolved_db_path),
        "state_dir": str(resolved_state_dir),
        "browser_profile_dir": str(resolve_browser_profile_base()),
        "browser_profile_paths": _browser_profile_paths(),
        "env_overrides": _collect_env_overrides(),
        "runtime_mirror_paths": _runtime_mirror_paths(),
        "runtime_home": str(runtime_home()),
        "runtime_user_env": os.getenv("JOB_INTEL_RUNTIME_USER", ""),
        "runtime_contract": contract,
    }
    return provenance

def runtime_paths() -> RuntimePaths:
    return RuntimePaths(
        user=runtime_user(),
        home=runtime_home(),
        workdir=resolve_workdir(),
        db_path=resolve_db_path(),
        environment=resolve_environment_name(),
        scripts_dir=resolve_scripts_dir(),
    )


def file_access_flags(path: Path) -> dict[str, bool]:
    return {
        "exists": path.exists(),
        "readable": os.access(path, os.R_OK),
        "writable": os.access(path, os.W_OK),
    }


def sha256_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def retry_with_backoff(
    operation: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except exceptions as exc:
            last_error = exc
            if attempt >= attempts:
                raise
            sleep(base_delay * (2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
