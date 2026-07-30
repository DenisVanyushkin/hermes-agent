#!/usr/bin/env python3
"""Keep the self-supplied SQLite current, without a human in the loop.

Three processes (gateway venv, job-intel-exporter, hermes-webui) run
pysqlite3 with a statically compiled SQLite, because no available supplier
ships a fixed one: Ubuntu 24.04 pins libsqlite3-0 at 3.45.1 for the life of
the LTS, and uv's python-build-standalone CPython bundles 3.50.4 — both
inside the WAL-reset affected range. That took those processes out of apt's
update path, so this job replaces it.

Run by hermes cron in script mode, weekly. Script mode sets cwd to the
script directory (outside the repo), so the repo is resolved from HERMES_HOME.
"""

import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.request

DOWNLOAD_URL = "https://www.sqlite.org/download.html"
HERMES_HOME = pathlib.Path(os.environ.get("HERMES_HOME", pathlib.Path.home() / ".hermes"))
REPO = HERMES_HOME / "hermes-agent"
VENV_PY = REPO / "venv" / "bin" / "python"
BUILD_ROOT = HERMES_HOME / "build"
WHEEL_DIR = BUILD_ROOT / "wheels"
RESULT_PATH = HERMES_HOME / "state" / "sqlite_autoupdate_last.json"

PRODUCT_RE = re.compile(
    r"^PRODUCT,(\d+)\.(\d+)\.(\d+),(\d{4}/sqlite-amalgamation-\d+\.zip),\d+,([0-9a-f]+)",
    re.MULTILINE,
)


class IntegrityError(RuntimeError):
    """The downloaded amalgamation does not match its published SHA3-256."""


def _is_vulnerable(version_info):
    """Ask upstream's detector, never a local copy of the affected range.

    hermes_cli.sqlite_runtime is the single source of truth for which SQLite
    versions carry the WAL-reset bug; duplicating the range here would let the
    two drift when upstream learns something new.
    """
    sys.path.insert(0, str(REPO))
    from hermes_cli.sqlite_runtime import is_sqlite_wal_reset_vulnerable

    return is_sqlite_wal_reset_vulnerable(tuple(version_info))


def parse_product_index(page_text):
    """Newest amalgamation version, URL and SHA3-256 from download.html.

    sqlite.org embeds a CSV index in an HTML comment so tools need not scrape
    prose:  PRODUCT,VERSION,RELATIVE-URL,SIZE-IN-BYTES,SHA3-HASH
    The page also lists prior releases, so pick by version, not row order.
    """
    rows = PRODUCT_RE.findall(page_text)
    if not rows:
        raise RuntimeError("no PRODUCT amalgamation row found in download.html")
    major, minor, patch, path, sha3 = max(
        rows, key=lambda r: (int(r[0]), int(r[1]), int(r[2]))
    )
    return {
        "version": (int(major), int(minor), int(patch)),
        "url": f"https://www.sqlite.org/{path}",
        "sha3": sha3,
    }


def resolve_latest_upstream():
    with urllib.request.urlopen(DOWNLOAD_URL, timeout=30) as response:
        return parse_product_index(response.read().decode("utf-8", "replace"))


def verify_sha3(path, expected):
    """SQLite publishes SHA3-256, not SHA-256. A mismatch aborts the build.

    This pipeline downloads and compiles C source unattended and installs the
    result into the live gateway; skipping this check would make a substituted
    download a code-execution path with nobody in front of it.
    """
    digest = hashlib.sha3_256(pathlib.Path(path).read_bytes()).hexdigest()
    if digest != expected:
        raise IntegrityError(f"{path}: sha3-256 {digest} != published {expected}")
    return True


def should_upgrade(current, latest):
    """Forward only, and never to a version upstream still calls vulnerable."""
    if _is_vulnerable(latest):
        return False
    return latest > current


REQUIRED_CHECKS = ("fts5", "trigram", "load_extension", "static")

SETUP_CFG = (
    "[build_ext]\n"
    "define=SQLITE_ENABLE_FTS5,SQLITE_ENABLE_JSON1,SQLITE_ENABLE_RTREE,"
    "SQLITE_ENABLE_MATH_FUNCTIONS,SQLITE_ENABLE_COLUMN_METADATA,"
    "SQLITE_ENABLE_STAT4,SQLITE_ENABLE_DBSTAT_VTAB,SQLITE_SOUNDEX,"
    "SQLITE_THREADSAFE\n"
)
# Bare macro names only. setuptools >= 83 turns a NAME=VALUE entry into
# -DNAME=VALUE=1 and the build fails. SQLITE_MAX_VARIABLE_NUMBER=250000 and
# SQLITE_ENABLE_LOAD_EXTENSION=1 are already in setup.py's own define_macros.

