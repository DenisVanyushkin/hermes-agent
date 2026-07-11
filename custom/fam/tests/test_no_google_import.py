"""Regression guard: `fam.cli` / `fam.mail` must import cleanly with the
Gmail API client libraries (google-auth, google-api-python-client) absent.

Mirrors test_no_pillow_import.py's technique and rationale: send_event_email
(Task 10) is the ONLY place allowed to touch google.*/googleapiclient.* --
lazy-imported inside the function body, and only on the branch that builds
real delegated credentials (the `_service` injection branch used by every
unit test in test_mail.py skips it entirely) -- see mail.py's module
docstring. So importing fam.mail or fam.cli at module level must never
require those packages to be installed (e.g. bin/fam's PATH-python3
fallback, same rationale as the Pillow guard).

This blocks `import google` / `import googleapiclient` (and any submodule)
at the import-system level via a sys.meta_path finder that raises
ImportError, purges any already-cached fam.cli/fam.mail/google* modules
from sys.modules, then forces a fresh import of fam.cli and fam.mail under
that block. If either module (or anything it imports eagerly) tries to
touch google/googleapiclient, the import blows up and the test fails
loudly instead of silently passing because those packages happened to
already be installed in this environment (they are, in this venv -- see
Task 10's report for the pinned versions).
"""
import importlib
import sys

_BLOCKED_ROOTS = ("google", "googleapiclient")


class _BlockGoogleLibs:
    """Meta path finder that makes `import google`/`import googleapiclient`
    (or any submodule) fail."""

    def find_spec(self, fullname, path, target=None):
        if fullname in _BLOCKED_ROOTS or fullname.startswith(
            tuple(f"{root}." for root in _BLOCKED_ROOTS)
        ):
            raise ImportError(f"blocked by test_no_google_import: {fullname}")
        return None  # defer to the normal finders for everything else


def _fresh_import_without_google(monkeypatch, module_names):
    # Purge already-cached copies of the target modules and of the google
    # libs themselves so the import below is guaranteed to re-execute their
    # module bodies.
    stale = [
        name for name in list(sys.modules)
        if name in _BLOCKED_ROOTS
        or name.startswith(tuple(f"{root}." for root in _BLOCKED_ROOTS))
        or name in module_names
        or name.startswith(tuple(f"{m}." for m in module_names))
    ]
    for name in stale:
        monkeypatch.delitem(sys.modules, name, raising=False)

    blocker = _BlockGoogleLibs()
    sys.meta_path.insert(0, blocker)
    try:
        for name in module_names:
            importlib.import_module(name)
    finally:
        sys.meta_path.remove(blocker)


def test_fam_mail_imports_without_google_libs(monkeypatch):
    _fresh_import_without_google(monkeypatch, ["fam.mail"])
    assert "google" not in sys.modules
    assert "googleapiclient" not in sys.modules


def test_fam_cli_imports_without_google_libs(monkeypatch):
    # fam.cli imports fam.mail at module level (for the `mail test`
    # subcommand and the cal add/update hook) -- this is the real
    # regression path bin/fam exercises for every non-mail command.
    _fresh_import_without_google(monkeypatch, ["fam.cli"])
    assert "google" not in sys.modules
    assert "googleapiclient" not in sys.modules
