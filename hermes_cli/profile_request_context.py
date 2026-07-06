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
# Runtime-collected data injected by cron/scheduler.py:_build_job_prompt().
# These sections carry script stdout / upstream-job output whose content
# (log signatures like "image_gen ...") must never drive role selection —
# see the 2026-07-06 morning-diagnostics artist misroute.
_INJECTED_DATA_SECTION_PREFIXES = (
    "## script output",
    "## script error",
    "## output from job",
)
# Deterministic role pin injected from a cron job's `role` field.
_ROLE_PIN_LINE_RE = re.compile(
    r"^\[role pin:\s*([a-z][a-z0-9_]{0,63})\]\s*$",
    re.IGNORECASE,
)
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


def _is_reply_quote_line(line: str) -> bool:
    return line.strip().lower().startswith("[replying to:")


def _reply_quote_region_closed(line: str) -> bool:
    return line.strip().endswith('"]')


def _strip_leading_reply_quote_region(text: str) -> str:
    """Drop Slack-style multiline reply quotes before the live user message."""

    kept: list[str] = []
    in_reply_quote = False

    for raw_line in text.splitlines():
        if in_reply_quote:
            if _reply_quote_region_closed(raw_line):
                in_reply_quote = False
            continue

        if not kept and _is_reply_quote_line(raw_line):
            in_reply_quote = not _reply_quote_region_closed(raw_line)
            continue

        kept.append(raw_line)

    return "\n".join(kept).strip()


def extract_role_pin(task: str) -> str | None:
    """Return the role pinned via a ``[ROLE PIN: <role>]`` line, if any.

    The pin is injected by the cron scheduler from a job's ``role`` field and
    deterministically overrides both the LLM role router and the keyword
    cascade. Only well-formed role identifiers match; the first pin wins.
    """

    if not isinstance(task, str) or not task:
        return None
    for raw_line in task.splitlines():
        match = _ROLE_PIN_LINE_RE.match(raw_line.strip())
        if match:
            return match.group(1).lower()
    return None


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
    in_injected_data = False
    injected_data_fences = 0

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

        if in_injected_data:
            if compact.startswith("```"):
                injected_data_fences += 1
                if injected_data_fences >= 2:
                    in_injected_data = False
            continue

        if any(lower.startswith(prefix) for prefix in _THREAD_CONTEXT_START_PREFIXES):
            in_thread_context = True
            continue

        if any(lower.startswith(prefix) for prefix in _INJECTED_DATA_SECTION_PREFIXES):
            in_injected_data = True
            injected_data_fences = 0
            continue

        if _ROLE_PIN_LINE_RE.match(compact):
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
    stripped = _strip_leading_reply_quote_region(stripped)
    if not stripped:
        return ""

    kept: list[str] = []
    in_thread_context = False
    in_cron_response = False
    in_injected_data = False
    injected_data_fences = 0

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

        if in_injected_data:
            if compact.startswith("```"):
                injected_data_fences += 1
                if injected_data_fences >= 2:
                    in_injected_data = False
            continue

        if any(lower.startswith(prefix) for prefix in _THREAD_CONTEXT_START_PREFIXES):
            in_thread_context = True
            continue

        if any(lower.startswith(prefix) for prefix in _INJECTED_DATA_SECTION_PREFIXES):
            in_injected_data = True
            injected_data_fences = 0
            continue

        if _ROLE_PIN_LINE_RE.match(compact):
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
