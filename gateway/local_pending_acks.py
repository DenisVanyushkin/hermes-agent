"""Fork: pending acknowledgements for the family assistant (custom/fam, hermes-home).

The fam reminder writer keeps a snapshot of open questions it already asked
(medication intakes, events) at ``pending_acks_file``. On a turn from the
target user the gateway

* carries the open items on the current user message as a sidecar note, so
  they survive session resets and restarts;
* runs the fam CLI once (``fam acks resolve``) to apply what the reply
  answered -- quoted reminder, or the open chains without a quote -- and puts
  the trusted receipt on the message;
* strips the clarification markers from the reply and, for candidates the
  turn left unresolved, sends one aggregated "action not recorded yet" line
  after the main reply (or inline when post-delivery is unavailable).

Off unless ``pending_acks_file`` is configured (the VPS has none). The module
code moved unchanged from the pre-v0.22.5 gateway/run.py; config and the
HERMES_HOME root are read through gateway.run at call time, where the gateway
and its tests set them. The upstream call sites are marked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from gateway.whatsapp_identity import expand_whatsapp_aliases as _expand_whatsapp_auth_aliases

logger = logging.getLogger("gateway.run")


def _gr():
    from gateway import run

    return run


# A snapshot older than this is ignored: if the writer died, silence beats
# nagging about a dose the user may already have taken.
_PENDING_ACKS_MAX_AGE_MINUTES = 60


def _read_pending_acks(path: str) -> Optional[dict]:
    """Load the snapshot file. Never raises — a broken file means no note."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


_PENDING_ACK_RESIDUAL = "действие пока не записано"


def _pending_ack_unresolved_refs(candidates, reason):
    refs = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        detail = {
            key: candidate[key]
            for key in ("kind", "ref_id", "title", "name", "last_outbound_at", "wa_message_ids")
            if key in candidate
        }
        detail["reason"] = reason
        refs.append(detail)
    return refs


def _pending_ack_candidate_key(detail):
    key = f"{detail.get('kind', '')}:{detail.get('ref_id', '')}"
    anchor = str(detail.get("last_outbound_at") or "").strip()
    if not anchor and isinstance(detail.get("wa_message_ids"), list):
        anchor = ",".join(str(item) for item in detail["wa_message_ids"])
    return f"{key}@{anchor}" if anchor else key


def _pending_ack_actionable_refs(receipt):
    if not isinstance(receipt, dict) or not receipt.get("residual"):
        return []
    refs = receipt.get("unresolved_refs")
    if not isinstance(refs, list):
        return []
    return [
        item for item in refs
        if isinstance(item, dict)
        and str(item.get("reason") or "").strip().lower()
        not in {"unrelated", "defer", "snooze"}
    ]

_PENDING_ACK_CLARIFICATION_MARKER_RE = re.compile(
    r"<!--\s*hermes:pending-ack-clarification\s+candidate=([^\s>]+)\s*-->"
)


def _pending_ack_clarification_marker(detail):
    return (
        "<!-- hermes:pending-ack-clarification candidate="
        f"{_pending_ack_candidate_key(detail)} -->"
    )


def _pending_ack_clarification_keys(response):
    return {
        match.group(1)
        for match in _PENDING_ACK_CLARIFICATION_MARKER_RE.finditer(
            str(response or "")
        )
    }


def _strip_pending_ack_clarification_markers(response):
    return _PENDING_ACK_CLARIFICATION_MARKER_RE.sub("", str(response or "")).strip()


