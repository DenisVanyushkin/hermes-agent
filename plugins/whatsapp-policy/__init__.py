from __future__ import annotations

import asyncio
import json
import logging
import shlex
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

from gateway.session_context import get_session_env
from gateway.whatsapp_identity import canonical_whatsapp_identifier, expand_whatsapp_aliases, normalize_whatsapp_identifier
from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_STATE_DIR = get_hermes_home() / "policy"
_STATE_PATH = _STATE_DIR / "whatsapp_delegations.json"
_SANDBOX_ROOT = _STATE_DIR / "whatsapp_sandboxes"
_DEFAULT_TTL_HOURS = 48
_MAX_PREVIEW_LEN = 160
_LOCK = threading.RLock()
_LAST_GATEWAY = None
_SAFE_EXTERNAL_TOOLS = {"send_message", "web_search", "web_extract"}


def _set_last_gateway(gateway) -> None:
    global _LAST_GATEWAY
    with _LOCK:
        _LAST_GATEWAY = gateway


def _get_last_gateway():
    with _LOCK:
        return _LAST_GATEWAY


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utcnow().isoformat()


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _generate_id(prefix: str) -> str:
    ts = _utcnow().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{ts}_{uuid.uuid4().hex[:6]}"


def _preview(text: str) -> str:
    text = " ".join((text or "").split())
    return text[:_MAX_PREVIEW_LEN]


def _default_state() -> Dict[str, Any]:
    return {
        "schema_version": 2,
        "contact_aliases": {},
        "active_threads": {},
        "pending_events": {},
        "telegram_control_chat_id": "",
        "telegram_control_thread_id": "",
    }


def _ensure_state_dir() -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    _ensure_state_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_state() -> Dict[str, Any]:
    with _LOCK:
        if not _STATE_PATH.exists():
            state = _default_state()
            _write_json_atomic(_STATE_PATH, state)
            return state
        try:
            state = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("whatsapp-policy: failed to read state; recreating %s", _STATE_PATH)
            state = _default_state()
            _write_json_atomic(_STATE_PATH, state)
            return state
        if not isinstance(state, dict):
            state = _default_state()
        state.setdefault("schema_version", 2)
        state.setdefault("contact_aliases", {})
        state.setdefault("active_threads", {})
        state.setdefault("pending_events", {})
        state.setdefault("telegram_control_chat_id", "")
        state.setdefault("telegram_control_thread_id", "")
        return state


def _save_state(state: Dict[str, Any]) -> None:
    with _LOCK:
        _write_json_atomic(_STATE_PATH, state)


def _current_platform() -> str:
    return (get_session_env("HERMES_SESSION_PLATFORM", "") or "").strip().lower()


def _current_chat_id() -> str:
    return (get_session_env("HERMES_SESSION_CHAT_ID", "") or "").strip()


def _current_thread_id() -> str:
    return (get_session_env("HERMES_SESSION_THREAD_ID", "") or "").strip()


def _current_user_name() -> str:
    return (get_session_env("HERMES_SESSION_USER_NAME", "") or "").strip()


def _canonical_sender(source=None) -> str:
    raw = ""
    if source is not None:
        raw = getattr(source, "user_id", None) or getattr(source, "chat_id", None) or ""
    if not raw:
        raw = get_session_env("HERMES_SESSION_USER_ID", "") or get_session_env("HERMES_SESSION_CHAT_ID", "")
    return canonical_whatsapp_identifier(raw)


def _normalize_phone(value: str) -> str:
    return normalize_whatsapp_identifier(value)


def _format_phone(value: str) -> str:
    normalized = _normalize_phone(value)
    return f"+{normalized}" if normalized else ""


def _alias_tokens(*values: str) -> Set[str]:
    tokens: Set[str] = set()
    for value in values:
        raw = (value or "").strip()
        if not raw:
            continue
        normalized = normalize_whatsapp_identifier(raw)
        if not normalized or not normalized.isdigit():
            continue
        lowered = raw.lower()
        tokens.add(normalized.lower())
        if raw.startswith("+") or raw.isdigit() or "@" in raw:
            tokens.add(lowered)
        for alias in expand_whatsapp_aliases(normalized):
            alias_raw = str(alias).strip().lower()
            if alias_raw and alias_raw.isdigit():
                tokens.add(alias_raw)
    return tokens


def _thread_alias_tokens(thread: Dict[str, Any]) -> Set[str]:
    aliases = thread.get("aliases", []) or []
    values = [thread.get("target", "")]
    values.extend(str(alias) for alias in aliases if alias)
    return _alias_tokens(*values)


def _remember_thread_aliases(thread: Dict[str, Any], *values: str) -> bool:
    known = _thread_alias_tokens(thread)
    merged = sorted(known | _alias_tokens(*values))
    if merged == sorted(str(alias).strip().lower() for alias in (thread.get("aliases", []) or []) if str(alias).strip()):
        return False
    thread["aliases"] = merged
    return True


def _update_sandbox_aliases(target: str, *values: str) -> bool:
    alias_values = sorted(_alias_tokens(*values))
    if not alias_values:
        return False
    _, sandbox_dir = _ensure_sandbox(target)
    profile_path = sandbox_dir / "profile.json"
    profile = _read_json(profile_path, {"aliases": []})
    current = sorted(str(alias).strip().lower() for alias in (profile.get("aliases", []) or []) if str(alias).strip())
    merged = sorted(set(current) | set(alias_values))
    if merged == current:
        return False
    profile["aliases"] = merged
    profile["updated_at"] = _iso_now()
    _write_json_atomic(profile_path, profile)
    return True


def _thread_matches_token(thread_id: str, thread: Dict[str, Any], token: str) -> bool:
    token = (token or "").strip()
    if not token:
        return False
    if token == thread_id:
        return True
    return bool(_alias_tokens(token) & _thread_alias_tokens(thread))


