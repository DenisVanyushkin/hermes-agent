"""The shadow collection deployment must be silent by construction.

These assertions are about the deployment artifacts. They cannot prove what the
namespace does at runtime — that is what the ExecStartPre preflight is for —
but they catch a unit that reintroduces credentials, drops the kill-switch, or
becomes boot-enabled long before it reaches systemd.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS = ROOT / "deploy/systemd/experiments"
SERVICE = EXPERIMENTS / "job-intel-shadow-collection.service"
TIMER = EXPERIMENTS / "job-intel-shadow-collection.timer"
GENERATOR = ROOT / "scripts/job_intel_make_shadow_env.sh"
PREFLIGHT = ROOT / "scripts/job_intel_shadow_preflight.sh"

CREDENTIAL_NAMES = (
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_HOME_CHANNEL",
    "JOB_INTEL_SLACK_WEBHOOK_URL",
)


def test_artifacts_exist() -> None:
    for path in (SERVICE, TIMER, GENERATOR, PREFLIGHT):
        assert path.is_file(), path


def test_service_reads_only_the_generated_shadow_env() -> None:
    service = SERVICE.read_text(encoding="utf-8")
    assert "EnvironmentFile=/etc/job-intel/job-intel-shadow.env" in service
    assert "EnvironmentFile=/etc/job-intel/job-intel.env" not in service


def test_service_makes_credential_files_unreachable() -> None:
    """Absence of a variable is not a barrier: the delivery path re-reads env files."""
    service = SERVICE.read_text(encoding="utf-8")
    assert "InaccessiblePaths=/etc/job-intel/job-intel.env" in service
    assert "InaccessiblePaths=/home/hermes/.hermes/.env" in service


def test_service_proves_isolation_before_collecting() -> None:
    service = SERVICE.read_text(encoding="utf-8")
    assert "ExecStartPre=" in service
    assert "job_intel_shadow_preflight.sh" in service
    exec_start = [
        line for line in service.splitlines() if line.startswith("ExecStart=")
    ]
    assert exec_start == [
        "ExecStart=/usr/bin/env bash "
        "/home/hermes/.hermes/hermes-agent/scripts/job_intel_host_wrapper.sh daily"
    ], exec_start


def test_oneshot_cannot_be_armed_at_boot() -> None:
    """A WantedBy here would let `systemctl enable` turn the experiment permanent."""
    service = SERVICE.read_text(encoding="utf-8")
    directives = [line.strip() for line in service.splitlines() if not line.startswith("#")]
    assert "[Install]" not in directives
    assert not [line for line in directives if line.startswith("WantedBy=")], directives


def test_no_delivery_credentials_in_any_artifact() -> None:
    for path in (SERVICE, TIMER):
        text = path.read_text(encoding="utf-8")
        for name in CREDENTIAL_NAMES:
            assert f"{name}=" not in text, f"{name} must not be set in {path.name}"


def test_generator_drops_every_credential_and_fails_closed() -> None:
    generator = GENERATOR.read_text(encoding="utf-8")
    for name in CREDENTIAL_NAMES:
        assert name in generator, f"{name} must be classified by the generator"
    assert "JOB_INTEL_DELIVERY_DISABLED]=1" in generator
    assert "unclassified keys" in generator, "an unknown production key must abort generation"


def test_preflight_checks_both_the_switch_and_the_credential_files() -> None:
    preflight = PREFLIGHT.read_text(encoding="utf-8")
    assert "JOB_INTEL_DELIVERY_DISABLED" in preflight
    assert "/etc/job-intel/job-intel.env" in preflight
    assert "/.env" in preflight
    assert "delivery_disabled" in preflight, "drift check must import the real switch"


def test_timer_points_at_the_shadow_service() -> None:
    timer = TIMER.read_text(encoding="utf-8")
    assert "Unit=job-intel-shadow-collection.service" in timer
    assert "OnCalendar=" in timer
