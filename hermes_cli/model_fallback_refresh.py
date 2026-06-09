from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from agent.credential_pool import load_pool
from hermes_cli.config import get_hermes_home, load_config
from hermes_cli.fallback_config import get_fallback_chain
from utils import atomic_replace

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_STATE_PATH = get_hermes_home() / "state" / "model_fallbacks.json"
_SECRET_QUERY_KEYS = {
    "access_key",
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "cookie",
    "key",
    "refresh_token",
    "secret",
    "session",
    "sessionid",
    "sig",
    "signature",
    "token",
}
_REDACTION = "[REDACTED]"

ProbeBackend = Callable[[dict[str, Any], float], Mapping[str, Any]]


def _utcnow_iso(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _normalized_base_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().rstrip("/")


def _candidate_identity(entry: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(entry.get("provider") or "").strip().lower(),
        str(entry.get("model") or "").strip().lower(),
        _normalized_base_url(entry.get("base_url")).lower(),
    )


def load_model_candidates(config: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    cfg = dict(config) if config is not None else load_config()
    candidates: list[dict[str, Any]] = []
    for index, entry in enumerate(get_fallback_chain(cfg), start=1):
        candidate = {
            "position": index,
            "provider": str(entry.get("provider") or "").strip(),
            "model": str(entry.get("model") or "").strip(),
            "base_url": _normalized_base_url(entry.get("base_url")),
        }
        api_mode = str(entry.get("api_mode") or "").strip()
        if api_mode:
            candidate["api_mode"] = api_mode
        if str(entry.get("api_key") or "").strip():
            candidate["explicit_api_key"] = True
        candidates.append(candidate)
    return candidates


def _config_hash(candidates: list[dict[str, Any]]) -> str:
    payload = json.dumps(candidates, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _snake_case(name: str) -> str:
    normalized = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized)
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", normalized)
    return normalized.strip("_").lower() or "provider_error"


def _sanitize_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except Exception:
        return url
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _SECRET_QUERY_KEYS:
            query.append((key, _REDACTION))
        else:
            query.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment))


def sanitize_provider_error(error: Any) -> tuple[str, str]:
    if isinstance(error, BaseException):
        error_type = _snake_case(error.__class__.__name__)
        raw = str(error)
    else:
        error_type = "provider_error"
        raw = str(error)

    message = raw or error_type
    message = re.sub(r"https?://[^\s'\"<>]+", lambda m: _sanitize_url(m.group(0)), message)
    patterns = [
        (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s,;]+)"), r"\1" + _REDACTION),
        (re.compile(r"(?i)(authorization\s*[:=]\s*)(?!bearer\s)([^\s,;]+)"), r"\1" + _REDACTION),
        (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer " + _REDACTION),
        (re.compile(r"(?i)(cookie\s*[:=]\s*)([^\s;]+(?:;[^\s;]+)*)"), r"\1" + _REDACTION),
        (re.compile(r"(?i)(set-cookie\s*[:=]\s*)([^\s;]+(?:;[^\s;]+)*)"), r"\1" + _REDACTION),
        (re.compile(r"(?i)\b(sk|rk)-[A-Za-z0-9_-]+\b"), _REDACTION),
        (re.compile(r"(?i)\b(ghp|gho|ghu|github_pat)_[A-Za-z0-9_]+\b"), _REDACTION),
        (re.compile(r"(?i)\b(access_token|refresh_token|id_token|api_key|apikey|secret|signature|sig|sessionid|session|token)\b\s*[:=]\s*([^\s,&;]+)"), r"\1=" + _REDACTION),
        (re.compile(r"(?i)\b(x-api-key|api-key)\b\s*[:=]\s*([^\s,&;]+)"), r"\1=" + _REDACTION),
    ]
    for pattern, repl in patterns:
        message = pattern.sub(repl, message)
    message = re.sub(r"\s+", " ", message).strip()
    if len(message) > 240:
        message = message[:237].rstrip() + "..."
    return error_type, message or error_type


def _pool_has_credentials(provider: str) -> tuple[bool, Exception | None]:
    try:
        pool = load_pool(provider)
    except Exception as exc:
        return False, exc
    has_credentials = getattr(pool, "has_credentials", None)
    if callable(has_credentials):
        try:
            return bool(has_credentials()), None
        except Exception as exc:
            return False, exc
    return False, None


def _default_probe_model_candidate(candidate: dict[str, Any], timeout_s: float) -> Mapping[str, Any]:
    del timeout_s
    provider = str(candidate.get("provider") or "").strip()
    model = str(candidate.get("model") or "").strip()
    base_url = _normalized_base_url(candidate.get("base_url"))

    if not provider or not model:
        return {
            "status": "unavailable",
            "error_type": "invalid_candidate",
            "error_summary": "Fallback entry is missing provider or model.",
        }

    if base_url and not base_url.startswith(("http://", "https://")):
        return {
            "status": "degraded",
            "error_type": "invalid_base_url",
            "error_summary": "Base URL is not an HTTP(S) endpoint.",
        }

    if candidate.get("explicit_api_key"):
        return {
            "status": "skipped",
            "error_type": "network_probe_disabled",
            "error_summary": "Explicit credentials detected; network probe disabled in safe mode.",
        }

    has_credentials, pool_error = _pool_has_credentials(provider)
    if has_credentials:
        return {
            "status": "skipped",
            "error_type": "network_probe_disabled",
            "error_summary": "Pooled credentials detected; network probe disabled in safe mode.",
        }
    if pool_error is not None:
        err_type, err_summary = sanitize_provider_error(pool_error)
        return {
            "status": "degraded",
            "error_type": err_type,
            "error_summary": err_summary,
        }
    return {
        "status": "skipped",
        "error_type": "missing_credentials",
        "error_summary": "No explicit credentials or pooled credentials found; safe probe skipped.",
    }


