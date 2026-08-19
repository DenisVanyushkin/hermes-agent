"""The runtime copy must be self-contained.

Cron runs scripts from $HERMES_HOME/scripts, not from the repo, and the syncer
historically copied a flat `scripts/*.sh` `scripts/*.py` set by basename. The
moment a synced script sources a helper from a subdirectory, the runtime copy
breaks while the repo copy keeps passing every test -- the failure only shows up
on the next scheduled run, hours later, as a bare "No such file or directory".
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SYNCER = REPO / "scripts" / "sync-runtime-scripts.sh"

_SOURCE_RE = re.compile(r'^\s*(?:source|\.)\s+"\$SCRIPT_DIR/([^"]+)"', re.M)


def _sync_into(tmp_path: Path) -> Path:
    home = tmp_path / "hermes-home"
    home.mkdir()
    proc = subprocess.run(
        ["bash", str(SYNCER)],
        capture_output=True,
        text=True,
        env={"HERMES_HOME": str(home), "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return home / "scripts"


class TestRuntimeCopyIsSelfContained:
    def test_every_sourced_helper_lands_in_the_runtime_dir(self, tmp_path):
        target = _sync_into(tmp_path)
        missing = []
        for script in sorted(target.glob("*.sh")):
            for rel in _SOURCE_RE.findall(script.read_text(encoding="utf-8")):
                if not (target / rel).exists():
                    missing.append(f"{script.name} -> {rel}")
        assert missing == []

    def test_the_git_retry_helper_is_synced(self, tmp_path):
        target = _sync_into(tmp_path)
        assert (target / "lib" / "git-retry.sh").is_file()

    def test_synced_preflight_can_actually_load_its_helper(self, tmp_path):
        """bash -n does not follow `source`, so exercise the real resolution."""
        target = _sync_into(tmp_path)
        probe = tmp_path / "probe.sh"
        probe.write_text(
            'set -euo pipefail\n'
            f'SCRIPT_DIR="{target}"\n'
            'source "$SCRIPT_DIR/lib/git-retry.sh"\n'
            'declare -F git_fetch_retry >/dev/null || exit 3\n',
            encoding="utf-8",
        )
        proc = subprocess.run(["bash", str(probe)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
