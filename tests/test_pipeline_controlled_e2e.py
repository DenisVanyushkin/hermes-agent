from __future__ import annotations

import importlib
import json
from pathlib import Path
import shutil
import subprocess

from hermes_cli.pipeline_router import RouterDecision
from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
from hermes_cli.pipeline_specs import load_pipeline_specs
from hermes_cli.runtime_factory import RuntimeFactory
from hermes_cli.subagent_runner import SubagentRunner


REPO_ROOT = Path("/home/hermes/.hermes/hermes-agent")


def _copy_spec_tree(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "config", repo_root / "config")
    shutil.copytree(REPO_ROOT / "prompts", repo_root / "prompts")
    return repo_root


def _loaded_specs(tmp_path: Path):
    repo_root = _copy_spec_tree(tmp_path)
    return repo_root, load_pipeline_specs(repo_root=repo_root)


def _session():
    decision = RouterDecision(
        pipeline_session_id="pipe-controlled-e2e-1",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.98,
        reasoning_summary="engineering",
        fallback_safe=False,
    )
    return create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=decision,
            execution_mode="observe",
            platform="telegram",
            session_id="sess-controlled-e2e-1",
            user_message="Add controlled engineering e2e acceptance coverage",
            created_at="2026-06-18T00:00:00+00:00",
        )
    )


def _config() -> dict[str, object]:
    return {
        "pipelines": {
            "enabled": True,
            "execution": {
                "mode": "controlled_one_step",
                "allow_pipelines": ["engineering_review_pipeline"],
                "allowed_subagents": ["hermes_engineer_core", "hermes_code_reviewer"],
                "allow_actual_subagent_invocation": True,
                "allow_actual_reviewer_invocation": True,
                "allow_actual_rework_loop": True,
            }
        }
    }


def _engineer_output(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "v1",
        "subagent_id": "hermes_engineer_core",
        "role": "engineer",
        "status": "succeeded",
        "summary": "Added generated example test.",
        "findings": [{"code": "test_added", "summary": "Added generated example test"}],
        "changes": [{"path": "tests/test_generated_example.py", "kind": "modify"}],
        "blockers": [],
        "artifacts": [{"artifact_id": "patch-1", "kind": "diff"}],
        "confidence": 0.93,
        "requires_review": False,
        "next_action": "none",
        "mutations": [
            {
                "operation": "write_text",
                "path": "tests/test_generated_example.py",
                "content": "def test_generated_example():\n    assert 1 + 1 == 2\n",
            }
        ],
        "tests": ["venv/bin/pytest -q tests/test_generated_example.py"],
    }
    payload.update(overrides)
    return payload


def _reviewer_output() -> dict[str, object]:
    return {
        "schema_version": "v1",
        "subagent_id": "hermes_code_reviewer",
        "role": "reviewer",
        "status": "succeeded",
        "summary": "Approved generated example test.",
        "findings": [],
        "changes": [],
        "blockers": [],
        "artifacts": [{"artifact_id": "review-1", "kind": "review"}],
        "confidence": 0.89,
        "requires_review": False,
        "next_action": "none",
    }


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    )


def _init_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "controlled-e2e-repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / ".gitignore").write_text("tests/__pycache__/\nvenv\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    (repo / "venv").symlink_to(REPO_ROOT / "venv")
    return repo


