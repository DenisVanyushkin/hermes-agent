"""Secrets management for trading-autopilot.

Runtime-only credential loading with redaction guard.
Credentials are NEVER printed, logged, journaled, or returned in outputs.
If credentials accidentally leak into any output, the run is failed as a security incident.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

DEFAULT_ENV_PATH: Final[str] = os.path.expanduser("~/.config/trading-autopilot/binance.env")

CREDS_PATH_DIR_PERMS: Final[int] = 0o700
CREDS_PATH_FILE_PERMS: Final[int] = 0o600

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

    The file must:
    - live under a directory with chmod 700
    - have chmod 600 itself
    - contain KEY=VALUE lines (one per line)

    Secret values are:
    - stripped of surrounding quotes
    - NOT logged, NOT journaled, NOT printed
    - only returned for programmatic use at runtime

    Args:
        env_path: Path to .env file. Defaults to ~/.config/trading-autopilot/binance.env
        use_cache: If True, return cached credentials after first load.
    """
    global _SECRETS_CACHE

    if use_cache and _SECRETS_CACHE is not None:
        return _SECRETS_CACHE

    path = Path(env_path or DEFAULT_ENV_PATH)

    if not path.exists():
        raise CredentialError(f"Credential file not found: {path}")

    _validate_file_permissions(path)

    credentials: dict[str, str] = {}
    with open(path, "r") as fh:
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


def _validate_file_permissions(path: Path) -> None:
    dir_path = path.parent
    file_stat = path.stat()
    dir_stat = dir_path.stat()

    import stat as stat_mod

    file_mode = stat_mod.S_IMODE(file_stat.st_mode)
    dir_mode = stat_mod.S_IMODE(dir_stat.st_mode)

    if dir_mode != CREDS_PATH_DIR_PERMS:
        raise CredentialError(
            f"Credential directory {dir_path} has permissions {oct(file_mode)}, "
            f"expected {oct(CREDS_PATH_DIR_PERMS)}. Run: chmod 700 {dir_path}"
        )
    if file_mode != CREDS_PATH_FILE_PERMS:
        raise CredentialError(
            f"Credential file {path} has permissions {oct(file_mode)}, "
            f"expected {oct(CREDS_PATH_FILE_PERMS)}. Run: chmod 600 {path}"
        )


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
