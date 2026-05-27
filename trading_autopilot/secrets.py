"""Secrets management for trading-autopilot.

Runtime-only credential loading with redaction guard.
Credentials are NEVER printed, logged, journaled, or returned in outputs.
If credentials accidentally leak into any output, the run is failed as a security incident.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Final

TRADING_AUTOPILOT_SECRET_FILE_ENV: Final[str] = "TRADING_AUTOPILOT_SECRET_FILE"
DEFAULT_SECRET_PATHS: Final[tuple[Path, ...]] = (
    Path("/run/secrets/binance.env"),
    Path("/home/hermes/.config/trading-autopilot/binance.env"),
    Path("/root/.config/trading-autopilot/binance.env"),
)

_SECRETS_CACHE: dict[str, str] | None = None

_SECRET_KEY_SUFFIXES: tuple[str, ...] = (
    "API_KEY",
    "API_SECRET",
    "SECRET",
    "SECRET_KEY",
    "PRIVATE_KEY",
    "PASSWORD",
    "PASSPHRASE",
    "ACCESS_KEY",
    "ACCESS_SECRET",
    "SIGNING_KEY",
)


class CredentialError(RuntimeError):
    """Raised when credential loading or validation fails."""


class CredentialLeakError(RuntimeError):
    """Raised when credentials are detected in an output that must not contain them."""


def _is_env_var_secret(key: str) -> bool:
    upper = key.upper()
    return any(upper.endswith(suffix) for suffix in _SECRET_KEY_SUFFIXES)


def load_credentials(
    env_path: str | None = None,
    *,
    use_cache: bool = False,
) -> dict[str, str]:
    """Load credentials from a .env file.

    Resolution order:
    1. Explicit env_path argument (programmatic override)
    2. TRADING_AUTOPILOT_SECRET_FILE environment variable
    3. Container/dev fallback paths

    Secret values are:
    - stripped of surrounding quotes
    - NOT logged, NOT journaled, NOT printed
    - only returned for programmatic use at runtime
    """
    global _SECRETS_CACHE

    if use_cache and _SECRETS_CACHE is not None:
        return _SECRETS_CACHE

    path = _resolve_secret_path(env_path)
    _validate_secret_path(path)

    credentials: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if not key:
                continue
            credentials[key] = value

    for req_key in ("BINANCE_API_KEY", "BINANCE_API_SECRET"):
        if req_key not in credentials or not credentials[req_key]:
            raise CredentialError(f"Missing or empty required credential: {req_key}")

    if use_cache:
        _SECRETS_CACHE = dict(credentials)

    return credentials


def get_api_credentials(env_path: str | None = None) -> tuple[str, str]:
    """Return (api_key, api_secret) from the env file.

    Shortcut for the Binance client. Validates and returns the pair.
    """
    creds = load_credentials(env_path, use_cache=True)
    return creds["BINANCE_API_KEY"], creds["BINANCE_API_SECRET"]


def clear_cache() -> None:
    """Clear the in-memory credential cache."""
    global _SECRETS_CACHE
    _SECRETS_CACHE = None


def _resolve_secret_path(env_path: str | None = None) -> Path:
    if env_path:
        return Path(env_path)

    explicit_env = os.environ.get(TRADING_AUTOPILOT_SECRET_FILE_ENV, "").strip()
    if explicit_env:
        return Path(explicit_env)

    for candidate in DEFAULT_SECRET_PATHS:
        if candidate.exists():
            return candidate

    candidates = ", ".join(str(path) for path in DEFAULT_SECRET_PATHS)
    raise CredentialError(
        f"Credential file not found. Set {TRADING_AUTOPILOT_SECRET_FILE_ENV} or create one of: {candidates}"
    )


def _validate_secret_path(path: Path) -> None:
    if not path.exists():
        raise CredentialError(f"Credential file not found: {path}")
    if not path.is_file():
        raise CredentialError(f"Credential path is not a regular file: {path}")
    if path.is_symlink():
        raise CredentialError(f"Credential file must not be a symlink: {path}")

    file_stat = path.stat()
    dir_stat = path.parent.stat()

    file_mode = stat.S_IMODE(file_stat.st_mode)
    dir_mode = stat.S_IMODE(dir_stat.st_mode)

    if file_mode & 0o022:
        raise CredentialError(
            f"Credential file {path} is group/other-writable (mode {oct(file_mode)}). "
            f"Run: chmod go-w {path}"
        )
    if dir_mode & 0o022:
        raise CredentialError(
            f"Credential directory {path.parent} is group/other-writable (mode {oct(dir_mode)}). "
            f"Run: chmod go-w {path.parent}"
        )
    if not os.access(path, os.R_OK):
        raise CredentialError(f"Credential file is not readable by the current runtime user: {path}")


def redact_secrets(text: str, *, replacement: str = "<redacted>") -> str:
    """Replace credential values in text with a redaction placeholder.

    After replacement, validates that no secret patterns remain.
    """
    if not text or not _SECRETS_CACHE:
        return text

    redacted = text
    # Sort by length descending so longer values are replaced first
    secret_keys = sorted(
        (k for k in _SECRETS_CACHE or {} if _is_env_var_secret(k)),
        key=len,
        reverse=True,
    )
    for key in secret_keys:
        value = _SECRETS_CACHE.get(key, "")
        if value and len(value) > 4:
            redacted = redacted.replace(value, replacement)

    return redacted


def assert_no_credential_leak(text: str, context: str = "output") -> str:
    """Scan text for credential values. If found, raise CredentialLeakError.

    This is the security incident guard. Call before:
    - writing to journal
    - formatting reports
    - sending messages
    - logging

    Returns the text unchanged if clean.
    """
    if not text or not _SECRETS_CACHE:
        return text

    secret_keys = [k for k in _SECRETS_CACHE if _is_env_var_secret(k)]
    for key in secret_keys:
        value = _SECRETS_CACHE.get(key, "")
        if value and len(value) > 4 and value in text:
            # Security incident: credential leaked into output
            raise CredentialLeakError(
                f"SECURITY INCIDENT: {key} value detected in {context}. "
                f"Run aborted. Credential must not appear in {context}."
            )

    return text
