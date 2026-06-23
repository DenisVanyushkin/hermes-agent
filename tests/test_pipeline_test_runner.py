from __future__ import annotations

import importlib
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO_ROOT = Path("/home/hermes/.hermes/hermes-agent")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    )


def _init_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "test-runner-repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_gate_disabled_denies_without_spawning_subprocess(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    calls: list[list[str]] = []

    def _unexpected_runner(argv, **kwargs):
        calls.append(list(argv))
        raise AssertionError("subprocess runner must not be called when gate is disabled")

    repo = _init_git_repo(tmp_path)
    summary = module.run_controlled_tests(
        allow_test_commands=False,
        test_workspace=repo,
        tests_payload=["venv/bin/pytest -q tests/test_example.py"],
        step_kind="engineer",
        step_subagent_id="hermes_engineer_core",
        subprocess_runner=_unexpected_runner,
    )

    payload = summary.to_safe_dict()

    assert payload["enabled"] is False
    assert payload["blocked_reason"] == "test_command_gate_disabled"
    assert payload["denied_count"] == 1
    assert payload["executed_count"] == 0
    assert calls == []


def test_safe_pytest_command_executes_in_workspace(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    repo = _init_git_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_example.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (repo / "venv").symlink_to(REPO_ROOT / "venv")

    summary = module.run_controlled_tests(
        allow_test_commands=True,
        test_workspace=repo,
        tests_payload=["venv/bin/pytest -q tests/test_example.py"],
        step_kind="engineer",
        step_subagent_id="hermes_engineer_core",
    )

    payload = summary.to_safe_dict()

    assert payload["status"] == "passed"
    assert payload["executed_count"] == 1
    assert payload["passed_count"] == 1
    assert payload["blocked_reason"] is None
    assert payload["results"][0]["command"] == ["venv/bin/pytest", "-q", "tests/test_example.py"]
    assert payload["results"][0]["cwd"] == repo.name


def test_python_module_pytest_executes_via_active_interpreter(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    repo = _init_git_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_example.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    calls: list[list[str]] = []

    def _runner(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=".\n1 passed\n", stderr="")

    summary = module.run_controlled_tests(
        allow_test_commands=True,
        test_workspace=repo,
        tests_payload=["python -m pytest -q tests/test_example.py"],
        step_kind="engineer",
        step_subagent_id="hermes_engineer_core",
        subprocess_runner=_runner,
    )

    payload = summary.to_safe_dict()

    assert payload["status"] == "passed"
    assert payload["results"][0]["command"] == ["python", "-m", "pytest", "-q", "tests/test_example.py"]
    assert calls == [[sys.executable, "-m", "pytest", "-q", "tests/test_example.py"]]


def test_structured_pytest_payload_executes_via_canonical_argv(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    repo = _init_git_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_example.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    calls: list[list[str]] = []

    def _runner(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=".\n1 passed\n", stderr="")

    summary = module.run_controlled_tests(
        allow_test_commands=True,
        test_workspace=repo,
        tests_payload=[{"targets": ["tests/test_example.py"], "quiet": True, "maxfail": 1}],
        step_kind="engineer",
        step_subagent_id="hermes_engineer_core",
        subprocess_runner=_runner,
    )

    payload = summary.to_safe_dict()

    assert payload["status"] == "passed"
    assert payload["results"][0]["command"] == [sys.executable, "-m", "pytest", "-q", "--maxfail=1", "tests/test_example.py"]
    assert calls == [[sys.executable, "-m", "pytest", "-q", "--maxfail=1", "tests/test_example.py"]]


def test_bare_pytest_executes_via_active_interpreter(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    repo = _init_git_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_example.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    calls: list[list[str]] = []

    def _runner(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=".\n1 passed\n", stderr="")

    summary = module.run_controlled_tests(
        allow_test_commands=True,
        test_workspace=repo,
        tests_payload=["pytest -q tests/test_example.py"],
        step_kind="engineer",
        step_subagent_id="hermes_engineer_core",
        subprocess_runner=_runner,
    )

    payload = summary.to_safe_dict()

    assert payload["status"] == "passed"
    assert payload["results"][0]["command"] == ["pytest", "-q", "tests/test_example.py"]
    assert calls == [[sys.executable, "-m", "pytest", "-q", "tests/test_example.py"]]


def test_path_traversal_command_is_denied_without_spawning_subprocess(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    calls: list[list[str]] = []

    def _unexpected_runner(argv, **kwargs):
        calls.append(list(argv))
        raise AssertionError("path traversal must not reach subprocess runner")

    repo = _init_git_repo(tmp_path)
    summary = module.run_controlled_tests(
        allow_test_commands=True,
        test_workspace=repo,
        tests_payload=["pytest -q tests/../secrets.py"],
        step_kind="engineer",
        step_subagent_id="hermes_engineer_core",
        subprocess_runner=_unexpected_runner,
    )

    payload = summary.to_safe_dict()

    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "test_command_denied"
    assert calls == []
    assert payload["results"][0]["denied_command_raw_sanitized"] == "pytest -q tests/../secrets.py"
    assert payload["results"][0]["validator_reason"] == "test_command_denied"


def test_unsafe_command_is_denied_without_spawning_subprocess(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    calls: list[list[str]] = []

    def _unexpected_runner(argv, **kwargs):
        calls.append(list(argv))
        raise AssertionError("unsafe command must not reach subprocess runner")

    repo = _init_git_repo(tmp_path)
    summary = module.run_controlled_tests(
        allow_test_commands=True,
        test_workspace=repo,
        tests_payload=["venv/bin/pytest -q tests/test_example.py && cat .env"],
        step_kind="engineer",
        step_subagent_id="hermes_engineer_core",
        subprocess_runner=_unexpected_runner,
    )

    payload = summary.to_safe_dict()

    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "test_command_denied"
    assert payload["denied_count"] == 1
    assert calls == []


def test_malformed_diagnostic_mapping_is_denied_with_forensics_without_claiming_pytest(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    calls: list[list[str]] = []

    def _unexpected_runner(argv, **kwargs):
        calls.append(list(argv))
        raise AssertionError("malformed diagnostic payload must not reach subprocess runner")

    repo = _init_git_repo(tmp_path)
    summary = module.run_controlled_tests(
        allow_test_commands=True,
        test_workspace=repo,
        tests_payload=[{"status": "observed", "summary": "workspace only contains tracked.txt"}],
        step_kind="engineer",
        step_subagent_id="hermes_engineer_core",
        subprocess_runner=_unexpected_runner,
    )

    payload = summary.to_safe_dict()

    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "test_command_denied"
    assert payload["results"][0]["command"] == ["[denied]"]
    assert payload["results"][0]["denied_command_raw_sanitized"] == "targets=[denied]"
    assert payload["results"][0]["validator_reason"] == "structured_pytest_payload_missing_targets"
    assert calls == []


def test_absolute_path_structured_target_is_denied_without_spawning_subprocess(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    calls: list[list[str]] = []

    def _unexpected_runner(argv, **kwargs):
        calls.append(list(argv))
        raise AssertionError("absolute path target must not reach subprocess runner")

    repo = _init_git_repo(tmp_path)
    summary = module.run_controlled_tests(
        allow_test_commands=True,
        test_workspace=repo,
        tests_payload=[{"targets": ["/tmp/test_example.py"], "quiet": True}],
        step_kind="engineer",
        step_subagent_id="hermes_engineer_core",
        subprocess_runner=_unexpected_runner,
    )

    payload = summary.to_safe_dict()

    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "test_command_denied"
    assert calls == []


def test_non_tests_structured_target_is_denied_without_spawning_subprocess(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    calls: list[list[str]] = []

    def _unexpected_runner(argv, **kwargs):
        calls.append(list(argv))
        raise AssertionError("non-tests target must not reach subprocess runner")

    repo = _init_git_repo(tmp_path)
    summary = module.run_controlled_tests(
        allow_test_commands=True,
        test_workspace=repo,
        tests_payload=[{"targets": ["src/test_example.py"], "quiet": True}],
        step_kind="engineer",
        step_subagent_id="hermes_engineer_core",
        subprocess_runner=_unexpected_runner,
    )

    payload = summary.to_safe_dict()

    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "test_command_denied"
    assert calls == []


def test_failed_pytest_blocks_completion(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    repo = _init_git_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_example.py").write_text("def test_fail():\n    assert False\n", encoding="utf-8")
    (repo / "venv").symlink_to(REPO_ROOT / "venv")

    summary = module.run_controlled_tests(
        allow_test_commands=True,
        test_workspace=repo,
        tests_payload=["venv/bin/pytest -q tests/test_example.py"],
        step_kind="engineer",
        step_subagent_id="hermes_engineer_core",
    )

    payload = summary.to_safe_dict()

    assert payload["status"] == "failed"
    assert payload["blocked_reason"] == "test_command_failed"
    assert payload["failed_count"] == 1
    assert payload["results"][0]["status"] == "failed"


def test_missing_executable_blocks_with_distinct_safe_reason(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    repo = _init_git_repo(tmp_path)

    def _missing_runner(argv, **kwargs):
        raise FileNotFoundError("/very/secret/location/python3")

    summary = module.run_controlled_tests(
        allow_test_commands=True,
        test_workspace=repo,
        tests_payload=["python3 -m pytest -q tests/test_example.py"],
        step_kind="engineer",
        step_subagent_id="hermes_engineer_core",
        subprocess_runner=_missing_runner,
    )

    payload = summary.to_safe_dict()
    encoded = str(payload)

    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "test_command_start_failed"
    assert payload["failed_count"] == 0
    assert payload["results"][0]["status"] == "blocked"
    assert payload["results"][0]["reason"] == "test_command_start_failed"
    assert payload["results"][0]["stdout_excerpt"] is None
    assert payload["results"][0]["stderr_excerpt"] is None
    assert "/very/secret/location/python3" not in encoded
    assert "secret" not in encoded.lower()


def test_timeout_is_fail_closed(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    repo = _init_git_repo(tmp_path)

    def _timeout_runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=list(argv), timeout=kwargs.get("timeout", 30))

    summary = module.run_controlled_tests(
        allow_test_commands=True,
        test_workspace=repo,
        tests_payload=["python3 -m pytest -q tests/test_example.py"],
        step_kind="engineer",
        step_subagent_id="hermes_engineer_core",
        subprocess_runner=_timeout_runner,
        timeout_seconds=1,
    )

    payload = summary.to_safe_dict()

    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "test_command_timeout"
    assert payload["timeout_count"] == 1
    assert payload["results"][0]["status"] == "timeout"


def test_reviewer_role_cannot_execute_tests(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    repo = _init_git_repo(tmp_path)

    summary = module.run_controlled_tests(
        allow_test_commands=True,
        test_workspace=repo,
        tests_payload=["venv/bin/pytest -q tests/test_example.py"],
        step_kind="reviewer",
        step_subagent_id="hermes_code_reviewer",
    )

    payload = summary.to_safe_dict()

    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "test_command_role_not_permitted"
    assert payload["denied_count"] == 1


def test_too_many_test_commands_fail_closed_without_spawning_subprocess(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    calls: list[list[str]] = []

    def _unexpected_runner(argv, **kwargs):
        calls.append(list(argv))
        raise AssertionError("runner must not be called when request coercion is denied")

    repo = _init_git_repo(tmp_path)
    summary = module.run_controlled_tests(
        allow_test_commands=True,
        test_workspace=repo,
        tests_payload=[
            "venv/bin/pytest -q tests/test_one.py",
            "venv/bin/pytest -q tests/test_two.py",
            "venv/bin/pytest -q tests/test_three.py",
            "venv/bin/pytest -q tests/test_four.py",
        ],
        step_kind="engineer",
        step_subagent_id="hermes_engineer_core",
        subprocess_runner=_unexpected_runner,
    )

    payload = summary.to_safe_dict()

    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "test_command_denied"
    assert payload["denied_count"] > 0
    assert payload["executed_count"] == 0
    assert calls == []


def test_denied_command_captures_sanitized_forensics(tmp_path: Path) -> None:
    module = importlib.import_module("hermes_cli.pipeline_test_runner")
    repo = _init_git_repo(tmp_path)

    summary = module.run_controlled_tests(
        allow_test_commands=True,
        test_workspace=repo,
        tests_payload=["pytest -q tests/test_example.py --api-key=secret-value"],
        step_kind="engineer",
        step_subagent_id="hermes_engineer_core",
    )

    payload = summary.to_safe_dict()
    denied = payload["results"][0]

    assert payload["blocked_reason"] == "test_command_denied"
    assert denied["status"] == "denied"
    assert denied["command"][0] == "pytest"
    assert "secret-value" not in str(denied)
