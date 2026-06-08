from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import hermes_cli.profile_preview as profile_preview_module
from hermes_cli.profile_preview import (
    ProfilePreviewError,
    build_profile_preview,
    preview_profile,
    preview_to_dict,
    preview_to_json,
)
from hermes_cli.profile_routing import RoutingError
from hermes_cli.profile_validation import ValidationIssue


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "preview_profile_chain.py"


def _run_json(args: list[str]) -> dict:
    result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_preview_only_produces_json_and_writes_nothing(tmp_path: Path):
    preview_root = tmp_path / "preview-output"
    assert not preview_root.exists()
    preview = preview_profile("Check WebUI status and inspect logs")
    payload = preview_to_dict(preview)

    assert payload["write_performed"] is False
    assert payload["write_verified"] is False
    assert not preview_root.exists()


def test_output_contains_all_required_top_level_fields():
    preview = preview_profile("Expose Hermes WebUI through Cloudflare and update docs")
    payload = preview_to_dict(preview)

    for field in [
        "task",
        "validation_status",
        "validation_issues",
        "route_decision",
        "model_selection",
        "approval_preview",
        "security_review_preview",
        "scribe_handoff_preview",
        "overall_profile_chain",
        "overall_status",
        "blocked_reasons",
        "required_operator_actions",
        "write_performed",
    ]:
        assert field in payload


def test_validation_status_and_validation_issues_are_surfaced(monkeypatch):
    monkeypatch.setattr(
        profile_preview_module,
        "validate_profile_architecture",
        lambda *args, **kwargs: [ValidationIssue(severity="error", message="bad registry", path="config/hermes-profiles.yaml")],
    )

    preview = preview_profile("Check WebUI status and inspect logs")
    payload = preview_to_dict(preview)

    assert payload["validation_status"] == "failed"
    assert payload["validation_issues"]
    assert payload["overall_status"] == "blocked_validation_failed"


def test_route_decision_is_embedded():
    preview = preview_profile("Expose Hermes WebUI through Cloudflare and update docs")
    payload = preview_to_dict(preview)

    assert payload["route_decision"] is not None
    assert payload["route_decision"]["primary_profile"] == "engineer"


def test_model_selection_is_recorded():
    preview = preview_profile("Expose Hermes WebUI through Cloudflare and update docs")
    payload = preview_to_dict(preview)

    assert payload["model_selection"] is not None
    assert payload["model_selection"]["selected_model"]
    assert payload["model_selection"]["route_hop"]["profile_id"] == payload["route_decision"]["route_chain"][0]["profile_id"]


def test_approval_preview_is_embedded():
    preview = preview_profile("Expose Hermes WebUI through Cloudflare and update docs")
    payload = preview_to_dict(preview)

    assert payload["approval_preview"] is not None
    assert payload["approval_preview"]["profile"] == "engineer"


def test_security_review_preview_is_embedded():
    preview = preview_profile("Expose Hermes WebUI through Cloudflare and update docs")
    payload = preview_to_dict(preview)

    assert payload["security_review_preview"] is not None
    assert payload["security_review_preview"]["review"]["reviewed_by_profile"] == "security_auditor"


def test_scribe_handoff_preview_is_embedded():
    preview = preview_profile("Expose Hermes WebUI through Cloudflare and update docs")
    payload = preview_to_dict(preview)

    assert payload["scribe_handoff_preview"] is not None
    assert payload["scribe_handoff_preview"]["handoff"]["to_profile"] == "scribe"


def test_overall_profile_chain_is_populated():
    preview = preview_profile("Expose Hermes WebUI through Cloudflare and update docs")
    payload = preview_to_dict(preview)

    assert payload["overall_profile_chain"]
    assert "engineer" in payload["overall_profile_chain"]


def test_blocked_reasons_is_populated():
    preview = preview_profile("Expose Hermes WebUI through Cloudflare and update docs")
    payload = preview_to_dict(preview)

    assert payload["blocked_reasons"]
    assert "security_review_failed" in payload["blocked_reasons"]


def test_required_operator_actions_is_populated():
    preview = preview_profile("Expose Hermes WebUI through Cloudflare and update docs")
    payload = preview_to_dict(preview)

    assert payload["required_operator_actions"]
    assert "provide_security_evidence_or_mitigations" in payload["required_operator_actions"]


