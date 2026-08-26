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
    blocked = {
        line.split("=", 1)[1].lstrip("-")
        for line in service.splitlines()
        if line.startswith("InaccessiblePaths=")
    }
    # A leading dash tolerates a store that does not exist yet, so compare on
    # the normalised path rather than on the literal directive text.
    for required in (
        "/etc/job-intel/job-intel.env",
        "/home/hermes/.hermes/.env",
        "/home/hermes/.hermes/config.yaml",
        "/home/hermes/.hermes/auth.json",
    ):
        assert required in blocked, f"{required} must be unreachable inside the unit"


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


def test_generator_and_preflight_are_executable() -> None:
    """Behaviour of the generator is exercised in test_shadow_env_generator.py.

    Here we only assert that the artifacts the unit invokes exist and are
    runnable: a non-executable script would fail the unit at ExecStartPre with a
    far less obvious message than a failed assertion here.
    """
    import os

    for path in (GENERATOR, PREFLIGHT):
        assert os.access(path, os.X_OK), f"{path.name} must be executable"


def test_preflight_covers_every_credential_store_the_unit_blocks() -> None:
    """The two lists must not drift apart.

    The unit makes the stores unreachable; the preflight proves it from inside.
    If a store is added to one and not the other, the guarantee has a hole that
    neither file reveals on its own.
    """
    service = SERVICE.read_text(encoding="utf-8")
    preflight = PREFLIGHT.read_text(encoding="utf-8")

    blocked = {
        line.split("=", 1)[1].lstrip("-")
        for line in service.splitlines()
        if line.startswith("InaccessiblePaths=")
    }
    assert blocked, "the unit must block at least one credential store"

    for path in blocked:
        leaf = path.replace("/home/hermes/.hermes", "").lstrip("/")
        assert leaf in preflight or path in preflight, (
            f"{path} is blocked by the unit but not asserted by the preflight"
        )


def test_preflight_pins_the_checkout_rather_than_probing_for_a_helper() -> None:
    preflight = PREFLIGHT.read_text(encoding="utf-8")
    assert "rev-parse HEAD" in preflight, "drift must be checked against a pinned commit"
    assert "pin file" in preflight, "a missing pin must stop the run"
    assert "delivery_disabled" in preflight


def test_timer_points_at_the_shadow_service() -> None:
    timer = TIMER.read_text(encoding="utf-8")
    assert "Unit=job-intel-shadow-collection.service" in timer
    assert "OnCalendar=" in timer
