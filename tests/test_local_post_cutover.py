"""Fork fixes found by the full gate after the v0.22.5 cutover: where the execution-locus footer
goes, and the nightly digest's SQLite section on a shim-free 3.14 runtime."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.local_finalizer import locus_wanted


@pytest.mark.parametrize("platform", ["slack", "telegram", "whatsapp", "email", "cron"])
def test_locus_goes_to_chat_and_cron_audiences(platform):
    assert locus_wanted(SimpleNamespace(platform=platform, _delegate_depth=0), "Done.") is True


@pytest.mark.parametrize("platform", ["cli", "local", "tui", "api_server", "kanban", "", None])
def test_no_locus_for_programs_and_the_operator_terminal(platform):
    assert locus_wanted(SimpleNamespace(platform=platform, _delegate_depth=0), "Done.") is False


def test_no_locus_on_a_delegated_child():
    assert locus_wanted(SimpleNamespace(platform="slack", _delegate_depth=1), "Done.") is False


def test_silent_reply_stays_silent():
    assert locus_wanted(SimpleNamespace(platform="cron", _delegate_depth=0), "[SILENT]") is False
    assert locus_wanted(SimpleNamespace(platform="cron", _delegate_depth=0), " [silent]\n") is False


def _collector():
    path = Path(__file__).resolve().parents[1] / "scripts" / "nightly_diagnostics_collect.py"
    spec = importlib.util.spec_from_file_location("nightly_diagnostics_collect_local", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sqlite_section(monkeypatch, tmp_path, *, module_file, vulnerable, autoupdate=None):
    collect = _collector()
    monkeypatch.setattr(collect.sqlite3, "__file__", module_file, raising=False)
    monkeypatch.setattr(collect, "_sqlite_runtime_vulnerable", lambda home, info: (vulnerable, None))
    monkeypatch.setattr(collect, "_collect_exporter_sqlite_health", lambda home: {"needs_attention": False})
    if autoupdate is not None:
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "sqlite_autoupdate_last.json").write_text(json.dumps(autoupdate))
    return collect.collect_sqlite_health(tmp_path)


def test_shim_free_fixed_runtime_raises_no_alarm(monkeypatch, tmp_path):
    info = _sqlite_section(monkeypatch, tmp_path, module_file="/usr/lib/python3.14/sqlite3/__init__.py",
                           vulnerable=False)

    assert info["shim_active"] is False
    assert info["needs_attention"] is False


def test_vulnerable_runtime_without_shim_still_alarms(monkeypatch, tmp_path):
    info = _sqlite_section(monkeypatch, tmp_path, module_file="/usr/lib/python3.12/sqlite3/__init__.py",
                           vulnerable=True)

    assert info["needs_attention"] is True


def test_shim_era_keeps_its_updater_alarms(monkeypatch, tmp_path):
    info = _sqlite_section(monkeypatch, tmp_path, module_file="/venv/site-packages/pysqlite3/__init__.py",
                           vulnerable=False, autoupdate={"action": "rolled_back"})

    assert info["needs_attention"] is True
