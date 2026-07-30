#!/usr/bin/env python3
"""Keep the self-supplied SQLite current, without a human in the loop.

Two processes (gateway venv and job-intel-exporter) run pysqlite3 with a
statically compiled SQLite, because no available supplier ships a fixed
one: Ubuntu 24.04 pins libsqlite3-0 at 3.45.1 for the life of the LTS, and
uv's python-build-standalone CPython bundles 3.50.4 — both inside the
WAL-reset affected range. (hermes-webui is deliberately excluded — see
rebuild_containers.) That took those two processes out of apt's update
path, so this job replaces it.

Run by hermes cron in script mode, weekly. Script mode sets cwd to the
script directory (outside the repo), so the repo is resolved from HERMES_HOME.
"""

import fcntl
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

DOWNLOAD_URL = "https://www.sqlite.org/download.html"

# pysqlite3 has no tagged releases; pin an exact commit so a weekly,
# unattended build never silently compiles unreviewed upstream code between
# runs. Obtained via `git ls-remote https://github.com/coleifer/pysqlite3
# HEAD` on 2026-07-30 (master branch tip at the time of pinning).
PYSQLITE3_COMMIT = "54c9e703d4a5ca530223ca9b0463a53d29d2477e"

HERMES_HOME = pathlib.Path(os.environ.get("HERMES_HOME", pathlib.Path.home() / ".hermes"))
REPO = HERMES_HOME / "hermes-agent"
VENV_PY = REPO / "venv" / "bin" / "python"
BUILD_ROOT = HERMES_HOME / "build"
WHEEL_DIR = BUILD_ROOT / "wheels"
RESULT_PATH = HERMES_HOME / "state" / "sqlite_autoupdate_last.json"
LOCK_PATH = BUILD_ROOT / "autoupdate.lock"
DOCKERFILE_PATH = REPO / "deploy" / "docker" / "job-intel-exporter.Dockerfile"

PRODUCT_RE = re.compile(
    r"^PRODUCT,(\d+)\.(\d+)\.(\d+),(\d{4}/sqlite-amalgamation-\d+\.zip),\d+,([0-9a-f]+)",
    re.MULTILINE,
)


class IntegrityError(RuntimeError):
    """The downloaded amalgamation does not match its published SHA3-256."""


# Hoisted to module scope: _is_vulnerable used to do this sys.path.insert on
# every call, growing sys.path unboundedly across a single run (build_wheel,
# verify_wheel, post_install_healthy and upstream_runtime_now_viable can all
# call it). One import attempt at load time is enough.
try:
    sys.path.insert(0, str(REPO))
    from hermes_cli.sqlite_runtime import (
        is_sqlite_wal_reset_vulnerable as _upstream_is_vulnerable,
    )
except ImportError:
    _upstream_is_vulnerable = None


def _is_vulnerable(version_info):
    """Ask upstream's detector, never a local copy of the affected range.

    hermes_cli.sqlite_runtime is the single source of truth for which SQLite
    versions carry the WAL-reset bug; duplicating the range here would let the
    two drift when upstream learns something new.
    """
    if _upstream_is_vulnerable is None:
        raise RuntimeError(
            f"hermes_cli.sqlite_runtime not importable from {REPO}"
        )
    return _upstream_is_vulnerable(tuple(version_info))


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

    This defends against a corrupted download or a mismatched mirror: the
    hash and the archive both travel from sqlite.org over the same TLS
    session, so this check cannot catch a compromised origin serving a
    tampered archive alongside a matching hash — only transit corruption and
    mirror substitution. Skipping it entirely would make a substituted
    download a code-execution path with nobody in front of it, which is why
    it still runs before anything compiles.
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
    src = workdir / "src"

    # Pin by commit, not "clone the default branch": the SQLite amalgamation
    # is SHA3-verified below, but a bare `git clone` of pysqlite3 would run
    # whatever setup.py happens to be at HEAD on the day cron fires, with no
    # review in front of it. Fetch the pinned SHA explicitly and verify the
    # checkout actually landed on it before compiling anything.
    subprocess.run(["git", "init", "-q", str(src)], check=True, timeout=30)
    subprocess.run(["git", "-C", str(src), "fetch", "--depth", "1",
                    "https://github.com/coleifer/pysqlite3", PYSQLITE3_COMMIT],
                   check=True, timeout=300)
    subprocess.run(["git", "-C", str(src), "checkout", "-q", "FETCH_HEAD"],
                   check=True, timeout=60)
    head = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True, timeout=30
                          ).stdout.strip()
    if head != PYSQLITE3_COMMIT:
        raise RuntimeError(
            f"pysqlite3 checkout HEAD {head} != pinned {PYSQLITE3_COMMIT}"
        )

    archive = workdir / "amalgamation.zip"
    urllib.request.urlretrieve(url, archive)
    verify_sha3(archive, sha3)   # raises IntegrityError before anything compiles
    subprocess.run(["unzip", "-q", "-o", str(archive)], cwd=workdir,
                   check=True, timeout=120)
    extracted = next(workdir.glob("sqlite-amalgamation-*"))
    for name in ("sqlite3.c", "sqlite3.h"):
        shutil.copy(extracted / name, src / name)
    # Static linkage comes from the amalgamation sitting in the checkout root.
    # There is NO build_static command in current pysqlite3 — invoking it fails.
    (src / "setup.cfg").write_text(SETUP_CFG)
    build_venv = workdir / "buildenv"
    subprocess.run([sys.executable, "-m", "venv", str(build_venv)],
                   check=True, timeout=180)
    pip = build_venv / "bin" / "pip"
    subprocess.run([str(pip), "install", "-q", "--upgrade",
                    "pip", "setuptools", "wheel"], check=True, timeout=600)
    subprocess.run([str(build_venv / "bin" / "python"), "setup.py",
                    "bdist_wheel"],
                   cwd=src, check=True, timeout=3600)
    return next((src / "dist").glob("*.whl"))


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
    probe_py = BUILD_ROOT / "probe-venv" / "bin" / "python"
    assert probe_py.is_file(), (
        f"{probe_py} missing — _probe_wheel() must run (and succeed) before "
        "smoke_databases() to create the probe venv"
    )
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
    try:
        out = subprocess.run([str(probe_py), "-c", script],
                             capture_output=True, text=True, timeout=1800)
        return "SMOKE_OK" in out.stdout, (out.stderr or "")[-2000:]
    finally:
        # multi-GB; backups are elsewhere. In a `finally` so a timeout does
        # not leak the snapshot under $HOME on every failed run.
        shutil.rmtree(snap, ignore_errors=True)


