"""Parse the supported pytest status-line grammar.

The helper covers status lines where every `` - `` belonging to a node id is
at positive bracket depth.  It is purely syntactic: the structured
node-report is authoritative downstream, while the human log is a legacy
fallback.  Callers retain policy such as path validation, skipped-path
handling, and readability decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


_STATUS_RE = re.compile(
    r"^(?P<status>PASSED|FAILED|SKIPPED|XFAIL|XPASS|RERUN|ERROR)\s+(?P<value>\S.*)$"
)
@dataclass(frozen=True)
class StatusLine:
    status: str
    nodeid: str | None
    detail: str | None
    value: str


def _split_value(value: str) -> tuple[str, str | None]:
    depth = 0
    for index, character in enumerate(value):
        if character == "[":
            depth += 1
        elif character == "]":
            depth = max(0, depth - 1)
        elif depth == 0 and value.startswith(" - ", index):
            return value[:index].rstrip(), value[index + 3 :].strip()
    return value, None


def parse_status_line(line: str) -> StatusLine | None:
    match = _STATUS_RE.match(line.strip())
    if match is None:
        return None

    status = match.group("status")
    value = match.group("value").strip()
    nodeid, detail = _split_value(value)
    if "::" not in nodeid:
        nodeid = None
    return StatusLine(status, nodeid, detail, value)