PROBE = r"""
import json, pathlib, subprocess, sys
import pysqlite3
conn = pysqlite3.connect(":memory:")
def ok(sql):
    try:
        conn.execute(sql); return True
    except Exception:
        return False
fts5 = ok("CREATE VIRTUAL TABLE t USING fts5(body)")
trigram = ok("CREATE VIRTUAL TABLE g USING fts5(body, tokenize='trigram')")
if trigram:
    conn.execute("INSERT INTO g(body) VALUES ('hermes gateway')")
    trigram = conn.execute(
        "SELECT count(*) FROM g WHERE g MATCH 'atew'").fetchone()[0] == 1
try:
    conn.enable_load_extension(True); conn.enable_load_extension(False)
    load_extension = True
except Exception:
    load_extension = False
so = next(pathlib.Path(pysqlite3.__file__).parent.glob("_sqlite3*.so"))
ldd = subprocess.run(["ldd", str(so)], capture_output=True, text=True).stdout
print(json.dumps({
    "version": [int(x) for x in pysqlite3.sqlite_version.split(".")],
    "fts5": fts5, "trigram": trigram,
    "load_extension": load_extension,
    "static": "libsqlite3" not in ldd,
}))
"""


def current_installed_version():
    out = subprocess.run(
        [str(VENV_PY), "-c", "import sqlite3; print(sqlite3.sqlite_version)"],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return tuple(int(x) for x in out.stdout.strip().split("."))


def upstream_runtime_now_viable():
    """Report when uv's CPython finally bundles a non-vulnerable SQLite.

    Purely informational: the day this turns True, `hermes update`'s own
    runtime repair could do this job and the shim becomes retirable. Measured
    2026-07-30: uv's cpython-3.11.15 and cpython-3.12.13 both bundle 3.50.4,
    which is inside the affected range, so the upstream path cannot work yet.
    """
    uv = pathlib.Path.home() / ".local" / "bin" / "uv"
    if not uv.is_file():
        return False, "uv not installed"
    try:
        found = subprocess.run([str(uv), "python", "find", "3.12"],
                               capture_output=True, text=True, timeout=60)
        python = found.stdout.strip()
        if not python:
            return False, "no uv-managed 3.12"
        out = subprocess.run(
            [python, "-c", "import sqlite3; print(sqlite3.sqlite_version)"],
            capture_output=True, text=True, timeout=60, check=True,
        )
        version = out.stdout.strip()
        return (not _is_vulnerable(
            tuple(int(x) for x in version.split(".")))), version
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return False, f"probe failed: {exc}"


def build_wheel(url, sha3, workdir):
    workdir = pathlib.Path(workdir)
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    subprocess.run(["git", "clone", "--depth", "1",
                    "https://github.com/coleifer/pysqlite3", "src"],
                   cwd=workdir, check=True, timeout=300)
    archive = workdir / "amalgamation.zip"
    urllib.request.urlretrieve(url, archive)
    verify_sha3(archive, sha3)   # raises IntegrityError before anything compiles
    subprocess.run(["unzip", "-q", "-o", str(archive)], cwd=workdir,
                   check=True, timeout=120)
    extracted = next(workdir.glob("sqlite-amalgamation-*"))
    for name in ("sqlite3.c", "sqlite3.h"):
        shutil.copy(extracted / name, workdir / "src" / name)
    # Static linkage comes from the amalgamation sitting in the checkout root.
    # There is NO build_static command in current pysqlite3 — invoking it fails.
    (workdir / "src" / "setup.cfg").write_text(SETUP_CFG)
    build_venv = workdir / "buildenv"
    subprocess.run([sys.executable, "-m", "venv", str(build_venv)],
                   check=True, timeout=180)
    pip = build_venv / "bin" / "pip"
    subprocess.run([str(pip), "install", "-q", "--upgrade",
                    "pip", "setuptools", "wheel"], check=True, timeout=600)
    subprocess.run([str(build_venv / "bin" / "python"), "setup.py",
                    "bdist_wheel"],
                   cwd=workdir / "src", check=True, timeout=3600)
    return next((workdir / "src" / "dist").glob("*.whl"))


def _probe_wheel(wheel):
    probe_venv = BUILD_ROOT / "probe-venv"
    if probe_venv.exists():
        shutil.rmtree(probe_venv)
    subprocess.run([sys.executable, "-m", "venv", str(probe_venv)],
                   check=True, timeout=180)
    subprocess.run([str(probe_venv / "bin" / "pip"), "install", "-q", str(wheel)],
                   check=True, timeout=600)
    out = subprocess.run([str(probe_venv / "bin" / "python"), "-c", PROBE],
                         capture_output=True, text=True, check=True, timeout=180)
    data = json.loads(out.stdout)
    data["version"] = tuple(data["version"])
    return data


def verify_wheel(wheel):
    """Return (ok, reasons). Upstream's predicate is the fitness oracle."""
    probe = _probe_wheel(wheel)
    reasons = []
    if _is_vulnerable(probe["version"]):
        reasons.append(
            f"candidate still links vulnerable SQLite {probe['version']}")
    for check in REQUIRED_CHECKS:
        if not probe.get(check):
            reasons.append(f"capability check failed: {check}")
    return (not reasons), reasons


def smoke_databases():
    """Open snapshots of the live databases with the candidate and query them.

    Snapshots, never the live files: a bad candidate must corrupt a copy.
    VACUUM INTO is consistent against a live WAL writer.
    """
    snap = BUILD_ROOT / "smoke-snapshots"
    if snap.exists():
        shutil.rmtree(snap)
    snap.mkdir(parents=True)
    script = f"""
import sys, pysqlite3 as sqlite3
sys.modules["sqlite3"] = sqlite3      # the probe venv has no .pth shim
for src, dst in [
    ({str(HERMES_HOME / "state.db")!r}, {str(snap / "state.db")!r}),
    ("/var/lib/job-intel/state/job_intel.sqlite3",
     {str(snap / "job_intel.sqlite3")!r}),
]:
    c = sqlite3.connect(f"file:{{src}}?mode=ro", uri=True)
    c.execute("VACUUM INTO ?", (dst,))
    c.close()
    d = sqlite3.connect(dst)
    assert d.execute("pragma quick_check(5)").fetchone()[0] == "ok", src
    d.close()
c = sqlite3.connect({str(snap / "state.db")!r})
c.execute("select count(*) from messages_fts where messages_fts match 'hermes'")
c.execute("select count(*) from messages_fts_trigram "
          "where messages_fts_trigram match 'gate'")
c.execute("create table _smoke(x)"); c.execute("insert into _smoke values (1)")
c.commit(); c.execute("drop table _smoke"); c.commit()
c.execute("pragma wal_checkpoint(TRUNCATE)")
assert c.execute("pragma integrity_check").fetchone()[0] == "ok"
c.close()
print("SMOKE_OK")
"""
    probe_py = BUILD_ROOT / "probe-venv" / "bin" / "python"
    out = subprocess.run([str(probe_py), "-c", script],
                         capture_output=True, text=True, timeout=1800)
    shutil.rmtree(snap, ignore_errors=True)   # multi-GB; backups are elsewhere
    return "SMOKE_OK" in out.stdout, (out.stderr or "")[-2000:]


def install_and_restart(wheel):
    previous = WHEEL_DIR / "previous"
    previous.mkdir(parents=True, exist_ok=True)
    for old in WHEEL_DIR.glob("*.whl"):
        shutil.copy(old, previous / old.name)
    target = WHEEL_DIR / pathlib.Path(wheel).name
    shutil.copy(wheel, target)
    subprocess.run([str(VENV_PY), "-m", "pip", "install", "--upgrade",
                    str(target)], check=True, timeout=600)
    subprocess.run([str(VENV_PY), "-m", "hermes_cli.main", "gateway", "restart"],
                   check=True, timeout=300)
    return post_install_healthy()


def post_install_healthy():
    """The shim is active, upstream agrees, both databases are intact, and the
    gateway process exists. The .pth shim itself is already in place — this
    only confirms it still resolves after the new wheel replaced the old one.
    """
    script = f"""
import sqlite3, sys
assert "pysqlite3" in getattr(sqlite3, "__file__", ""), "shim inactive"
sys.path.insert(0, {str(REPO)!r})
from hermes_cli.sqlite_runtime import is_sqlite_wal_reset_vulnerable
assert not is_sqlite_wal_reset_vulnerable(sqlite3.sqlite_version_info), \
    f"still vulnerable: {{sqlite3.sqlite_version}}"
for p in [{str(HERMES_HOME / "state.db")!r},
          "/var/lib/job-intel/state/job_intel.sqlite3"]:
    c = sqlite3.connect(f"file:{{p}}?mode=ro", uri=True)
    assert c.execute("pragma quick_check(5)").fetchone()[0] == "ok", p
print("HEALTHY", sqlite3.sqlite_version)
"""
    out = subprocess.run([str(VENV_PY), "-c", script],
                         capture_output=True, text=True, timeout=600)
    running = subprocess.run(["pgrep", "-f", "hermes_cli.main gateway run"],
                             capture_output=True, text=True, timeout=30)
    return "HEALTHY" in out.stdout and bool(running.stdout.strip())


def rollback():
    """Reinstall the previous wheel and restart. Best-effort, never raises."""
    previous = sorted((WHEEL_DIR / "previous").glob("*.whl"))
    if not previous:
        return False
    try:
        subprocess.run([str(VENV_PY), "-m", "pip", "install",
                        "--force-reinstall", str(previous[-1])],
                       check=True, timeout=600)
        subprocess.run([str(VENV_PY), "-m", "hermes_cli.main",
                        "gateway", "restart"], check=True, timeout=300)
        return post_install_healthy()
    except Exception:      # noqa: BLE001 - a failing rollback must still report
        return False


def rebuild_containers(url, sha3):
    """Rebuild the exporter image. The webui is deliberately excluded."""
    cmd = ["docker", "compose", "-f",
           str(HERMES_HOME / "monitoring" / "docker-compose.yml"), "build",
           "--build-arg", f"SQLITE_AMALGAMATION_URL={url}",
           "--build-arg", f"SQLITE_AMALGAMATION_SHA3={sha3}",
           "job-intel-exporter"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        return {"job-intel-exporter":
                "ok" if proc.returncode == 0 else proc.stderr[-500:]}
    except Exception as exc:   # noqa: BLE001 - never undo a healthy host upgrade
        return {"job-intel-exporter": f"error: {exc}"}


def _write_result(payload):
    viable, uv_version = upstream_runtime_now_viable()
    payload["upstream_runtime_viable"] = viable
    payload["uv_cpython_sqlite"] = uv_version
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(payload, indent=2))
    return payload


def run():
    fmt = lambda v: ".".join(str(x) for x in v)   # noqa: E731
    latest = resolve_latest_upstream()
    current = current_installed_version()

    if not should_upgrade(current, latest["version"]):
        return _write_result({"action": "up_to_date",
                              "installed": fmt(current),
                              "upstream": fmt(latest["version"])})

    # Never swap SQLite while the daily pipeline is writing job_intel.
    active = subprocess.run(["systemctl", "is-active", "job-intel-daily.service"],
                            capture_output=True, text=True, timeout=30)
    if active.stdout.strip() == "active":
        return _write_result({"action": "deferred",
                              "reason": "job-intel-daily is running",
                              "installed": fmt(current),
                              "upstream": fmt(latest["version"])})

    try:
        wheel = build_wheel(latest["url"], latest["sha3"],
                            BUILD_ROOT / "autoupdate-work")
    except IntegrityError as exc:
        return _write_result({"action": "rejected",
                              "reasons": [f"integrity: {exc}"],
                              "installed": fmt(current),
                              "upstream": fmt(latest["version"])})

    ok, reasons = verify_wheel(wheel)
    if not ok:
        return _write_result({"action": "rejected", "reasons": reasons,
                              "installed": fmt(current),
                              "upstream": fmt(latest["version"])})

    smoked, stderr = smoke_databases()
    if not smoked:
        return _write_result({"action": "rejected",
                              "reasons": ["smoke test failed", stderr],
                              "installed": fmt(current),
                              "upstream": fmt(latest["version"])})

    if install_and_restart(wheel):
        return _write_result({"action": "upgraded",
                              "installed": fmt(latest["version"]),
                              "previous": fmt(current),
                              "containers": rebuild_containers(
                                  latest["url"], latest["sha3"])})

    recovered = rollback()
    return _write_result({"action": "rolled_back",
                          "recovered": recovered,
                          "installed": fmt(current) if recovered else "UNKNOWN",
                          "upstream": fmt(latest["version"])})


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
