"""Inspect or install the three no-agent weekly shortlist cron jobs.

Default is read-only. Run with --apply only during an approved deployment,
after the runtime scripts and shortlist dependencies have been installed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPO = Path.home() / ".hermes" / "hermes-agent"
SPECS = (
    ("job-intel-shortlist-weekly", "10 9 * * 1", "job_intel_shortlist_weekly.py"),
    ("job-intel-shortlist-poll", "*/30 * * * *", "job_intel_shortlist_poll_cron.py"),
    ("job-intel-shortlist-report", "5,35 * * * *", "job_intel_shortlist_report_cron.py"),
)


def plan_jobs(existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return missing jobs; refuse a same-name job with changed behavior."""
    by_name: dict[str, list[dict[str, Any]]] = {}
    for job in existing:
        if isinstance(job, dict) and isinstance(job.get("name"), str):
            by_name.setdefault(job["name"], []).append(job)
    missing: list[dict[str, Any]] = []
    for name, schedule, script in SPECS:
        desired = {
            "name": name, "schedule": schedule, "script": script,
            "no_agent": True, "deliver": "local", "workdir": str(REPO),
        }
        found = by_name.get(name, [])
        if not found:
            missing.append(desired)
            continue
        if len(found) != 1:
            raise ValueError(f"duplicate shortlist cron job: {name}")
        actual = found[0]
        actual_schedule = actual.get("schedule")
        if isinstance(actual_schedule, dict):
            actual_schedule = actual_schedule.get("expr")
        if (actual_schedule != schedule or actual.get("script") != script
                or actual.get("no_agent") is not True or actual.get("deliver") != "local"
                or actual.get("workdir") != str(REPO) or actual.get("enabled") is False):
            raise ValueError(f"shortlist cron job drift: {name}")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write missing jobs")
    args = parser.parse_args()
    from cron.jobs import create_job, list_jobs

    missing = plan_jobs(list_jobs(include_disabled=True))
    if args.apply:
        for job in missing:
            if not (Path.home() / ".hermes" / "scripts" / job["script"]).is_file():
                raise FileNotFoundError(f"runtime shortlist script missing: {job['script']}")
        created = [create_job(prompt=None, **job)["id"] for job in missing]
        print(json.dumps({"created": created, "already_present": len(SPECS) - len(created)}, sort_keys=True))
    else:
        print(json.dumps({"would_create": missing, "already_present": len(SPECS) - len(missing)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