def install_and_restart(wheel, expected_version):
    previous = WHEEL_DIR / "previous"
    # Clear before repopulating: `previous/` must hold only the wheel(s)
    # being replaced by *this* run, never a stale accumulation from earlier
    # runs that a later rollback could pick up by accident.
    if previous.exists():
        shutil.rmtree(previous)
    previous.mkdir(parents=True)
    existing = list(WHEEL_DIR.glob("*.whl"))
    for old in existing:
        shutil.copy(old, previous / old.name)
    if existing:
        # pysqlite3's own wheel version (currently 0.6.0), not SQLite's, is
        # what appears in the filename — a wheel carrying SQLite 3.54.0 and
        # one carrying 3.53.4 can have the identical name. Record exactly
        # which file and which SQLite version rollback() must restore,
        # rather than trusting a lexicographic filename sort.
        (previous / "replaced.json").write_text(json.dumps({
            "filename": existing[0].name,
            "sqlite_version": ".".join(str(x) for x in current_installed_version()),
        }))
    target = WHEEL_DIR / pathlib.Path(wheel).name
    shutil.copy(wheel, target)
    # --force-reinstall: pip's ordinary --upgrade treats an identically
    # versioned wheel (pysqlite3's version, not SQLite's) as already
    # satisfied and installs nothing. --no-deps: this wheel's only
    # dependency surface is the amalgamation baked into it.
    subprocess.run([str(VENV_PY), "-m", "pip", "install",
                    "--force-reinstall", "--no-deps", str(target)],
                   check=True, timeout=600)
    subprocess.run([str(VENV_PY), "-m", "hermes_cli.main", "gateway", "restart"],
                   check=True, timeout=300)
    return post_install_healthy(expected_version)


def _gateway_pid():
    """PID of the live gateway process, matched on the venv python path.

    A bare `pgrep -f "hermes_cli.main gateway run"` also matches unrelated
    command lines that merely contain that substring (observed during
    manual testing). Anchoring on the venv's own python path is specific to
    this deployment's actual invocation.
    """
    pattern = f"{VENV_PY} -m hermes_cli.main gateway run"
    out = subprocess.run(["pgrep", "-f", pattern],
                         capture_output=True, text=True, timeout=30)
    pids = [p for p in out.stdout.split() if p.strip()]
    return pids[0] if pids else None


