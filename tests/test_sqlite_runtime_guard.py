"""Guards for the self-supplied SQLite runtime.

Upstream already detects the WAL-reset bug (hermes_state.is_sqlite_wal_reset_vulnerable,
hermes_cli/sqlite_runtime.py, the `hermes doctor` check, and
tests/test_sqlite_wal_reset_gate.py). This module deliberately does NOT
re-implement that. It asserts two things upstream cannot:

1. That this deployment's effective runtime is actually non-vulnerable —
   using upstream's own predicate as the oracle, because neither apt
   (3.45.1, LTS-pinned) nor uv's CPython (3.50.4) supplies a fixed SQLite,
   so we compile our own and alias sqlite3 -> pysqlite3.
2. That repo code stays inside the API surface pysqlite3 can serve.

Plan: docs/superpowers/plans/2026-07-30-sqlite-self-supplied-runtime.md
"""

import pathlib
import re
import sqlite3
import subprocess

from hermes_state import is_sqlite_wal_reset_vulnerable

REPO = pathlib.Path(__file__).resolve().parents[1]

# Present in CPython 3.12's stdlib sqlite3, absent from pysqlite3 0.5.x.
# `serialize`/`deserialize` are deliberately EXCLUDED: the repo has legitimate
# non-sqlite `.serialize()` calls (Matrix SDK objects in
# plugins/platforms/matrix/adapter.py), so matching them would make this guard
# permanently red for the wrong reason. A receiver-blind substring scan cannot
# tell those apart; if a sqlite3 Connection ever needs serialize(), the
# reviewer rule is to catch it, not this test.
#
# `autocommit` is matched as an attribute or keyword with optional spacing,
# because `conn.autocommit = True` is the idiomatic form and a bare
# `autocommit=` pattern would miss it.
STDLIB_ONLY = re.compile(
    r"\.autocommit\s*=|\bautocommit\s*=\s*(?:True|False|sqlite3\.)"
    r"|\.blobopen\(|\bcreate_window_function\b|\.setlimit\(|\.getlimit\("
)

# Matches any import statement that brings the sqlite3 module into scope:
# `import sqlite3`, `import sqlite3 as db`, `from sqlite3 import connect`,
# `import contextlib, sqlite3`. Anchored per-line (^...$ via MULTILINE) so a
# bare mention of the word "sqlite3" in a comment or string literal does not
# count as an import. Deliberately not a full parse of Python import syntax —
# a regex over import statements is sufficient for this guard's purpose.
IMPORT_SQLITE3 = re.compile(r"^\s*(?:import|from)\s+.*\bsqlite3\b", re.MULTILINE)

EXCLUDED_PATH_PARTS = ("/venv/", "/.venv/", "/.uv-cache/", "site-packages", "/node_modules/")

# This file necessarily contains the very patterns it searches for.
SELF = pathlib.Path(__file__).name


def _imports_sqlite3(text):
    """True if `text` contains an import statement that brings sqlite3 into scope.

    This is the actual gating predicate used by _sqlite3_importing_python_files
    below, factored out so it can be exercised directly and hermetically by
    test_the_import_scoping_actually_discriminates.
    """
    return bool(IMPORT_SQLITE3.search(text))


def _sqlite3_importing_python_files():
    """Tracked .py files, filtered to those that import sqlite3.

    The guard protects sqlite3 only, but `autocommit` also exists on psycopg2
    connections and `.serialize()` on Matrix SDK objects (see
    plugins/platforms/matrix/adapter.py) — a receiver-blind substring scan
    cannot tell those apart. A file that never imports sqlite3 cannot hold a
    locally-created sqlite3.Connection, so restricting the scan to sqlite3
    importers makes the scope match the guard's stated purpose. This is
    deliberately an import check, not a path exclusion: a path exclusion rots
    the moment that code moves, while this criterion travels with the file.

    Each file is read exactly once; the same text is reused for the import
    check and the offender scan.
    """
    listing = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "*.py"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    files = []
    for rel in listing:
        path = REPO / rel
        if any(part in "/" + rel for part in EXCLUDED_PATH_PARTS):
            continue
        if pathlib.Path(rel).name == SELF:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not _imports_sqlite3(text):
            continue
        files.append((path, text))
    return files