def probe_model_candidate(
    candidate: dict[str, Any],
    probe_backend: ProbeBackend | None = None,
    *,
    timeout_s: float = 1.5,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        raw = dict((probe_backend or _default_probe_model_candidate)(dict(candidate), timeout_s) or {})
    except Exception as exc:
        err_type, err_summary = sanitize_provider_error(exc)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "provider": str(candidate.get("provider") or "").strip(),
            "model": str(candidate.get("model") or "").strip(),
            "base_url": _normalized_base_url(candidate.get("base_url")),
            "status": "unavailable",
            "latency_ms": elapsed_ms,
            "sanitized_error_type": err_type,
            "sanitized_error_summary": err_summary,
        }

    status = str(raw.get("status") or "degraded").strip().lower()
    if status not in {"ok", "degraded", "unavailable", "skipped"}:
        status = "degraded"
    latency_ms = raw.get("latency_ms")
    if not isinstance(latency_ms, int) or latency_ms < 0:
        latency_ms = int((time.perf_counter() - started) * 1000)

    err_type = raw.get("sanitized_error_type") or raw.get("error_type") or ""
    err_summary = raw.get("sanitized_error_summary") or raw.get("error_summary") or ""
    if err_type or err_summary:
        if err_type:
            err_type = _snake_case(str(err_type))
        _, err_summary = sanitize_provider_error(err_summary)
    else:
        err_type = ""
        err_summary = ""

    result = {
        "provider": str(candidate.get("provider") or "").strip(),
        "model": str(candidate.get("model") or "").strip(),
        "base_url": _normalized_base_url(candidate.get("base_url")),
        "status": status,
        "latency_ms": latency_ms,
        "sanitized_error_type": err_type,
        "sanitized_error_summary": err_summary,
    }
    if candidate.get("api_mode"):
        result["api_mode"] = str(candidate["api_mode"])
    return result


def _load_previous_state(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def _recommended_fallback_chain(checked_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    for entry in checked_candidates:
        if entry.get("status") == "unavailable":
            continue
        if entry.get("sanitized_error_type") == "missing_credentials":
            continue
        normalized = {
            "provider": str(entry.get("provider") or "").strip(),
            "model": str(entry.get("model") or "").strip(),
        }
        base_url = _normalized_base_url(entry.get("base_url"))
        if base_url:
            normalized["base_url"] = base_url
        chain.append(normalized)
    return chain


def refresh_model_fallbacks(
    *,
    config: Mapping[str, Any] | None = None,
    probe_backend: ProbeBackend | None = None,
    output_path: str | os.PathLike[str] | None = None,
    now: datetime | None = None,
    timeout_s: float = 1.5,
) -> dict[str, Any]:
    candidates = load_model_candidates(config)
    generated_at = _utcnow_iso(now)
    path = Path(output_path) if output_path is not None else DEFAULT_STATE_PATH
    previous_state = _load_previous_state(path)
    previous_success: dict[tuple[str, str, str], str] = {}
    if isinstance(previous_state, Mapping):
        for entry in previous_state.get("checked_candidates") or []:
            if not isinstance(entry, Mapping):
                continue
            last_success_at = str(entry.get("last_success_at") or "").strip()
            if last_success_at:
                previous_success[_candidate_identity(entry)] = last_success_at

    checked_candidates: list[dict[str, Any]] = []
    counts = {"ok": 0, "degraded": 0, "unavailable": 0, "skipped": 0}
    for candidate in candidates:
        checked = probe_model_candidate(candidate, probe_backend, timeout_s=timeout_s)
        identity = _candidate_identity(checked)
        if checked["status"] == "ok":
            checked["last_success_at"] = generated_at
        else:
            checked["last_success_at"] = previous_success.get(identity) or None
        checked_candidates.append(checked)
        counts[checked["status"]] += 1

    state = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "config_hash": _config_hash(candidates),
        "checked_candidates": checked_candidates,
        "recommended_fallback_chain": _recommended_fallback_chain(checked_candidates),
        "summary": {
            "total_candidates": len(checked_candidates),
            "status_counts": counts,
        },
    }
    return state


def write_fallback_state(
    state: Mapping[str, Any],
    output_path: str | os.PathLike[str] | None = None,
) -> Path:
    path = Path(output_path) if output_path is not None else DEFAULT_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        atomic_replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return path


def format_refresh_report(state: Mapping[str, Any]) -> str:
    summary = state.get("summary") or {}
    counts = summary.get("status_counts") or {}
    lines = [
        f"Fallback refresh generated_at={state.get('generated_at', '')}",
        (
            "Status counts: "
            f"ok={counts.get('ok', 0)} "
            f"degraded={counts.get('degraded', 0)} "
            f"unavailable={counts.get('unavailable', 0)} "
            f"skipped={counts.get('skipped', 0)}"
        ),
    ]
    for entry in state.get("checked_candidates") or []:
        if not isinstance(entry, Mapping):
            continue
        status = entry.get("status") or "unknown"
        provider = entry.get("provider") or "?"
        model = entry.get("model") or "?"
        detail = entry.get("sanitized_error_summary") or "ready for future selection"
        lines.append(f"- {provider}/{model}: {status} ({detail})")
    return "\n".join(lines)
