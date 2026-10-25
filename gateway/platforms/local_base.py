"""Fork: what this deployment's platform base adds on top of upstream.

Called from upstream's ``gateway/platforms/base.py`` at marked sites.

* Narrow sandbox → host media translation. Upstream maps a container path
  through EVERY configured docker volume, read-only ones included. With this
  deployment's mounts that made ``MEDIA:/workspace/live-hermes/.env`` (the
  agent repo's .env, EMAIL_PASSWORD) and the SSH keys under
  ``/opt/hermes-webui/ssh`` deliverable to a chat -- the denylist covers
  neither. Only the documented writable export mounts (``/output``,
  ``/outputs``) are translated from ``docker_volumes``; upstream's cache-dir
  mounts and the agent's own sandbox roots (``/workspace``, ``/root``) still
  are. Owner decision 2026-10-25.
* A reply that was nothing but an undeliverable MEDIA tag tells the user why,
  instead of arriving as an empty message.
* Busy-session merges carry the reply-quote group from ONE event: the incoming
  one when it has a renderable quote, else the buffered one -- never an id
  from one message beside the text of another.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, List, Optional, Tuple

logger = logging.getLogger("gateway.platforms.base")

# Container paths that are, by convention, an operator-configured export mount
# (the same set gateway warnings tell operators to configure).
_MEDIA_EXPORT_CONTAINER_PATHS = ("/output", "/outputs")

# Terminal backends that run the agent's file tools somewhere other than the host.
_SANDBOX_TERMINAL_BACKENDS = frozenset({"docker", "singularity", "modal", "daytona", "ssh", "managed_modal"})


# ── narrow translation ──────────────────────────────────────────────────────

def _parse_bind_mount_spec(spec: str) -> Optional[Tuple[str, str, List[str]]]:
    """Split ``host:container[:opts]`` into its parts, or None if malformed."""
    parts = str(spec or "").strip().split(":")
    if len(parts) < 2:
        return None
    options: List[str] = []
    if len(parts) >= 3 and not parts[-1].startswith("/"):
        options = [o.strip() for o in parts[-1].split(",") if o.strip()]
        parts = parts[:-1]
    container_path = parts[-1]
    host_path = ":".join(parts[:-1])
    if not container_path.startswith("/") or not host_path:
        return None
    return host_path, container_path, options


def media_export_mounts() -> List[Tuple[Path, Path]]:
    """``(host, container)`` for the writable export mounts in TERMINAL_DOCKER_VOLUMES.

    The only docker volumes media delivery may translate through. A read-only
    mount cannot hold anything the agent produced; any other rw mount (a source
    checkout, a service dir) must not become a delivery channel.
    """
    from gateway.platforms.base import _docker_env_active, _tenv

    # Without a docker sandbox there is no container path: /output means /output.
    if not _docker_env_active():
        return []
    raw = _tenv("TERMINAL_DOCKER_VOLUMES", "").strip()
    if not raw:
        return []
    try:
        specs = json.loads(raw)
    except Exception:
        logger.debug("Could not parse TERMINAL_DOCKER_VOLUMES for media translation", exc_info=True)
        return []
    mounts: List[Tuple[Path, Path]] = []
    for spec in specs if isinstance(specs, list) else ():
        parsed = _parse_bind_mount_spec(spec) if isinstance(spec, str) else None
        if not parsed:
            continue
        host_path, container_path, options = parsed
        if "ro" in options:
            continue
        container = container_path.rstrip("/")
        host_expanded = os.path.expanduser(host_path)
        if container in _MEDIA_EXPORT_CONTAINER_PATHS and host_expanded.startswith("/"):
            mounts.append((Path(host_expanded), Path(container)))
    return mounts


# ── rejection reasons and the user-facing notice ────────────────────────────

MEDIA_REJECT_SANDBOX_ONLY = "sandbox_only"
MEDIA_REJECT_MISSING = "missing_on_host"
MEDIA_REJECT_BLOCKED = "blocked_by_policy"
MEDIA_REJECT_INVALID = "invalid_path"


def _agent_files_run_in_sandbox() -> bool:
    from gateway.platforms.base import _tenv

    return (_tenv("TERMINAL_ENV", "") or "local").strip().lower() in _SANDBOX_TERMINAL_BACKENDS


def classify_media_delivery_rejection(path: str) -> Optional[str]:
    """Why *path* cannot be delivered, or None when it can be.

    ``sandbox_only`` -- no host file, and file tools run in a sandbox: the agent
    wrote it somewhere the host cannot reach. ``missing_on_host`` -- no such
    file, no sandbox to blame. ``blocked_by_policy`` -- the file exists but
    delivery policy refuses it.
    """
    from gateway.platforms import base as _b

    candidate = _b._normalize_media_tag_path(path)
    if not candidate:
        return MEDIA_REJECT_INVALID
    if _b.validate_media_delivery_path(candidate):
        return None
    try:
        expanded = Path(os.path.expanduser(candidate))
    except (OSError, RuntimeError, ValueError):
        return MEDIA_REJECT_INVALID
    host = (_b._translate_docker_container_media_path(expanded, quiet=True, chat_delivery=True)
            if expanded.is_absolute() else None)
    if host is None:
        host = _b._resolve_path(expanded, strict=True)
    if host is not None and host.is_file():
        return MEDIA_REJECT_BLOCKED
    if _agent_files_run_in_sandbox():
        return MEDIA_REJECT_SANDBOX_ONLY
    return MEDIA_REJECT_MISSING


# English fallbacks, used when the locale catalog is unavailable.
_MEDIA_FAILURE_FALLBACKS = {
    MEDIA_REJECT_SANDBOX_ONLY: (
        "⚠️ `{path}` was created inside the sandbox and is not visible to the host, so it could "
        "not be attached. Ask the agent to write it to a shared directory and send it again."
    ),
    MEDIA_REJECT_MISSING: "⚠️ `{path}` does not exist, so it could not be attached.",
    MEDIA_REJECT_BLOCKED: "⚠️ Attaching `{path}` was refused by the file delivery policy.",
    MEDIA_REJECT_INVALID: "⚠️ A file was promised but its path was unusable, so nothing could be attached.",
}
_MEDIA_FAILURE_I18N_KEYS = {
    MEDIA_REJECT_SANDBOX_ONLY: "gateway.media_delivery_failed_sandbox",
    MEDIA_REJECT_MISSING: "gateway.media_delivery_failed_missing",
    MEDIA_REJECT_BLOCKED: "gateway.media_delivery_failed_blocked",
    MEDIA_REJECT_INVALID: "gateway.media_delivery_failed_invalid",
}


def format_media_delivery_failure_notice(rejected) -> str:
    """User-facing notice for ``(raw_path, reason)`` pairs that could not be attached.

    A dropped attachment used to be visible only in gateway.log: when the reply
    was nothing but the MEDIA tag, the user received an empty message.
    """
    from gateway.platforms.base import _log_safe_path

    lines: List[str] = []
    for raw, reason in rejected or []:
        safe = _log_safe_path(raw)
        key = _MEDIA_FAILURE_I18N_KEYS.get(reason, _MEDIA_FAILURE_I18N_KEYS[MEDIA_REJECT_MISSING])
        text = ""
        try:
            from agent.i18n import t

            text = t(key, path=safe)
        except Exception:
            logger.debug("i18n unavailable for media delivery notice", exc_info=True)
        if not text or text == key:
            fallback = _MEDIA_FAILURE_FALLBACKS.get(reason, _MEDIA_FAILURE_FALLBACKS[MEDIA_REJECT_MISSING])
            text = fallback.format(path=safe)
        if text not in lines:
            lines.append(text)
    return "\n".join(lines)


def partition_media_delivery_paths(media_files, session_key: str = ""):
    """Split MEDIA paths into ``(accepted, rejected)``; ``rejected`` carries
    ``(raw_path, reason)`` with a reason from classify_media_delivery_rejection()."""
    from gateway.platforms.base import BasePlatformAdapter

    dropped: list[dict] = []
    accepted = BasePlatformAdapter.filter_media_delivery_paths(media_files, session_key=session_key, dropped=dropped)
    rejected = [
        (d["path"], classify_media_delivery_rejection(d["path"]) or MEDIA_REJECT_MISSING) for d in dropped
    ]
    return accepted, rejected


def notice_for_dropped_media(dropped: list) -> str:
    """The notice for the ``dropped`` list upstream's filter collected, or ""."""
    if not dropped:
        return ""
    return format_media_delivery_failure_notice(
        [(d["path"], classify_media_delivery_rejection(d["path"]) or MEDIA_REJECT_MISSING) for d in dropped]
    )


