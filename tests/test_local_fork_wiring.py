"""Fork wiring that lives at upstream call sites: config bridges, the package-skill guards,
the image cost, the fork's CLI subcommands (toolsets.py, plugins/, tools/, hermes_cli/main.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# --- config.yaml -> env bridges ---------------------------------------------------------------

def test_slack_idea_capture_keys_reach_the_env(monkeypatch):
    from plugins.platforms.slack.adapter import _apply_yaml_config

    for name in ("SLACK_IDEA_DOC_ID", "SLACK_IDEA_REACTION_CHANNELS", "SLACK_IDEA_REACTION_EMOJIS"):
        monkeypatch.delenv(name, raising=False)
    extra = _apply_yaml_config({}, {
        "idea_reaction_doc_id": "doc-1",
        "idea_reaction_channels": ["C0B3ZV4BUKC", "C0SECOND00"],
        "idea_reaction_emojis": ["+1", "bulb"],
    })

    import os
    assert os.environ["SLACK_IDEA_DOC_ID"] == "doc-1"
    assert os.environ["SLACK_IDEA_REACTION_CHANNELS"].split(",") == ["C0B3ZV4BUKC", "C0SECOND00"]
    assert os.environ["SLACK_IDEA_REACTION_EMOJIS"].split(",") == ["+1", "bulb"]
    assert extra["idea_reaction_doc_id"] == "doc-1"


def test_whatsapp_profile_photo_path_is_seeded_into_extra(monkeypatch):
    from plugins.platforms.whatsapp.adapter import _apply_yaml_config

    monkeypatch.delenv("WHATSAPP_PROFILE_PHOTO_PATH", raising=False)
    extra = _apply_yaml_config({}, {"profile_photo_path": "/srv/avatar.jpg"})

    assert extra["profile_photo_path"] == "/srv/avatar.jpg"


# --- role-package skills -----------------------------------------------------------------------

@pytest.fixture
def package_skill(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    skill = home / "role-packages" / "pkg" / "skills" / "owned"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: owned\ndescription: d\n---\nbody\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    return skill


def test_agent_cannot_edit_a_package_owned_skill(package_skill, monkeypatch):
    import tools.skill_manager_tool as smt

    monkeypatch.setattr(smt, "_find_skill", lambda name: {"path": package_skill})
    monkeypatch.setattr("hermes_cli.role_packages.get_package_for_skill_path",
                        lambda path: "pkg" if Path(path) == package_skill else None)
    out = json.loads(smt.skill_manage(action="patch", name="owned", old_string="body", new_string="x"))

    assert out["success"] is False
    assert "owned by an installed role package" in out["error"]
    assert (package_skill / "SKILL.md").read_text().endswith("body\n")


def test_package_skill_env_passthrough_is_capped(monkeypatch, tmp_path):
    import tools.skills_tool as st

    registered = []
    monkeypatch.setattr("tools.env_passthrough.register_env_passthrough", lambda names: registered.append(set(names)))
    monkeypatch.setattr("hermes_cli.role_packages.cap_env_passthrough_for_skill",
                        lambda *, skill_path, skill_env_names: {"ALLOWED"} & skill_env_names)
    monkeypatch.setattr(st, "load_env", lambda: {"ALLOWED": "1", "SECRET": "2"})
    monkeypatch.setattr(st, "_is_env_var_persisted", lambda name, snapshot: name in snapshot)
    frontmatter = {"name": "s", "required_environment_variables": ["ALLOWED", "SECRET"]}

    st._skill_readiness(frontmatter, "s", tmp_path / "SKILL.md")

    assert registered and all(names <= {"ALLOWED"} for names in registered)


# --- image generation cost ---------------------------------------------------------------------

def test_openrouter_image_reports_the_request_cost():
    from unittest.mock import patch

    from tests.plugins.image_gen.test_openrouter_compat_provider import (
        _PNG_DATA_URI, _RUNTIME, _mock_chat_response, _openrouter, _runtime_ok)

    resp = _mock_chat_response([_PNG_DATA_URI])
    resp.json.return_value["usage"] = {"cost": 0.0123456789}
    with patch(_RUNTIME, return_value=_runtime_ok()), \
         patch("requests.post", return_value=resp) as post, \
         patch("plugins.image_gen.openrouter.save_b64_image", return_value=Path("/tmp/x.png")):
        result = _openrouter().generate(prompt="a cat")

    assert post.call_args.kwargs["json"]["usage"] == {"include": True}
    assert result["success"] is True
    assert result["cost_usd"] == pytest.approx(0.012346)


# --- the fork's CLI subcommands ----------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "controlled-report", "role", "recruiter-context", "recruiter-decision", "recruiter-document", "recruiter-skill",
])
def test_fork_subcommands_are_builtin_and_parse(command):
    from hermes_cli import main

    assert command in main._BUILTIN_SUBCOMMANDS
    parser, _ = main._build_cli_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args([command, "--help"])
    assert exc.value.code == 0


def test_controlled_report_cli_reads_a_persisted_report(tmp_path, monkeypatch, capsys):
    from hermes_cli import main
    from hermes_cli.local_cli import cmd_controlled_report
    from hermes_state import SessionDB

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = SessionDB(db_path=tmp_path / "state.db")
    db.persist_controlled_execution_report(report_run_id="run-1", payload={
        "status": "completed", "routing": {"selected_pipeline_id": "eng"},
        "execution": {"execution_mode": "controlled", "final_verdict": "pass"},
        "tests": {"status": "passed"},
    })
    monkeypatch.setattr("hermes_state.SessionDB", lambda: db)
    parser, _ = main._build_cli_parser()

    assert cmd_controlled_report(parser.parse_args(["controlled-report", "get", "run-1"])) == 0
    assert "Verdict:      pass" in capsys.readouterr().out
