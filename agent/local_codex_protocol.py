"""Fork: text-bound tool intent on the Codex Responses API -- recover once, never leak.

gpt-5.x sometimes emits a tool call as assistant *text* in the commentary or
analysis channel (``<|start|>assistant<|channel|>commentary to=functions.X
<|constrain|>json ...`` or ``<tool_call>...</tool_call>``) instead of a
structured ``function_call`` item. No tool runs, and the raw protocol text used
to reach the user as an interim message.

* Stream: ``ProtocolStreamGuard`` holds commentary/analysis deltas while they
  can still become a registered envelope; disproven text is released to the
  normal channel handling unchanged, confirmed protocol text is never emitted
  -- only a SHA-256 fingerprint is kept (agent/malformed_tool_intent.py does the
  detection; nothing is parsed into an executable call).
* Turn: ``handle_malformed_tool_intent`` re-asks for a native call ONCE per
  user turn; a second text-bound intent ends the turn with
  MALFORMED_TOOL_INTENT_REPEATED instead of looping.
* Persistence: the invalid assistant carrier is kept as an auditable
  ``protocol_invalid`` row (content and reasoning blanked, Codex items kept for
  RCA) and is never replayed as an answer.

Moved from the pre-v0.22.5 fork (series 2026-08-24); upstream's own leak
recovery for final-answer text (``to=functions.``, shell JSON) stays in place.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import replace
from typing import Any, Dict, Iterable, List, Optional

from agent.malformed_tool_intent import (
    MAX_INSPECTED_TEXT_CHARS,
    MalformedToolIntent,
    detect_malformed_tool_intent,
)

logger = logging.getLogger("agent.conversation_loop")

MALFORMED_TOOL_INTENT_RECOVERY = (
    "[System: Your previous response expressed a tool request as assistant text. "
    "No tool was executed. If the action is still required, emit the native structured "
    "function call now. Do not print tool-call syntax or describe the action.]"
)
#: Ephemeral-scaffolding flag on the recovery prompt (never persisted, never replayed).
RECOVERY_FLAG = "_malformed_tool_intent_recovery"
_EVIDENCE_KEYS = ("tool_name", "source_phase", "format", "fingerprint")
_GUARDED_PHASES = frozenset({"commentary", "analysis"})


def bounded_intent(intent: Any) -> Optional[Dict[str, str]]:
    """The bounded evidence contract: four strings, never arguments or raw text."""
    if intent is None:
        return None
    get = intent.get if isinstance(intent, dict) else (lambda name, default=None: getattr(intent, name, default))
    return {key: str(get(key, "") or "") for key in _EVIDENCE_KEYS}


# ── stream ──────────────────────────────────────────────────────────────────

def _prefix_could_be_envelope(text: str, *, phase: str, valid_tool_names: set[str]) -> bool:
    """True while *text* can still become a registered ChatML/XML envelope."""
    candidate = text.lstrip().casefold()
    if not candidate:
        return True
    xml = "<tool_call>"
    if xml.startswith(candidate) or candidate.startswith(xml):
        return True
    header = f"<|start|>assistant<|channel|>{phase} to=functions."
    return any(
        (prefix := f"{header}{name}<|constrain|>json".casefold()).startswith(candidate)
        or candidate.startswith(prefix)
        for name in valid_tool_names
    )


class ProtocolStreamGuard:
    """Per-stream guard for commentary/analysis message text.

    ``feed`` returns the text pieces that are now known to be ordinary and may
    be handled exactly as before (possibly several, when a held prefix is
    released), or ``None`` while text is held or suppressed.
    """

    def __init__(self, valid_tool_names: Optional[Iterable[str]]):
        self.valid_tool_names = {str(n) for n in (valid_tool_names or ())}
        self.malformed_tool_intent: Optional[MalformedToolIntent] = None
        self._reset(None)

    @property
    def enabled(self) -> bool:
        return bool(self.valid_tool_names)

    def _reset(self, phase: Optional[str]) -> None:
        self.phase = phase if (phase in _GUARDED_PHASES and self.enabled) else None
        self.state = "candidate" if self.phase else "off"
        self.held: List[str] = []
        self.held_chars = 0
        self.hash = hashlib.sha256()
        self.saw_delta = False
        self.intent: Optional[MalformedToolIntent] = None

    def start_message(self, phase: Optional[str]) -> None:
        self._reset(phase)

    def feed(self, delta: str) -> Optional[List[str]]:
        if self.state == "off":
            return [delta]
        self.saw_delta = True
        self.hash.update(delta.encode("utf-8"))
        if self.state in ("malformed", "oversized"):
            return None  # keep hashing for the fingerprint, never retain or emit
        if self.state == "ordinary":
            return [delta]
        remaining = MAX_INSPECTED_TEXT_CHARS - self.held_chars
        inspected, overflow = (delta[:remaining], delta[remaining:]) if remaining > 0 else ("", delta)
        if inspected:
            self.held.append(inspected)
            self.held_chars += len(inspected)
        text = "".join(self.held)
        intent = detect_malformed_tool_intent(text, phase=self.phase, valid_tool_names=self.valid_tool_names)
        if intent is not None:
            self.state, self.intent, self.held = "malformed", intent, []
            return None
        if _prefix_could_be_envelope(text, phase=self.phase, valid_tool_names=self.valid_tool_names):
            if self.held_chars >= MAX_INSPECTED_TEXT_CHARS:
                self.state, self.held = "oversized", []
                self.intent = MalformedToolIntent(
                    tool_name="unknown", source_phase=self.phase,
                    format="oversized_protocol_candidate", fingerprint="",
                )
                return None
            return None  # still a possible envelope: hold
        self.state = "ordinary"
        released = self.held + ([overflow] if overflow else [])
        self.held = []
        return released

    def finish_message(self, done_text: str) -> tuple[bool, List[str]]:
        """At the message's done event: ``(suppress, release)``.

        ``suppress`` -- the message was a text-bound tool envelope; the caller
        must not surface it. ``release`` -- a harmless prefix still held at the
        end, to be handled as ordinary text now.
        """
        if self.state == "off":
            return False, []
        intent = self.intent
        if intent is None and not self.saw_delta and done_text:
            # Some backends deliver the text only on the done item.
            self.hash.update(done_text.encode("utf-8"))
            intent = detect_malformed_tool_intent(
                done_text, phase=self.phase, valid_tool_names=self.valid_tool_names)
        if intent is not None:
            self.malformed_tool_intent = replace(intent, fingerprint="sha256:" + self.hash.hexdigest())
            logger.debug("codex stream: suppressed text-bound tool intent (%s)", intent.format)
            self._reset(None)
            return True, []
        release = self.held if self.state == "candidate" else []
        self._reset(None)
        return False, release


# ── turn ────────────────────────────────────────────────────────────────────

def handle_malformed_tool_intent(agent: Any, assistant_message: Any, messages: list,
                                 conversation_history: Any, api_call_count: int) -> Optional[Any]:
    """The turn-level policy. Returns None (nothing to do), ``"continue"`` (the
    recovery prompt was queued: re-issue the call) or a result dict (end the turn).

    The recovery window is scoped to the whole user turn, ordinary incomplete
    continuations included: a clean non-native response cannot reopen it. A
    native structured call closes the episode.
    """
    from agent.message_metadata import append_message

    intent = getattr(assistant_message, "malformed_tool_intent", None)
    if getattr(assistant_message, "tool_calls", None):
        agent._malformed_tool_intent_recovery_used = False
        return None
    if intent is None:
        return None
    evidence = bounded_intent(intent)
    attempt = 2 if getattr(agent, "_malformed_tool_intent_recovery_used", False) else 1
    agent._malformed_tool_intent_recovery_used = True
    logger.warning(
        "event=malformed_tool_intent provider=%s api_mode=%s model=%s tool_name=%s source_phase=%s "
        "format=%s fingerprint=%s recovery_attempt=%d",
        agent.provider, agent.api_mode, agent.model, evidence["tool_name"], evidence["source_phase"],
        evidence["format"], evidence["fingerprint"], attempt,
    )
    if attempt == 1:
        # Keep the provider's opaque Codex item sidecar available to the retry; retain the
        # invalid carrier as an auditable non-final row and make only the prompt ephemeral,
        # so protocol text can never become a durable assistant answer.
        carrier = agent._build_assistant_message(assistant_message, "incomplete")
        carrier["_protocol_invalid"] = True
        append_message(messages, carrier)
        append_message(messages, {"role": "user", "content": MALFORMED_TOOL_INTENT_RECOVERY, RECOVERY_FLAG: True})
        agent._session_messages = messages
        emit = getattr(agent, "_emit_wait_notice", None) or getattr(agent, "_emit_diagnostic_status", None)
        if callable(emit):
            emit("↻ Provider emitted a tool request as text; requesting a native call (1/1)")
        return "continue"

    while messages and isinstance(messages[-1], dict) and messages[-1].get(RECOVERY_FLAG):
        messages.pop()
    carrier = agent._build_assistant_message(assistant_message, "incomplete")
    carrier["_protocol_invalid"] = True
    append_message(messages, carrier)
    agent._session_messages = messages
    agent._persist_session(messages, conversation_history)
    return {
        "final_response": "The provider emitted a tool request as text twice. No action was executed. "
                          "Please try again.",
        "messages": messages,
        "api_calls": api_call_count,
        "completed": False,
        "partial": False,
        "failed": True,
        "error": "MALFORMED_TOOL_INTENT_REPEATED",
        "turn_exit_reason": "malformed_tool_intent_repeated",
    }


# ── persistence and replay ──────────────────────────────────────────────────

def _bounded_evidence(value: Any) -> Optional[Dict[str, str]]:
    if not isinstance(value, dict):
        return None
    evidence = {key: value[key] for key in _EVIDENCE_KEYS if isinstance(value.get(key), str)}
    return evidence or None


def is_protocol_invalid_assistant_record(message: Dict[str, Any]) -> bool:
    """Recognize both in-memory and durable invalid assistant carriers."""
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return False
    if message.get("_protocol_invalid") or "malformed_tool_intent" in message:
        return True
    if message.get("display_kind") == "protocol_invalid":
        return True
    display_metadata = message.get("display_metadata")
    return isinstance(display_metadata, dict) and (
        display_metadata.get("protocol_invalid") is True
        or isinstance(display_metadata.get("malformed_tool_intent"), dict)
    )


def project_protocol_invalid_persistence(message: Dict[str, Any]) -> Dict[str, Any]:
    """Make an invalid assistant row safe for durable persistence (a copy; else the same dict).

    The raw provider carrier stays only in the Codex item sidecar; content and
    reasoning columns are blank and metadata is reduced to the bounded evidence.
    """
    if not is_protocol_invalid_assistant_record(message):
        return message
    projected = dict(message)
    for key in ("reasoning", "reasoning_content", "reasoning_details", "codex_reasoning_items", "api_content"):
        projected[key] = None
    projected["content"] = ""
    projected["display_kind"] = "protocol_invalid"
    evidence = _bounded_evidence(message.get("malformed_tool_intent"))
    if evidence is None and isinstance(message.get("display_metadata"), dict):
        evidence = _bounded_evidence(message["display_metadata"].get("malformed_tool_intent"))
    metadata: Dict[str, Any] = {"protocol_invalid": True}
    if evidence is not None:
        metadata["malformed_tool_intent"] = evidence
    projected["display_metadata"] = metadata
    return projected


def strip_protocol_invalid_assistant_records(agent_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Exclude provider-invalid assistant turns from model replay (same list when none)."""
    if not agent_history or not any(is_protocol_invalid_assistant_record(m) for m in agent_history):
        return agent_history
    return [m for m in agent_history if not is_protocol_invalid_assistant_record(m)]
