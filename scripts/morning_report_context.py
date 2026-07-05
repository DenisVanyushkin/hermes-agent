#!/usr/bin/env python3
"""Print the latest diagnostics digest for injection into the morning reporter prompt."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

STALE_HOURS = 12
MAX_CHARS = 24000

MAX_FINDINGS = 30
MAX_EXAMPLES_PER_FINDING = 1
MAX_EXAMPLE_CHARS = 200
MAX_RESOLVED = 10
MAX_OUTPUT_TAIL_CHARS = 300


def _trim_finding(finding: dict) -> dict:
    trimmed = dict(finding)
    examples = trimmed.get("examples")
    if isinstance(examples, list):
        capped = examples[:MAX_EXAMPLES_PER_FINDING]
        trimmed["examples"] = [
            (ex[:MAX_EXAMPLE_CHARS] if isinstance(ex, str) else ex) for ex in capped
        ]
    return trimmed


def _compact_logs(logs: dict) -> dict:
    compact: dict = {}
    if "memory" in logs:
        compact["memory"] = logs["memory"]
    findings = logs.get("findings")
    if isinstance(findings, list):
        capped_findings = findings[:MAX_FINDINGS]
        compact["findings"] = [_trim_finding(f) if isinstance(f, dict) else f for f in capped_findings]
        dropped = len(findings) - len(capped_findings)
        if dropped > 0:
            compact["findings_truncated"] = dropped
    elif findings is not None:
        compact["findings"] = findings
    resolved = logs.get("resolved")
    if isinstance(resolved, list):
        compact["resolved"] = resolved[:MAX_RESOLVED]
    elif resolved is not None:
        compact["resolved"] = resolved
    for key, value in logs.items():
        if key not in compact and key not in ("memory", "findings", "resolved"):
            compact[key] = value
    return compact


def _compact_cron_jobs(cron_jobs: dict) -> dict:
    compact: dict = {}
    failed = cron_jobs.get("failed")
    if isinstance(failed, list):
        trimmed_failed = []
        for entry in failed:
            if isinstance(entry, dict):
                entry = dict(entry)
                tail = entry.get("output_tail")
                if isinstance(tail, str):
                    entry["output_tail"] = tail[-MAX_OUTPUT_TAIL_CHARS:]
            trimmed_failed.append(entry)
        compact["failed"] = trimmed_failed
    elif failed is not None:
        compact["failed"] = failed
    if "paused" in cron_jobs:
        compact["paused"] = cron_jobs["paused"]
    ok = cron_jobs.get("ok")
    if isinstance(ok, list):
        names = []
        for entry in ok:
            if isinstance(entry, dict):
                names.append(entry.get("name", entry.get("job_id", entry)))
            else:
                names.append(entry)
        compact["ok"] = names
    elif ok is not None:
        compact["ok"] = ok
    for key, value in cron_jobs.items():
        if key not in compact and key not in ("failed", "paused", "ok"):
            compact[key] = value
    return compact


def _build_compact_digest(digest: dict) -> dict:
    sections = digest.get("sections")
    if not isinstance(sections, dict) or not sections:
        return digest

    compact: dict = {}
    compact["generated_at"] = digest.get("generated_at")
    compact["window_hours"] = digest.get("window_hours")

    if "section_errors" in digest:
        compact["section_errors"] = digest.get("section_errors")

    if "cron_jobs" in sections:
        cron_jobs = sections.get("cron_jobs")
        compact["cron_jobs"] = _compact_cron_jobs(cron_jobs) if isinstance(cron_jobs, dict) else cron_jobs

    for key in ("systemd", "job_intel", "doctors", "system", "docker"):
        if key in sections:
            compact[key] = sections[key]

    if "logs" in sections:
        logs = sections.get("logs")
        compact["logs"] = _compact_logs(logs) if isinstance(logs, dict) else logs

    return compact


def render(digest_path: Path, now: datetime) -> str:
    if not digest_path.exists():
        return f"DIGEST MISSING: collector did not produce {digest_path}"
    try:
        digest = json.loads(digest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return f"DIGEST MISSING: unreadable ({exc})"
    compact_digest = _build_compact_digest(digest)
    body = json.dumps(compact_digest, ensure_ascii=False, indent=1)[:MAX_CHARS]
    generated_raw = str(digest.get("generated_at", ""))
    try:
        generated = datetime.fromisoformat(generated_raw)
    except ValueError:
        return f"DIGEST STALE (generated_at unparseable: {generated_raw!r})\n{body}"
    age_hours = (now - generated).total_seconds() / 3600
    if age_hours > STALE_HOURS:
        return f"DIGEST STALE (generated_at={generated_raw}, age={age_hours:.0f}h)\n{body}"
    return body


def main() -> int:
    home = Path(os.environ.get("HERMES_HOME", "").strip() or "/home/hermes/.hermes")
    print(render(home / "diagnostics" / "digest-latest.json", datetime.now()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