def _find_thread_by_target(state: Dict[str, Any], target: str, *, active_only: bool = True) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    if not _alias_tokens(target):
        return None, None
    for thread_id, thread in (state.get("active_threads") or {}).items():
        if not isinstance(thread, dict):
            continue
        if active_only and thread.get("status") != "active":
            continue
        if _thread_matches_token(thread_id, thread, target):
            return thread_id, thread
    return None, None


def _resolve_thread(state: Dict[str, Any], token: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    for thread_id, thread in (state.get("active_threads") or {}).items():
        if isinstance(thread, dict) and _thread_matches_token(thread_id, thread, token):
            return thread_id, thread
    return None, None


def _thread_display_name(state: Dict[str, Any], thread: Dict[str, Any]) -> str:
    target = thread.get("target", "")
    aliases = state.get("contact_aliases") or {}
    alias = aliases.get(target)
    return alias or thread.get("contact_name") or _format_phone(target) or target


def _sandbox_contact_id(target: str) -> str:
    canonical = canonical_whatsapp_identifier(target)
    return f"wa_{canonical}" if canonical else ""


def _sandbox_dir(target: str) -> Path:
    contact_id = _sandbox_contact_id(target)
    if not contact_id:
        raise ValueError("invalid sandbox target")
    resolved = (_SANDBOX_ROOT / contact_id).resolve()
    if not resolved.is_relative_to(_SANDBOX_ROOT.resolve()):
        raise ValueError("sandbox path escapes root")
    return resolved


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default)
    return data if isinstance(data, dict) else dict(default)


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for _ in fh)


def _ensure_sandbox(target: str, *, purpose: str = "", contact_name: str = "") -> Tuple[str, Path]:
    canonical = canonical_whatsapp_identifier(target)
    if not canonical:
        raise ValueError("invalid WhatsApp target")
    contact_id = _sandbox_contact_id(canonical)
    sandbox_dir = _sandbox_dir(canonical)
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    profile_path = sandbox_dir / "profile.json"
    policy_path = sandbox_dir / "policy.json"
    status_path = sandbox_dir / "status.json"
    notes_path = sandbox_dir / "notes.md"

    profile = _read_json(
        profile_path,
        {
            "contact_id": contact_id,
            "whatsapp_id": canonical,
            "display_name": contact_name or _format_phone(canonical) or canonical,
            "aliases": [],
            "created_at": _iso_now(),
            "updated_at": _iso_now(),
        },
    )
    if contact_name:
        profile["display_name"] = contact_name
    profile["updated_at"] = _iso_now()
    _write_json_atomic(profile_path, profile)

    policy = _read_json(
        policy_path,
        {
            "contact_id": contact_id,
            "allowed_disclosures": [],
            "restricted_disclosures": [],
            "approval_required": ["payments", "calendar_commitments", "identity_docs", "extra_contacts", "media_files"],
            "purpose": purpose,
            "updated_at": _iso_now(),
        },
    )
    if purpose:
        policy["purpose"] = purpose
    policy["updated_at"] = _iso_now()
    _write_json_atomic(policy_path, policy)

    status = _read_json(
        status_path,
        {
            "contact_id": contact_id,
            "status": "active",
            "created_at": _iso_now(),
            "updated_at": _iso_now(),
            "last_inbound_at": "",
            "last_outbound_at": "",
            "last_clarification_at": "",
            "pending_question_ids": [],
        },
    )
    status.setdefault("pending_question_ids", [])
    status["updated_at"] = _iso_now()
    _write_json_atomic(status_path, status)

    if not notes_path.exists():
        notes_path.write_text("# Notes\n", encoding="utf-8")

    for filename in ("transcript.jsonl", "facts.jsonl"):
        path = sandbox_dir / filename
        if not path.exists():
            path.write_text("", encoding="utf-8")

    return contact_id, sandbox_dir


def _update_sandbox_status(target: str, **changes: Any) -> None:
    _, sandbox_dir = _ensure_sandbox(target)
    status_path = sandbox_dir / "status.json"
    status = _read_json(status_path, {"status": "active", "pending_question_ids": []})
    status.setdefault("pending_question_ids", [])
    status.update(changes)
    status["updated_at"] = _iso_now()
    _write_json_atomic(status_path, status)


def _record_transcript(
    target: str,
    *,
    direction: str,
    text: str,
    source_type: str,
    message_id: str = "",
    thread_id: str = "",
    purpose: str = "",
    contact_name: str = "",
) -> None:
    contact_id, sandbox_dir = _ensure_sandbox(target, purpose=purpose, contact_name=contact_name)
    _append_jsonl(
        sandbox_dir / "transcript.jsonl",
        {
            "timestamp": _iso_now(),
            "contact_id": contact_id,
            "target": canonical_whatsapp_identifier(target),
            "thread_id": thread_id,
            "direction": direction,
            "message_id": message_id,
            "text": text or "",
            "preview": _preview(text or ""),
            "source_type": source_type,
        },
    )


def _record_fact(
    target: str,
    *,
    text: str,
    source_type: str,
    source_ref: str = "",
    fact_type: str = "message_claim",
    confirmation_state: str = "unconfirmed",
    thread_id: str = "",
    purpose: str = "",
    contact_name: str = "",
) -> None:
    if not (text or "").strip():
        return
    contact_id, sandbox_dir = _ensure_sandbox(target, purpose=purpose, contact_name=contact_name)
    _append_jsonl(
        sandbox_dir / "facts.jsonl",
        {
            "timestamp": _iso_now(),
            "contact_id": contact_id,
            "target": canonical_whatsapp_identifier(target),
            "thread_id": thread_id,
            "fact_type": fact_type,
            "text": _preview(text),
            "source_type": source_type,
            "source_ref": source_ref,
            "confirmation_state": confirmation_state,
        },
    )


