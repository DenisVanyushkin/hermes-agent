"""Block messages must point at the reviewed mode, not the permissive one."""
from pathlib import Path

# Anchored to the repo, not the cwd: this assertion is about file contents, so
# it must not silently pass just because pytest was invoked from elsewhere.
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_no_block_message_recommends_bare_approve():
    src = (REPO_ROOT / "tools" / "approval.py").read_text()
    offenders = [
        line.strip()
        for line in src.splitlines()
        if "approvals.cron_mode: approve" in line
    ]
    assert offenders == [], f"block messages still push cron_mode: approve: {offenders}"
