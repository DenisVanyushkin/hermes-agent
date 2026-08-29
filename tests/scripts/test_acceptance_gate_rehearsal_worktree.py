import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = REPO_ROOT / "scripts" / "acceptance" / "gate-rehearsal-worktree.sh"


def test_gate_rehearsal_worktree_acceptance_script_is_executable():
    assert ACCEPTANCE.is_file()
    assert os.access(ACCEPTANCE, os.X_OK)
