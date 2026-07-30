"""The SQLite updater replaces the apt coverage we gave up for three processes."""

import importlib.util
import json
import pathlib

MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "sqlite_autoupdate.py"
)

# Real format, verified 2026-07-30 against sqlite.org/download.html:
#   PRODUCT,VERSION,RELATIVE-URL,SIZE-IN-BYTES,SHA3-HASH
# Note the older release listed AFTER the newer one — document order is not
# authority, and the amalgamation must be picked over src/tools archives.
DOWNLOAD_PAGE = """
<!-- Download page
PRODUCT,VERSION,RELATIVE-URL,SIZE-IN-BYTES,SHA3-HASH
PRODUCT,3.53.4,2026/sqlite-src-3530400.zip,14557315,b834d474b9b3
PRODUCT,3.53.4,2026/sqlite-amalgamation-3530400.zip,2946650,628a44cfe82c66aed1ccbbe85a562d2e33ebe64b3288981ed76285612227934e
PRODUCT,3.53.4,2026/sqlite-tools-linux-x64-3530400.zip,4265665,6eeb57e8f2ae
PRODUCT,3.51.3,2026/sqlite-amalgamation-3510300.zip,2900000,aaaabbbbcccc
-->
"""


def _load():
    spec = importlib.util.spec_from_file_location("sau", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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
    monkeypatch.setattr(module, "RESULT_PATH", tmp_path / "result.json")
    monkeypatch.setattr(module, "resolve_latest_upstream",
                        lambda: {"version": (3, 53, 4), "url": "x", "sha3": "y"})
    monkeypatch.setattr(module, "current_installed_version", lambda: (3, 53, 4))
    monkeypatch.setattr(module, "upstream_runtime_now_viable", lambda: (False, "3.50.4"))
    module.run()
    payload = json.loads((tmp_path / "result.json").read_text())
    assert payload["action"] == "up_to_date"
    assert payload["installed"] == "3.53.4"
