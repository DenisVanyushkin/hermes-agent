#!/usr/bin/env python3
"""Print the latest fam diagnostics digest for the nightly reporter prompt.

Deliberately self-contained (stdlib only, no `fam` import): the agent runs
this with workdir=/home/denis/.hermes/hermes-agent, where the fam package
is not importable. Mirrors morning_report_context.py on hermes-agent.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

STALE_HOURS = 12
MAX_CHARS = 24000
MAX_FINDINGS = 30
MAX_RESOLVED = 10
EXAMPLE_CHARS = 200

DEFAULT_PATH = "/home/denis/.hermes/diagnostics/fam-digest-latest.json"


def compact_digest(digest):
    sections = digest.get("sections")
    if not isinstance(sections, dict):
        return digest
    compact = dict(sections)
    errors = sections.get("errors")
    if isinstance(errors, dict):
        findings = errors.get("findings") or []
        trimmed = []
        for finding in findings[:MAX_FINDINGS]:
            finding = dict(finding)
            examples = finding.get("examples")
            if isinstance(examples, list):
                finding["examples"] = [str(e)[:EXAMPLE_CHARS] for e in examples[:1]]
            trimmed.append(finding)
        section = {"findings": trimmed,
                   "resolved": (errors.get("resolved") or [])[:MAX_RESOLVED]}
        dropped = len(findings) - len(trimmed)
        if dropped > 0:
            # Never truncate silently: a capped list that does not say so
            # reads to the reporter as full coverage.
            section["findings_truncated"] = dropped
        compact["errors"] = section
    return {"generated_at": digest.get("generated_at"),
            "window": digest.get("window"),
            "fam_schema_version": digest.get("fam_schema_version"),
            "delivery": digest.get("delivery"),
            "section_errors": digest.get("section_errors", {}),
            "sections": compact}


def render(digest_path, now):
    digest_path = Path(digest_path)
    if not digest_path.exists():
        return f"DIGEST MISSING: collector did not produce {digest_path}"
    try:
        digest = json.loads(digest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return f"DIGEST MISSING: unreadable ({exc})"
    body = json.dumps(compact_digest(digest), ensure_ascii=False, indent=1)[:MAX_CHARS]
    generated_raw = str(digest.get("generated_at", ""))
    try:
        generated = datetime.fromisoformat(generated_raw)
    except ValueError:
        return f"DIGEST STALE (generated_at unparseable: {generated_raw!r})\n{body}"[:MAX_CHARS]
    # fam writes generated_at with a UTC offset (aware); main() passes a
    # naive datetime.now(). Subtracting an aware and a naive datetime
    # raises TypeError, which would crash the script and hand the LLM
    # nothing. Normalize whichever side is aware down to naive so the
    # subtraction below always succeeds, regardless of which of the two
    # datetimes (generated, now) carries tzinfo.
    if generated.tzinfo and not now.tzinfo:
        generated = generated.replace(tzinfo=None)
    elif now.tzinfo and not generated.tzinfo:
        now = now.replace(tzinfo=None)
    age_hours = (now - generated).total_seconds() / 3600
    if age_hours > STALE_HOURS:
        return (f"DIGEST STALE (generated_at={generated_raw}, "
                f"age={age_hours:.0f}h)\n{body}")[:MAX_CHARS]
    return body


def main():
    path = os.environ.get("FAM_DIGEST_PATH", "").strip() or DEFAULT_PATH
    print(render(path, datetime.now()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
