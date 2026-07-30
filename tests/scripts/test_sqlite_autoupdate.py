"""The SQLite updater replaces the apt coverage we gave up for two processes."""

import importlib.util
import json
import pathlib

MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "sqlite_autoupdate.py"
)

# Real format, verified 2026-07-30 against sqlite.org/download.html:
#   PRODUCT,VERSION,RELATIVE-URL,SIZE-IN-BYTES,SHA3-HASH
# Note the older release listed BEFORE the newer one — document order is not
# authority, and the amalgamation must be picked over src/tools archives.
# A naive "first matching row" implementation would return 3.51.3 here; only
# picking by max(version) returns the correct 3.53.4.
DOWNLOAD_PAGE = """
<!-- Download page
PRODUCT,VERSION,RELATIVE-URL,SIZE-IN-BYTES,SHA3-HASH
PRODUCT,3.53.4,2026/sqlite-src-3530400.zip,14557315,b834d474b9b3
PRODUCT,3.51.3,2026/sqlite-amalgamation-3510300.zip,2900000,aaaabbbbcccc
PRODUCT,3.53.4,2026/sqlite-amalgamation-3530400.zip,2946650,628a44cfe82c66aed1ccbbe85a562d2e33ebe64b3288981ed76285612227934e
PRODUCT,3.53.4,2026/sqlite-tools-linux-x64-3530400.zip,4265665,6eeb57e8f2ae
-->
"""