# ── reply quotes through merges ─────────────────────────────────────────────

def has_reply_quote(event: Any) -> bool:
    """True when *event* carries a quote that would actually render.

    The gateway renders the ``[Replying to: ...]`` pointer only when BOTH
    ``reply_to_message_id`` and ``reply_to_text`` are truthy, so an id without
    text is not a quote for merge purposes -- adopting one would replace a
    renderable pending quote with a silent nothing.
    """
    return bool(getattr(event, "reply_to_message_id", None)) and bool(getattr(event, "reply_to_text", None))


def adopt_reply_quote(target: Any, source: Any) -> None:
    """Copy the whole reply-quote group from *source* onto *target*.

    ``reply_to_message_id`` / ``reply_to_text`` / ``reply_to_author_id`` /
    ``reply_to_author_name`` / ``reply_to_is_own_message`` describe one quoted
    message and only mean anything together: an id from one message beside the
    text of another renders a confidently wrong quote.
    """
    if not hasattr(target, "reply_to_message_id"):
        return
    target.reply_to_message_id = getattr(source, "reply_to_message_id", None)
    target.reply_to_text = getattr(source, "reply_to_text", None)
    target.reply_to_author_id = getattr(source, "reply_to_author_id", None)
    target.reply_to_author_name = getattr(source, "reply_to_author_name", None)
    target.reply_to_is_own_message = bool(getattr(source, "reply_to_is_own_message", False))


def merge_reply_quote(target: Any, incoming: Any) -> None:
    """The merge rule: take the incoming event's quote when it has one, else keep the buffered one."""
    if has_reply_quote(incoming):
        adopt_reply_quote(target, incoming)
