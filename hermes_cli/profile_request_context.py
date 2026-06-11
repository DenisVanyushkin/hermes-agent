"""Helpers for extracting classification-only user intent text."""

from __future__ import annotations

import re


_CRON_ROLE_ROUTING_BANNER_RE = re.compile(
    r'^\[important:\s+you are running as a scheduled cron job\..*?nothing more\.\]\s*',
    re.IGNORECASE | re.DOTALL,
)

_THREAD_CONTEXT_START_PREFIXES = ("[thread context",)
_THREAD_CONTEXT_END_PREFIX = "[end of thread context]"
_THREAD_CONTEXT_LINE_PREFIXES = (
    "[replying to:",
    "[thread parent]",
    "[thread reply]",
)
_CRONJOB_RESPONSE_PREFIX = "cronjob response:"
_SEPARATOR_RE = re.compile(r"^-{3,}\s*$")
_BRACKETED_SPEAKER_RE = re.compile(r"^\[[^\]\n]{1,80}\]\s*")
_APPROVAL_CONSTRAINT_PREFIXES = (
    "do not",
    "don't",
    "dont",
    "do not read",
    "do not print",
    "do not run",
    "no ",
    "не ",
    "не",
)
_APPROVAL_GRANT_RE = re.compile(
    r"^(?:approve|approved|yes(?:[, ]+proceed)?|proceed|go ahead|разрешаю|одобряю|да[, ]+(?:выполняй|делай))[.! ]*$",
    re.IGNORECASE,
)


def routing_request_text(task: str) -> str:
    """Return routing text with safe reply pointers but without evidence bodies."""

    if not isinstance(task, str) or not task.strip():
        return ""

    stripped = _CRON_ROLE_ROUTING_BANNER_RE.sub("", task.lstrip(), count=1).strip()
    if not stripped:
        return ""

    kept: list[str] = []
    in_thread_context = False
    in_cron_response = False

    for raw_line in stripped.splitlines():
        line = raw_line.rstrip()
        compact = line.strip()
        lower = compact.lower()

        if in_thread_context:
            if lower.startswith(_THREAD_CONTEXT_END_PREFIX):
                in_thread_context = False
            continue

        if in_cron_response:
            if not compact or _SEPARATOR_RE.match(compact) or lower.startswith(_THREAD_CONTEXT_END_PREFIX):
                in_cron_response = False
            continue

        if any(lower.startswith(prefix) for prefix in _THREAD_CONTEXT_START_PREFIXES):
            in_thread_context = True
            continue

        if lower.startswith(_CRONJOB_RESPONSE_PREFIX):
            in_cron_response = True
            continue

        if lower.startswith("[thread parent]") or lower.startswith("[thread reply]"):
            continue

        kept.append(line)

    cleaned = "\n".join(kept).strip()
    return cleaned or stripped


def classification_request_text(task: str) -> str:
    """Return the latest actionable user instruction for routing/classification.

    Reply pointers, thread context, and cron/report evidence remain available in
    the full prompt, but they should not drive approval or role selection.
    """

    if not isinstance(task, str) or not task.strip():
        return ""

    stripped = _CRON_ROLE_ROUTING_BANNER_RE.sub("", task.lstrip(), count=1).strip()
    if not stripped:
        return ""

    kept: list[str] = []
    in_thread_context = False
    in_cron_response = False

    for raw_line in stripped.splitlines():
        line = raw_line.rstrip()
        compact = line.strip()
        lower = compact.lower()

        if in_thread_context:
            if lower.startswith(_THREAD_CONTEXT_END_PREFIX):
                in_thread_context = False
            continue

        if in_cron_response:
            if not compact or _SEPARATOR_RE.match(compact) or lower.startswith(_THREAD_CONTEXT_END_PREFIX):
                in_cron_response = False
            continue

        if any(lower.startswith(prefix) for prefix in _THREAD_CONTEXT_START_PREFIXES):
            in_thread_context = True
            continue

        if any(lower.startswith(prefix) for prefix in _THREAD_CONTEXT_LINE_PREFIXES):
            continue

        if lower.startswith(_CRONJOB_RESPONSE_PREFIX):
            in_cron_response = True
            continue

        kept.append(line)

    cleaned = "\n".join(kept).strip()
    return cleaned or stripped


def approval_intent_text(task: str) -> str:
    """Return the latest user approval utterance without quoted/reply context."""

    cleaned = classification_request_text(task)
    if not cleaned:
        return ""

    kept: list[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("```"):
            continue
        if line.startswith(">"):
            continue
        if line.lower().startswith("[replying to:"):
            continue
        line = _BRACKETED_SPEAKER_RE.sub("", line, count=1).strip()
        if not line:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def approval_constraints_text(task: str) -> list[str]:
    """Extract narrowing constraints from the latest approval utterance."""

    cleaned = approval_intent_text(task)
    if not cleaned:
        return []

    constraints: list[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip().lstrip("-* ").strip()
        if not line:
            continue
        lower = line.lower()
        if any(lower.startswith(prefix) for prefix in _APPROVAL_CONSTRAINT_PREFIXES):
            constraints.append(line)
    return constraints


def has_explicit_approval(task: str) -> bool:
    """Return True when the latest user utterance clearly grants approval."""

    cleaned = approval_intent_text(task)
    if not cleaned:
        return False
    first_line = cleaned.splitlines()[0].strip()
    return bool(_APPROVAL_GRANT_RE.match(first_line))
