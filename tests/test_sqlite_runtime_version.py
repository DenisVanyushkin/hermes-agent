"""Guards for the pysqlite3 shim that carries SQLite >= 3.53.4.

See docs/superpowers/plans/2026-07-29-sqlite-wal-reset-migration.md.
The system libsqlite3 (3.45.1) is affected by the WAL-reset corruption bug
fixed upstream in 3.51.3; the gateway venv aliases sqlite3 -> pysqlite3.
"""

import pathlib
import re
import subprocess
import sqlite3

REPO = pathlib.Path(__file__).resolve().parents[1]

# APIs present in CPython 3.12's stdlib sqlite3 but not in pysqlite3 0.5.x.
STDLIB_ONLY = re.compile(
    r"autocommit=|\.blobopen\(|create_window_function|\.setlimit\(|\.getlimit\("
)

EXCLUDED = ("/venv/", "/.venv/", "/.uv-cache/", "site-packages", "/node_modules/")


def _repo_python_files():
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "*.py"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    return [REPO / p for p in out if not any(x in "/" + p for x in EXCLUDED)]


def test_no_stdlib_only_sqlite_apis():
    """pysqlite3 lacks these; using one would break at runtime under the shim."""
    offenders = []
    for path in _repo_python_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if STDLIB_ONLY.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert offenders == [], "stdlib-only sqlite3 APIs found:\n" + "\n".join(offenders)


def test_fts5_and_trigram_available():
    """state.db carries messages_fts and messages_fts_trigram."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
    conn.execute("CREATE VIRTUAL TABLE g USING fts5(body, tokenize='trigram')")
    conn.execute("INSERT INTO t(body) VALUES ('hermes gateway')")
    assert conn.execute("SELECT count(*) FROM t WHERE t MATCH 'gateway'").fetchone()[0] == 1


def test_extension_loading_compiled_in():
    """hermes_state.load_fts5_cjk_extension needs these to exist."""
    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    conn.enable_load_extension(False)
