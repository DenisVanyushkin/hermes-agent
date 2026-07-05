#!/usr/bin/env python3
"""Collect a 24h diagnostics digest for the morning reporter agent.

Writes ~/.hermes/diagnostics/digest-latest.json (+ dated copy, 14-day
rotation) and maintains known-issues.json across nights. Prints nothing on
success so the no-agent cron job stays silent in Slack.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

WINDOW_HOURS = 24
DOCTOR_TIMEOUT = 90
ROTATE_DAYS = 14
MAX_EXAMPLES = 3
MAX_TAIL_CHARS = 700

LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+(?P<level>[A-Z]+)\s+(?P<rest>.*)$"
)
MEMORY_RE = re.compile(r"\[MEMORY\] rss=(\d+)mb", re.IGNORECASE)


def parse_log_line(line: str) -> dict | None:
    match = LOG_LINE_RE.match(line.strip())
    if not match:
        return None
    try:
        ts = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return {"ts": ts, "level": match.group("level"), "rest": match.group("rest")}


def normalize_signature(text: str) -> str:
    text = re.sub(r"[0-9a-fA-F]{8,}", "<hex>", text)
    text = re.sub(r"\d+", "<n>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:300]


def extract_log_findings(lines, since: datetime) -> list[dict]:
    buckets: dict[str, dict] = {}
    for raw in lines:
        parsed = parse_log_line(raw)
        if not parsed or parsed["ts"] < since:
            continue
        if parsed["level"] not in ("ERROR", "WARNING", "CRITICAL"):
            continue
        sig = normalize_signature(parsed["rest"])
        bucket = buckets.setdefault(
            sig, {"level": parsed["level"], "signature": sig, "count": 0, "examples": []}
        )
        bucket["count"] += 1
        if len(bucket["examples"]) < MAX_EXAMPLES:
            bucket["examples"].append(raw.strip()[:300])
    return sorted(buckets.values(), key=lambda b: -b["count"])


def memory_trend(lines, since: datetime) -> dict | None:
    values: list[int] = []
    for raw in lines:
        parsed = parse_log_line(raw)
        if not parsed or parsed["ts"] < since:
            continue
        match = MEMORY_RE.search(parsed["rest"])
        if match:
            values.append(int(match.group(1)))
    if not values:
        return None
    return {
        "min_mb": min(values),
        "max_mb": max(values),
        "last_mb": values[-1],
        "delta_mb": values[-1] - values[0],
        "samples": len(values),
    }


def latest_output_tail(job_dir: Path) -> str | None:
    if not job_dir.is_dir():
        return None
    files = sorted(job_dir.glob("*.md"))
    if not files:
        return None
    text = files[-1].read_text(encoding="utf-8", errors="replace")
    return text[-MAX_TAIL_CHARS:]


def summarize_cron_jobs(jobs: list[dict], output_root: Path) -> dict:
    ok: list[dict] = []
    failed: list[dict] = []
    paused: list[dict] = []
    for job in jobs:
        entry = {
            "name": job.get("name") or job.get("id", "?"),
            "id": job.get("id"),
            "last_run_at": job.get("last_run_at"),
            "schedule": job.get("schedule_display"),
        }
        if not job.get("enabled", True):
            paused.append(entry)
            continue
        status = (job.get("last_status") or "").lower()
        if status and status != "ok":
            entry["last_status"] = job.get("last_status")
            entry["last_error"] = job.get("last_error")
            entry["output_tail"] = latest_output_tail(output_root / str(job.get("id")))
            failed.append(entry)
        else:
            entry["last_status"] = status or "never-ran"
            ok.append(entry)
    return {"ok": ok, "failed": failed, "paused": paused}
