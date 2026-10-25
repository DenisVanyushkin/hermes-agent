"""Fork: what the runtime footer says on this host.

* ``model`` shows ``requested → effective`` whenever the turn ran on a
  different model than was asked for (a fallback), even when the short names
  match -- the provider prefix is the difference.
* ``context_pct`` reads ``ctx 42%``, not a bare percentage.
* A footer that would be only the cwd (model unknown) is dropped: a lone
  ``~`` under a reply is noise.
* ``display.runtime_footer.account_usage: true`` adds a second line with the
  provider's quota windows (``Quota: 5h: 62% left until ...``), cached for 60 s
  so a busy chat does not hammer the usage endpoint.

Moved from the pre-v0.22.5 gateway/runtime_footer.py; that file calls in at
marked sites.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

_SEP = " · "
_USAGE_CACHE_TTL_SECONDS = 60.0
_USAGE_CACHE: dict[tuple[str, str, str], tuple[float, Any]] = {}
_RESET_TZ = "Asia/Almaty"


def _short(model: str) -> str:
    return model.rsplit("/", 1)[-1] if model else ""


def format_model_pair(requested_model: Optional[str], effective_model: Optional[str]) -> str:
    requested_raw = str(requested_model or "").strip()
    effective_raw = str(effective_model or "").strip()
    requested, effective = _short(requested_raw), _short(effective_raw)
    if requested_raw and effective_raw and requested_raw != effective_raw:
        return f"{requested or requested_raw} → {effective or effective_raw}"
    return effective or requested


def format_context_pct(pct: int) -> str:
    return f"ctx {pct}%"


def is_cwd_only(emitted_fields: list[str], requested_fields: list[str]) -> bool:
    """True when only the cwd rendered although the model was asked for too."""
    return emitted_fields == ["cwd"] and "model" in requested_fields and len(requested_fields) > 1


def _format_reset_timestamp(dt: Optional[datetime]) -> str:
    if not dt:
        return "unknown"
    try:
        return dt.astimezone(ZoneInfo(_RESET_TZ)).strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return str(dt)


def _account_usage_snapshot(*, provider: Optional[str], base_url: Optional[str], api_key: Optional[str]):
    from agent.account_usage import fetch_account_usage

    key = (str(provider or "").strip().lower(), str(base_url or "").strip(),
           "present" if str(api_key or "").strip() else "")
    now = time.time()
    cached = _USAGE_CACHE.get(key)
    if cached and (now - cached[0]) < _USAGE_CACHE_TTL_SECONDS:
        return cached[1]
    snapshot = fetch_account_usage(provider, base_url=base_url, api_key=api_key)
    _USAGE_CACHE[key] = (now, snapshot)
    return snapshot


def format_account_usage_footer(snapshot: Any) -> str:
    if not snapshot or not getattr(snapshot, "available", False) or not getattr(snapshot, "windows", None):
        return ""
    parts: list[str] = []
    for window in snapshot.windows:
        if window.used_percent is None:
            continue
        piece = f"{window.label}: {max(0, round(100 - float(window.used_percent)))}% left"
        if window.reset_at:
            piece += f" until {_format_reset_timestamp(window.reset_at)}"
        elif window.detail:
            piece += f" ({window.detail})"
        parts.append(piece)
    return ("Quota: " + _SEP.join(parts)) if parts else ""


def quota_footer_line(user_config: dict[str, Any] | None) -> str:
    """The quota line for the configured primary provider; "" on any failure."""
    try:
        model_cfg = (user_config or {}).get("model") or {}
        if not isinstance(model_cfg, dict):
            return ""
        return format_account_usage_footer(_account_usage_snapshot(
            provider=model_cfg.get("provider"), base_url=model_cfg.get("base_url"),
            api_key=model_cfg.get("api_key")))
    except Exception:
        return ""
