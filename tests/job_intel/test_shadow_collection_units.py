"""The shadow collection units must be silent by construction.

These assertions are about the deployment artifacts, not about runtime: they
catch a unit that reintroduces Slack credentials or drops the kill-switch long
before such a unit reaches systemd.
"""

from __future__ import annotations

from pathlib import Path

EXPERIMENTS = Path(__file__).resolve().parents[2] / "deploy/systemd/experiments"
SERVICE = EXPERIMENTS / "job-intel-shadow-collection.service"
TIMER = EXPERIMENTS / "job-intel-shadow-collection.timer"
ENV_EXAMPLE = EXPERIMENTS / "job-intel-shadow-collection.env.example"

CREDENTIAL_NAMES = ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "JOB_INTEL_SLACK_WEBHOOK_URL")


def test_artifacts_exist() -> None:
    for path in (SERVICE, TIMER, ENV_EXAMPLE):
        assert path.is_file(), path


def test_service_reads_only_the_shadow_env_file() -> None:
    service = SERVICE.read_text(encoding="utf-8")
    assert "EnvironmentFile=/etc/job-intel/job-intel-shadow.env" in service
    assert "EnvironmentFile=/etc/job-intel/job-intel.env" not in service


def test_service_runs_the_exact_collection_entrypoint() -> None:
    service = SERVICE.read_text(encoding="utf-8")
    assert (
        "ExecStart=/usr/bin/env bash "
        "/home/hermes/.hermes/hermes-agent/scripts/job_intel_host_wrapper.sh daily"
    ) in service


def test_no_slack_credentials_anywhere_in_the_artifacts() -> None:
    for path in (SERVICE, TIMER, ENV_EXAMPLE):
        text = path.read_text(encoding="utf-8")
        for name in CREDENTIAL_NAMES:
            assert f"{name}=" not in text, f"{name} must not be set in {path.name}"


def test_env_example_carries_the_kill_switch() -> None:
    env = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "JOB_INTEL_DELIVERY_DISABLED=1" in env
    assert "JOB_INTEL_RUN_TYPE=shadow" in env


def test_timer_points_at_the_shadow_service() -> None:
    timer = TIMER.read_text(encoding="utf-8")
    assert "Unit=job-intel-shadow-collection.service" in timer
    assert "OnCalendar=" in timer