def test_default_write_performed_is_false():
    preview = preview_profile("Check WebUI status and inspect logs")
    assert preview.write_performed is False


def test_security_fail_outranks_approval_required():
    preview = preview_profile("Expose Hermes WebUI through Cloudflare and update docs")
    payload = preview_to_dict(preview)

    assert payload["overall_status"] == "blocked_security_review_failed"
    assert "security_review_failed" in payload["blocked_reasons"]
    assert "engineer_approval_required" in payload["blocked_reasons"]


def test_approval_required_gives_blocked_pending_approval_when_security_does_not_fail():
    preview = preview_profile("Deploy Hermes WebUI and document the result")
    payload = preview_to_dict(preview)

    assert payload["overall_status"] == "blocked_pending_approval"
    assert "engineer_approval_required" in payload["blocked_reasons"]


def test_security_conditional_pass_gives_conditional_pending_mitigations():
    preview = preview_profile(
        "Expose Hermes WebUI through Cloudflare and update docs",
        security_evidence=["Loopback bind verified", "SSH tunnel only"],
        security_required_changes=["Keep the service loopback-only and behind SSH tunnel"],
        security_residual_risks=["Public exposure remains blocked until network boundary review"],
    )
    payload = preview_to_dict(preview)

    assert payload["security_review_preview"]["review"]["security_review_status"] == "conditional_pass"
    assert payload["overall_status"] == "conditional_pending_mitigations"


def test_clean_non_mutating_preview_gives_preview_ready():
    preview = preview_profile("Check WebUI status and inspect logs")
    payload = preview_to_dict(preview)

    assert payload["overall_status"] == "preview_ready"


def test_scribe_dry_run_hook_skipped_does_not_block_overall_status():
    preview = preview_profile("Check WebUI status and inspect logs")
    payload = preview_to_dict(preview)

    assert payload["scribe_handoff_preview"]["write_performed"] is False
    assert payload["overall_status"] == "preview_ready"


def test_validation_failure_gives_blocked_validation_failed(monkeypatch):
    monkeypatch.setattr(
        profile_preview_module,
        "validate_profile_architecture",
        lambda *args, **kwargs: [ValidationIssue(severity="error", message="broken policy", path="config/hermes-model-policy.yaml")],
    )

    preview = preview_profile("Check WebUI status and inspect logs")
    payload = preview_to_dict(preview)

    assert payload["overall_status"] == "blocked_validation_failed"
    assert payload["validation_issues"]


def test_routing_failure_gives_blocked_routing_failed_if_applicable(monkeypatch):
    monkeypatch.setattr(profile_preview_module, "route_task", lambda task: (_ for _ in ()).throw(RoutingError("boom")))

    preview = preview_profile("Check WebUI status and inspect logs")
    payload = preview_to_dict(preview)

    assert payload["overall_status"] == "blocked_routing_failed"
    assert payload["route_error"] == "boom"


def test_import_boundary_does_not_pull_runtime_modules():
    code = textwrap.dedent(
        r'''
        import sys
        import hermes_cli.profile_preview  # noqa: F401
        forbidden = [name for name in sys.modules if name.startswith(('agent', 'gateway', 'run_agent', 'cron.scheduler'))]
        print('\n'.join(sorted(forbidden)))
        '''
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    forbidden = [line for line in result.stdout.splitlines() if line.strip()]
    assert forbidden == []


def test_script_exits_non_zero_on_empty_task():
    result = subprocess.run([sys.executable, str(SCRIPT), "--task", "   ", "--json"], capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode != 0
    assert "must not be empty" in result.stderr


def test_script_prints_valid_json():
    payload = _run_json(["--task", "Check WebUI status and inspect logs", "--json"])
    assert payload["task"] == "Check WebUI status and inspect logs"
    assert payload["write_performed"] is False


def test_build_profile_preview_rejects_empty_task():
    with pytest.raises(ProfilePreviewError):
        build_profile_preview("")


def test_preview_to_json_round_trips():
    preview = preview_profile("Check WebUI status and inspect logs")
    encoded = preview_to_json(preview)
    decoded = json.loads(encoded)
    assert decoded["task"] == "Check WebUI status and inspect logs"
