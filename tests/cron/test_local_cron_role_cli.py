"""Fork: the per-job role pin is settable from `hermes cron create/edit --role` and
cronjob(), and not from the model-facing tool (tools/cronjob_tools.py, hermes_cli/cron.py)."""

from __future__ import annotations

import argparse
import json

import pytest

from tools.cronjob_tools import cronjob


@pytest.fixture(autouse=True)
def _cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")


def _stored(job_id):
    from cron.jobs import get_job

    return get_job(job_id)


def test_create_persists_the_role_pin():
    created = json.loads(cronjob(action="create", prompt="report", schedule="every 1h", role="scribe"))

    assert created["success"] is True
    assert _stored(created["job_id"])["role"] == "scribe"


def test_update_sets_and_empty_string_clears_the_pin():
    job_id = json.loads(cronjob(action="create", prompt="report", schedule="every 1h"))["job_id"]

    assert json.loads(cronjob(action="update", job_id=job_id, role="engineer"))["success"] is True
    assert _stored(job_id)["role"] == "engineer"

    assert json.loads(cronjob(action="update", job_id=job_id, role=""))["success"] is True
    assert _stored(job_id).get("role") is None


def test_update_without_role_keeps_the_pin():
    job_id = json.loads(cronjob(action="create", prompt="report", schedule="every 1h", role="scribe"))["job_id"]

    assert json.loads(cronjob(action="update", job_id=job_id, name="renamed"))["success"] is True
    assert _stored(job_id)["role"] == "scribe"


def test_malformed_role_is_refused():
    out = json.loads(cronjob(action="create", prompt="x", schedule="every 1h", role="rm -rf /"))

    assert out["success"] is False


def test_model_facing_tool_cannot_pin_a_role():
    from tools.cronjob_tools import CRONJOB_SCHEMA
    from tools.registry import registry

    assert "role" not in CRONJOB_SCHEMA["parameters"]["properties"]
    out = json.loads(registry.dispatch(
        "cronjob_manage", {"action": "create", "prompt": "x", "schedule": "every 1h", "role": "engineer"}))
    assert out["success"] is True
    assert _stored(out["job_id"]).get("role") is None


def _cron_args(argv):
    from hermes_cli.subcommands.cron import build_cron_parser

    parser = argparse.ArgumentParser()
    build_cron_parser(parser.add_subparsers(dest="command"), cmd_cron=lambda a: 0)
    return parser.parse_args(["cron", *argv])


def test_cli_create_and_edit_reach_the_job(capsys):
    from hermes_cli.cron import cron_create, cron_edit

    assert cron_create(_cron_args(["create", "every 1h", "report", "--role", "scribe"])) == 0
    job_id = json.loads(cronjob(action="list"))["jobs"][0]["job_id"]
    assert _stored(job_id)["role"] == "scribe"
    assert "Role: scribe (pinned)" in capsys.readouterr().out

    assert cron_edit(_cron_args(["edit", job_id, "--role", ""])) == 0
    assert _stored(job_id).get("role") is None
