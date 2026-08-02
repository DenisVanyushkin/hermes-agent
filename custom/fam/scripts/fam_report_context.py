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
from datetime import datetime, timezone
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
        resolved = errors.get("resolved") or []
        resolved_trimmed = resolved[:MAX_RESOLVED]
        section = {"findings": trimmed, "resolved": resolved_trimmed}
        findings_dropped = len(findings) - len(trimmed)
        if findings_dropped > 0:
            # Never truncate silently: a capped list that does not say so
            # reads to the reporter as full coverage.
            section["findings_truncated"] = findings_dropped
        resolved_dropped = len(resolved) - len(resolved_trimmed)
        if resolved_dropped > 0:
            # Same mechanism, mirrored: a resolved list quietly cut from
            # 25 to 10 would read as "everything open got fixed" unless we
            # say how much was cut.
            section["resolved_truncated"] = resolved_dropped
        compact["errors"] = section
    return {"generated_at": digest.get("generated_at"),
            "window": digest.get("window"),
            "fam_schema_version": digest.get("fam_schema_version"),
            "delivery": digest.get("delivery"),
            "section_errors": digest.get("section_errors", {}),
            "sections": compact}


def _fit_to_budget(compact, budget):
    """Serialise `compact` within `budget` chars, shedding whole findings first.

    A blind tail slice would cut mid-token: the reader gets invalid JSON
    that still looks like a complete digest, which is the silent-coverage
    failure this module exists to avoid. Shedding whole findings keeps the
    structure valid and the loss counted; the hard cut is a last resort and
    announces itself.
    """
    budget = max(budget, 0)
    body = json.dumps(compact, ensure_ascii=False, indent=1)
    # compact_digest() guards `sections` by type, not just by key presence
    # (it returns the digest unchanged when `sections` is present but not a
    # dict). A `.get(key, default)` chain here only covers a MISSING key —
    # `sections` can still be None/[]/a string/a number, and `.get` on any
    # of those raises AttributeError. Check the type at each step instead.
    sections = compact.get("sections")
    errors = sections.get("errors") if isinstance(sections, dict) else None
    if not isinstance(errors, dict):
        errors = None
    while len(body) > budget and isinstance(errors, dict) and errors.get("findings"):
        errors["findings"].pop()
        errors["findings_truncated"] = errors.get("findings_truncated", 0) + 1
        body = json.dumps(compact, ensure_ascii=False, indent=1)
    if len(body) > budget:
        marker = "\n... DIGEST TRUNCATED: output exceeded the prompt budget"
        cut = max(budget - len(marker), 0)
        body = body[:cut] + marker
    return body


def _to_naive_utc(dt):
    """Normalize an aware datetime to naive UTC; leave naive values as-is.

    Naive datetimes are treated as already being UTC (the convention this
    module uses throughout for both `generated_at` and `now`). Dropping
    tzinfo without converting first would keep the aware value's local
    wall-clock hour but silently discard its offset, mis-measuring age by
    that offset (an aware digest carrying a +05:00 offset would otherwise
    look several hours fresher than it really is).
    """
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def render(digest_path, now):
    digest_path = Path(digest_path)
    if not digest_path.exists():
        return f"DIGEST MISSING: collector did not produce {digest_path}"
    try:
        digest = json.loads(digest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return f"DIGEST MISSING: unreadable ({exc})"
    compact = compact_digest(digest)
    generated_raw = str(digest.get("generated_at", ""))
    try:
        generated = datetime.fromisoformat(generated_raw)
    except ValueError:
        prefix = f"DIGEST STALE (generated_at unparseable: {generated_raw!r})\n"
        return prefix + _fit_to_budget(compact, MAX_CHARS - len(prefix))
    # fam writes generated_at with a UTC offset (aware); main() passes an
    # aware datetime.now(timezone.utc). A caller (or a test) may still pass
    # either side as naive, so normalize both independently: naive values
    # are treated as already-UTC, aware values are converted to UTC first.
    # This keeps the subtraction below valid — and correct — for every
    # combination of aware/naive on either side.
    generated = _to_naive_utc(generated)
    now = _to_naive_utc(now)
    age_hours = (now - generated).total_seconds() / 3600
    if age_hours > STALE_HOURS:
        prefix = (f"DIGEST STALE (generated_at={generated_raw}, "
                  f"age={age_hours:.0f}h)\n")
        return prefix + _fit_to_budget(compact, MAX_CHARS - len(prefix))
    return _fit_to_budget(compact, MAX_CHARS)


def main():
    path = os.environ.get("FAM_DIGEST_PATH", "").strip() or DEFAULT_PATH
    print(render(path, datetime.now(timezone.utc)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
