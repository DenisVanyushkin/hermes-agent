"""Resolve the immutable task executed by the engineering pipeline.

Transport enrichment and generic bounded conversation context are useful for
routing, but neither is an approval store.  This module keeps the operator's
current instruction separate from the concrete task selected from canonical
same-session dialogue.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "engineering_task_envelope.v1"
MAX_APPROVED_TASK_CHARS = 32 * 1024

_CONTINUATION_PATTERNS = (
    re.compile(
        r"^\s*(?:(?:ок|да|давай)\s*[,,:-]?\s*)?"
        r"(?:пусть|пускай)\s+инженер\s+"
        r"(?:исполняет|выполняет|делает|бер[её]т)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:(?:ок|да|давай)\s*[,,:-]?\s*)?"
        r"(?:передай|отдай)\s+(?:это\s+)?инженеру\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:(?:ок|да|давай)\s*[,,:-]?\s*)?"
        r"(?:инженер|engineering)\s*[,,:-]?\s*"
        r"(?:исполняй|выполняй|execute|proceed)\b",
        re.IGNORECASE,
    ),
)
_PLAN_REQUEST_RE = re.compile(r"\bплан\w*\b", re.IGNORECASE)
_ENGINEERING_PLAN_HEADING_RE = re.compile(
    r"^[ \t]{0,3}#{0,3}[ \t]*(?:план|задача)\s+"
    r"(?:для\s+)?(?:инженер\w*|engineering)\b",
    re.IGNORECASE | re.MULTILINE,
)
_PLAN_HANDOFF_READY_RE = re.compile(
    r"\bесли\s+подтвержда\w*\s*[—–-]\s*передам\s+именно\s+"
    r"эту\s+версию,\s+а\s+не\s+предыдущ\w+\s+картонн\w+\s+скелет\w*",
    re.IGNORECASE,
)
_DIRECT_REQUEST_RE = re.compile(
    r"^\s*(?:(?:ок|да|давай|можешь|нужно|надо|please|can\s+you)\s*[,,:-]?\s*)?"
    r"(?:исправ(?:ь|ить)|почин(?:и|ить)|реализ(?:уй|овать)|добав(?:ь|ить)|"
    r"удал(?:и|ить)|измен(?:и|ить)|провер(?:ь|ить)|расслед(?:уй|овать)|"
    r"разбер(?:ись|аться)|обнов(?:и|ить)|перепиш(?:и|ать)|созд(?:ай|ать)|"
    r"сдел(?:ай|ать)|запуст(?:и|ить)|выполн(?:и|ить)|"
    r"implement|fix|debug|investigate|update|create|run)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EngineeringTaskEnvelope:
    schema_version: str
    resolution_status: str
    source_kind: str
    task_text: str | None
    operator_instruction: str
    source_session_id: str
    source_message_id: str | None
    task_sha256: str | None
    task_chars: int

    @property
    def resolved(self) -> bool:
        return self.resolution_status == "resolved" and bool(self.task_text)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_engineering_execution_continuation(text: str) -> bool:
    normalized = " ".join(str(text or "").split())
    return any(pattern.search(normalized) for pattern in _CONTINUATION_PATTERNS)


def is_concrete_engineering_request(text: str) -> bool:
    return bool(_DIRECT_REQUEST_RE.search(str(text or "")))


def _message_text(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        content = " ".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, Mapping)
        )
    return str(content or "")


def _plan_candidates(
    history: Iterable[Mapping[str, Any]],
    *,
    session_id: str,
) -> list[tuple[Mapping[str, Any], str, int]]:
    dialogue = [
        message
        for message in history
        if isinstance(message, Mapping)
        and str(message.get("role") or "") in {"user", "assistant"}
        and _message_text(message).strip()
    ]
    candidates: list[tuple[Mapping[str, Any], str, int]] = []
    last_user_text = ""
    user_turn = 0
    for message in dialogue:
        role = str(message.get("role") or "")
        text = _message_text(message)
        if role == "user":
            last_user_text = text
            user_turn += 1
            continue
        requested_plan = bool(_PLAN_REQUEST_RE.search(last_user_text))
        task_metadata = message.get("_engineering_task")
        typed_ready = bool(
            isinstance(task_metadata, Mapping)
            and task_metadata.get("status") == "ready_for_approval"
        )
        response_looks_like_plan = bool(
            _ENGINEERING_PLAN_HEADING_RE.search(text)
            and (typed_ready or _PLAN_HANDOFF_READY_RE.search(text))
        )
        if requested_plan and response_looks_like_plan:
            candidates.append((message, text, user_turn))
    return candidates


def resolve_engineering_task_context(
    *,
    operator_instruction: str,
    history: Iterable[Mapping[str, Any]] | None,
    session_id: str,
    history_session_id: str | None = None,
    enriched_message: str | None = None,
) -> EngineeringTaskEnvelope:
    """Resolve a direct task or the latest approved plan from canonical history.

    ``enriched_message`` is intentionally not inspected.  It may contain a
    Slack thread-parent quote or attachment notes and is not operator intent.
    """

    del enriched_message
    instruction = str(operator_instruction or "")
    if not is_engineering_execution_continuation(instruction):
        if not is_concrete_engineering_request(instruction):
            return EngineeringTaskEnvelope(
                schema_version=SCHEMA_VERSION,
                resolution_status="not_engineering_task",
                source_kind="direct_request",
                task_text=None,
                operator_instruction=instruction,
                source_session_id=str(session_id or ""),
                source_message_id=None,
                task_sha256=None,
                task_chars=0,
            )
        return EngineeringTaskEnvelope(
            schema_version=SCHEMA_VERSION,
            resolution_status="resolved",
            source_kind="direct_request",
            task_text=instruction,
            operator_instruction=instruction,
            source_session_id=str(session_id or ""),
            source_message_id=None,
            task_sha256=_sha256(instruction),
            task_chars=len(instruction),
        )

    canonical_session_id = str(session_id or "")
    if str(history_session_id or "") != canonical_session_id:
        return EngineeringTaskEnvelope(
            schema_version=SCHEMA_VERSION,
            resolution_status="history_session_mismatch",
            source_kind="approved_plan",
            task_text=None,
            operator_instruction=instruction,
            source_session_id=canonical_session_id,
            source_message_id=None,
            task_sha256=None,
            task_chars=0,
        )
    for message in history or []:
        if not isinstance(message, Mapping):
            continue
        row_session_id = str(message.get("session_id") or "").strip()
        if row_session_id and row_session_id != canonical_session_id:
            return EngineeringTaskEnvelope(
                schema_version=SCHEMA_VERSION,
                resolution_status="history_session_mismatch",
                source_kind="approved_plan",
                task_text=None,
                operator_instruction=instruction,
                source_session_id=canonical_session_id,
                source_message_id=None,
                task_sha256=None,
                task_chars=0,
            )

    candidates = _plan_candidates(history or [], session_id=canonical_session_id)
    if not candidates:
        return EngineeringTaskEnvelope(
            schema_version=SCHEMA_VERSION,
            resolution_status="missing_approved_plan",
            source_kind="approved_plan",
            task_text=None,
            operator_instruction=instruction,
            source_session_id=str(session_id or ""),
            source_message_id=None,
            task_sha256=None,
            task_chars=0,
        )

    if len(candidates) >= 2 and candidates[-1][2] == candidates[-2][2]:
        return EngineeringTaskEnvelope(
            schema_version=SCHEMA_VERSION,
            resolution_status="ambiguous_approved_plan",
            source_kind="approved_plan",
            task_text=None,
            operator_instruction=instruction,
            source_session_id=str(session_id or ""),
            source_message_id=None,
            task_sha256=None,
            task_chars=0,
        )

    message, task_text, _request_turn = candidates[-1]
    task_hash = _sha256(task_text)
    source_message_id = message.get("id") or message.get("platform_message_id")
    if len(task_text) > MAX_APPROVED_TASK_CHARS:
        return EngineeringTaskEnvelope(
            schema_version=SCHEMA_VERSION,
            resolution_status="approved_task_too_large",
            source_kind="approved_plan",
            task_text=None,
            operator_instruction=instruction,
            source_session_id=str(session_id or ""),
            source_message_id=(
                str(source_message_id) if source_message_id is not None else None
            ),
            task_sha256=task_hash,
            task_chars=len(task_text),
        )

    return EngineeringTaskEnvelope(
        schema_version=SCHEMA_VERSION,
        resolution_status="resolved",
        source_kind="approved_plan",
        task_text=task_text,
        operator_instruction=instruction,
        source_session_id=str(session_id or ""),
        source_message_id=(
            str(source_message_id) if source_message_id is not None else None
        ),
        task_sha256=task_hash,
        task_chars=len(task_text),
    )
