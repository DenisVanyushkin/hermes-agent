"""The three scheduler stages need distinct provenance and safe installation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def test_cron_plan_is_idempotent_and_refuses_drift() -> None:
    path = Path(__file__).resolve().parents[2] / "scripts" / "job_intel_shortlist_cron_install.py"
    assert path.is_file(), "shortlist cron installer is missing"
    spec = importlib.util.spec_from_file_location("job_intel_shortlist_cron_install", path)
    assert spec and spec.loader
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)

    wanted = installer.plan_jobs([])
    assert [job["name"] for job in wanted] == [
        "job-intel-shortlist-weekly", "job-intel-shortlist-poll", "job-intel-shortlist-report",
    ]
    assert [job["schedule"] for job in wanted] == ["10 9 * * 1", "*/30 * * * *", "5,35 * * * *"]
    assert all(job["no_agent"] is True and job["deliver"] == "local" for job in wanted)
    assert installer.plan_jobs(wanted) == []
    persisted = [{**job, "schedule": {"kind": "cron", "expr": job["schedule"]}}
                 for job in wanted]
    assert installer.plan_jobs(persisted) == []
    with pytest.raises(ValueError, match="drift"):
        installer.plan_jobs([{**wanted[0], "schedule": "15 9 * * 1"}])
