import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = REPO_ROOT / "scripts" / "acceptance" / "publish-runtime.sh"
RUNTIME_FILES = (
    "upstream-sync-finalize.sh",
    "run-fork-tests.sh",
    "upstream_sync_gate.py",
    "sync-local-customizations.sh",
    "upstream-sync-smoketest.sh",
    "upstream_sync_triage.py",
    "upstream_sync_slack.py",
    "upstream_sync_apply.py",
)


def test_publish_runtime_dry_run_copies_contract_set_without_touching_systemd(tmp_path):
    target = tmp_path / "published"
    env = os.environ.copy()
    env.update(
        {
            "HERMES_RUNTIME_SOURCE_ROOT": str(REPO_ROOT),
            "HERMES_RUNTIME_TARGET_ROOT": str(target),
            "HERMES_RUNTIME_DRY_RUN": "1",
        }
    )

    result = subprocess.run(
        [str(ACCEPTANCE), "--dry-run"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "dry_run=1" in result.stdout
    assert "path_unit=not_touched" in result.stdout
    assert "files=8" in result.stdout
    assert not (target / "state" / "finalize-request.json").exists()

    for name in RUNTIME_FILES:
        source = REPO_ROOT / "scripts" / name
        published = target / "scripts" / name
        assert published.is_file(), name
        assert published.read_bytes() == source.read_bytes(), name
