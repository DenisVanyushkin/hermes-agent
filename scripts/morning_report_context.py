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
MAX_RESOLVED = 10
EXAMPLE_CHARS = 200
OUTPUT_TAIL_CHARS = 300


def _compact_cron_jobs(cron: dict) -> dict:
    failed = []
    for entry in cron.get("failed", []) or []:
        if isinstance(entry, dict):
            entry = dict(entry)
            tail = entry.get("output_tail")
            if isinstance(tail, str) and len(tail) > OUTPUT_TAIL_CHARS:
                entry["output_tail"] = tail[-OUTPUT_TAIL_CHARS:]
        failed.append(entry)
    ok_names = []
    for entry in cron.get("ok", []) or []:
        if isinstance(entry, dict):
            ok_names.append(entry.get("name") or entry.get("id") or str(entry))
        else:
            ok_names.append(entry)
    return {"failed": failed, "paused": cron.get("paused", []), "ok": ok_names}


def _compact_logs(logs: dict) -> dict:
    findings = logs.get("findings", []) or []
    compact_findings = []
    for finding in findings[:MAX_FINDINGS]:
        if isinstance(finding, dict):
            finding = dict(finding)
            examples = finding.get("examples")
            if isinstance(examples, list):
                finding["examples"] = [str(ex)[:EXAMPLE_CHARS] for ex in examples[:1]]
        compact_findings.append(finding)
    out = {"memory": logs.get("memory"), "findings": compact_findings}
    dropped = max(0, len(findings) - MAX_FINDINGS)
    if dropped:
        out["findings_truncated"] = dropped
    out["resolved"] = (logs.get("resolved", []) or [])[:MAX_RESOLVED]
    return out


def compact_digest(digest: dict) -> dict:
    sections = digest.get("sections")
    if not isinstance(sections, dict):
        return digest
    compact = {
        "generated_at": digest.get("generated_at"),
        "window_hours": digest.get("window_hours"),
        "section_errors": digest.get("section_errors", {}),
    }
    compact_sections: dict = {}
    cron = sections.get("cron_jobs")
    if isinstance(cron, dict):
        compact_sections["cron_jobs"] = _compact_cron_jobs(cron)
    for name in ("systemd", "job_intel", "doctors", "system", "docker"):
        if name in sections:
            compact_sections[name] = sections[name]
    if "logs" in sections:
        logs = sections["logs"]
        compact_sections["logs"] = _compact_logs(logs) if isinstance(logs, dict) else logs
    compact["sections"] = compact_sections
    return compact


def render(digest_path: Path, now: datetime) -> str:
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
        out = f"DIGEST STALE (generated_at unparseable: {generated_raw!r})\n{body}"
        return out[:MAX_CHARS]
    age_hours = (now - generated).total_seconds() / 3600
    if age_hours > STALE_HOURS:
        out = f"DIGEST STALE (generated_at={generated_raw}, age={age_hours:.0f}h)\n{body}"
        return out[:MAX_CHARS]
    return body


def main() -> int:
    home = Path(os.environ.get("HERMES_HOME", "").strip() or "/home/hermes/.hermes")
    print(render(home / "diagnostics" / "digest-latest.json", datetime.now()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
