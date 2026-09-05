"""The ordering guarantee, proved by executing the guard.

Reading the shell told us the order looked right once before, and it was not:
the verification ran under the very interpreter it verified. These tests give
the guard a target command that writes a marker, and assert on whether the
marker exists — which is the only way to see whether the gate opened.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "scripts/job_intel_startup_guard.sh"
CHECKER = ROOT / "scripts/job_intel_site_integrity.py"
SYSTEM_PYTHON = "/usr/bin/python3.12"


def build(tmp_path: Path):
    venv = tmp_path / "venv"
    (venv / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
    (venv / "bin").mkdir()
    os.symlink(SYSTEM_PYTHON, venv / "bin" / "python")
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.12.3\n", encoding="utf-8")

    manifest = tmp_path / "site.manifest"
    subprocess.run(
        [SYSTEM_PYTHON, "-I", "-S", str(CHECKER), "write", str(manifest), str(venv)],
        check=True, capture_output=True,
    )
    os.chmod(manifest, 0o644)

    marker = tmp_path / "target-ran"
    target = tmp_path / "target.sh"
    target.write_text(f"#!/bin/sh\necho ran > {marker}\n", encoding="utf-8")
    os.chmod(target, 0o755)
    return venv, manifest, marker, target


def run_guard(manifest: Path, venv: Path, target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(GUARD), str(manifest), str(venv), str(CHECKER),
         str(os.getuid()), str(target)],
        capture_output=True, text=True, check=False,
    )


def test_intact_tree_lets_the_target_run(tmp_path) -> None:
    """Control group: without this the refusal tests could pass for any reason."""
    venv, manifest, marker, target = build(tmp_path)

    result = run_guard(manifest, venv, target)

    assert result.returncode == 0, result.stderr
    assert marker.exists(), "the guard never ran the target command"


def test_tampered_tree_stops_the_target_from_running(tmp_path) -> None:
    venv, manifest, marker, target = build(tmp_path)
    (venv / "lib" / "python3.12" / "site-packages" / "99-new.pth").write_text(
        "import os\n", encoding="utf-8"
    )

    result = run_guard(manifest, venv, target)

    assert result.returncode == 1
    assert not marker.exists(), "the target ran despite an unverified startup tree"


def test_swapped_interpreter_symlink_stops_the_target(tmp_path) -> None:
    """bin/python is a symlink the owner of the venv can replace."""
    venv, manifest, marker, target = build(tmp_path)
    (venv / "bin" / "python").unlink()
    os.symlink("/bin/true", venv / "bin" / "python")

    result = run_guard(manifest, venv, target)

    assert result.returncode == 1
    assert not marker.exists()


def test_alternative_dist_packages_stops_the_target(tmp_path) -> None:
    """A Debian venv resolves several site directories; a new one executes its
    own .pth files and the old manifest would not have covered it."""
    venv, manifest, marker, target = build(tmp_path)
    alt = venv / "lib" / "python3.12" / "dist-packages"
    alt.mkdir(parents=True)
    (alt / "99-alt.pth").write_text("import os\n", encoding="utf-8")

    result = run_guard(manifest, venv, target)

    assert result.returncode == 1
    assert not marker.exists()


def test_changed_pyvenv_cfg_stops_the_target(tmp_path) -> None:
    venv, manifest, marker, target = build(tmp_path)
    (venv / "pyvenv.cfg").write_text("home = /somewhere/else\n", encoding="utf-8")

    result = run_guard(manifest, venv, target)

    assert result.returncode == 1
    assert not marker.exists()


def test_group_writable_manifest_is_refused(tmp_path) -> None:
    """A manifest the collecting user can rewrite blesses whatever it likes."""
    venv, manifest, marker, target = build(tmp_path)
    os.chmod(manifest, 0o664)

    result = run_guard(manifest, venv, target)

    assert result.returncode == 1
    assert "group- or world-writable" in result.stderr
    assert not marker.exists()


def test_symlinked_manifest_is_refused(tmp_path) -> None:
    venv, manifest, marker, target = build(tmp_path)
    link = tmp_path / "manifest-link"
    os.symlink(manifest, link)

    result = run_guard(link, venv, target)

    assert result.returncode == 1
    assert "not a regular file" in result.stderr
    assert not marker.exists()


def test_manifest_owned_by_someone_else_is_refused(tmp_path) -> None:
    """In production the expected owner is root; the unit passes it explicitly
    so the generated collection environment cannot lower the bar."""
    venv, manifest, marker, target = build(tmp_path)

    result = subprocess.run(
        ["bash", str(GUARD), str(manifest), str(venv), str(CHECKER), "0", str(target)],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 1
    assert "must be owned by uid 0" in result.stderr
    assert not marker.exists()


def test_the_trusted_interpreter_cannot_be_chosen_by_the_environment(tmp_path) -> None:
    """Codex's reproduction: /bin/true ignores the checker arguments and exits
    0, so a guard that took its interpreter from the environment would verify
    nothing and then exec the target anyway."""
    venv, manifest, marker, target = build(tmp_path)
    (venv / "lib" / "python3.12" / "site-packages" / "99-tampered.pth").write_text(
        "import os\n", encoding="utf-8"
    )

    result = subprocess.run(
        ["bash", str(GUARD), str(manifest), str(venv), str(CHECKER),
         str(os.getuid()), str(target)],
        capture_output=True, text=True, check=False,
        env=dict(os.environ, JOB_INTEL_SYSTEM_PYTHON="/bin/true"),
    )

    assert result.returncode == 1, result.stdout
    assert "not selectable" in result.stderr
    assert not marker.exists(), "the target ran with verification bypassed"


def test_the_variable_is_refused_even_on_an_intact_tree(tmp_path) -> None:
    """Refusing only when the tree is already tampered would leave the
    redirection usable as a foothold."""
    venv, manifest, marker, target = build(tmp_path)

    result = subprocess.run(
        ["bash", str(GUARD), str(manifest), str(venv), str(CHECKER),
         str(os.getuid()), str(target)],
        capture_output=True, text=True, check=False,
        env=dict(os.environ, JOB_INTEL_SYSTEM_PYTHON="/usr/bin/python3.12"),
    )

    assert result.returncode == 1
    assert not marker.exists()
