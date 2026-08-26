"""The verification must not be performed by the interpreter it verifies.

A .pth file executes during interpreter startup, before any code in this
project runs. Verifying the target venv *with* the target venv therefore lets
the suspect code run first and reports on it afterwards — a guard placed after
the door. These tests build a minimal venv whose .pth writes a marker file, and
assert on whether that marker appears.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

CHECKER = Path(__file__).resolve().parents[2] / "scripts/job_intel_site_integrity.py"
SYSTEM_PYTHON = "/usr/bin/python3.12"


def build_venv(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A minimal venv: pyvenv.cfg, a python symlink and a site-packages dir."""
    venv = tmp_path / "venv"
    site_packages = venv / "lib" / f"python3.12" / "site-packages"
    site_packages.mkdir(parents=True)
    (venv / "bin").mkdir()
    os.symlink(SYSTEM_PYTHON, venv / "bin" / "python")
    (venv / "pyvenv.cfg").write_text(
        f"home = /usr/bin\ninclude-system-site-packages = false\nversion = 3.12.3\n",
        encoding="utf-8",
    )
    marker = tmp_path / "marker"
    (site_packages / "00-hostile.pth").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    return venv, site_packages, marker


def test_the_target_venv_really_executes_its_pth(tmp_path) -> None:
    """Control group: without this, the other tests would prove nothing."""
    venv, _, marker = build_venv(tmp_path)

    subprocess.run([str(venv / "bin" / "python"), "-c", "pass"], check=True)

    assert marker.exists(), "the fixture venv does not execute its own .pth"


def test_verification_by_the_system_interpreter_does_not_execute_target_pth(tmp_path) -> None:
    venv, site_packages, marker = build_venv(tmp_path)
    manifest = tmp_path / "site.manifest"

    subprocess.run(
        [SYSTEM_PYTHON, "-I", "-S", str(CHECKER), "write", str(manifest), str(site_packages)],
        check=True,
        capture_output=True,
    )
    marker.unlink(missing_ok=True)

    result = subprocess.run(
        [SYSTEM_PYTHON, "-I", "-S", str(CHECKER), "verify", str(manifest), str(site_packages)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists(), "verification executed the code it was meant to verify"


def test_a_tampered_startup_tree_is_refused_without_executing_it(tmp_path) -> None:
    """The two halves of the guarantee: refusal, and no execution before it."""
    venv, site_packages, marker = build_venv(tmp_path)
    manifest = tmp_path / "site.manifest"
    subprocess.run(
        [SYSTEM_PYTHON, "-I", "-S", str(CHECKER), "write", str(manifest), str(site_packages)],
        check=True,
        capture_output=True,
    )
    marker.unlink(missing_ok=True)

    (site_packages / "00-hostile.pth").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('tampered')\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [SYSTEM_PYTHON, "-I", "-S", str(CHECKER), "verify", str(manifest), str(site_packages)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4, result.stdout
    assert "startup tree changed" in result.stderr
    assert not marker.exists(), "the tampered .pth ran before it was refused"


def test_a_module_imported_by_a_pth_is_covered(tmp_path) -> None:
    """The live shim imports _virtualenv.py and pysqlite3; hashing only the
    .pth files would leave that code unguarded."""
    _, site_packages, _ = build_venv(tmp_path)
    (site_packages / "_helper.py").write_text("x = 1\n", encoding="utf-8")
    manifest = tmp_path / "site.manifest"
    subprocess.run(
        [SYSTEM_PYTHON, "-I", "-S", str(CHECKER), "write", str(manifest), str(site_packages)],
        check=True,
        capture_output=True,
    )

    (site_packages / "_helper.py").write_text("x = 2\n", encoding="utf-8")

    result = subprocess.run(
        [SYSTEM_PYTHON, "-I", "-S", str(CHECKER), "verify", str(manifest), str(site_packages)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 4
    assert "_helper.py" in result.stderr


def test_missing_manifest_is_refused(tmp_path) -> None:
    _, site_packages, _ = build_venv(tmp_path)

    result = subprocess.run(
        [SYSTEM_PYTHON, "-I", "-S", str(CHECKER), "verify",
         str(tmp_path / "absent.manifest"), str(site_packages)],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 3
    assert "site manifest missing" in result.stderr


def test_missing_site_root_is_refused(tmp_path) -> None:
    result = subprocess.run(
        [SYSTEM_PYTHON, "-I", "-S", str(CHECKER), "verify",
         str(tmp_path / "m"), str(tmp_path / "no-such-tree")],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 6
    assert "target site-packages not found" in result.stderr


def test_verification_refuses_an_interpreter_that_ran_site_init(tmp_path) -> None:
    """Without -S the interpreter has already executed startup code, and if it
    is the target venv the guarded code has already run."""
    _, site_packages, _ = build_venv(tmp_path)
    manifest = tmp_path / "site.manifest"
    subprocess.run(
        [SYSTEM_PYTHON, "-I", "-S", str(CHECKER), "write", str(manifest), str(site_packages)],
        check=True, capture_output=True,
    )

    result = subprocess.run(  # no -S: site initialisation happened
        [SYSTEM_PYTHON, "-I", str(CHECKER), "verify", str(manifest), str(site_packages)],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 5
    assert "ran site init" in result.stderr