def _load():
    spec = importlib.util.spec_from_file_location("sau", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _isolate_build_root(module, tmp_path):
    """Point BUILD_ROOT/LOCK_PATH/WHEEL_DIR at tmp_path so tests never touch
    the real production HERMES_HOME/build directory or its lockfile."""
    build_root = tmp_path / "build"
    module.BUILD_ROOT = build_root
    module.LOCK_PATH = build_root / "autoupdate.lock"
    module.WHEEL_DIR = build_root / "wheels"
    return module


def test_parses_version_url_and_hash():
    result = _load().parse_product_index(DOWNLOAD_PAGE)
    assert result["version"] == (3, 53, 4)
    assert result["url"].endswith("/2026/sqlite-amalgamation-3530400.zip")
    assert result["sha3"] == (
        "628a44cfe82c66aed1ccbbe85a562d2e33ebe64b3288981ed76285612227934e"
    )


def test_picks_the_highest_version_not_the_first_row():
    assert _load().parse_product_index(DOWNLOAD_PAGE)["version"] == (3, 53, 4)


def test_ignores_src_and_tools_archives():
    assert "amalgamation" in _load().parse_product_index(DOWNLOAD_PAGE)["url"]


def test_verify_sha3_rejects_substituted_bytes(tmp_path):
    module = _load()
    payload = tmp_path / "amalgamation.zip"
    payload.write_bytes(b"not the real archive")
    try:
        module.verify_sha3(payload, "0" * 64)
    except module.IntegrityError:
        return
    raise AssertionError("a corrupted or substituted archive must not compile")


def test_verify_sha3_accepts_matching_hash(tmp_path):
    """The positive case: an implementation that always raises would still
    pass a suite that only ever exercises rejection."""
    import hashlib

    module = _load()
    payload = tmp_path / "amalgamation.zip"
    payload.write_bytes(b"the real archive bytes")
    digest = hashlib.sha3_256(b"the real archive bytes").hexdigest()
    assert module.verify_sha3(payload, digest) is True


def test_refuses_downgrades_and_vulnerable_targets():
    module = _load()
    assert module.should_upgrade(current=(3, 53, 4), latest=(3, 53, 4)) is False
    assert module.should_upgrade(current=(3, 53, 4), latest=(3, 53, 3)) is False
    assert module.should_upgrade(current=(3, 53, 4), latest=(3, 54, 0)) is True
    # A "newer" upstream that is still inside the affected range is not an upgrade.
    assert module.should_upgrade(current=(3, 45, 1), latest=(3, 50, 4)) is False


def test_records_a_result_file_even_when_it_declines(tmp_path, monkeypatch):
    """Silence must be distinguishable from 'never ran'."""
    module = _load()
    _isolate_build_root(module, tmp_path)
    monkeypatch.setattr(module, "RESULT_PATH", tmp_path / "result.json")
    monkeypatch.setattr(module, "resolve_latest_upstream",
                        lambda: {"version": (3, 53, 4), "url": "x", "sha3": "y"})
    monkeypatch.setattr(module, "current_installed_version", lambda: (3, 53, 4))
    monkeypatch.setattr(module, "upstream_runtime_now_viable", lambda: (False, "3.50.4"))
    module.run()
    payload = json.loads((tmp_path / "result.json").read_text())
    assert payload["action"] == "up_to_date"
    assert payload["installed"] == "3.53.4"
    assert "ts" in payload, "every result must carry a timestamp"


def _base_upgrade_monkeypatches(module, monkeypatch, tmp_path, *, current=(3, 53, 4),
                                latest=(3, 54, 0)):
    _isolate_build_root(module, tmp_path)
    monkeypatch.setattr(module, "RESULT_PATH", tmp_path / "result.json")
    monkeypatch.setattr(module, "resolve_latest_upstream",
                        lambda: {"version": latest, "url": "u", "sha3": "s"})
    monkeypatch.setattr(module, "current_installed_version", lambda: current)
    monkeypatch.setattr(module, "upstream_runtime_now_viable", lambda: (False, "3.50.4"))
    monkeypatch.setattr(module, "_job_intel_daily_active", lambda: False)
    # These orchestration tests are about run()'s branching, not about the
    # upstream vulnerability oracle itself (covered separately by
    # test_refuses_downgrades_and_vulnerable_targets and
    # test_verify_wheel_rejects_vulnerable_candidate_via_upstream_predicate),
    # so stub it rather than depending on hermes_cli being importable here.
    monkeypatch.setattr(module, "_is_vulnerable", lambda version: False)
    monkeypatch.setattr(module, "build_wheel", lambda url, sha3, workdir: "wheel.whl")
    monkeypatch.setattr(module, "verify_wheel", lambda wheel: (True, []))
    monkeypatch.setattr(module, "smoke_databases", lambda: (True, ""))
    monkeypatch.setattr(module, "rewrite_dockerfile_pins", lambda latest: {
        "dockerfile_args_rewritten": True, "needs_commit": "x"})
    monkeypatch.setattr(module, "rebuild_containers",
                        lambda url, sha3, version: {"job-intel-exporter": "ok"})


def test_upgrade_success_threads_expected_version(monkeypatch, tmp_path):
    module = _load()
    _base_upgrade_monkeypatches(module, monkeypatch, tmp_path)

    calls = {}

    def fake_install(wheel, expected_version):
        calls["expected_version"] = expected_version
        return True

    monkeypatch.setattr(module, "install_and_restart", fake_install)

    payload = module.run()
    assert payload["action"] == "upgraded"
    assert payload["installed"] == "3.54.0"
    assert calls["expected_version"] == (3, 54, 0), (
        "the resolved upstream version must be threaded into install_and_restart, "
        "which threads it into post_install_healthy"
    )


def test_post_install_unhealthy_triggers_rollback(monkeypatch, tmp_path):
    module = _load()
    _base_upgrade_monkeypatches(module, monkeypatch, tmp_path)
    monkeypatch.setattr(module, "install_and_restart", lambda wheel, expected: False)

    rollback_calls = []

    def fake_rollback(expected_version):
        rollback_calls.append(expected_version)
        return True

    monkeypatch.setattr(module, "rollback", fake_rollback)

    payload = module.run()
    assert payload["action"] == "rolled_back"
    assert rollback_calls == [(3, 53, 4)]


def test_exception_in_install_window_rolls_back_and_reports_failed(monkeypatch, tmp_path):
    module = _load()
    _base_upgrade_monkeypatches(module, monkeypatch, tmp_path)

    def raising_install(wheel, expected):
        raise RuntimeError("gateway restart timed out")

    monkeypatch.setattr(module, "install_and_restart", raising_install)

    rollback_calls = []

    def fake_rollback(expected_version):
        rollback_calls.append(expected_version)
        return True

    monkeypatch.setattr(module, "rollback", fake_rollback)

    payload = module.run()
    assert payload["action"] == "failed"
    assert payload["stage"] == "install"
    assert "RuntimeError" in payload["error"]
    assert rollback_calls == [(3, 53, 4)], "an exception mid-install must still roll back"
    assert payload["rollback_attempted"] is True
    assert payload["rollback_recovered"] is True


def test_verify_wheel_rejects_vulnerable_candidate_via_upstream_predicate(monkeypatch):
    module = _load()
    monkeypatch.setattr(module, "_probe_wheel", lambda wheel: {
        "version": (3, 50, 4), "fts5": True, "trigram": True,
        "load_extension": True, "static": True,
    })
    monkeypatch.setattr(module, "_is_vulnerable", lambda version: version == (3, 50, 4))

    ok, reasons = module.verify_wheel("wheel.whl")
    assert ok is False
    assert any("vulnerable" in r for r in reasons)


def test_rollback_uses_pointer_file_not_lexicographic_sort(tmp_path, monkeypatch):
    """`previous/` holds both an older and a newer *pysqlite3* version
    (0.6.0, 0.7.0) — filenames that sort lexicographically to the wrong one
    when what actually matters is which SQLite version each carries."""
    module = _load()
    wheel_dir = tmp_path / "wheels"
    previous = wheel_dir / "previous"
    previous.mkdir(parents=True)
    older = previous / "pysqlite3-0.6.0-cp311-cp311-linux_x86_64.whl"
    newer = previous / "pysqlite3-0.7.0-cp311-cp311-linux_x86_64.whl"
    older.write_bytes(b"old")
    newer.write_bytes(b"new")
    # The pointer records that 0.6.0 is the one to restore to.
    (previous / "replaced.json").write_text(json.dumps({
        "filename": older.name, "sqlite_version": "3.53.4",
    }))
    monkeypatch.setattr(module, "WHEEL_DIR", wheel_dir)

    installed = {}

    def fake_run(cmd, **kwargs):
        if "pip" in cmd:
            installed["wheel"] = cmd[-1]

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module, "post_install_healthy", lambda expected: True)

    result = module.rollback((3, 53, 4))
    assert result is True
    assert installed["wheel"] == str(older), (
        "rollback must restore the wheel recorded in replaced.json, not "
        "sorted(...)[-1] which would pick 0.7.0 (the one being rolled back from)"
    )
