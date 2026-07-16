"""Regression guard: `fam.cli` / `fam.grid` must import cleanly with Pillow
absent.

Pillow (PIL) is only needed inside grid.render_month()/render_week() (lazy
import — see grid.py's module docstring); the *module-level* import of
fam.grid, and of fam.cli (which imports fam.grid to wire up `cal grid`),
must never require PIL to be installed. Every other fam subcommand has to
keep working in environments without Pillow (e.g. bin/fam's PATH-python3
fallback, used inside the agent sandbox before Pillow is installed there).

This blocks `import PIL` (and any `PIL.*` submodule) at the import-system
level via a sys.meta_path finder that raises ImportError, purges any
already-cached fam.cli/fam.grid/PIL modules from sys.modules, then forces a
fresh import of fam.cli and fam.grid under that block. If either module (or
anything it imports eagerly) tries to touch PIL, the import blows up and the
test fails loudly instead of silently passing because PIL happened to
already be importable in this environment.
"""
import importlib
import sys


class _BlockPIL:
    """Meta path finder that makes `import PIL` (or any submodule) fail."""

    def find_spec(self, fullname, path, target=None):
        if fullname == "PIL" or fullname.startswith("PIL."):
            raise ImportError(f"blocked by test_no_pillow_import: {fullname}")
        return None  # defer to the normal finders for everything else


def _fresh_import_without_pil(monkeypatch, module_names):
    # Purge already-cached copies of the target modules and of PIL itself so
    # the import below is guaranteed to re-execute their module bodies.
    stale = [
        name for name in list(sys.modules)
        if name == "PIL" or name.startswith("PIL.")
        or name in module_names
        or name.startswith(tuple(f"{m}." for m in module_names))
    ]
    for name in stale:
        monkeypatch.delitem(sys.modules, name, raising=False)

    blocker = _BlockPIL()
    sys.meta_path.insert(0, blocker)
    try:
        for name in module_names:
            importlib.import_module(name)
    finally:
        sys.meta_path.remove(blocker)


def test_fam_grid_imports_without_pillow(monkeypatch):
    _fresh_import_without_pil(monkeypatch, ["fam.grid"])
    assert "PIL" not in sys.modules


def test_fam_cli_imports_without_pillow(monkeypatch):
    # fam.cli imports fam.grid at module level (for the `cal grid`
    # subcommand); this is the real regression path bin/fam exercises for
    # every non-grid command.
    _fresh_import_without_pil(monkeypatch, ["fam.cli"])
    assert "PIL" not in sys.modules
