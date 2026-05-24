"""Gateway runtime-metadata footer.

Renders a compact footer showing runtime state (model, context %, cwd) and
appends it to the FINAL message of an agent turn when enabled. Off by default
to keep replies minimal.

Config (``~/.hermes/config.yaml``)::

    display:
      runtime_footer:
        enabled: true                        # off by default
        fields: [model, context_pct, cwd]    # order shown; drop any to hide
        account_usage: true                  # optional provider quota line

Per-platform overrides live under ``display.platforms.<platform>.runtime_footer``.
Users can toggle the global setting with ``/footer on|off`` from both the CLI
and any gateway platform.

The footer is appended to the final response text in ``gateway/run.py`` right
before returning the response to the adapter send path — so it only lands on
the final message a user sees, not on tool-progress updates or streaming
partials. When streaming is on and the final text has already been delivered
piecemeal, the footer is sent as a separate trailing message via
``send_trailing_footer()``.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from agent.account_usage import AccountUsageSnapshot, fetch_account_usage

_DEFAULT_FIELDS: tuple[str, ...] = ("model", "context_pct", "cwd")
_SEP = " · "
_USAGE_CACHE_TTL_SECONDS = 60.0
_USAGE_CACHE: dict[tuple[str, str, str], tuple[float, Any]] = {}


def _home_relative_cwd(cwd: str) -> str:
    """Return *cwd* with ``$HOME`` collapsed to ``~``. Empty string if unset."""
    if not cwd:
        return ""
    try:
        home = os.path.expanduser("~")
        p = os.path.abspath(cwd)
        if home and (p == home or p.startswith(home + os.sep)):
            return "~" + p[len(home):]
        return p
    except Exception:
        return cwd


def _model_short(model: Optional[str]) -> str:
    """Drop ``vendor/`` prefix for readability (``openai/gpt-5.4`` → ``gpt-5.4``)."""
    if not model:
        return ""
    return model.rsplit("/", 1)[-1]


def resolve_footer_config(
    user_config: dict[str, Any] | None,
    platform_key: str | None = None,
) -> dict[str, Any]:
    """Resolve effective runtime-footer config for *platform_key*.

    Merge order (later wins):
        1. Built-in defaults (enabled=False)
        2. ``display.runtime_footer``
        3. ``display.platforms.<platform_key>.runtime_footer``
    """
    resolved = {"enabled": False, "fields": list(_DEFAULT_FIELDS), "account_usage": False}
    cfg = (user_config or {}).get("display") or {}

    global_cfg = cfg.get("runtime_footer")
    if isinstance(global_cfg, dict):
        if "enabled" in global_cfg:
            resolved["enabled"] = bool(global_cfg.get("enabled"))
        if isinstance(global_cfg.get("fields"), list):
            resolved["fields"] = [str(f) for f in global_cfg["fields"]]
        if "account_usage" in global_cfg:
            resolved["account_usage"] = bool(global_cfg.get("account_usage"))

    if platform_key:
        platforms = cfg.get("platforms") or {}
        plat_cfg = platforms.get(platform_key)
        if isinstance(plat_cfg, dict):
            plat_footer = plat_cfg.get("runtime_footer")
            if isinstance(plat_footer, dict):
                if "enabled" in plat_footer:
                    resolved["enabled"] = bool(plat_footer.get("enabled"))
                if isinstance(plat_footer.get("fields"), list):
                    resolved["fields"] = [str(f) for f in plat_footer["fields"]]
                if "account_usage" in plat_footer:
                    resolved["account_usage"] = bool(plat_footer.get("account_usage"))

    return resolved


def _format_model_pair(requested_model: Optional[str], effective_model: Optional[str]) -> str:
    """Render requested/effective model names for the footer."""
    requested_raw = str(requested_model or "").strip()
    effective_raw = str(effective_model or "").strip()
    requested = _model_short(requested_raw)
    effective = _model_short(effective_raw)
    if requested_raw and effective_raw and requested_raw != effective_raw:
        return f"{requested or requested_raw} → {effective or effective_raw}"
    return effective or requested


def format_runtime_footer(
    *,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
    cwd: Optional[str] = None,
    fields: Iterable[str] = _DEFAULT_FIELDS,
    requested_model: Optional[str] = None,
) -> str:
    """Render the footer line, or return "" if no fields have data.

    Fields are skipped silently when their underlying data is missing — a
    partially-populated footer is better than a line with ``?%`` or empty slots.
    """
    parts: list[str] = []
    for field in fields:
        if field == "model":
            m = _format_model_pair(requested_model, model)
            if m:
                parts.append(m)
        elif field == "context_pct":
            if context_length and context_length > 0 and context_tokens >= 0:
                pct = max(0, min(100, round((context_tokens / context_length) * 100)))
                parts.append(f"ctx {pct}%")
        elif field == "cwd":
            rel = _home_relative_cwd(cwd or os.environ.get("TERMINAL_CWD", ""))
            if rel:
                parts.append(rel)
        # Unknown field names are silently ignored.

    if not parts:
        return ""
    return _SEP.join(parts)


def _format_reset_timestamp(dt: Optional[datetime]) -> str:
    if not dt:
        return "unknown"
    try:
        return dt.astimezone(ZoneInfo("Asia/Almaty")).strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return str(dt)


def _usage_cache_key(provider: Optional[str], base_url: Optional[str], api_key: Optional[str]) -> tuple[str, str, str]:
    return (
        str(provider or "").strip().lower(),
        str(base_url or "").strip(),
        "present" if str(api_key or "").strip() else "",
    )


def _get_account_usage_snapshot(
    *,
    provider: Optional[str],
    base_url: Optional[str],
    api_key: Optional[str],
) -> Optional[AccountUsageSnapshot]:
    key = _usage_cache_key(provider, base_url, api_key)
    now = time.time()
    cached = _USAGE_CACHE.get(key)
    if cached and (now - cached[0]) < _USAGE_CACHE_TTL_SECONDS:
        return cached[1]
    snapshot = fetch_account_usage(provider, base_url=base_url, api_key=api_key)
    _USAGE_CACHE[key] = (now, snapshot)
    return snapshot


def format_account_usage_footer(snapshot: Optional[AccountUsageSnapshot]) -> str:
    if not snapshot or not snapshot.available or not snapshot.windows:
        return ""
    parts: list[str] = []
    for window in snapshot.windows:
        if window.used_percent is None:
            continue
        remaining = max(0, round(100 - float(window.used_percent)))
        piece = f"{window.label}: {remaining}% left"
        if window.reset_at:
            piece += f" until {_format_reset_timestamp(window.reset_at)}"
        elif window.detail:
            piece += f" ({window.detail})"
        parts.append(piece)
    if not parts:
        return ""
    return "Quota: " + _SEP.join(parts)


def build_footer_line(
    *,
    user_config: dict[str, Any] | None,
    platform_key: str | None,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
    cwd: Optional[str] = None,
    requested_model: Optional[str] = None,
) -> str:
    """Top-level entry point used by gateway/run.py.

    Returns the footer text (empty string when disabled or no data). Callers
    append this to the final response themselves, preserving a single blank
    line of separation.
    """
    cfg = resolve_footer_config(user_config, platform_key)
    if not cfg.get("enabled"):
        return ""

    runtime_footer = format_runtime_footer(
        model=model,
        requested_model=requested_model,
        context_tokens=context_tokens,
        context_length=context_length,
        cwd=cwd,
        fields=cfg.get("fields", _DEFAULT_FIELDS),
    )

    quota_footer = ""
    if cfg.get("account_usage"):
        model_cfg = user_config or {}
        provider = (model_cfg.get("model") or {}).get("provider")
        base_url = (model_cfg.get("model") or {}).get("base_url")
        api_key = (model_cfg.get("model") or {}).get("api_key")
        snapshot = _get_account_usage_snapshot(provider=provider, base_url=base_url, api_key=api_key)
        quota_footer = format_account_usage_footer(snapshot)

    if runtime_footer and quota_footer:
        return f"{runtime_footer}\n{quota_footer}"
    if quota_footer:
        return quota_footer
    return runtime_footer