def _sandbox_summary(target: str) -> str:
    canonical = canonical_whatsapp_identifier(target)
    if not canonical:
        return ""
    sandbox_dir = _sandbox_dir(canonical)
    if not sandbox_dir.exists():
        return ""
    profile = _read_json(sandbox_dir / "profile.json", {})
    status = _read_json(sandbox_dir / "status.json", {})
    facts_count = _count_jsonl(sandbox_dir / "facts.jsonl")
    transcript_count = _count_jsonl(sandbox_dir / "transcript.jsonl")
    return (
        f"sandbox={profile.get('contact_id', _sandbox_contact_id(canonical))}; "
        f"status={status.get('status', 'unknown')}; "
        f"facts={facts_count}; transcript={transcript_count}"
    )


def _prune_expired_threads(state: Dict[str, Any]) -> bool:
    changed = False
    now = _utcnow()
    for thread in (state.get("active_threads") or {}).values():
        if not isinstance(thread, dict) or thread.get("status") != "active":
            continue
        expires_at = _parse_iso(thread.get("expires_at", ""))
        if expires_at and expires_at <= now:
            thread["status"] = "expired"
            thread["closed_at"] = _iso_now()
            changed = True
            try:
                _update_sandbox_status(thread.get("target", ""), status="archived")
            except Exception:
                logger.debug("whatsapp-policy: failed to archive expired sandbox", exc_info=True)
    return changed


def _open_or_refresh_thread(
    state: Dict[str, Any],
    *,
    target: str,
    purpose: str,
    contact_name: str = "",
    ttl_hours: int = _DEFAULT_TTL_HOURS,
    opened_via: str = "manual",
    last_message_preview: str = "",
) -> Tuple[str, Dict[str, Any], bool]:
    canonical_target = canonical_whatsapp_identifier(target)
    if not canonical_target:
        raise ValueError("invalid WhatsApp target")
    now = _utcnow()
    expires_at = (now + timedelta(hours=max(1, ttl_hours))).isoformat()
    thread_id, thread = _find_thread_by_target(state, canonical_target, active_only=False)
    changed = False
    if thread is None:
        thread_id = _generate_id("wt")
        thread = {
            "target": canonical_target,
            "contact_name": contact_name,
            "purpose": purpose,
            "status": "active",
            "opened_at": _iso_now(),
            "opened_via": opened_via,
            "expires_at": expires_at,
            "last_message_preview": last_message_preview,
            "aliases": [],
        }
        state.setdefault("active_threads", {})[thread_id] = thread
        changed = True
    else:
        thread["status"] = "active"
        thread["expires_at"] = expires_at
        thread["purpose"] = purpose or thread.get("purpose", "")
        if contact_name:
            thread["contact_name"] = contact_name
        if last_message_preview:
            thread["last_message_preview"] = last_message_preview
        thread["reopened_at"] = _iso_now()
        changed = True

    changed = _remember_thread_aliases(thread, target, canonical_target) or changed
    _ensure_sandbox(canonical_target, purpose=thread.get("purpose", purpose), contact_name=thread.get("contact_name", contact_name))
    _update_sandbox_aliases(canonical_target, target, canonical_target)
    _update_sandbox_status(canonical_target, status="active")
    return thread_id, thread, changed


def _set_thread_status(state: Dict[str, Any], token: str, status: str) -> str:
    thread_id, thread = _resolve_thread(state, token)
    if thread is None:
        raise ValueError("thread not found")
    thread["status"] = status
    thread["closed_at"] = _iso_now()
    _update_sandbox_status(thread.get("target", ""), status="archived" if status in {"closed", "expired"} else status)
    return thread_id


def _create_pending_event(
    state: Dict[str, Any],
    *,
    kind: str,
    sender: str,
    text: str,
    message_id: str = "",
    thread_id: str = "",
    purpose: str = "",
    contact_name: str = "",
) -> str:
    event_id = _generate_id("waevt")
    state.setdefault("pending_events", {})[event_id] = {
        "event_id": event_id,
        "kind": kind,
        "sender": canonical_whatsapp_identifier(sender),
        "text": text or "",
        "text_preview": _preview(text or ""),
        "message_id": message_id,
        "thread_id": thread_id,
        "purpose": purpose,
        "contact_name": contact_name,
        "status": "pending",
        "created_at": _iso_now(),
        "updated_at": _iso_now(),
    }
    return event_id


def _resolve_pending_event(state: Dict[str, Any], token: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    pending = state.get("pending_events") or {}
    entry = pending.get(token)
    if isinstance(entry, dict):
        return token, entry
    return None, None


def _find_pending_event_for_thread(state: Dict[str, Any], *, thread_id: str = "", target: str = "") -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    pending = state.get("pending_events") or {}
    target_id = canonical_whatsapp_identifier(target)
    candidates: list[tuple[str, Dict[str, Any]]] = []
    for event_id, entry in pending.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "pending":
            continue
        if thread_id and entry.get("thread_id") == thread_id:
            candidates.append((event_id, entry))
            continue
        if target_id and canonical_whatsapp_identifier(entry.get("sender", "")) == target_id:
            candidates.append((event_id, entry))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: str(item[1].get("created_at", "")), reverse=True)
    return candidates[0]


def _mark_pending_event(state: Dict[str, Any], event_id: str, status: str, note: str = "") -> None:
    entry = (state.get("pending_events") or {}).get(event_id)
    if not isinstance(entry, dict):
        return
    entry["status"] = status
    entry["updated_at"] = _iso_now()
    if note:
        entry["note"] = note


def _looks_like_question(text: str) -> bool:
    lowered = " ".join((text or "").lower().split())
    if not lowered:
        return False
    if "?" in lowered:
        return True
    prefixes = (
        "какой",
        "какая",
        "какие",
        "когда",
        "где",
        "как ",
        "как?",
        "можно",
        "нужно",
        "какова",
        "what",
        "when",
        "where",
        "which",
        "can you",
        "could you",
    )
    return lowered.startswith(prefixes)