def post_install_healthy(expected_version):
    """The shim is active, the version matches what we just installed,
    upstream agrees it's not vulnerable, both databases are intact, and the
    gateway process's own memory map shows pysqlite3 loaded with no
    libsqlite3.so present.

    Non-vulnerability alone is too weak a post-condition for an upgrade: the
    version we are upgrading *from* also satisfies it, so a same-version,
    do-nothing "upgrade" would pass. Asserting the resolved upstream version
    was actually installed is the load-bearing check.
    """
    expected = tuple(expected_version)
    script = f"""
import sqlite3, sys
assert "pysqlite3" in getattr(sqlite3, "__file__", ""), "shim inactive"
sys.path.insert(0, {str(REPO)!r})
from hermes_cli.sqlite_runtime import is_sqlite_wal_reset_vulnerable
assert not is_sqlite_wal_reset_vulnerable(sqlite3.sqlite_version_info), \
    f"still vulnerable: {{sqlite3.sqlite_version}}"
assert sqlite3.sqlite_version_info[:3] == {expected!r}, \
    f"expected {expected}, got {{sqlite3.sqlite_version_info[:3]}}"
for p in [{str(HERMES_HOME / "state.db")!r},
          "/var/lib/job-intel/state/job_intel.sqlite3"]:
    c = sqlite3.connect(f"file:{{p}}?mode=ro", uri=True)
    assert c.execute("pragma quick_check(5)").fetchone()[0] == "ok", p
print("HEALTHY", sqlite3.sqlite_version)
"""
    out = subprocess.run([str(VENV_PY), "-c", script],
                         capture_output=True, text=True, timeout=600)
    if "HEALTHY" not in out.stdout:
        return False

    pid = _gateway_pid()
    if not pid:
        return False
    try:
        maps = pathlib.Path(f"/proc/{pid}/maps").read_text()
    except OSError:
        return False
    return "pysqlite3" in maps and "libsqlite3.so" not in maps


def rollback(expected_version):
    """Reinstall the previous wheel and restart. Best-effort, never raises."""
    previous_dir = WHEEL_DIR / "previous"
    pointer = previous_dir / "replaced.json"
    candidate = None
    try:
        record = json.loads(pointer.read_text())
        recorded = previous_dir / record["filename"]
        if recorded.is_file():
            candidate = recorded
    except (OSError, ValueError, KeyError):
        pass
    if candidate is None:
        # Belt and braces: no pointer (or it didn't resolve) falls back to
        # the old lexicographic-last heuristic, which is wrong precisely
        # when there are two different pysqlite3-versioned wheels — but a
        # rollback with *something* plausible beats one that gives up.
        remaining = sorted(previous_dir.glob("*.whl"))
        if not remaining:
            return False
        candidate = remaining[-1]
    try:
        subprocess.run([str(VENV_PY), "-m", "pip", "install",
                        "--force-reinstall", "--no-deps", str(candidate)],
                       check=True, timeout=600)
        subprocess.run([str(VENV_PY), "-m", "hermes_cli.main",
                        "gateway", "restart"], check=True, timeout=300)
        return post_install_healthy(expected_version)
    except Exception:      # noqa: BLE001 - a failing rollback must still report
        return False


def rewrite_dockerfile_pins(latest):
    """Point the exporter Dockerfile's default ARGs at the version just
    installed on the host.

    Without this, the running image is current only until the next plain
    `docker compose build`, which rebuilds the old pinned version with no
    commit and no signal — the same silent-divergence channel this branch
    already rejected once for the host venv. This edits the Dockerfile in
    the *live checkout* ($HERMES_HOME/hermes-agent/...), the one compose
    actually uses as its build context. It deliberately does not commit —
    that stays a human action, flagged via `needs_commit` in the result.
    """
    try:
        text = DOCKERFILE_PATH.read_text()
        new_text = re.sub(
            r"^ARG SQLITE_AMALGAMATION_URL=.*$",
            f"ARG SQLITE_AMALGAMATION_URL={latest['url']}",
            text, count=1, flags=re.MULTILINE,
        )
        new_text = re.sub(
            r"^ARG SQLITE_AMALGAMATION_SHA3=.*$",
            f"ARG SQLITE_AMALGAMATION_SHA3={latest['sha3']}",
            new_text, count=1, flags=re.MULTILINE,
        )
        if new_text == text:
            return {"dockerfile_args_rewritten": False}
        DOCKERFILE_PATH.write_text(new_text)
        return {"dockerfile_args_rewritten": True,
                "needs_commit": str(DOCKERFILE_PATH)}
    except OSError as exc:
        return {"dockerfile_args_rewritten": False,
                "dockerfile_rewrite_error": str(exc)}