def test_controlled_engineering_pipeline_e2e_mutates_tests_reviews_and_reports(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_rework_loop")
    repo_root, loaded_specs = _loaded_specs(tmp_path)
    repo = _init_git_repo(tmp_path)

    factory_calls: list[tuple[str, str, str]] = []

    def _real_provider_factory(runtime):
        factory_calls.append((runtime.subagent_id, str(runtime.provider), str(runtime.model)))

        def _client(_request):
            if runtime.subagent_id == "hermes_engineer_core":
                return {
                    "provider": runtime.provider,
                    "model": runtime.model,
                    "structured_output": _engineer_output(),
                    "output_text": "engineer runtime completed",
                    "token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                    "tool_calls": [{"tool_name": "apply_patch", "call_count": 1, "status": "not_invoked"}],
                }
            return {
                "provider": runtime.provider,
                "model": runtime.model,
                "structured_output": _reviewer_output(),
                "output_text": "reviewer runtime approved",
                "token_usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
                "tool_calls": [{"tool_name": "pytest", "call_count": 1, "status": "not_invoked"}],
            }

        return _client

    result = module.execute_bounded_rework_loop(
        config=_config(),
        session=_session(),
        loaded_specs=loaded_specs,
        runtime_factory=RuntimeFactory(repo_root=repo_root),
        runner=SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        user_message="Add controlled engineering e2e acceptance coverage",
        repo_path=str(repo),
        allow_completion_after_review=True,
        controlled_runtime_context={
            "invocation_client": lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fake runtime must not be used")),
            "controlled_runner": module.ControlledRuntimeRunner(),
            "allow_real_provider_execution": True,
            "request_real_provider_execution": True,
            "allowed_real_providers": ("openrouter", "openai-codex"),
            "allowed_real_models": ("xiaomi/mimo-v2.5-pro", "gpt-5.5"),
            "allowed_real_providers_by_role": {
                "engineer": ("openrouter",),
                "reviewer": ("openai-codex",),
            },
            "allowed_real_models_by_role": {
                "engineer": ("xiaomi/mimo-v2.5-pro",),
                "reviewer": ("gpt-5.5",),
            },
            "allowed_real_providers_by_subagent": {
                "hermes_engineer_core": ("openrouter",),
                "hermes_code_reviewer": ("openai-codex",),
            },
            "allowed_real_models_by_subagent": {
                "hermes_engineer_core": ("xiaomi/mimo-v2.5-pro",),
                "hermes_code_reviewer": ("gpt-5.5",),
            },
            "real_provider_client_factory": _real_provider_factory,
            "allow_mutations": True,
            "mutation_workspace": str(repo),
            "allow_test_commands": True,
            "test_workspace": str(repo),
        },
    )

    safe_result = result.to_safe_dict()
    report_payload = result.execution_report.to_safe_dict()
    encoded_result = json.dumps(safe_result, sort_keys=True)
    encoded_report = json.dumps(report_payload, sort_keys=True)
    created_file = repo / "tests/test_generated_example.py"

    assert factory_calls == [
        ("hermes_engineer_core", "openrouter", "xiaomi/mimo-v2.5-pro"),
        ("hermes_code_reviewer", "openai-codex", "gpt-5.5"),
    ]
    assert created_file.exists()
    assert created_file.read_text(encoding="utf-8") == "def test_generated_example():\n    assert 1 + 1 == 2\n"

    assert safe_result["completion_allowed"] is True
    assert safe_result["candidate_complete"] is True
    assert safe_result["blocked_reason"] is None
    assert report_payload["completion"]["completion_allowed"] is True
    assert report_payload["completion"]["candidate_complete"] is True
    assert report_payload["review"]["reviewer_invoked"] is True
    assert report_payload["review"]["reviewer_approved"] is True
    assert report_payload["review"]["final_review_decision"] == "approved"
    assert report_payload["review"]["blocked_reason"] is None

    assert safe_result["subagent_runs"][0]["runtime_mode"] == "real_provider"
    assert safe_result["subagent_runs"][0]["real_provider_allowed"] is True
    assert safe_result["subagent_runs"][0]["provider_policy_status"] == "allowed"
    assert safe_result["subagent_runs"][0]["actual_provider"] == "openrouter"
    assert safe_result["subagent_runs"][0]["actual_model"] == "xiaomi/mimo-v2.5-pro"
    assert safe_result["subagent_runs"][1]["runtime_mode"] == "real_provider"
    assert report_payload["subagent_runs"][0]["runtime_mode"] == "real_provider"
    assert report_payload["subagent_runs"][0]["raw_output_redacted"] is True

    assert safe_result["mutation_summary"]["enabled"] is True
    assert safe_result["mutation_summary"]["applied_count"] == 1
    assert safe_result["mutation_summary"]["denied_count"] == 0
    assert report_payload["mutation_summary"]["applied_count"] == 1

    assert safe_result["test_summary"]["status"] == "passed"
    assert len(safe_result["test_summary"]["results"]) == 1
    assert safe_result["test_summary"]["results"][0]["status"] == "passed"
    assert safe_result["test_summary"]["results"][0]["command"] == ["venv/bin/pytest", "-q", "tests/test_generated_example.py"]
    assert safe_result["test_summary"]["results"][0]["cwd"] == repo.name
    assert report_payload["tests"]["status"] == "passed"
    assert len(report_payload["tests"]["results"]) == 1
    assert report_payload["tests"]["results"][0]["command"] == ["venv/bin/pytest", "-q", "tests/test_generated_example.py"]

    assert safe_result["git_gate"]["material_change_status"] == "material_changes_detected"
    assert safe_result["git_gate"]["material_changes_present"] is True
    assert safe_result["git_gate"]["review_required"] is True
    assert safe_result["git_gate"]["changed_files"] == ["tests/test_generated_example.py"]
    assert report_payload["git_gate"]["changed_files"] == ["tests/test_generated_example.py"]

    assert safe_result["reviewer_packet"]["present"] is True
    assert safe_result["reviewer_packet"]["packet_status"] == "ready_for_review"
    assert safe_result["reviewer_packet"]["safe_packet"]["git"]["changed_files"] == ["tests/test_generated_example.py"]
    assert safe_result["reviewer_packet"]["safe_packet"]["tests"]["status"] == "passed"
    assert safe_result["reviewer_packet"]["safe_packet"]["tests"]["results"][0]["cwd"] == repo.name
    assert report_payload["reviewer_packet"]["present"] is True
    assert report_payload["reviewer_packet"]["safe_packet"]["tests"]["status"] == "passed"

    assert report_payload["schema_version"] == "pipeline_execution_report.v1"
    assert report_payload["summary"]["pipeline_id"] == "engineering_review_pipeline"
    assert report_payload["usage"]["total_tokens"] == 21
    assert safe_result["usage_summary"]["total_tokens"] == 21
    assert "usage_summary" in safe_result
    assert "peer_messages" in report_payload and report_payload["peer_messages"] == []
    assert "disagreements" in report_payload and report_payload["disagreements"] == []
    assert "model_escalations" in report_payload and report_payload["model_escalations"] == []
    assert report_payload["safety"]["prompts_redacted"] is True
    assert report_payload["safety"]["environment_redacted"] is True
    assert report_payload["safety"]["raw_task_redacted"] is True
    assert report_payload["safety"]["raw_outputs_redacted"] is True
    assert report_payload["safety"]["live_execution_enabled"] is False

    for forbidden in (
        str(repo),
        "assert 1 + 1 == 2",
        "engineer runtime completed",
        "reviewer runtime approved",
    ):
        assert forbidden not in encoded_result
        assert forbidden not in encoded_report
