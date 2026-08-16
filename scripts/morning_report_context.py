#!/usr/bin/env python3
"""Print the latest diagnostics digest for injection into the morning reporter prompt."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

STALE_HOURS = 2
STATUS_SCHEMA_VERSION = "collector-status.v1"
STATUS_FILENAME = "collector-status.json"
MAX_CHARS = 24000


MAX_FINDINGS = 30
MAX_RESOLVED = 10
EXAMPLE_CHARS = 200
OUTPUT_TAIL_CHARS = 300


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(raw, *, require_timezone: bool = False) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        if require_timezone:
            return None
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _max_age_hours() -> float:
    raw = os.environ.get("DIAGNOSTICS_MAX_AGE_HOURS", "").strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return float(STALE_HOURS)


def _notice(prefix: str, body: str) -> str:
    return f"{prefix}\n{body}"[:MAX_CHARS]


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
        "run_id": digest.get("run_id"),
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
    generated = _parse_timestamp(generated_raw)
    if generated is None:
        return _notice(f"DIGEST STALE (generated_at unparseable: {generated_raw!r})", body)

    status_path = digest_path.with_name(STATUS_FILENAME)
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return _notice(f"COLLECTOR STATUS MISSING ({exc})", body)
    if not isinstance(status, dict) or status.get("schema_version") != STATUS_SCHEMA_VERSION:
        return _notice("COLLECTOR STATUS MISSING (invalid schema)", body)

    state = status.get("state")
    run_id = status.get("run_id")
    age_limit = _max_age_hours()
    now_utc = _as_aware_utc(now)
    if state == "failed":
        return _notice(
            f"COLLECTOR FAILED (run_id={run_id or '?'}, reason_code={status.get('reason_code') or 'unknown'})",
            body,
        )
    if state == "running":
        started = _parse_timestamp(status.get("started_at"), require_timezone=True)
        if started is None:
            return _notice("COLLECTOR STATUS MISSING (running.started_at invalid)", body)
        age_hours = (now_utc - started).total_seconds() / 3600
        prefix = "COLLECTOR STUCK" if age_hours > age_limit else "COLLECTOR RUNNING"
        return _notice(f"{prefix} (run_id={run_id or '?'}, age={age_hours:.1f}h)", body)
    if state != "ok":
        return _notice(f"COLLECTOR STATUS MISSING (state={state!r})", body)

    finished = _parse_timestamp(status.get("finished_at"), require_timezone=True)
    status_generated = _parse_timestamp(
        status.get("digest_generated_at"), require_timezone=True
    )
    if finished is None or status_generated is None:
        return _notice("COLLECTOR STATUS MISSING (ok status incomplete)", body)
    digest_run_id = digest.get("run_id")
    if not run_id or not digest_run_id or run_id != digest_run_id:
        return _notice("DIGEST STALE (collector status run metadata mismatch)", body)
    if status_generated != generated:
        return _notice("DIGEST STALE (collector status does not match digest)", body)

    digest_age_hours = (now_utc - generated).total_seconds() / 3600
    finished_age_hours = (now_utc - finished).total_seconds() / 3600
    if max(digest_age_hours, finished_age_hours) > age_limit:
        return _notice(
            f"DIGEST STALE (generated_at={generated_raw}, age={max(digest_age_hours, finished_age_hours):.1f}h)",
            body,
        )
    return body


def main() -> int:
    home = Path(os.environ.get("HERMES_HOME", "").strip() or "/home/hermes/.hermes")
    # Capture the host's local timezone explicitly, then render() normalizes
    # all comparisons to UTC. A naive datetime would be misread as UTC on a
    # CEST host and make a fresh digest look two hours older than it is.
    print(render(home / "diagnostics" / "digest-latest.json", datetime.now().astimezone()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