def rebuild_containers(url, sha3, version):
    """Rebuild AND deploy the exporter image, then report the SQLite version
    actually observed running inside the container — not the build's exit
    code. The webui is deliberately excluded.

    A failed build or deploy must never undo a healthy host upgrade: every
    failure path here is caught and reported, not raised.
    """
    compose = ["docker", "compose", "-f",
               str(HERMES_HOME / "monitoring" / "docker-compose.yml")]
    try:
        build = subprocess.run(
            compose + ["build",
                       "--build-arg", f"SQLITE_AMALGAMATION_URL={url}",
                       "--build-arg", f"SQLITE_AMALGAMATION_SHA3={sha3}",
                       "job-intel-exporter"],
            capture_output=True, text=True, timeout=3600)
        if build.returncode != 0:
            return {"job-intel-exporter": f"build failed: {build.stderr[-500:]}"}

        up = subprocess.run(compose + ["up", "-d", "job-intel-exporter"],
                            capture_output=True, text=True, timeout=300)
        if up.returncode != 0:
            return {"job-intel-exporter": f"up failed: {up.stderr[-500:]}"}

        probe = subprocess.run(
            ["docker", "exec", "monitoring-job-intel-exporter", "python", "-c",
             "import sqlite3; print(sqlite3.sqlite_version, sqlite3.__name__)"],
            capture_output=True, text=True, timeout=60)
        if probe.returncode != 0:
            return {"job-intel-exporter":
                    f"deployed but version probe failed: {probe.stderr[-300:]}"}
        return {"job-intel-exporter": "ok", "observed": probe.stdout.strip()}
    except Exception as exc:   # noqa: BLE001 - never undo a healthy host upgrade
        return {"job-intel-exporter": f"error: {exc}"}


def _job_intel_daily_active():
    """Never swap SQLite while the daily pipeline is writing job_intel."""
    active = subprocess.run(["systemctl", "is-active", "job-intel-daily.service"],
                            capture_output=True, text=True, timeout=30)
    return active.stdout.strip() == "active"


def _acquire_lock():
    """Non-blocking exclusive lock over BUILD_ROOT's work dirs and venvs, so
    a manual run and the Sunday cron cannot stomp on the same directories.
    Raises OSError (BlockingIOError) if another run already holds it.
    """
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise
    return fh


def _write_result(payload):
    viable, uv_version = upstream_runtime_now_viable()
    payload["upstream_runtime_viable"] = viable
    payload["uv_cpython_sqlite"] = uv_version
    payload["ts"] = datetime.now(timezone.utc).isoformat()
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(payload, indent=2))
    return payload


def run():
    fmt = lambda v: ".".join(str(x) for x in v)   # noqa: E731
    stage = "resolve"
    try:
        lock_fh = _acquire_lock()
    except OSError:
        return _write_result({"action": "skipped",
                              "reason": "concurrent run already holds the lock"})
    try:
        latest = resolve_latest_upstream()
        current = current_installed_version()

        if not should_upgrade(current, latest["version"]):
            return _write_result({"action": "up_to_date",
                                  "installed": fmt(current),
                                  "upstream": fmt(latest["version"])})

        if _job_intel_daily_active():
            return _write_result({"action": "deferred",
                                  "reason": "job-intel-daily is running",
                                  "installed": fmt(current),
                                  "upstream": fmt(latest["version"])})

        stage = "build"
        try:
            wheel = build_wheel(latest["url"], latest["sha3"],
                                BUILD_ROOT / "autoupdate-work")
        except IntegrityError as exc:
            return _write_result({"action": "rejected",
                                  "reasons": [f"integrity: {exc}"],
                                  "installed": fmt(current),
                                  "upstream": fmt(latest["version"])})

        stage = "verify"
        ok, reasons = verify_wheel(wheel)
        if not ok:
            return _write_result({"action": "rejected", "reasons": reasons,
                                  "installed": fmt(current),
                                  "upstream": fmt(latest["version"])})

        stage = "smoke"
        smoked, stderr = smoke_databases()
        if not smoked:
            return _write_result({"action": "rejected",
                                  "reasons": ["smoke test failed", stderr],
                                  "installed": fmt(current),
                                  "upstream": fmt(latest["version"])})

        stage = "install"
        try:
            healthy = install_and_restart(wheel, latest["version"])
        except Exception as exc:
            # The module may already be replaced even though the exception
            # fired mid-window (e.g. a timed-out gateway restart) — attempt
            # recovery rather than leaving production on an unverified,
            # possibly-half-installed build.
            recovered = rollback(current)
            return _write_result({"action": "failed", "stage": "install",
                                  "error": f"{type(exc).__name__}: {exc}",
                                  "rollback_attempted": True,
                                  "rollback_recovered": recovered})

        if healthy:
            stage = "containers"
            result = {"action": "upgraded",
                      "installed": fmt(latest["version"]),
                      "previous": fmt(current),
                      "containers": rebuild_containers(
                          latest["url"], latest["sha3"], latest["version"])}
            result.update(rewrite_dockerfile_pins(latest))
            return _write_result(result)

        recovered = rollback(current)
        return _write_result({"action": "rolled_back",
                              "recovered": recovered,
                              "installed": fmt(current) if recovered else "UNKNOWN",
                              "upstream": fmt(latest["version"])})
    except Exception as exc:
        return _write_result({"action": "failed", "stage": stage,
                              "error": f"{type(exc).__name__}: {exc}"})
    finally:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        finally:
            lock_fh.close()


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