async def _async_send_gateway_message(gateway, *, platform_name: str, chat_id: str, text: str, thread_id: str = "") -> None:
    if not gateway or not text.strip() or not chat_id:
        return
    from gateway.config import Platform

    platform = Platform(platform_name)
    adapter = gateway.adapters.get(platform)
    if adapter is None:
        logger.warning("whatsapp-policy: no adapter available for %s", platform_name)
        return
    metadata = {"thread_id": thread_id} if thread_id else None
    try:
        if metadata:
            await adapter.send(str(chat_id), text, metadata=metadata)
        else:
            await adapter.send(str(chat_id), text)
    except Exception:
        logger.warning("whatsapp-policy: failed to send %s policy message", platform_name, exc_info=True)


def _schedule_gateway_message(gateway, *, platform_name: str, chat_id: str, text: str, thread_id: str = "") -> bool:
    if not gateway or not text.strip() or not chat_id:
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("whatsapp-policy: no running loop to schedule %s message", platform_name)
        return False
    loop.create_task(
        _async_send_gateway_message(
            gateway,
            platform_name=platform_name,
            chat_id=chat_id,
            text=text,
            thread_id=thread_id,
        )
    )
    return True


def _schedule_telegram_control_message(gateway, text: str) -> bool:
    if not gateway or not text.strip():
        return False
    state = _load_state()
    try:
        from gateway.config import Platform

        home = gateway.config.get_home_channel(Platform.TELEGRAM)
    except Exception:
        logger.warning("whatsapp-policy: failed to resolve Telegram home channel", exc_info=True)
        return False
    if not home or not getattr(home, "chat_id", None):
        logger.warning("whatsapp-policy: Telegram home channel is not configured")
        return False
    state["telegram_control_chat_id"] = str(home.chat_id)
    state["telegram_control_thread_id"] = str(getattr(home, "thread_id", None) or "")
    _save_state(state)
    return _schedule_gateway_message(
        gateway,
        platform_name="telegram",
        chat_id=str(home.chat_id),
        text=text,
        thread_id=str(home.thread_id) if getattr(home, "thread_id", None) else "",
    )


def _schedule_whatsapp_message(gateway, target: str, text: str) -> bool:
    canonical = canonical_whatsapp_identifier(target)
    if not canonical:
        return False
    return _schedule_gateway_message(gateway, platform_name="whatsapp", chat_id=canonical, text=text)


def _is_home_whatsapp_chat(source, gateway) -> bool:
    if source is None or gateway is None:
        return False
    try:
        config = getattr(gateway, "config", None)
        if config is None:
            return False
        home = config.get_home_channel(getattr(source, "platform", None))
        if not home or not getattr(home, "chat_id", None):
            return False
        source_chat = getattr(source, "chat_id", "") or ""
        return canonical_whatsapp_identifier(source_chat) == canonical_whatsapp_identifier(str(home.chat_id))
    except Exception:
        return False


def _target_from_send_message_target(target: str) -> str:
    if not isinstance(target, str):
        return ""
    target = target.strip()
    if not target or not target.lower().startswith("whatsapp:"):
        return ""
    ref = target.split(":", 1)[1].strip()
    if not ref or ref.startswith("#"):
        return ""
    if ref.lower() == "whatsapp":
        return ""
    if ref.endswith("@s.whatsapp.net") or ref.endswith("@lid") or ref.startswith("+"):
        return canonical_whatsapp_identifier(ref)
    normalized = _normalize_phone(ref)
    return canonical_whatsapp_identifier(normalized) if normalized else ""


def _send_message_result_succeeded(result: Any) -> bool:
    payload = result
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return False
    if not isinstance(payload, dict):
        return False
    if payload.get("error"):
        return False
    if payload.get("skipped"):
        return False
    return bool(payload.get("success"))