def _offenders_in_text(rel_label, text):
    return [
        f"{rel_label}:{lineno}: {line.strip()}"
        for lineno, line in enumerate(text.splitlines(), 1)
        if STDLIB_ONLY.search(line)
    ]


def test_no_apis_pysqlite3_lacks():
    """Using one of these would break at runtime once sqlite3 is aliased."""
    offenders = []
    for path, text in _sqlite3_importing_python_files():
        offenders.extend(_offenders_in_text(path.relative_to(REPO), text))
    assert offenders == [], (
        "APIs pysqlite3 does not provide:\n" + "\n".join(offenders)
    )


def test_the_guard_can_actually_fire():
    """A guard that cannot fail is not a guard. Prove the regex matches."""
    assert STDLIB_ONLY.search("conn.autocommit = True")
    assert STDLIB_ONLY.search("sqlite3.connect(db, autocommit=False)")
    assert STDLIB_ONLY.search("blob = conn.blobopen('t', 'c', 1)")
    assert not STDLIB_ONLY.search("payload = encrypted_file.serialize()")


def test_the_import_scoping_actually_discriminates():
    """Prove the sqlite3-import gate itself works, hermetically, on the real
    predicate (_imports_sqlite3) rather than re-deriving its own copy.

    No files are created under the repo for this. It covers every import
    form the guard claims to handle, plus a negative case proving a mere
    mention of the word "sqlite3" in a comment or string does not count,
    and finally shows that the regex alone (STDLIB_ONLY / _offenders_in_text)
    matches both a sqlite3 snippet and a psycopg2 snippet identically — so
    the import gate, not the regex, is what must do the discriminating.
    """
    assert _imports_sqlite3("import sqlite3\n")
    assert _imports_sqlite3("import sqlite3 as db\n")
    assert _imports_sqlite3("from sqlite3 import connect\n")
    assert _imports_sqlite3("import contextlib, sqlite3\n")

    assert not _imports_sqlite3("import psycopg2\nconn.autocommit = True\n")
    assert not _imports_sqlite3(
        "# this module talks to sqlite3 indirectly\nDRIVER = 'sqlite3'\n"
    )

    with_import = "import sqlite3\nconn.autocommit = True\n"
    without_import = "import psycopg2\nconn.autocommit = True\n"
    # The regex alone cannot tell these apart — both are flagged.
    assert _offenders_in_text("snippet.py", with_import) != []
    assert _offenders_in_text("snippet.py", without_import) != []
    # Only the import gate discriminates them.
    assert _imports_sqlite3(with_import)
    assert not _imports_sqlite3(without_import)


def test_runtime_is_not_wal_reset_vulnerable():
    """Upstream's own predicate is the oracle — we do not re-derive the range.

    Fails on the unmigrated host (SQLite 3.45.1) and passes once the
    sitecustomize shim aliases sqlite3 to the statically built pysqlite3.
    """
    assert not is_sqlite_wal_reset_vulnerable(), (
        f"linked SQLite {sqlite3.sqlite_version} still has the WAL-reset bug "
        "(https://sqlite.org/wal.html#walresetbug)"
    )


def test_fts5_and_trigram_actually_query():
    """state.db carries messages_fts and messages_fts_trigram."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
    conn.execute("CREATE VIRTUAL TABLE g USING fts5(body, tokenize='trigram')")
    conn.execute("INSERT INTO t(body) VALUES ('hermes gateway')")
    conn.execute("INSERT INTO g(body) VALUES ('hermes gateway')")
    assert conn.execute("SELECT count(*) FROM t WHERE t MATCH 'gateway'").fetchone()[0] == 1
    # Query the trigram table too: creation alone would not catch a tokenizer
    # that registers but does not work.
    assert conn.execute("SELECT count(*) FROM g WHERE g MATCH 'atew'").fetchone()[0] == 1


def test_extension_loading_compiled_in():
    """hermes_state.load_fts5_cjk_extension needs these to exist."""
    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    conn.enable_load_extension(False)
