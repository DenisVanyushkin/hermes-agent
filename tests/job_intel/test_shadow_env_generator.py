"""Behavioural tests for the shadow env generator.

The generator is executed against synthetic production files rather than
grepped: a test that searches the script for a variable name passes even when
the classification logic is broken, which is exactly the failure mode these
assertions exist to catch.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

GENERATOR = Path(__file__).resolve().parents[2] / "scripts/job_intel_make_shadow_env.sh"

BASE_ENV = """JOB_INTEL_DB_PATH=/var/lib/job-intel/state/job_intel.sqlite3
JOB_INTEL_ATS_SEEDS_GREENHOUSE=adyen,wolt
JOB_INTEL_HH_CLIENT_ID=hh-id
JOB_INTEL_HH_CLIENT_SECRET=hh-secret
HERMES_HOME=/home/hermes/.hermes
SLACK_BOT_TOKEN=xoxb-real
SLACK_APP_TOKEN=xapp-real
SLACK_HOME_CHANNEL=C0DEADBEEF
JOB_INTEL_SLACK_WEBHOOK_URL=https://hooks.slack.test/real
JOB_INTEL_RUN_TYPE=production
"""


def generate(tmp_path: Path, content: str) -> subprocess.CompletedProcess[str]:
    source = tmp_path / "prod.env"
    source.write_text(content, encoding="utf-8")
    env = dict(os.environ, SHADOW_ENV_STDOUT="1")
    return subprocess.run(
        ["bash", str(GENERATOR), str(source), str(tmp_path / "shadow.env")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_credentials_are_dropped_and_settings_are_carried(tmp_path) -> None:
    result = generate(tmp_path, BASE_ENV)

    assert result.returncode == 0, result.stderr
    out = result.stdout
    for dropped in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_HOME_CHANNEL",
                    "JOB_INTEL_SLACK_WEBHOOK_URL"):
        assert f"{dropped}=" not in out, dropped
    assert "xoxb-real" not in out and "hooks.slack.test" not in out
    assert "JOB_INTEL_ATS_SEEDS_GREENHOUSE=adyen,wolt" in out
    assert "JOB_INTEL_DB_PATH=/var/lib/job-intel/state/job_intel.sqlite3" in out


def test_acquisition_secrets_are_carried_deliberately(tmp_path) -> None:
    """HeadHunter credentials are credential-shaped but required to collect."""
    result = generate(tmp_path, BASE_ENV)

    assert "JOB_INTEL_HH_CLIENT_SECRET=hh-secret" in result.stdout
    assert "JOB_INTEL_HH_CLIENT_ID=hh-id" in result.stdout


def test_overrides_replace_production_values(tmp_path) -> None:
    result = generate(tmp_path, BASE_ENV)

    assert "JOB_INTEL_RUN_TYPE=shadow" in result.stdout
    assert "JOB_INTEL_RUN_TYPE=production" not in result.stdout
    assert "JOB_INTEL_DELIVERY_DISABLED=1" in result.stdout


def test_unknown_key_aborts_generation(tmp_path) -> None:
    result = generate(tmp_path, BASE_ENV + "SOME_NEW_SETTING=1\n")

    assert result.returncode == 3, result.stdout
    assert "unclassified keys" in result.stderr
    assert "SOME_NEW_SETTING" in result.stderr


def test_future_credential_shaped_key_cannot_ride_in_on_a_prefix(tmp_path) -> None:
    """A JOB_INTEL_* key that looks like a credential must not be carried."""
    result = generate(tmp_path, BASE_ENV + "JOB_INTEL_TELEGRAM_BOT_TOKEN=secret\n")

    assert result.returncode == 4, result.stdout
    assert "credential-shaped keys not classified" in result.stderr
    assert "JOB_INTEL_TELEGRAM_BOT_TOKEN" in result.stderr
    assert "secret" not in result.stdout


def test_a_new_delivery_destination_is_refused_too(tmp_path) -> None:
    result = generate(tmp_path, BASE_ENV + "JOB_INTEL_REPORT_CHANNEL=C0NEW\n")

    assert result.returncode == 4, result.stdout
    assert "JOB_INTEL_REPORT_CHANNEL" in result.stderr


def test_authority_paths_are_never_carried(tmp_path) -> None:
    """Not credentials, but redirections: carrying either would let the
    production env file choose which pin is verified and where the managed
    credential store is resolved."""
    result = generate(
        tmp_path,
        BASE_ENV
        + "JOB_INTEL_SHADOW_PIN_FILE=/tmp/operator-controlled.pin\n"
        + "HERMES_MANAGED_DIR=/tmp/operator-controlled\n",
    )

    assert result.returncode == 0, result.stderr
    assert "JOB_INTEL_SHADOW_PIN_FILE" not in result.stdout
    assert "HERMES_MANAGED_DIR" not in result.stdout
    assert "operator-controlled" not in result.stdout
