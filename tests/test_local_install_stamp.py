"""Fork: the live checkout is externally owned -- no dependency sync on launch.

v0.22.5 adopts a stampless ``$HERMES_HOME/hermes-agent`` checkout as
``updateMechanism: self`` on first start, and a self-managed install syncs its
dependencies through pm at the start of every launch. On this host the venv is
built by the operator and ``hermes update`` is never run, so the cutover lays
down ``install-stamp.json`` with ``updateMechanism: external`` first. These
tests pin both halves of that: what happens without the stamp, and that the
stamp stops it.
"""

from __future__ import annotations

import json
import sys

import pytest

from hermes_cli import venv_sync


@pytest.fixture
def blessed_checkout(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "hermes-agent"
    (root / ".git").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_DISABLE_LAZY_INSTALLS", raising=False)
    monkeypatch.setattr(sys, "argv", ["hermes"])
    return root


@pytest.fixture
def pm_probe(monkeypatch):
    calls = []

    def venv_is_current(project_root=None):
        calls.append(project_root)
        raise RuntimeError("stop here: pm reached")

    monkeypatch.setattr("pm.venv_is_current", venv_is_current)
    return calls


def test_without_a_stamp_the_live_checkout_is_adopted_and_reaches_pm(blessed_checkout, pm_probe):
    with pytest.raises(RuntimeError, match="pm reached"):
        venv_sync.prepare_launch(blessed_checkout, ["gateway", "run"])

    stamp = json.loads((blessed_checkout / "install-stamp.json").read_text())
    assert stamp["updateMechanism"] == "self"
    assert pm_probe


def test_an_external_stamp_stops_adoption_and_the_launch_sync(blessed_checkout, pm_probe):
    stamp = {"schemaVersion": 2, "updateMechanism": "external", "source": "adoption",
             "distribution": "hermes fork, updated by the operator"}
    (blessed_checkout / "install-stamp.json").write_text(json.dumps(stamp))

    assert venv_sync.prepare_launch(blessed_checkout, ["gateway", "run"]) is None

    assert json.loads((blessed_checkout / "install-stamp.json").read_text())["updateMechanism"] == "external"
    assert pm_probe == []


def test_hermes_update_refuses_an_externally_owned_checkout(blessed_checkout):
    from hermes_cli import source_check

    stamp = {"schemaVersion": 2, "updateMechanism": "external", "source": "adoption"}
    refusal = source_check._unsupported_reason(stamp, blessed_checkout, explicit_root=True, embedded=None)

    assert refusal and refusal["reason"] == "update-root-steward-owned-git-tree"
