"""Fail-closed detection of provider tool-call protocol emitted as text."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, Literal


MAX_INSPECTED_TEXT_CHARS = 16_384

_CODEX_CHATML_RE = re.compile(
    r"^<\|start\|>assistant<\|channel\|>"
    r"(?:commentary|analysis)\s+to=functions\."
    r"(?P<name>[A-Za-z_][A-Za-z0-9_.:-]*)<\|constrain\|>json"
    r"(?:\s|$)",
    re.IGNORECASE,
)
_TOOL_CALL_XML_RE = re.compile(
    r"^<tool_call>\s*(?P<body>.*?)\s*</tool_call>$",
    re.IGNORECASE | re.DOTALL,
)
_XML_NAME_RE = re.compile(
    r"<name>\s*(?P<name>[A-Za-z_][A-Za-z0-9_.:-]*)\s*</name>",
    re.IGNORECASE | re.DOTALL,
)
_JSON_NAME_RE = re.compile(
    r"[\{,]\s*[\"']name[\"']\s*:\s*[\"']"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_.:-]*)[\"']",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class MalformedToolIntent:
    """Evidence that text contained a registered tool protocol envelope.

    This type intentionally has no arguments field.  It is a diagnostic signal,
    never an intermediate representation for tool execution.
    """

    tool_name: str
    source_phase: str
    format: Literal["codex_chatml", "tool_call_xml"]
    fingerprint: str


def detect_malformed_tool_intent(
    text: str,
    *,
    phase: str | None,
    valid_tool_names: Iterable[str] | None,
) -> MalformedToolIntent | None:
    """Return bounded evidence for a high-confidence text-bound tool envelope.

    The original text is only hashed; neither XML nor JSON arguments are parsed
    into an executable mapping.
    """

    normalized_phase = str(phase or "").strip().lower()
    if normalized_phase not in {"commentary", "analysis"} or not isinstance(text, str):
        return None

    valid_names = {str(name) for name in (valid_tool_names or ())}
    if not valid_names:
        return None

    original = text
    inspected = original.strip()[:MAX_INSPECTED_TEXT_CHARS]
    match = _CODEX_CHATML_RE.match(inspected)
    detected_format: Literal["codex_chatml", "tool_call_xml"] | None = None
    tool_name: str | None = None
    if match:
        detected_format = "codex_chatml"
        tool_name = match.group("name")
    else:
        xml_match = _TOOL_CALL_XML_RE.match(inspected)
        if xml_match:
            body = xml_match.group("body")
            name_match = _XML_NAME_RE.search(body) or _JSON_NAME_RE.search(body)
            if name_match:
                detected_format = "tool_call_xml"
                tool_name = name_match.group("name")

    if detected_format is None or tool_name not in valid_names:
        return None

    fingerprint = "sha256:" + hashlib.sha256(original.encode("utf-8")).hexdigest()
    return MalformedToolIntent(
        tool_name=tool_name,
        source_phase=normalized_phase,
        format=detected_format,
        fingerprint=fingerprint,
    )