def _fresh_pending_ack_snapshot_for_source(source):
    try:
        cfg = _gr()._load_gateway_config()
        path = str((cfg or {}).get("pending_acks_file", "") or "")
        snapshot = _gr()._read_pending_acks(path)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("items"), list):
            return None

        platform = getattr(getattr(source, "platform", None), "value", None)
        platform = platform or str(getattr(source, "platform", "") or "")
        target = snapshot.get("target")
        if not isinstance(target, str) or ":" not in target:
            return None
        target_platform, _, target_user = target.partition(":")
        if target_platform.strip().lower() != platform.strip().lower():
            return None
        target_aliases = _expand_whatsapp_auth_aliases(target_user)
        user_aliases = _expand_whatsapp_auth_aliases(getattr(source, "user_id", "") or "")
        if not target_aliases or not user_aliases or target_aliases.isdisjoint(user_aliases):
            return None

        generated = datetime.fromisoformat(str(snapshot.get("generated_at")))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        generated = generated.astimezone(timezone.utc)
        if datetime.now(timezone.utc) - generated > timedelta(
            minutes=_PENDING_ACKS_MAX_AGE_MINUTES
        ):
            return None
        return snapshot
    except Exception:
        return None


def _filter_pending_ack_residual_plan(plan, source):
    snapshot = _fresh_pending_ack_snapshot_for_source(source)
    if snapshot is None:
        return plan

    open_by_ref = {}
    for item in snapshot["items"]:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind == "event":
            is_open = item.get("current_state") == "active"
        elif kind == "med_intake":
            is_open = item.get("current_state") == "pending"
        else:
            continue
        identity = (str(kind), str(item.get("ref_id")))
        open_by_ref[identity] = open_by_ref.get(identity, False) or is_open

    open_keys = []
    for key in plan.get("candidate_keys") or []:
        kind, _, ref = str(key).partition(":")
        ref_id, _, _ = ref.partition("@")
        if open_by_ref.get((kind, ref_id), False):
            open_keys.append(key)
    if not open_keys:
        return None
    return {**plan, "candidate_keys": open_keys}



def _pending_ack_residual_plan(receipt, response, seen_keys=None):
    """Build one post-turn residual plan from a typed FAM receipt."""
    refs = _pending_ack_actionable_refs(receipt)
    seen = set(seen_keys or ())
    marked = _pending_ack_clarification_keys(response)
    refs = [
        item for item in refs
        if _pending_ack_candidate_key(item) not in seen
        and _pending_ack_candidate_key(item) not in marked
    ]
    if not refs:
        return None
    keys = [_pending_ack_candidate_key(item) for item in refs]
    return {"message": _PENDING_ACK_RESIDUAL, "candidate_keys": keys}


def _pending_ack_candidates(
    snapshot: Any, reply_to_message_id: Optional[str]
) -> Optional[list[dict]]:
    """Return quoted candidates or all open events and pending medications.

    A quoted inbound message selects exactly one matching item and fans out
    pending medication items. An unquoted inbound message selects every active
    event and pending medication from the fresh projection; fam resolves each
    disposition independently and leaves unrelated or ambiguous candidates
    open.
    """
    if not isinstance(snapshot, dict):
        return None
    items = snapshot.get("items")
    if not isinstance(items, list):
        return None
    if not reply_to_message_id:
        event_candidates = [
            item for item in items
            if isinstance(item, dict)
            and item.get("kind") == "event"
            and item.get("current_state") == "active"
        ]
        med_candidates = [
            item for item in items
            if isinstance(item, dict)
            and item.get("kind") == "med_intake"
            and item.get("current_state") == "pending"
        ]
        candidates = event_candidates + med_candidates
        return candidates or None
    matches = []
    for item in snapshot.get("items", []):
        if not isinstance(item, dict):
            continue
        ids = item.get("wa_message_ids")
        if isinstance(ids, list) and reply_to_message_id in ids:
            matches.append(item)
    if len(matches) != 1:
        return None
    candidates = [matches[0]]
    candidates.extend(
        item for item in snapshot.get("items", [])
        if isinstance(item, dict)
        and item.get("kind") == "med_intake"
        and item.get("current_state") == "pending"
        and item is not matches[0]
    )
    return candidates




_PENDING_ACK_RESOLVE_TIMEOUT_SECONDS = 120


def _terminate_pending_ack_process_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (AttributeError, OSError):
        try:
            process.kill()
        except (AttributeError, OSError):
            pass
    try:
        process.wait(timeout=5)
    except (AttributeError, OSError, subprocess.TimeoutExpired):
        pass


