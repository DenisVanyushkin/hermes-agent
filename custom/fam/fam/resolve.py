"""Resolve one inbound turn against FAM's durable acknowledgement state.

The gateway supplies a typed candidate snapshot and this process owns the
classifier call, domain mutation, projection refresh, and postcondition.
Nothing is considered applied until the postcondition is true.
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import signal
from typing import Any

from fam import acks, audit, cal, gate, meds, rem

PROMPT_VERSION = "amina-ack-resolution-s2-v1"
RECEIPT_SCHEMA_VERSION = 1
_EVENT_DISPOSITIONS = {"ack_chain_prepare", "ack_chain_all",
                       "cancel_reminders", "cancel_occurrence",
                       "unrelated", "ambiguous"}
_MED_DISPOSITIONS = {"taken", "skipped", "unrelated", "ambiguous"}


def _json_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _turn_key(request: dict[str, Any], candidate: dict[str, Any] | None = None) -> str:
    parts = [str(request.get(name, "")) for name in (
        "platform", "canonical_target", "inbound_message_id")]
    if isinstance(candidate, dict):
        parts.extend((str(candidate.get("kind", "")), str(candidate.get("ref_id", ""))))
    return "|".join(parts)


def _classifier_prompt(request: dict[str, Any]) -> str:
    payload = {
        "prompt_version": PROMPT_VERSION,
        "candidate": request.get("candidates", []),
        "user_text": str(request.get("user_text") or ""),
        "quoted_text": str(request.get("quoted_text") or ""),
        "instructions": (
            "Return strict JSON only: {\"dispositions\":[{\"kind\":...,'"
            "ref_id':...,\"disposition\":...}]}. Return one independent "
            "disposition per candidate; use unrelated or ambiguous only for "
            "the candidate it describes. Never emit tool calls or commands."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _terminate_process_group(process):
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


def _call_classifier(prompt: str, cfg: dict[str, Any]) -> dict[str, Any] | None:
    command = cfg.get("classifier_command", gate.HERMES)
    if not isinstance(command, list) or not command or not all(
            isinstance(part, str) and part for part in command):
        return None
    argv = command + [
        "-z", prompt,
        "-m", str(cfg.get("gate_model", "")),
        "--provider", str(cfg.get("gate_provider", "")),
        "-t", "clarify",
    ]
    timeout = cfg.get("resolve_classifier_timeout_seconds", 45)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        return None
    timeout = min(float(timeout), 45.0)
    for _attempt in range(2):
        try:
            process = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, shell=False, start_new_session=True,
            )
            stdout, _stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_process_group(process)
            continue
        except OSError:
            continue
        if process.returncode != 0:
            continue
        try:
            output = json.loads((stdout or "").strip())
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(output, dict):
            return output


def _normalize_receipt(receipt):
    if not isinstance(receipt, dict):
        return None
    result = dict(receipt)
    result.setdefault("schema_version", RECEIPT_SCHEMA_VERSION)
    return result


def _load_existing(conn, key: str) -> dict[str, Any] | None:
    try:
        row = conn.execute(
            "SELECT receipt FROM resolve_receipts WHERE idempotency_key=?",
            (key,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is not None:
        try:
            receipt = _normalize_receipt(json.loads(row["receipt"]))
        except (TypeError, json.JSONDecodeError):
            receipt = None
        if receipt is not None:
            return receipt
    rows = conn.execute(
        "SELECT payload FROM audit_log "
        "WHERE kind='resolve.turn' "
        "AND CASE WHEN json_valid(payload) "
        "THEN json_extract(payload, '$.idempotency_key') END=? "
        "ORDER BY id ASC",
        (key,),
    ).fetchall()
    receipts = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        if payload.get("idempotency_key") == key:
            receipt = payload.get("receipt")
            normalized = _normalize_receipt(receipt)
            if normalized is not None:
                receipts.append(normalized)
    if not receipts:
        return None
    return receipts[0] if len(receipts) == 1 else _aggregate_receipts(receipts)


def _store_receipt(conn, key, candidate, receipt, created_at=""):
    conn.execute(
        "INSERT INTO resolve_receipts(idempotency_key, kind, ref_id, "
        "receipt, created_at) VALUES(?,?,?,?,?)",
        (key, candidate.get("kind"), int(candidate["ref_id"]),
         json.dumps(receipt, ensure_ascii=False, sort_keys=True),
         created_at),
    )
def _unresolved(conn, key, candidate, reason, input_hash=None, output_hash=None,
                model=None):
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": "unresolved", "residual": True, "reason": reason,
        "kind": candidate.get("kind"), "ref_id": candidate.get("ref_id"),
        "disposition": None,
    }
    for field in ("title", "name", "last_outbound_at", "wa_message_ids"):
        if field in candidate:
            receipt[field] = candidate[field]
    audit.log(conn, "resolve.turn", {
        "idempotency_key": key, "kind": candidate.get("kind"),
        "ref_id": candidate.get("ref_id"), "disposition": None,
        "model": model, "prompt_version": PROMPT_VERSION,
        "input_sha256": input_hash, "output_sha256": output_hash,
        "postcondition": False, "receipt": receipt,
    })
    audit.log(conn, "unresolved_after_turn", {
        "idempotency_key": key, "kind": candidate.get("kind"),
        "ref_id": candidate.get("ref_id"), "reason": reason,
    })
    conn.commit()
    return receipt


def _strict_dispositions(output, candidates):
    """Validate model output independently against each candidate ref."""
    result = {}
    if not isinstance(output, dict) or set(output) != {"dispositions"}:
        return result
    values = output["dispositions"]
    if not isinstance(values, list):
        return result
    candidate_map = {
        (str(candidate.get("kind")), str(candidate.get("ref_id"))): candidate
        for candidate in candidates
    }
    for value in values:
        if not isinstance(value, dict) or set(value) != {"kind", "ref_id", "disposition"}:
            continue
        key = (str(value["kind"]), str(value["ref_id"]))
        candidate = candidate_map.get(key)
        if candidate is None or key in result:
            continue
        allowed = _MED_DISPOSITIONS if candidate.get("kind") == "med_intake" else _EVENT_DISPOSITIONS
        result[key] = value["disposition"] if value["disposition"] in allowed else None
    return result


def _receipt(candidate, disposition):
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": "applied", "residual": False,
        "kind": candidate.get("kind"), "ref_id": candidate.get("ref_id"),
        "disposition": disposition,
        "trusted_sidecar": (
            f"[Trusted FAM resolution: {candidate.get('kind')} "
            f"{candidate.get('ref_id')} applied and verified; do not repeat.]"
        ),
    }


def _aggregate_receipts(receipts):
    applied = [item for item in receipts if item.get("status") == "applied"]
    unresolved = [item for item in receipts if item.get("residual")]
    unresolved_refs = []
    for item in unresolved:
        detail = {
            key: item[key] for key in ("kind", "ref_id", "reason")
            if key in item
        }
        for key in ("title", "name", "last_outbound_at", "wa_message_ids"):
            if key in item:
                detail[key] = item[key]
        unresolved_refs.append(detail)
    if applied and not unresolved:
        return {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "status": "applied", "residual": False,
            "dispositions": [
                {key: item[key] for key in ("kind", "ref_id", "disposition")}
                for item in applied
            ],
            "trusted_sidecar": "\n".join(item["trusted_sidecar"] for item in applied),
        }
    if applied:
        result = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "status": "partial", "residual": True,
            "applied": [
                {key: item[key] for key in ("kind", "ref_id", "disposition")}
                for item in applied
            ],
            "unresolved": len(unresolved),
            "unresolved_refs": unresolved_refs,
        }
        sidecars = [item["trusted_sidecar"] for item in applied
                    if item.get("trusted_sidecar")]
        if sidecars:
            result["trusted_sidecar"] = "\n".join(sidecars)
        return result
    return {"schema_version": RECEIPT_SCHEMA_VERSION, "status": "unresolved", "residual": True, "unresolved": len(unresolved), "unresolved_refs": unresolved_refs}


def _apply(conn, candidate, disposition, request):
    kind = candidate.get("kind")
    ref_id = int(candidate["ref_id"])
    if kind == "event":
        event = cal.get(conn, ref_id)
        if event is None or event["owner"] != "hermes":
            return False, "owner_not_hermes"
        if disposition == "ack_chain_prepare":
            rem.ack_chain(conn, ref_id, scope="prepare")
        elif disposition == "ack_chain_all":
            rem.ack_chain(conn, ref_id, scope="all")
        elif disposition == "cancel_reminders":
            rem.cancel_chain(conn, ref_id)
        elif disposition == "cancel_occurrence":
            cal.cancel(conn, ref_id)
        else:
            return False, "non_terminal"
    elif kind == "med_intake":
        if disposition == "taken":
            meds.take(conn, ref_id)
        elif disposition == "skipped":
            meds.skip(conn, ref_id)
        else:
            return False, "non_terminal"
    else:
        return False, "unknown_kind"
    return True, None


def _postcondition(conn, candidate, disposition):
    kind = candidate.get("kind")
    ref_id = int(candidate["ref_id"])
    if kind == "event":
        event = cal.get(conn, ref_id)
        if disposition == "cancel_occurrence":
            return bool(event and event["status"] == "cancelled")
        if disposition in {"cancel_reminders", "ack_chain_prepare", "ack_chain_all"}:
            if disposition == "ack_chain_prepare":
                row = conn.execute(
                    "SELECT COUNT(*) FROM reminders WHERE event_id=? AND kind='prepare' AND status='pending'",
                    (ref_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM reminders WHERE event_id=? AND status='pending'",
                    (ref_id,),
                ).fetchone()
            return row[0] == 0
    if kind == "med_intake":
        row = conn.execute("SELECT status FROM med_intakes WHERE id=?", (ref_id,)).fetchone()
        expected = {"taken": "taken", "skipped": "skipped"}.get(disposition)
        return bool(row and expected == row["status"])
    return False


def _set_terminal_ack(conn, candidate, disposition):
    ids = candidate.get("wa_message_ids") or []
    status = "confirmed" if disposition in {"ack_chain_prepare", "ack_chain_all", "taken"} else "skipped"
    for message_id in ids:
        conn.execute(
            "UPDATE sent_messages SET ack_status=? WHERE wa_message_id=? AND ack_status='none'",
            (status, message_id),
        )


def resolve_turn(conn, request, cfg=None):
    cfg = cfg or {}
    candidates = request.get("candidates")
    if (not isinstance(candidates, list) or not candidates or
            not all(isinstance(candidate, dict) for candidate in candidates)):
        candidate = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else {}
        return _unresolved(
            conn, _turn_key(request, candidate), candidate, "invalid_candidates",
            input_hash=_json_hash(request), model=cfg.get("gate_model"),
        )
    existing_receipts = []
    pending_candidates = []
    for candidate in candidates:
        candidate_key = _turn_key(request, candidate)
        existing = _load_existing(conn, candidate_key)
        if existing is None:
            pending_candidates.append(candidate)
        else:
            existing_receipts.append(existing)
    if not pending_candidates:
        return _aggregate_receipts(existing_receipts)
    classify_request = dict(request)
    classify_request["candidates"] = pending_candidates
    prompt = _classifier_prompt(classify_request)
    input_hash = _json_hash(request)
    output = _call_classifier(prompt, cfg)
    output_hash = _json_hash(output) if output is not None else None
    dispositions = _strict_dispositions(output, candidates) if output is not None else {}
    results = list(existing_receipts)
    for candidate in pending_candidates:
        key = _turn_key(request, candidate)
        candidate_key = (str(candidate.get("kind")), str(candidate.get("ref_id")))
        disposition = dispositions.get(candidate_key)
        if output is None:
            reason = "classifier_failure"
        elif disposition is None:
            reason = "invalid_disposition"
        elif disposition in {"unrelated", "ambiguous"}:
            reason = disposition
        else:
            receipt = None
            try:
                conn.execute("BEGIN IMMEDIATE")
                existing = _load_existing(conn, key)
                if existing is not None:
                    conn.rollback()
                    results.append(existing)
                    continue
                changed, reason = _apply(conn, candidate, disposition, request)
                if not changed or not _postcondition(conn, candidate, disposition):
                    conn.rollback()
                    reason = reason or "postcondition_failed"
                else:
                    _set_terminal_ack(conn, candidate, disposition)
                    receipt = _receipt(candidate, disposition)
                    audit.log(conn, "resolve.turn", {
                        "idempotency_key": key, "kind": candidate.get("kind"),
                        "ref_id": candidate.get("ref_id"), "event_id": candidate.get("event_id"),
                        "disposition": disposition, "model": cfg.get("gate_model"),
                        "prompt_version": PROMPT_VERSION, "input_sha256": input_hash,
                        "output_sha256": output_hash, "postcondition": True,
                        "receipt": receipt,
                    })
                    _store_receipt(
                        conn, key, candidate, receipt,
                        created_at=request.get("now_utc") or "",
                    )
                    conn.commit()
            except Exception as exc:  # noqa: BLE001 -- every ref is residual
                conn.rollback()
                receipt = None
                reason = f"effect_failed:{type(exc).__name__}"
            if receipt is not None:
                acks.write(conn, cfg=cfg, now_utc=request.get("now_utc"))
                results.append(receipt)
                continue
        results.append(_unresolved(
            conn, key, candidate, reason, input_hash, output_hash,
            model=cfg.get("gate_model"),
        ))

    return _aggregate_receipts(results)
