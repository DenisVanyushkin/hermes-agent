"""Constrained AIAgent executor bridge for controlled engineering runs."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any, Callable, Mapping

import model_tools
import run_agent
from hermes_cli.pipeline_mutations import apply_controlled_mutations
from hermes_cli.pipeline_test_runner import run_controlled_tests


_ALLOWED_TOOL_NAMES = (
    "read_file",
    "search_files",
    "patch",
    "write_file",
    "git_status",
    "git_diff",
    "pytest",
)


class AIAgentExecutorBridgeError(RuntimeError):
    """Bridge configuration or execution failed closed."""


class AIAgentSubagentExecutorBridge:
    def __init__(
        self,
        *,
        workspace_root: str | Path,
        repo_root: str | Path | None = None,
        agent_factory: Callable[..., Any] | None = None,
        conversation_runner: Callable[["AIAgentSubagentExecutorBridge", Any, Any, Any], Mapping[str, Any]] | None = None,
        subprocess_runner: Callable[..., Any] = subprocess.run,
        max_iterations: int = 12,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.repo_root = Path(repo_root).resolve() if repo_root is not None else None
        self.agent_factory = agent_factory or run_agent.AIAgent
        self.conversation_runner = conversation_runner or self._default_conversation_runner
        self.subprocess_runner = subprocess_runner
        self.max_iterations = max_iterations
        self._tool_calls: list[dict[str, Any]] = []

    def __call__(self, request: Any, runtime_plan: Any) -> dict[str, Any]:
        self._validate_runtime_plan(runtime_plan)
        self._tool_calls = []
        agent = self._build_agent(runtime_plan)
        result = self.conversation_runner(self, agent, request, runtime_plan)
        normalized = self._normalize_result(result)
        tool_intents = [
            {"name": item["tool_name"], "arguments": dict(item.get("arguments") or {})}
            for item in self._tool_calls
        ]
        raw_metadata = dict(normalized.get("raw_metadata") or {})
        raw_metadata.setdefault("tool_calls", list(self._tool_calls))
        raw_metadata.setdefault(
            "bridge_metadata",
            {
                "workspace_root": self.workspace_root.name,
                "subagent_id": runtime_plan.subagent_id,
                "role_id": "engineer",
            },
        )
        return {
            "output_text": normalized.get("output_text"),
            "completion_reason": normalized.get("completion_reason") or "completed",
            "execution_status": normalized.get("execution_status") or "completed",
            "token_usage": dict(normalized.get("token_usage") or {}),
            "tool_intents": tool_intents,
            "raw_metadata": raw_metadata,
        }

    def execute_tool(self, tool_name: str, arguments: Mapping[str, Any] | None = None) -> str:
        args = dict(arguments or {})
        if tool_name not in _ALLOWED_TOOL_NAMES:
            raise AIAgentExecutorBridgeError(f"tool_not_allowed:{tool_name}")

        if tool_name == "read_file":
            path = self._resolve_workspace_path(str(args.get("path") or ""), allow_missing=False)
            result = {"path": self._relative_path(path), "content": path.read_text(encoding="utf-8")}
        elif tool_name == "search_files":
            pattern = str(args.get("pattern") or "").strip()
            if not pattern:
                raise AIAgentExecutorBridgeError("invalid_search_pattern")
            completed = self.subprocess_runner(
                ["rg", "-n", "--color", "never", pattern, "."],
                cwd=str(self.workspace_root),
                text=True,
                capture_output=True,
                check=False,
            )
            result = {
                "pattern": pattern,
                "status": "matched" if completed.returncode in {0, 1} else "failed",
                "output": completed.stdout,
            }
        elif tool_name == "write_file":
            path_value = str(args.get("path") or "")
            content = str(args.get("content") or "")
            summary = apply_controlled_mutations(
                allow_mutations=True,
                mutation_workspace=self.workspace_root,
                mutations_payload=[{"operation": "write_text", "path": path_value, "content": content}],
            )
            result = summary.to_safe_dict()
            if summary.denied_count:
                raise AIAgentExecutorBridgeError(result["results"][0]["reason"])
        elif tool_name == "patch":
            path = self._resolve_workspace_path(str(args.get("path") or ""), allow_missing=False)
            original = path.read_text(encoding="utf-8")
            if "content" in args:
                new_content = str(args.get("content") or "")
            else:
                old = str(args.get("old") or "")
                new = str(args.get("new") or "")
                if not old:
                    raise AIAgentExecutorBridgeError("patch_old_missing")
                if old not in original:
                    raise AIAgentExecutorBridgeError("patch_old_not_found")
                new_content = original.replace(old, new, 1)
            summary = apply_controlled_mutations(
                allow_mutations=True,
                mutation_workspace=self.workspace_root,
                mutations_payload=[{"operation": "write_text", "path": self._relative_path(path), "content": new_content}],
            )
            result = summary.to_safe_dict()
            if summary.denied_count:
                raise AIAgentExecutorBridgeError(result["results"][0]["reason"])
        elif tool_name == "git_status":
            completed = self.subprocess_runner(
                ["git", "status", "--short", "--untracked-files=all"],
                cwd=str(self.workspace_root),
                text=True,
                capture_output=True,
                check=False,
            )
            result = {"status": completed.returncode, "output": completed.stdout}
        elif tool_name == "git_diff":
            completed = self.subprocess_runner(
                ["git", "diff", "--no-ext-diff"],
                cwd=str(self.workspace_root),
                text=True,
                capture_output=True,
                check=False,
            )
            result = {"status": completed.returncode, "output": completed.stdout}
        elif tool_name == "pytest":
            command = str(args.get("command") or "").strip()
            summary = run_controlled_tests(
                allow_test_commands=True,
                test_workspace=self.workspace_root,
                tests_payload=[command],
                step_kind="engineer",
                step_subagent_id="hermes_engineer_core",
                subprocess_runner=self.subprocess_runner,
            )
            result = summary.to_safe_dict()
            if summary.blocked_reason is not None:
                raise AIAgentExecutorBridgeError(summary.blocked_reason)
        else:
            raise AIAgentExecutorBridgeError(f"tool_not_implemented:{tool_name}")

        self._tool_calls.append(
            {
                "tool_name": tool_name,
                "arguments": self._redacted_arguments(args),
                "status": "succeeded",
            }
        )
        return json.dumps(result, ensure_ascii=False)

    def _build_agent(self, runtime_plan: Any) -> Any:
        kwargs = runtime_plan.to_aiagent_kwargs()
        kwargs.update(
            {
                "quiet_mode": True,
                "max_iterations": self.max_iterations,
                "enabled_toolsets": [],
                "disabled_toolsets": ["terminal", "browser", "web", "code_execution", "computer_use", "messaging"],
                "skip_context_files": True,
                "skip_memory": True,
                "load_soul_identity": False,
                "ephemeral_system_prompt": self._load_prompt_text(runtime_plan),
            }
        )
        agent = self.agent_factory(**kwargs)
        agent.tools = self._tool_definitions()
        agent.valid_tool_names = set(_ALLOWED_TOOL_NAMES)
        agent.enabled_toolsets = []
        agent.disabled_toolsets = list(kwargs["disabled_toolsets"])
        return agent

    def _default_conversation_runner(self, _bridge: "AIAgentSubagentExecutorBridge", agent: Any, request: Any, _runtime_plan: Any) -> Mapping[str, Any]:
        user_message = ""
        if getattr(request, "input_messages", None):
            first = request.input_messages[0]
            if isinstance(first, Mapping):
                user_message = str(first.get("content") or "")
        with self.patched_tool_dispatch():
            return agent.run_conversation(user_message)

    def _normalize_result(self, result: Any) -> dict[str, Any]:
        if isinstance(result, Mapping):
            if "final_response" in result and "output_text" not in result:
                return {
                    "output_text": result.get("final_response"),
                    "execution_status": result.get("execution_status") or "completed",
                    "completion_reason": result.get("completion_reason") or "completed",
                    "token_usage": result.get("token_usage") or {},
                    "raw_metadata": {
                        "structured_output": result.get("structured_output"),
                    },
                }
            return dict(result)
        if isinstance(result, str):
            return {"output_text": result, "execution_status": "completed", "completion_reason": "completed", "raw_metadata": {}}
        raise AIAgentExecutorBridgeError("invalid_agent_result")

    def _validate_runtime_plan(self, runtime_plan: Any) -> None:
        if getattr(runtime_plan, "subagent_id", None) != "hermes_engineer_core":
            raise AIAgentExecutorBridgeError("unsupported_subagent")
        if getattr(runtime_plan, "actual_runtime_status", None) != "ready_to_construct":
            raise AIAgentExecutorBridgeError("runtime_plan_not_ready")
        if not (self.workspace_root / ".git").exists():
            raise AIAgentExecutorBridgeError("workspace_not_git_repo")

    def _load_prompt_text(self, runtime_plan: Any) -> str | None:
        prompt = getattr(runtime_plan, "prompt", None)
        prompt_path = getattr(prompt, "path", None)
        if not prompt_path:
            return None
        if self.repo_root is None:
            return None
        path = (self.repo_root / str(prompt_path)).resolve()
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _tool_definitions(self) -> list[dict[str, Any]]:
        return [
            self._tool_definition("read_file", "Read a file inside the controlled workspace.", {"path": {"type": "string"}}, ["path"]),
            self._tool_definition("search_files", "Search files inside the controlled workspace.", {"pattern": {"type": "string"}}, ["pattern"]),
            self._tool_definition("patch", "Replace file content inside the controlled workspace.", {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}, "content": {"type": "string"}}, ["path"]),
            self._tool_definition("write_file", "Write a file inside the controlled workspace.", {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
            self._tool_definition("git_status", "Show git status for the controlled workspace.", {}, []),
            self._tool_definition("git_diff", "Show git diff for the controlled workspace.", {}, []),
            self._tool_definition("pytest", "Run an allowed pytest command inside the controlled workspace.", {"command": {"type": "string"}}, ["command"]),
        ]

    def _tool_definition(self, name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }

    def _resolve_workspace_path(self, path_value: str, *, allow_missing: bool) -> Path:
        if not path_value:
            raise AIAgentExecutorBridgeError("missing_path")
        raw = PurePosixPath(path_value)
        if raw.is_absolute():
            raise AIAgentExecutorBridgeError("absolute_path_denied")
        if ".." in raw.parts:
            raise AIAgentExecutorBridgeError("path_outside_workspace")
        destination = self.workspace_root.joinpath(*raw.parts)
        current = self.workspace_root
        for part in raw.parts[:-1]:
            current = current / part
            if current.exists() and current.is_symlink():
                raise AIAgentExecutorBridgeError("symlink_target_denied")
            resolved_current = current.resolve(strict=False)
            try:
                resolved_current.relative_to(self.workspace_root)
            except ValueError as exc:
                raise AIAgentExecutorBridgeError("path_outside_workspace") from exc
        if destination.exists() and destination.is_symlink():
            raise AIAgentExecutorBridgeError("symlink_target_denied")
        resolved_destination = destination.resolve(strict=False)
        try:
            resolved_destination.relative_to(self.workspace_root)
        except ValueError as exc:
            raise AIAgentExecutorBridgeError("path_outside_workspace") from exc
        if not allow_missing and not destination.exists():
            raise AIAgentExecutorBridgeError("path_missing")
        return destination

    def _relative_path(self, path: Path) -> str:
        return path.resolve(strict=False).relative_to(self.workspace_root).as_posix()

    def _redacted_arguments(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        redacted: dict[str, Any] = {}
        for key, value in arguments.items():
            if key == "content":
                redacted[key] = "<redacted_content>"
            else:
                redacted[key] = value
        return redacted

    @contextmanager
    def patched_tool_dispatch(self):
        # This mutates module-level dispatch globals. That is acceptable for the
        # current controlled/manual single-threaded execution path, but
        # concurrent bridge invocations would require explicit synchronization
        # or a per-invocation dispatch context.
        original_run_agent = run_agent.handle_function_call
        original_model_tools = model_tools.handle_function_call

        def _dispatch(function_name: str, function_args: Mapping[str, Any] | None = None, *_args: Any, **_kwargs: Any) -> str:
            return self.execute_tool(function_name, function_args)

        run_agent.handle_function_call = _dispatch
        model_tools.handle_function_call = _dispatch
        try:
            yield
        finally:
            run_agent.handle_function_call = original_run_agent
            model_tools.handle_function_call = original_model_tools
