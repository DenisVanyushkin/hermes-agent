"""The exporter is a second opener of job_intel.sqlite3 (WAL, rw mount).

Its Debian base ships SQLite 3.46.1: inside the WAL-reset affected range and
unpatched for the 2026 FTS5/session CVEs. It must carry the same statically
built pysqlite3 as the host venv, or the concurrent-writer condition survives.
"""

import pathlib

DOCKERFILE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "deploy" / "docker" / "job-intel-exporter.Dockerfile"
)
SHIM = DOCKERFILE.parent / "00-pysqlite3-shim.pth"


def _text():
    return DOCKERFILE.read_text(encoding="utf-8")


def test_pins_the_amalgamation_by_url_and_hash():
    text = _text()
    assert "ARG SQLITE_AMALGAMATION_URL" in text, "the URL must be a build arg"
    assert "ARG SQLITE_AMALGAMATION_SHA3" in text, "the hash must be a build arg"
    assert "sqlite-amalgamation-3530400" in text, "default must pin 3.53.4"


def test_builds_statically_with_fts5():
    text = _text()
    # Static linkage comes from placing the amalgamation in the pysqlite3
    # checkout root — this pysqlite3 release has no `build_static` command
    # (that belonged to an older version; using it fails the build).
    assert "cp sqlite-amalgamation-" in text, "amalgamation must land in the checkout root"
    assert "build_static" not in text, "build_static does not exist in current pysqlite3"
    assert "SQLITE_ENABLE_FTS5" in text, "feature flags must be explicit"
    assert "SQLITE_OMIT_LOAD_EXTENSION" not in text, "load_extension must stay in"
    # setuptools >= 83 rejects NAME=VALUE in build_ext --define; bare names only.
    assert "=250000" not in text, "NAME=VALUE defines break the build"
    assert "ldd" in text, "the build must assert static linkage, not assume it"


def test_verifies_the_download_with_sha3_not_sha256():
    text = _text()
    assert "sha3_256" in text, "sqlite.org publishes SHA3-256, not SHA-256"
    assert "sha256sum" not in text, "sha256sum will never match the published index"


def test_fails_the_build_on_a_stale_sqlite():
    """A wrong version must fail the image, not ship."""
    assert "(3,53,4)" in _text(), "in-build version assertion missing"


def test_ships_the_shim_as_a_pth():
    """A venv/system sitecustomize.py can be shadowed; a .pth cannot.

    Debian ships /usr/lib/pythonX.Y/sitecustomize.py and the stdlib dir
    precedes site-packages on sys.path, so only one module of that name is
    ever imported. Every .pth in site-packages is processed instead.
    """
    text = _text()
    assert "00-pysqlite3-shim.pth" in text, "the alias hook must be copied in"
    assert "sitecustomize" not in text, "sitecustomize is shadowable; use the .pth"
    assert SHIM.is_file(), "deploy/docker/00-pysqlite3-shim.pth must exist"
    shim = SHIM.read_text(encoding="utf-8")
    assert "pysqlite3" in shim
    exec_lines = [l for l in shim.splitlines() if l.strip() and not l.startswith("#")]
    assert len(exec_lines) == 1, ".pth allows exactly one executable line"
    assert exec_lines[0].startswith("import "), ".pth executable lines must start with import"