def _run_pending_ack_process(argv, request_json, env, cwd, timeout):
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, shell=False, env=env, cwd=cwd,
            start_new_session=True,
        )
        stdout, stderr = process.communicate(input=request_json, timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_pending_ack_process_group(process)
        return subprocess.CompletedProcess(argv, -signal.SIGKILL, "", "timeout")
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
_PENDING_ACK_RECEIPT_SCHEMA_VERSION = 1
_PENDING_ACK_RECEIPT_STATUSES = {"applied", "partial", "unresolved"}
_PENDING_ACK_DISPOSITIONS = {
    "ack_chain_prepare", "ack_chain_all", "cancel_reminders",
    "cancel_occurrence", "taken", "skipped", "unrelated", "ambiguous",
}


def _validate_pending_ack_receipt(receipt, candidates):
    """Accept only a versioned FAM receipt for the requested refs."""
    if not isinstance(receipt, dict):
        return None
    if receipt.get("schema_version") != _PENDING_ACK_RECEIPT_SCHEMA_VERSION:
        return None
    status = receipt.get("status")
    if status not in _PENDING_ACK_RECEIPT_STATUSES:
        return None
    residual = receipt.get("residual")
    if not isinstance(residual, bool):
        return None
    if residual != (status != "applied"):
        return None
    sidecar = receipt.get("trusted_sidecar")
    if sidecar is not None and not isinstance(sidecar, str):
        return None
    requested = {
        (str(item.get("kind")), str(item.get("ref_id")))
        for item in candidates or ()
        if isinstance(item, dict)
    }

    def valid_ref(item, require_disposition=False):
        if not isinstance(item, dict):
            return None
        key = (str(item.get("kind")), str(item.get("ref_id")))
        if key not in requested:
            return None
        if (require_disposition and
                item.get("disposition") not in _PENDING_ACK_DISPOSITIONS):
            return None
        return key

    applied = []
    unresolved = []
    if status == "applied":
        if isinstance(receipt.get("dispositions"), list):
            applied = receipt["dispositions"]
        elif all(field in receipt for field in ("kind", "ref_id", "disposition")):
            applied = [receipt]
        else:
            return None
        if not applied:
            return None
    elif status == "partial":
        applied = receipt.get("applied")
        unresolved = receipt.get("unresolved_refs")
        if not isinstance(applied, list) or not isinstance(unresolved, list):
            return None
        if not applied or not unresolved:
            return None
        if receipt.get("unresolved") != len(unresolved):
            return None
    else:
        unresolved = receipt.get("unresolved_refs")
        if not isinstance(unresolved, list) or not unresolved:
            return None

    seen = set()
    for item in applied:
        key = valid_ref(item, require_disposition=True)
        if key is None or key in seen:
            return None
        seen.add(key)
    for item in unresolved:
        key = valid_ref(item)
        if key is None or key in seen:
            return None
        seen.add(key)
    return receipt


async def _resolve_pending_ack_turn(event, source, snapshot, cfg):
    """Run the FAM prepass and return a receipt; all failures are residual."""
    if _gr()._pending_acks_note(
        snapshot,
        source.platform.value if source.platform else "",
        source.user_id or "",
    ) is None:
        return None
    reply_id = getattr(event, "reply_to_message_id", None)
    candidates = _gr()._pending_ack_candidates(snapshot, reply_id)
    if candidates is None:
        return None
    repo_root = Path(__file__).resolve().parents[1]
    fam_python = Path(_gr()._hermes_home) / "hermes-agent" / "venv" / "bin" / "python"
    if not fam_python.exists():
        fam_python = Path(sys.executable)
    argv = [str(fam_python), "-m", "fam", "resolve", "turn", "--json"]
    env = os.environ.copy()
    fam_path = str(repo_root / "custom" / "fam")
    env["PYTHONPATH"] = fam_path + os.pathsep + env.get("PYTHONPATH", "")
    env["FAM_DB"] = str(cfg.get("fam_db_path") or Path(_gr()._hermes_home) / "private" / "amina" / "assistant.db")
    if cfg.get("fam_config_path"):
        env["FAM_CONFIG"] = str(cfg["fam_config_path"])
    request = {
        "platform": source.platform.value if source.platform else "",
        "canonical_target": snapshot.get("target", ""),
        "inbound_message_id": str(getattr(event, "message_id", "") or ""),
        "reply_to_message_id": reply_id,
        "user_text": str(getattr(event, "text", "") or ""),
        "quoted_text": str(getattr(event, "reply_to_text", "") or ""),
        "candidates": candidates,
    }
    try:
        completed = await asyncio.to_thread(
            _run_pending_ack_process, argv, json.dumps(request, ensure_ascii=False),
            env, str(repo_root), _PENDING_ACK_RESOLVE_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            return {"residual": True, "reason": "fam_nonzero",
                    "unresolved_refs": _pending_ack_unresolved_refs(candidates, "fam_nonzero")}
        receipt = _validate_pending_ack_receipt(
            json.loads((completed.stdout or "").strip()), candidates
        )
        if receipt is None:
            return {"residual": True, "reason": "invalid_receipt",
                    "unresolved_refs": _pending_ack_unresolved_refs(candidates, "invalid_receipt")}
        if receipt.get("residual") and not receipt.get("unresolved_refs"):
            receipt["unresolved_refs"] = _pending_ack_unresolved_refs(
                candidates, receipt.get("reason", "unresolved")
            )
        return receipt
    except Exception as exc:
        logger.debug("pending-ack resolution failed", exc_info=True)
        return {"residual": True, "reason": "fam_failure",
                "unresolved_refs": _pending_ack_unresolved_refs(candidates, "fam_failure")}
def _pending_acks_note(
    snapshot: Any,
    platform: str,
    user_id: str,
    now_utc: Optional[str] = None,
) -> Optional[str]:
    """Render the per-turn note, or None when there is nothing to say.

    Returns None unless the snapshot is well-formed, fresh, non-empty and
    addressed to *this* channel — the assistant's other channels (e.g. the
    admin's Telegram) must never receive someone else's open questions.
    """
    from datetime import timedelta, timezone

    if not isinstance(snapshot, dict):
        return None
    items = snapshot.get("items")
    if not isinstance(items, list) or not items:
        return None

    target = snapshot.get("target")
    if not isinstance(target, str) or ":" not in target:
        return None
    target_platform, _, target_user = target.partition(":")
    if target_platform.strip().lower() != str(platform).strip().lower():
        return None
    target_aliases = _expand_whatsapp_auth_aliases(target_user)
    user_aliases = _expand_whatsapp_auth_aliases(user_id)
    if not target_aliases or not user_aliases or target_aliases.isdisjoint(user_aliases):
        return None

    def _parse(value):
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    try:
        now = _parse(now_utc) if now_utc else datetime.now(timezone.utc)
        generated = _parse(snapshot.get("generated_at"))
    except Exception:
        return None
    if now - generated > timedelta(minutes=_PENDING_ACKS_MAX_AGE_MINUTES):
        return None

    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "event":
            title = str(item.get("title") or "").strip()
            if title:
                lines.append(f"- событие «{title}» (id={item.get('ref_id')}); "
                             f"маркер уточнения: {_pending_ack_clarification_marker(item)}")
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        dose = str(item.get("dose") or "").strip()
        due = str(item.get("due_local") or "").strip()
        ack = str(item.get("ack_cmd") or "").strip()
        skip = str(item.get("skip_cmd") or "").strip()
        head = f"«{name}»" + (f" ({dose})" if dose else "")
        when = f", запланирован на {due}" if due else ""
        how = ""
        if ack and skip:
            how = f" — подтвердила: `{ack}`; пропустила/отказалась: `{skip}`"
        elif ack:
            how = f" — подтвердила: `{ack}`"
        lines.append(f"- приём {head}{when}{how}; "
                     f"маркер уточнения: {_pending_ack_clarification_marker(item)}")

    if not lines:
        return None

    return (
        "[Системная заметка: есть неподтверждённые пункты — пользователь уже "
        "получил напоминание, но ответ ещё не записан:\n"
        + "\n".join(lines)
        + "\nЕсли текущее сообщение подтверждает или отклоняет один из них "
        "(например «готово», «выпила», «приняла», «позже», «не буду»), "
        "закрой его соответствующей командой, прежде чем отвечать. Если "
        "сообщение не про это — не спрашивай о нём заново. Если задаёшь "
        "уточняющий вопрос, добавь соответствующий маркер из пункта выше в "
        "конец ответа; gateway удалит маркер перед отправкой пользователю.]"
    )


# ---------------------------------------------------------------------------------------------
# Call sites (gateway/run_turn_prepare.py, gateway/run_turn.py)
# ---------------------------------------------------------------------------------------------

async def prepare_turn_notes(event: Any, source: Any, turn_sidecar_notes: list) -> Optional[dict]:
    """Add the pending-ack note and the prepass receipt to this turn's sidecar notes.

    Returns the prepass result to finish the reply with, or None. Never raises.
    """
    try:
        try:
            cfg = _gr()._load_gateway_config() or {}
            path = str(cfg.get("pending_acks_file", "") or "")
        except Exception:
            cfg, path = {}, ""
        if not path:
            return None
        snapshot = _gr()._read_pending_acks(path)
        note = _gr()._pending_acks_note(
            snapshot, source.platform.value if source.platform else "", source.user_id or "")
        if note:
            turn_sidecar_notes.append(note)
        prepass = await _gr()._resolve_pending_ack_turn(event, source, snapshot, cfg)
        if prepass:
            if prepass.get("trusted_sidecar"):
                turn_sidecar_notes.append(prepass["trusted_sidecar"])
            return prepass
    except Exception:
        logger.debug("pending-acks note skipped (non-fatal)", exc_info=True)
    return None


async def finish_response(runner: Any, source: Any, session_key: str, residual: Optional[dict],
                          response: str) -> str:
    """Strip clarification markers; schedule (or inline) the residual for unresolved candidates."""
    raw = response
    response = _strip_pending_ack_clarification_markers(response)
    if not residual:
        return response
    seen = runner._session_state(session_key).conversation.pending_ack_residuals
    plan = _pending_ack_residual_plan(residual, raw, seen.keys())
    if plan is not None:
        plan = _filter_pending_ack_residual_plan(plan, source)
    if plan is not None and not await runner._defer_pending_ack_residual_after_delivery(source, session_key, plan):
        response = (response.rstrip() + "\n\n" + _PENDING_ACK_RESIDUAL).strip()
    return response


async def defer_residual_after_delivery(runner: Any, source: Any, session_key: str, plan: dict) -> bool:
    """Schedule one aggregated pending-ack residual after MAIN."""
    adapter = runner._adapter_for_source(source)
    if not adapter:
        return False
    keys = list(plan.get("candidate_keys") or [])
    if not keys or not hasattr(adapter, "register_post_delivery_callback"):
        return False

    async def _deliver() -> None:
        try:
            delivery_plan = _filter_pending_ack_residual_plan(plan, source)
            if delivery_plan is None:
                return
            delivery_keys = list(delivery_plan.get("candidate_keys") or [])
            delivery_adapter = runner._adapter_for_source(source) or adapter
            result = await delivery_adapter.send(
                source.chat_id,
                delivery_plan.get("message") or _PENDING_ACK_RESIDUAL,
                metadata=runner._thread_metadata_for_source(source),
            )
            if result is not None and getattr(result, "success", True):
                state = runner._session_state(session_key).conversation
                for key in delivery_keys:
                    state.pending_ack_residuals[key] = "sent"
        except Exception:
            logger.warning("pending-ack residual delivery failed", exc_info=True)

    try:
        generation = None
        active = getattr(adapter, "_active_sessions", {}).get(session_key)
        if active is not None:
            generation = getattr(active, "_hermes_run_generation", None)
        adapter.register_post_delivery_callback(session_key, _deliver, generation=generation)
        return True
    except Exception:
        logger.debug("pending-ack residual registration failed", exc_info=True)
        return False
