"""Behavioural tests for the pre-import code manifest.

The live virtualenv is never touched: ``site.getsitepackages`` is redirected at
a temporary directory so every state — matching, changed, added, removed — can
be built and executed. Asserting these by reading the script would prove
nothing about what it does.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts/job_intel_site_integrity.py"


def load_module():
    spec = importlib.util.spec_from_file_location("job_intel_site_integrity", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(tmp_path, monkeypatch, files: dict[str, str]):
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    for name, body in files.items():
        (site_dir / name).write_text(body, encoding="utf-8")
    module = load_module()
    monkeypatch.setattr(module.site, "getsitepackages", lambda: [str(site_dir)])
    return module, site_dir


def run(module, action: str, manifest: Path) -> int:
    return module.main(["job_intel_site_integrity.py", action, str(manifest)])


def test_matching_manifest_verifies(tmp_path, monkeypatch) -> None:
    module, _ = prepare(tmp_path, monkeypatch, {"00-shim.pth": "import shim\n"})
    manifest = tmp_path / "site.manifest"

    assert run(module, "write", manifest) == 0
    assert run(module, "verify", manifest) == 0


def test_changed_pth_is_detected(tmp_path, monkeypatch) -> None:
    """A .pth executes before any import, so a silent edit would run ahead of
    the delivery kill-switch."""
    module, site_dir = prepare(tmp_path, monkeypatch, {"00-shim.pth": "import shim\n"})
    manifest = tmp_path / "site.manifest"
    run(module, "write", manifest)

    (site_dir / "00-shim.pth").write_text("import something_else\n", encoding="utf-8")

    assert run(module, "verify", manifest) == 4


def test_added_pth_is_detected(tmp_path, monkeypatch) -> None:
    module, site_dir = prepare(tmp_path, monkeypatch, {"00-shim.pth": "import shim\n"})
    manifest = tmp_path / "site.manifest"
    run(module, "write", manifest)

    (site_dir / "99-new.pth").write_text("import injected\n", encoding="utf-8")

    assert run(module, "verify", manifest) == 4


def test_added_sitecustomize_is_detected(tmp_path, monkeypatch) -> None:
    module, site_dir = prepare(tmp_path, monkeypatch, {"00-shim.pth": "import shim\n"})
    manifest = tmp_path / "site.manifest"
    run(module, "write", manifest)

    (site_dir / "sitecustomize.py").write_text("import os\n", encoding="utf-8")

    assert run(module, "verify", manifest) == 4


def test_removed_entry_is_detected(tmp_path, monkeypatch) -> None:
    module, site_dir = prepare(
        tmp_path, monkeypatch, {"00-shim.pth": "import shim\n", "01-other.pth": "import other\n"}
    )
    manifest = tmp_path / "site.manifest"
    run(module, "write", manifest)

    (site_dir / "01-other.pth").unlink()

    assert run(module, "verify", manifest) == 4


def test_missing_manifest_is_refused(tmp_path, monkeypatch) -> None:
    module, _ = prepare(tmp_path, monkeypatch, {"00-shim.pth": "import shim\n"})

    assert run(module, "verify", tmp_path / "absent.manifest") == 3


def test_unrelated_file_is_ignored(tmp_path, monkeypatch) -> None:
    """Control group: only files Python auto-executes belong in the manifest."""
    module, site_dir = prepare(tmp_path, monkeypatch, {"00-shim.pth": "import shim\n"})
    manifest = tmp_path / "site.manifest"
    run(module, "write", manifest)

    (site_dir / "regular_module.py").write_text("x = 1\n", encoding="utf-8")

    assert run(module, "verify", manifest) == 0
