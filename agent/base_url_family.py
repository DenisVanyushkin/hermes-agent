"""Fork: canonical transport family of a model endpoint (codex, openrouter, ...).

Used by the engineering pipeline's executor to tag runtime requests. Moved out
of agent/chat_completion_helpers.py when the fork was merged onto upstream
v0.22.5, unchanged.
"""

from __future__ import annotations

from urllib.parse import urlparse as _urlparse

_PROVIDER_URL_FAMILIES = {
    "openai-codex": "codex",
    "openai": "openai",
    "xai-oauth": "codex",
    "openrouter": "openrouter",
    "nous": "openrouter",
    "anthropic": "anthropic",
    "bedrock": "bedrock",
}


# Each entry: (exact_hostname, required_path_prefix) -> family.
# Path must equal the prefix exactly or start with prefix + '/'.
_URL_FAMILY_RULES: tuple[tuple[str, str, str], ...] = (
    ("chatgpt.com", "/backend-api/codex", "codex"),
    ("openrouter.ai", "/api/v1", "openrouter"),
)


def _normalize_base_url_family(
    provider: str | None,
    api_mode: str | None,
    base_url: str | None,
) -> str:
    """Derive a canonical transport family from base_url, falling back to provider/api_mode.

    Returns one of: 'codex', 'openrouter', 'openai', 'anthropic', 'bedrock', 'unknown'.

    Rules:
    - If base_url is present: parse with urlparse; match by exact hostname and
      path prefix only. Any mismatch or unrecognized URL returns 'unknown' — no
      fallback to provider/api_mode for a present URL. This prevents a bad URL
      from silently inheriting a known family.
    - If base_url is absent/empty: derive conservatively from provider, then api_mode.
    """
    raw = (base_url or "").strip()
    if raw:
        try:
            parsed = _urlparse(raw)
            hostname = (parsed.hostname or "").lower()
            path = parsed.path or ""
        except Exception:
            return "unknown"
        for exact_host, path_prefix, family in _URL_FAMILY_RULES:
            if hostname == exact_host:
                # Path must equal prefix exactly or start with prefix + '/'
                norm_path = path.rstrip("/") or "/"
                norm_prefix = path_prefix.rstrip("/")
                if norm_path == norm_prefix or norm_path.startswith(norm_prefix + "/"):
                    return family
                return "unknown"  # right host, wrong path
        return "unknown"
    # No base_url: derive from provider then api_mode
    p = (provider or "").strip().lower()
    if p in _PROVIDER_URL_FAMILIES:
        return _PROVIDER_URL_FAMILIES[p]
    m = (api_mode or "").strip().lower()
    if m == "codex_responses":
        return "codex"
    if m == "anthropic_messages":
        return "anthropic"
    if m == "bedrock_converse":
        return "bedrock"
    return "unknown"
