from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hermes_cli.profile_approval import classify_engineer_approval
from hermes_cli.profile_routing import route_task
from hermes_cli.profile_security_review import (
    build_security_review,
    decision_to_dict,
    preview_security_review,
    render_security_review_markdown,
    write_security_review,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "preview_security_review.py"


def _security_task_route_and_approval(task: str):
    route_decision = route_task(task)
    approval_preview = classify_engineer_approval(task, route_decision=route_decision)
    return route_decision, approval_preview


def _json_output(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "task, expected_trigger",
    [
        ("Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения", "public exposure"),
        ("Review the WebUI access model, auth/session cookies, and local access", "WebUI access model"),
        ("Audit auth/session cookies and login flow", "auth/session/cookies"),
        ("Review secrets, tokens, and API keys handling", "secrets/tokens/API keys"),
        ("Check SSH access path for admin operations", "SSH"),
        ("Audit browser profiles and browser-desktop boundaries", "browser profiles"),
        ("Review file manager, shell, terminal, git, and upload permissions", "file manager / shell / terminal / git / upload permissions"),
        ("Review scheduler and memory writes for persistent state", "scheduler/memory writes"),
        ("Review tool permissions and allowlists", "tool permissions"),
        ("Review Cloudflare reverse proxy and firewall rules", "Cloudflare/reverse proxy/firewall"),
        ("Review persistent storage of untrusted external content", "persistent storage of untrusted external content"),
    ],
)
def test_security_trigger_coverage(task: str, expected_trigger: str):
    result = preview_security_review(task)
    payload = decision_to_dict(result.review)
    assert expected_trigger in payload["security_triggers"]


def test_preview_mode_writes_nothing(tmp_path: Path):
    result = preview_security_review(
        "Review SSH access and browser profile permissions",
        evidence=["SSH tunnel only", "browser profiles limited to dedicated directory"],
        output_root=tmp_path,
    )

    assert result.write_performed is False
    assert result.write_verified is False
    assert result.review.write_performed is False
    assert result.review.write_verified is False
    assert not any(tmp_path.rglob("*.md"))


def test_write_mode_creates_artifact_under_allowed_output_root(tmp_path: Path):
    result = write_security_review(
        "Review SSH access and browser profile permissions",
        evidence=["SSH tunnel only", "browser profiles limited to dedicated directory"],
        output_root=tmp_path,
        review_id="security-review-smoke",
        timestamp_utc="2026-06-08T14:22:33Z",
    )

    artifact_path = Path(result.artifact_path)
    assert result.write_performed is True
    assert result.write_verified is True
    assert result.review.write_performed is True
    assert result.review.write_verified is True
    assert artifact_path.exists()
    assert artifact_path.read_text(encoding="utf-8").startswith("---\n")
    assert artifact_path.is_relative_to(tmp_path)


def test_tests_use_tmp_path_output_root_not_real_repo_docs(tmp_path: Path):
    result = preview_security_review(
        "Review SSH access and browser profile permissions",
        evidence=["SSH tunnel only", "browser profiles limited to dedicated directory"],
        output_root=tmp_path,
    )

    assert result.artifact_path.startswith(str(tmp_path))
    assert not Path(result.artifact_path).exists()


def test_path_allowlist_rejects_symlink_escape_outside_root(tmp_path: Path):
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (docs_root / "security-reviews").symlink_to(outside, target_is_directory=True)

    result = write_security_review(
        "Review SSH access and browser profile permissions",
        evidence=["SSH tunnel only", "browser profiles limited to dedicated directory"],
        output_root=docs_root,
    )

    assert result.write_performed is True
    assert result.write_verified is False
    assert result.write_error is not None
    assert not any(outside.rglob("*.md"))


def test_existing_artifact_is_not_overwritten_silently(tmp_path: Path):
    first = write_security_review(
        "Review SSH access and browser profile permissions",
        evidence=["SSH tunnel only", "browser profiles limited to dedicated directory"],
        output_root=tmp_path,
        review_id="same-review-id",
        timestamp_utc="2026-06-08T14:22:33Z",
    )
    second = write_security_review(
        "Review SSH access and browser profile permissions",
        evidence=["SSH tunnel only", "browser profiles limited to dedicated directory"],
        output_root=tmp_path,
        review_id="same-review-id",
        timestamp_utc="2026-06-08T14:22:33Z",
    )

    assert first.write_verified is True
    assert second.write_verified is False
    assert second.write_error is not None


def test_route_and_approval_metadata_are_serialized(tmp_path: Path):
    task = "Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения"
    route_decision = route_task(task)
    approval_preview = classify_engineer_approval(task, route_decision=route_decision)
    result = preview_security_review(
        task,
        route_decision=route_decision,
        approval_preview=approval_preview,
        output_root=tmp_path,
        evidence=["Loopback bind verified", "SSH tunnel required"],
    )
    payload = decision_to_dict(result.review)

    assert payload["route_decision"]["primary_profile"] == "engineer"
    assert payload["approval_preview"]["profile"] == "engineer"
    assert payload["reviewed_by_profile"] == "security_auditor"


def test_non_security_task_returns_not_applicable(tmp_path: Path):
    result = preview_security_review("Update docs table of contents", output_root=tmp_path)
    assert result.review.security_review_status == "not_applicable"
    assert "No security trigger" in result.review.status_reason


def test_security_sensitive_task_with_insufficient_evidence_returns_fail(tmp_path: Path):
    result = preview_security_review("Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения", output_root=tmp_path)
    assert result.review.security_review_status == "fail"
    assert "insufficiently evidenced" in result.review.status_reason


def test_security_sensitive_task_with_required_changes_and_residual_risks_returns_conditional_pass(tmp_path: Path):
    result = preview_security_review(
        "Review WebUI auth/session model and local access",
        output_root=tmp_path,
        evidence=["Password auth enabled on loopback", "Session cookies use secure flags"],
        required_changes=["Verify session invalidation after auth changes"],
        residual_risks=["Manual review still required for browser session replay"] ,
    )
    assert result.review.security_review_status == "conditional_pass"
    assert result.review.required_changes
    assert result.review.residual_risks


def test_security_sensitive_task_with_sufficient_evidence_and_no_required_changes_can_return_pass(tmp_path: Path):
    result = preview_security_review(
        "Review SSH access and browser profile permissions",
        output_root=tmp_path,
        evidence=["SSH tunnel only", "browser profiles are restricted to a dedicated directory"],
    )
    assert result.review.security_review_status == "pass"
    assert not result.review.required_changes
    assert not result.review.residual_risks


def test_webui_public_exposure_without_mitigations_does_not_return_pass(tmp_path: Path):
    result = preview_security_review("Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения", output_root=tmp_path)
    assert result.review.security_review_status == "fail"
    assert result.review.security_review_status != "pass"


def test_webui_public_exposure_with_explicit_mitigations_returns_conditional_pass_not_pass(tmp_path: Path):
    result = preview_security_review(
        "Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения",
        output_root=tmp_path,
        evidence=["Loopback bind verified", "SSH tunnel required", "Password auth enabled"],
        required_changes=["Keep the service loopback-only and behind SSH tunnel"],
        residual_risks=["Public exposure remains blocked until network boundary is reviewed"],
    )
    assert result.review.security_review_status == "conditional_pass"
    assert result.review.security_review_status != "pass"


def test_markdown_contains_required_metadata_and_sections(tmp_path: Path):
    result = preview_security_review(
        "Review SSH access and browser profile permissions",
        output_root=tmp_path,
        evidence=["SSH tunnel only", "browser profiles are restricted to a dedicated directory"],
    )
    markdown = render_security_review_markdown(result.review)

    for section in [
        "## Summary",
        "## Security Triggers",
        "## Reviewed Risks",
        "## Required Changes",
        "## Residual Risks",
        "## Evidence",
        "## Assumptions",
        "## Route Decision",
        "## Approval Preview",
        "## Security Review Status",
    ]:
        assert section in markdown
    for field in [
        "schema_version: 1",
        "review_id:",
        "timestamp_utc:",
        "reviewed_by_profile:",
        "security_review_status:",
        "security_triggers:",
        "write_verified:",
    ]:
        assert field in markdown


def test_typed_evidence_is_normalized_and_serialized(tmp_path: Path):
    result = build_security_review(
        "Review SSH access and browser profile permissions",
        evidence=[
            {"type": "doc", "source": "docs", "summary": "Loopback bind verified"},
            "SSH tunnel required",
        ],
    )
    payload = decision_to_dict(result)
    assert payload["evidence"][0]["type"] == "doc"
    assert payload["evidence"][0]["source"] == "docs"
    assert payload["evidence"][1]["type"] == "operator_note"
    assert payload["evidence"][1]["source"] == "cli"


def test_import_boundary_does_not_pull_runtime_modules():
    code = textwrap.dedent(
        r'''
        import sys
        import hermes_cli.profile_security_review  # noqa: F401
        forbidden = [name for name in sys.modules if name.startswith(('agent', 'gateway', 'cron.scheduler', 'run_agent'))]
        print('\n'.join(sorted(forbidden)))
        '''
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    forbidden = [line for line in result.stdout.splitlines() if line.strip()]
    assert forbidden == []


def test_cli_preview_prints_json(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task",
            "Review SSH access and browser profile permissions",
            "--json",
            "--evidence",
            "SSH tunnel only",
            "--evidence",
            "browser profiles are restricted to a dedicated directory",
            "--output-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    payload = _json_output(result)
    assert payload["write_performed"] is False
    assert payload["review"]["task_summary"] == "Review SSH access and browser profile permissions"
    assert payload["review"]["security_review_status"] == "pass"


def test_cli_write_prints_artifact_path_and_json_summary(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task",
            "Review SSH access and browser profile permissions",
            "--json",
            "--write",
            "--evidence",
            "SSH tunnel only",
            "--evidence",
            "browser profiles are restricted to a dedicated directory",
            "--output-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    payload = _json_output(result)
    assert payload["write_performed"] is True
    assert payload["write_verified"] is True
    assert Path(payload["artifact_path"]).exists()
    assert payload["review"]["security_review_status"] == "pass"


@pytest.mark.parametrize("task", ["   ", ""])
def test_invalid_input_exits_non_zero(task: str):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--task", task, "--json"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode != 0
    assert "must not be empty" in result.stderr