def _send_message_result_aliases(result: Any) -> list[str]:
    payload = result
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return []
    if not isinstance(payload, dict):
        return []

    raw_candidates: list[str] = []
    for key in ("normalized_chat_id", "bridge_chat_id", "remote_jid", "jid", "recipient_jid"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            raw_candidates.append(value.strip())

    chat_id = payload.get("chat_id")
    if isinstance(chat_id, str) and chat_id.strip() and "@" in chat_id:
        raw_candidates.append(chat_id.strip())

    nested = payload.get("raw_response")
    if isinstance(nested, dict):
        for key in ("normalizedChatId", "chatId", "remoteJid", "jid"):
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                raw_candidates.append(value.strip())

    seen: Set[str] = set()
    aliases: list[str] = []
    for candidate in raw_candidates:
        normalized = candidate.strip().lower()
        if normalized and normalized not in seen:
            aliases.append(candidate.strip())
            seen.add(normalized)
    return aliases


def _target_is_same_correspondent(target: str, correspondent_id: str) -> bool:
    resolved = _target_from_send_message_target(target)
    return bool(resolved and resolved == canonical_whatsapp_identifier(correspondent_id))


def _target_is_telegram_control_route(target: str) -> bool:
    if not isinstance(target, str):
        return False
    lowered = target.strip().lower()
    return lowered == "telegram" or lowered.startswith("telegram:")


def _status_text(state: Dict[str, Any]) -> str:
    lines = ["WhatsApp policy status"]
    aliases = state.get("contact_aliases") or {}
    pending = [e for e in (state.get("pending_events") or {}).values() if isinstance(e, dict) and e.get("status") == "pending"]
    lines.append(f"Aliases: {len(aliases)}")
    lines.append(f"Pending events: {len(pending)}")
    active_threads = []
    for thread_id, thread in (state.get("active_threads") or {}).items():
        if isinstance(thread, dict) and thread.get("status") == "active":
            active_threads.append((thread_id, thread))
    if not active_threads:
        lines.append("Active threads: none")
        return "\n".join(lines)
    lines.append("Active threads:")
    for thread_id, thread in active_threads:
        label = _thread_display_name(state, thread)
        purpose = thread.get("purpose", "")
        expires_at = thread.get("expires_at", "")
        lines.append(f"- {thread_id}: {label}; expires={expires_at}")
        if purpose:
            lines.append(f"  purpose: {purpose}")
        sandbox_info = _sandbox_summary(thread.get("target", ""))
        if sandbox_info:
            lines.append(f"  {sandbox_info}")
    return "\n".join(lines)


def _pending_text(state: Dict[str, Any]) -> str:
    pending_items = [
        item for item in (state.get("pending_events") or {}).values() if isinstance(item, dict) and item.get("status") == "pending"
    ]
    if not pending_items:
        return "No pending WhatsApp events."
    lines = ["Pending WhatsApp events:"]
    for item in sorted(pending_items, key=lambda x: x.get("created_at", "")):
        sender = item.get("sender", "")
        alias = (state.get("contact_aliases") or {}).get(sender, "")
        label = alias or _format_phone(sender) or sender
        lines.append(f"- {item.get('event_id')}: {item.get('kind')} from {label}")
        lines.append(f"  preview: {item.get('text_preview', '')}")
        thread_id = item.get("thread_id", "")
        if thread_id:
            lines.append(f"  thread: {thread_id}")
    return "\n".join(lines)


def _control_only() -> Tuple[bool, str]:
    if _current_platform() != "telegram":
        return False, "WhatsApp policy control commands are only accepted from Telegram."
    chat_id = _current_chat_id()
    thread_id = _current_thread_id()
    if not chat_id:
        return False, "Telegram control chat could not be resolved."
    state = _load_state()
    allowed_chat = str(state.get("telegram_control_chat_id", "") or "")
    allowed_thread = str(state.get("telegram_control_thread_id", "") or "")
    if not allowed_chat:
        state["telegram_control_chat_id"] = chat_id
        state["telegram_control_thread_id"] = thread_id
        _save_state(state)
        return True, ""
    if chat_id != allowed_chat:
        return False, "This Telegram chat is not authorized for WhatsApp policy control."
    if allowed_thread and thread_id and thread_id != allowed_thread:
        return False, "This Telegram thread is not authorized for WhatsApp policy control."
    return True, ""


def _handle_status(_: list[str]) -> str:
    state = _load_state()
    if _prune_expired_threads(state):
        _save_state(state)
    return _status_text(state)


def _handle_pending(_: list[str]) -> str:
    state = _load_state()
    if _prune_expired_threads(state):
        _save_state(state)
    return _pending_text(state)


def _handle_alias(argv: list[str]) -> str:
    ok, msg = _control_only()
    if not ok:
        return msg
    if len(argv) >= 4 and argv[1] == "set":
        target = canonical_whatsapp_identifier(argv[2])
        if not target:
            return "Usage: /wa-policy alias set <phone> <name>"
        name = " ".join(argv[3:]).strip()
        if not name:
            return "Usage: /wa-policy alias set <phone> <name>"
        state = _load_state()
        state.setdefault("contact_aliases", {})[target] = name
        _save_state(state)
        _ensure_sandbox(target, contact_name=name)
        return f"Alias saved: {_format_phone(target)} → {name}."
    if len(argv) == 3 and argv[1] in {"rm", "del", "delete"}:
        target = canonical_whatsapp_identifier(argv[2])
        state = _load_state()
        removed = (state.get("contact_aliases") or {}).pop(target, None)
        _save_state(state)
        return f"Alias removed for {_format_phone(target)}." if removed else "Alias not found."
    return "Usage: /wa-policy alias set <phone> <name> | /wa-policy alias rm <phone>"


def _handle_open(argv: list[str]) -> str:
    ok, msg = _control_only()
    if not ok:
        return msg
    if len(argv) < 3:
        return "Usage: /wa-policy open <phone> <purpose...> [--hours N]"
    hours = _DEFAULT_TTL_HOURS
    clean_argv = list(argv)
    if "--hours" in clean_argv:
        idx = clean_argv.index("--hours")
        try:
            hours = int(clean_argv[idx + 1])
        except Exception:
            return "Usage: /wa-policy open <phone> <purpose...> [--hours N]"
        clean_argv = clean_argv[:idx] + clean_argv[idx + 2:]
    target = canonical_whatsapp_identifier(clean_argv[1])
    if not target:
        return "Usage: /wa-policy open <phone> <purpose...> [--hours N]"
    purpose = " ".join(clean_argv[2:]).strip()
    if not purpose:
        return "Usage: /wa-policy open <phone> <purpose...> [--hours N]"
    state = _load_state()
    contact_name = (state.get("contact_aliases") or {}).get(target, "")
    thread_id, thread, _ = _open_or_refresh_thread(
        state,
        target=target,
        purpose=purpose,
        contact_name=contact_name,
        ttl_hours=hours,
        opened_via="telegram_open",
    )
    _save_state(state)
    return f"Opened {thread_id} for {_thread_display_name(state, thread)} until {thread.get('expires_at')}"


def _handle_approve(argv: list[str]) -> str:
    ok, msg = _control_only()
    if not ok:
        return msg
    if len(argv) < 2:
        return "Usage: /wa-policy approve <pending-id> [purpose...]"
    state = _load_state()
    event_id, entry = _resolve_pending_event(state, argv[1])
    if entry is None:
        return "Pending event not found."
    sender = entry.get("sender", "")
    if not sender:
        return "Pending event has no sender."
    purpose = " ".join(argv[2:]).strip() or entry.get("purpose") or entry.get("text_preview") or "Scoped WhatsApp correspondence"
    contact_name = (state.get("contact_aliases") or {}).get(sender, "") or entry.get("contact_name", "")
    thread_id, thread, _ = _open_or_refresh_thread(
        state,
        target=sender,
        purpose=purpose,
        contact_name=contact_name,
        opened_via="telegram_approve",
    )
    _mark_pending_event(state, event_id, "approved", note=f"approved to {thread_id}")
    _save_state(state)
    return f"Approved {event_id} -> {thread_id} for {_thread_display_name(state, thread)}."


def _handle_reject(argv: list[str]) -> str:
    ok, msg = _control_only()
    if not ok:
        return msg
    if len(argv) < 2:
        return "Usage: /wa-policy reject <pending-id> [note...]"
    state = _load_state()
    event_id, entry = _resolve_pending_event(state, argv[1])
    if entry is None:
        return "Pending event not found."
    note = " ".join(argv[2:]).strip()
    _mark_pending_event(state, event_id, "rejected", note=note)
    sender = entry.get("sender", "")
    if sender:
        existing_tid, existing_thread = _find_thread_by_target(state, sender, active_only=True)
        if existing_thread is not None:
            existing_thread["status"] = "closed"
            existing_thread["closed_at"] = _iso_now()
            _update_sandbox_status(sender, status="archived")
    _save_state(state)
    return f"Rejected {event_id}."


def _resolve_target_from_token(state: Dict[str, Any], token: str) -> Tuple[str, str]:
    event_id, entry = _resolve_pending_event(state, token)
    if entry is not None:
        sender = entry.get("sender", "")
        thread_id = entry.get("thread_id", "")
        if sender:
            return sender, thread_id
    thread_id, thread = _resolve_thread(state, token)
    if thread is not None:
        return thread.get("target", ""), thread_id or ""
    sender = canonical_whatsapp_identifier(token)
    if sender:
        known_thread_id, known_thread = _find_thread_by_target(state, sender, active_only=False)
        if known_thread is not None:
            return known_thread.get("target", ""), known_thread_id or ""
        return sender, ""
    return "", ""


def _handle_note(argv: list[str]) -> str:
    ok, msg = _control_only()
    if not ok:
        return msg
    if len(argv) < 3:
        return "Usage: /wa-policy note <thread-id-or-phone-or-pending-id> <text...>"
    state = _load_state()
    target, thread_id = _resolve_target_from_token(state, argv[1])
    if not target:
        return "Target not found."
    note_text = " ".join(argv[2:]).strip()
    if not note_text:
        return "Usage: /wa-policy note <thread-id-or-phone-or-pending-id> <text...>"
    _ensure_sandbox(target)
    _record_fact(
        target,
        text=note_text,
        source_type="from_denis",
        source_ref=f"telegram:{_current_chat_id()}",
        fact_type="owner_note",
        confirmation_state="confirmed_by_denis",
        thread_id=thread_id,
    )
    return f"Saved note for {_format_phone(target) or target}."


def _handle_answer(argv: list[str]) -> str:
    ok, msg = _control_only()
    if not ok:
        return msg
    if len(argv) < 3:
        return "Usage: /wa-policy answer <thread-id-or-phone-or-pending-id> <reply...>"
    state = _load_state()
    token = argv[1]
    target, thread_id = _resolve_target_from_token(state, token)
    if not target:
        return "Target not found."
    reply_text = " ".join(argv[2:]).strip()
    if not reply_text:
        return "Usage: /wa-policy answer <thread-id-or-phone-or-pending-id> <reply...>"
    contact_name = (state.get("contact_aliases") or {}).get(target, "")
    if thread_id:
        known_thread_id, known_thread = _resolve_thread(state, thread_id)
        if known_thread is not None:
            contact_name = contact_name or known_thread.get("contact_name", "")
    pending_id, pending_entry = _resolve_pending_event(state, token)
    if pending_entry is None:
        pending_id, pending_entry = _find_pending_event_for_thread(state, thread_id=thread_id or "", target=target)

    _record_fact(
        target,
        text=reply_text,
        source_type="from_denis",
        source_ref=f"telegram:{_current_chat_id()}",
        fact_type="clarification_answer",
        confirmation_state="confirmed_by_denis",
        thread_id=thread_id,
        contact_name=contact_name,
    )

    sent = _schedule_whatsapp_message(_get_last_gateway(), target, reply_text)
    if sent:
        _record_transcript(
            target,
            direction="outbound",
            text=reply_text,
            source_type="from_denis",
            thread_id=thread_id,
            contact_name=contact_name,
        )
        _update_sandbox_status(target, status="active", last_outbound_at=_iso_now(), last_clarification_at=_iso_now())
        if pending_entry is not None:
            _mark_pending_event(state, pending_id, "resolved", note="answered from Telegram")
            if thread_id:
                status_path = _sandbox_dir(target) / "status.json"
                status = _read_json(status_path, {"pending_question_ids": []})
                pending_ids = [x for x in status.get("pending_question_ids", []) if x != pending_id]
                status["pending_question_ids"] = pending_ids
                status["updated_at"] = _iso_now()
                _write_json_atomic(status_path, status)
        _save_state(state)
        return f"Saved answer and queued WhatsApp reply to {_format_phone(target) or target}."
    _save_state(state)
    return f"Saved answer for {_format_phone(target) or target}, but no live gateway send was available."


def _handle_close(argv: list[str]) -> str:
    ok, msg = _control_only()
    if not ok:
        return msg
    if len(argv) != 2:
        return "Usage: /wa-policy close <thread-id-or-phone>"
    state = _load_state()
    try:
        thread_id = _set_thread_status(state, argv[1], "closed")
    except ValueError:
        return "Thread not found."
    _save_state(state)
    return f"Closed {thread_id}."


_HELP = """/wa-policy — WhatsApp sandbox/operator policy

Control commands are accepted only from Telegram.

Subcommands:
  status
  pending
  open <phone> <purpose...> [--hours N]
  approve <pending-id> [purpose...]
  reject <pending-id> [note...]
  answer <thread-id-or-phone-or-pending-id> <reply...>
  note <thread-id-or-phone-or-pending-id> <text...>
  close <thread-id-or-phone>
  alias set <phone> <name>
  alias rm <phone>

Notes:
- unknown WhatsApp inbound is escalated to Telegram as a pending event
- Denis tasks sent in WhatsApp are not executed there; they are escalated to Telegram
- active correspondents can continue only inside their scoped thread
- Telegram answers can be saved and optionally sent back to WhatsApp via `answer`
"""


def _handle_slash(raw_args: str) -> Optional[str]:
    try:
        argv = shlex.split(raw_args or "")
    except ValueError as exc:
        return f"Argument parse error: {exc}"
    if not argv:
        return _HELP
    sub = argv[0].lower()
    if sub in {"help", "-h", "--help"}:
        return _HELP
    if sub == "status":
        return _handle_status(argv)
    if sub == "pending":
        return _handle_pending(argv)
    if sub == "alias":
        return _handle_alias(argv)
    if sub == "open":
        return _handle_open(argv)
    if sub == "approve":
        return _handle_approve(argv)
    if sub == "reject":
        return _handle_reject(argv)
    if sub == "answer":
        return _handle_answer(argv)
    if sub == "note":
        return _handle_note(argv)
    if sub == "close":
        return _handle_close(argv)
    return _HELP


def _unknown_inbound_message(state: Dict[str, Any], event_id: str, sender: str, text: str) -> str:
    alias = (state.get("contact_aliases") or {}).get(sender, "")
    label = alias or _format_phone(sender) or sender
    return (
        "⚠️ Unknown WhatsApp inbound\n"
        f"Event: {event_id}\n"
        f"Sender: {label}\n"
        f"Message: {_preview(text)}\n\n"
        "Suggested actions:\n"
        f"- Approve scoped conversation: /wa-policy approve {event_id} <purpose>\n"
        f"- Reject/ignore: /wa-policy reject {event_id} <note>\n"
        "- Or reply here in Telegram with a custom instruction"
    )


def _owner_control_message(event_id: str, text: str) -> str:
    return (
        "ℹ️ WhatsApp control attempt from Denis was not executed there\n"
        f"Event: {event_id}\n"
        f"Message: {_preview(text)}\n\n"
        "Telegram is the only control channel. Continue the task here if you want it executed."
    )


def _clarification_message(state: Dict[str, Any], event_id: str, thread_id: str, thread: Dict[str, Any], text: str) -> str:
    label = _thread_display_name(state, thread)
    purpose = thread.get("purpose", "") or "Scoped WhatsApp correspondence"
    return (
        "❓ WhatsApp clarification needed\n"
        f"Event: {event_id}\n"
        f"Thread: {thread_id}\n"
        f"Correspondent: {label}\n"
        f"Purpose: {purpose}\n"
        f"Question: {_preview(text)}\n\n"
        "Suggested actions:\n"
        f"- Save a fact only: /wa-policy note {thread_id} <text>\n"
        f"- Save + send reply: /wa-policy answer {event_id} <reply>\n"
        f"- Close thread: /wa-policy close {thread_id}"
    )


def _admission_note(state: Dict[str, Any], thread_id: str, thread: Dict[str, Any], incoming_text: str) -> str:
    label = _thread_display_name(state, thread)
    purpose = thread.get("purpose", "") or "Scoped WhatsApp correspondence"
    sandbox_info = _sandbox_summary(thread.get("target", ""))
    suffix = f"\nSandbox: {sandbox_info}" if sandbox_info else ""
    return (
        "[Scoped external WhatsApp thread]\n"
        f"Thread: {thread_id}\n"
        f"Correspondent: {label}\n"
        f"Purpose: {purpose}{suffix}\n"
        "Rules: stay within this correspondent thread only; do not accept new tasks, add third parties, schedule calendar changes, send sensitive data, or perform unrelated actions without escalating to Telegram.\n\n"
        "Incoming correspondent message:\n"
        f"{incoming_text}"
    )


def on_pre_gateway_dispatch(event=None, gateway=None, session_store=None, **_: Any):
    global _LAST_GATEWAY
    source = getattr(event, "source", None)
    if gateway is not None:
        _set_last_gateway(gateway)
    if source is None or getattr(getattr(source, "platform", None), "value", "") != "whatsapp":
        return None
    if getattr(source, "chat_type", "dm") != "dm":
        return None

    state = _load_state()
    changed = _prune_expired_threads(state)
    sender = _canonical_sender(source)
    text = getattr(event, "text", "") or ""
    message_id = getattr(event, "message_id", "") or ""

    if _is_home_whatsapp_chat(source, gateway):
        event_id = _create_pending_event(
            state,
            kind="owner_control_attempt",
            sender=sender,
            text=text,
            message_id=message_id,
        )
        _save_state(state)
        _schedule_telegram_control_message(gateway, _owner_control_message(event_id, text))
        return {"action": "skip", "reason": "whatsapp control redirected to telegram"}

    thread_id, thread = _find_thread_by_target(state, sender, active_only=True)
    source_tokens = [
        getattr(source, "user_id", "") or "",
        getattr(source, "chat_id", "") or "",
        sender,
    ]
    if thread is None:
        for token in source_tokens:
            if not token:
                continue
            thread_id, thread = _resolve_thread(state, token)
            if thread is not None and thread.get("status") == "active":
                changed = _remember_thread_aliases(thread, *source_tokens) or changed
                _update_sandbox_aliases(thread.get("target", sender), *source_tokens)
                break
    else:
        changed = _remember_thread_aliases(thread, *source_tokens) or changed
        _update_sandbox_aliases(thread.get("target", sender), *source_tokens)
    if thread is None:
        event_id = _create_pending_event(
            state,
            kind="unknown_inbound",
            sender=sender,
            text=text,
            message_id=message_id,
        )
        _save_state(state)
        _schedule_telegram_control_message(gateway, _unknown_inbound_message(state, event_id, sender, text))
        return {"action": "skip", "reason": "unknown whatsapp inbound escalated to telegram"}

    if getattr(event, "is_command", lambda: False)():
        return {"action": "skip", "reason": "external correspondents cannot issue slash commands"}

    thread["last_inbound_at"] = _iso_now()
    if text:
        thread["last_message_preview"] = _preview(text)
    changed = True

    target = thread.get("target", sender)
    contact_name = thread.get("contact_name", "")
    _record_transcript(
        target,
        direction="inbound",
        text=text,
        source_type="from_correspondent",
        message_id=message_id,
        thread_id=thread_id or "",
        purpose=thread.get("purpose", ""),
        contact_name=contact_name,
    )
    _record_fact(
        target,
        text=text,
        source_type="from_correspondent",
        source_ref=message_id,
        fact_type="message_claim",
        thread_id=thread_id or "",
        purpose=thread.get("purpose", ""),
        contact_name=contact_name,
    )
    _update_sandbox_status(target, status="active", last_inbound_at=_iso_now())

    if _looks_like_question(text):
        event_id = _create_pending_event(
            state,
            kind="clarification_needed",
            sender=sender,
            text=text,
            message_id=message_id,
            thread_id=thread_id or "",
            purpose=thread.get("purpose", ""),
            contact_name=contact_name,
        )
        status_path = _sandbox_dir(target) / "status.json"
        status = _read_json(status_path, {"pending_question_ids": []})
        pending_ids = list(status.get("pending_question_ids", []))
        pending_ids.append(event_id)
        status["pending_question_ids"] = pending_ids
        status["last_clarification_at"] = _iso_now()
        status["status"] = "waiting_on_denis"
        status["updated_at"] = _iso_now()
        _write_json_atomic(status_path, status)
        _save_state(state)
        _schedule_telegram_control_message(gateway, _clarification_message(state, event_id, thread_id or "", thread, text))
        return {"action": "skip", "reason": "clarification escalated to telegram"}

    _save_state(state)
    rewritten = _admission_note(state, thread_id, thread, text)
    return {"action": "rewrite", "text": rewritten, "bypass_auth": True}


def on_pre_tool_call(tool_name: str = "", args: Optional[Dict[str, Any]] = None, **_: Any):
    if _current_platform() != "whatsapp":
        return None
    state = _load_state()
    if _prune_expired_threads(state):
        _save_state(state)
    sender = _canonical_sender()
    if not sender:
        return None
    thread_id, thread = _find_thread_by_target(state, sender, active_only=True)
    if thread is None:
        return {"action": "block", "message": "WhatsApp policy blocked this tool: no active scoped thread for this correspondent."}
    args = args if isinstance(args, dict) else {}
    if tool_name == "send_message":
        target = args.get("target", "")
        if _target_is_same_correspondent(target, sender) or _target_is_telegram_control_route(target):
            return None
        return {
            "action": "block",
            "message": (
                f"WhatsApp policy blocked send_message in external thread {thread_id}: "
                "external correspondents may only message the same correspondent or Telegram control."
            ),
        }
    if tool_name in _SAFE_EXTERNAL_TOOLS:
        return None
    return {
        "action": "block",
        "message": (
            f"WhatsApp policy blocked tool '{tool_name}' in external thread {thread_id}. "
            "Allowed tools here are send_message to the same correspondent or Telegram control, plus safe read-only web lookups."
        ),
    }


def on_post_tool_call(tool_name: str = "", args: Optional[Dict[str, Any]] = None, result: Any = None, **_: Any):
    if tool_name != "send_message":
        return None
    if not _send_message_result_succeeded(result):
        return None
    args = args if isinstance(args, dict) else {}
    target = args.get("target", "")
    target_id = _target_from_send_message_target(target)
    if not target_id:
        return None

    state = _load_state()
    changed = _prune_expired_threads(state)
    message = _preview(args.get("message", "") or "")
    current_platform = _current_platform()
    current_sender = _canonical_sender()
    contact_name = (state.get("contact_aliases") or {}).get(target_id, "")
    result_aliases = _send_message_result_aliases(result)

    thread_id, thread, opened_changed = _open_or_refresh_thread(
        state,
        target=target_id,
        purpose=message or "Auto-opened from outbound WhatsApp message",
        contact_name=contact_name,
        opened_via=f"post_tool_call:{current_platform or 'unknown'}",
        last_message_preview=message,
    )
    changed = changed or opened_changed
    changed = _remember_thread_aliases(thread, target, target_id, current_sender, *result_aliases) or changed
    _update_sandbox_aliases(target_id, target, target_id, current_sender, *result_aliases)

    source_type = "from_denis" if current_platform == "telegram" else "from_hermes"
    if current_platform == "whatsapp" and current_sender and current_sender == target_id:
        source_type = "from_hermes"

    _record_transcript(
        target_id,
        direction="outbound",
        text=args.get("message", "") or "",
        source_type=source_type,
        thread_id=thread_id,
        purpose=thread.get("purpose", ""),
        contact_name=thread.get("contact_name", contact_name),
    )
    _update_sandbox_status(target_id, status="active", last_outbound_at=_iso_now())

    if changed:
        _save_state(state)
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", on_pre_gateway_dispatch)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_command(
        "wa-policy",
        handler=_handle_slash,
        description="Manage WhatsApp operator/sandbox policy from Telegram.",
        args_hint="status | pending | open <phone> <purpose>",
    )
