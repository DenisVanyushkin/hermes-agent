"""The exporter is a second opener of job_intel.sqlite3 (WAL, rw mount).

Its Debian base ships SQLite 3.46.1: inside the WAL-reset affected range and
unpatched for the 2026 FTS5/session CVEs. It must carry the same statically
built pysqlite3 as the host venv, or the concurrent-writer condition survives.
"""

import pathlib
import re

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


def test_pins_pysqlite3_by_commit():
    """The SQLite amalgamation is SHA3-verified; the code that compiles it
    (pysqlite3 itself) must be pinned too, or a weekly unattended build
    compiles whatever unreviewed commit sits at the tip of master that day."""
    text = _text()
    assert "ARG PYSQLITE3_COMMIT=" in text, "pysqlite3 checkout must be pinned by commit"
    match = re.search(r"ARG PYSQLITE3_COMMIT=([0-9a-f]{40})\b", text)
    assert match, "pin must be a full 40-character commit SHA"
    assert "rev-parse HEAD" in text, "the build must verify the checkout landed on the pin"
    assert "PYSQLITE3_COMMIT" in text.split("rev-parse HEAD")[1][:200], (
        "the pin must actually be compared against the resolved HEAD"
    )


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
    # A bare "ldd" substring would also match a comment that merely mentions
    # ldd (the Dockerfile now has one, explaining why the assertion exists) —
    # assert on the actual guard: grepping ldd's output for libsqlite3 and
    # failing the build if found.
    assert "grep -qi libsqlite3" in text, (
        "the build must assert static linkage by grepping ldd output for "
        "libsqlite3, not merely mention ldd in a comment"
    )
    assert re.search(r"if ldd .*\| grep -qi libsqlite3.*exit 1", text), (
        "the libsqlite3 grep must actually gate the build's exit code"
    )


def test_verifies_the_download_with_sha3_not_sha256():
    text = _text()
    assert "sha3_256" in text, "sqlite.org publishes SHA3-256, not SHA-256"
    assert "sha256sum" not in text, "sha256sum will never match the published index"


def test_fails_the_build_on_a_stale_sqlite():
    """A wrong version must fail the image, not ship."""
    text = _text()
    # A bare "(3,53,4)" substring would pass even if someone deleted the
    # comparison and left only a comment mentioning the tuple. Assert on the
    # actual branching expression that turns a stale version into exit 1.
    assert "v >= (3,53,4)" in text, "in-build version comparison missing"
    assert "sys.exit(0 if v >= (3,53,4) else 1)" in text, (
        "the version gate must actually branch the exit code, not merely "
        "mention the tuple in a comment"
    )
    assert "sys.exit(0 if tuple(int(x) for x in sqlite3.sqlite_version.split('.')) >= (3,53,4) else 1)" in text, (
        "stage 2's effective-runtime check must also branch on the same gate"
    )


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
