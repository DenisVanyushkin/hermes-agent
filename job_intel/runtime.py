from __future__ import annotations

import getpass
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")

DEFAULT_DB_RELATIVE = Path(".hermes") / "job_intel" / "job_intel.sqlite3"
DEFAULT_WORKDIR_CANDIDATES = (
    Path("/home/hermes/.hermes/hermes-agent"),
    Path("/workspace/live-hermes"),
    Path.cwd(),
)
REPO_SCRIPTS_CANDIDATE = Path(__file__).resolve().parents[1] / "scripts"
DEFAULT_SCRIPTS_CANDIDATES = (
    Path("/home/hermes/.hermes/scripts"),
    Path("/root/.hermes/scripts"),
    REPO_SCRIPTS_CANDIDATE,
)


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
    return runtime_home() / DEFAULT_DB_RELATIVE


def resolve_workdir() -> Path:
    override = os.getenv("JOB_INTEL_WORKDIR", "").strip()
    if override:
        return Path(override).expanduser()
    for candidate in DEFAULT_WORKDIR_CANDIDATES:
        if (candidate / "job_intel").is_dir():
            return candidate
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
