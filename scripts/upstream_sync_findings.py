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


def findings_from_log(text: str) -> list:
    """Findings of the most recent invariants_failed payload in ``text``."""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{") or "invariants_failed" not in line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if payload.get("status") == "invariants_failed":
            return payload.get("findings", [])
    return []


def render(findings: list) -> str:
    if not findings:
        # Never render silence: an empty report reads as "nothing was wrong",
        # which is exactly what this path has already disproved.
        return "(the findings could not be read — see finalize-detail.log)"
    lines = []
    for f in findings:
        where = f.get("symbol") or (f"line {f['line']}" if f.get("line") else "?")
        lines.append(f"- {f.get('path')}: {f.get('kind')} ({where})")
    return "\n".join(lines)


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
        print(render(findings_from_log(fh.read())))
