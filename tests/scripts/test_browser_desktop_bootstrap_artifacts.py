from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/browser-desktop-bootstrap.sh"


def _run_artifact_check(
    tmp_path: Path,
    *,
    relay_source: str,
    relay_target: str,
    chromium_bin: str,
    launcher_target: str,
):
    source = tmp_path / "relay-source.py"
    relay = tmp_path / "relay-target.py"
    launcher = tmp_path / "browser-chromium"
    source.write_text(relay_source, encoding="utf-8")
    relay.write_text(relay_target, encoding="utf-8")
    launcher.write_text(launcher_target, encoding="utf-8")
    source.chmod(0o755)
    relay.chmod(0o755)
    launcher.chmod(0o755)
    env = {
        **os.environ,
        "JOB_INTEL_BROWSER_CDP_RELAY_SOURCE": str(source),
        "JOB_INTEL_BROWSER_CDP_RELAY": str(relay),
        "JOB_INTEL_BROWSER_CHROMIUM_BIN": chromium_bin,
        "JOB_INTEL_BROWSER_CHROMIUM_LAUNCHER": str(launcher),
    }
    return subprocess.run(
        ["bash", str(SCRIPT), "--check-install-artifacts"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    ), relay, launcher


@pytest.mark.skipif(os.geteuid() != 0, reason="bootstrap artifact check requires root")
def test_bootstrap_skips_matching_relay_and_launcher(tmp_path: Path) -> None:
    relay = "relay-v1\n"
    chromium = "/opt/chromium-v1"
    launcher = f'#!/usr/bin/env bash\nexec "{chromium}" "$@"\n'

    result, _, _ = _run_artifact_check(
        tmp_path,
        relay_source=relay,
        relay_target=relay,
        chromium_bin=chromium,
        launcher_target=launcher,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


@pytest.mark.skipif(os.geteuid() != 0, reason="bootstrap artifact check requires root")
@pytest.mark.parametrize("drift", ["relay", "launcher"])
def test_bootstrap_fails_loudly_without_rewriting_drifted_artifacts(
    tmp_path: Path, drift: str
) -> None:
    relay_source = "relay-v2\n" if drift == "relay" else "relay-v1\n"
    relay_target = "relay-v1\n"
    chromium = "/opt/chromium-v2" if drift == "launcher" else "/opt/chromium-v1"
    launcher_target = (
        '#!/usr/bin/env bash\nexec "/opt/chromium-v1" "$@"\n'
        if drift == "launcher"
        else '#!/usr/bin/env bash\nexec "/opt/chromium-v1" "$@"\n'
    )

    result, relay, launcher = _run_artifact_check(
        tmp_path,
        relay_source=relay_source,
        relay_target=relay_target,
        chromium_bin=chromium,
        launcher_target=launcher_target,
    )

    assert result.returncode != 0
    assert "run the browser desktop installer with write permission on /usr/local" in result.stderr
    assert relay.read_text(encoding="utf-8") == relay_target
    assert launcher.read_text(encoding="utf-8") == launcher_target
