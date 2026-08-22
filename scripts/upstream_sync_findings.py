#!/usr/bin/env python3
"""Render the findings of a failed structural check for the operator.

Reads a detail log, takes the last invariants_failed payload the apply step
emitted, and prints one line per finding. A real module rather than a heredoc
inside the finalizer: as shell it was one quoting mistake away from turning the
operator's only explanation into a shell error.
"""

from __future__ import annotations

import json
import sys


def payload_from_log(text: str) -> dict:
    """The most recent invariants_failed payload in ``text``, or ``{}``."""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{") or "invariants_failed" not in line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if payload.get("status") == "invariants_failed":
            return payload
    return {}


def findings_from_log(text: str) -> list:
    """Findings of the most recent invariants_failed payload in ``text``."""
    return payload_from_log(text).get("findings", [])


def render(findings: list, unmatched: list = ()) -> str:
    """One line per finding, plus any acknowledgement that matched none of them.

    Each line carries the exact ``ack path:symbol`` the operator can send back.
    Rendering the symbol in some other shape means transcribing it by hand, and
    a slip there produces a byte-identical refusal — indistinguishable from the
    acknowledgement not working at all.
    """
    lines = []
    if not findings:
        # Never render silence: an empty report reads as "nothing was wrong",
        # which is exactly what this path has already disproved.
        lines.append("(the findings could not be read — see finalize-detail.log)")
    for f in findings:
        symbol = f.get("symbol")
        where = symbol or (f"line {f['line']}" if f.get("line") else "?")
        line = f"- {f.get('path')}: {f.get('kind')} ({where})"
        if symbol:
            line += f"   →  ack {f.get('path')}:{symbol}"
        lines.append(line)
    for entry in unmatched or ():
        lines.append(f"- acknowledgement {entry} matched no finding — check the spelling above")
    return "\n".join(lines)


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
        payload = payload_from_log(fh.read())
    print(render(payload.get("findings", []), payload.get("invariants_ack_unmatched") or []))
