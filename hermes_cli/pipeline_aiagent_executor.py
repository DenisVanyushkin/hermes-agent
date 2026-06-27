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
from hermes_cli.runtime_factory import RuntimeFactory
from agent.chat_completion_helpers import _normalize_base_url_family
from hermes_cli.pipeline_test_runner import run_controlled_tests


_ENGINEER_ALLOWED_TOOL_NAMES = (
    "read_file",
    "find_files",
    "search_files",
    "patch",
    "write_file",
    "git_status",
    "git_diff",
    "pytest",
)
_REVIEWER_ALLOWED_TOOL_NAMES = (
    "read_file",
    "search_files",
    "git_status",
    "git_diff",
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
        self._validate_request(request, runtime_plan)
        self._tool_calls = []
        agent = self._build_agent(runtime_plan)
        result = self.conversation_runner(self, agent, request, runtime_plan)
        normalized = self._normalize_result(result)
        tool_intents = [
            {"name": item["tool_name"], "arguments": dict(item.get("arguments") or {})}
            for item in self._tool_calls
        ]
        raw_metadata = dict(normalized.get("raw_metadata") or {})
        fallback_metadata = self._fallback_metadata(normalized, runtime_plan)
        raw_metadata.update(fallback_metadata)
        raw_metadata.setdefault("tool_calls", list(self._tool_calls))
        raw_metadata.setdefault(
            "bridge_metadata",
            {
                "workspace_root": self.workspace_root.name,
                "subagent_id": runtime_plan.subagent_id,
                "role_id": self._bridge_role_id(runtime_plan),
            },
        )
        return {
            "output_text": normalized.get("output_text"),
            "completion_reason": normalized.get("completion_reason") or "completed",
            "execution_status": normalized.get("execution_status") or "completed",
            "token_usage": dict(normalized.get("token_usage") or {}),
            "tool_intents": tool_intents,
            "effective_provider": fallback_metadata["effective_provider"],
            "effective_model": fallback_metadata["effective_model"],
            "fallback_attempted": fallback_metadata["fallback_attempted"],
            "fallback_activated": fallback_metadata["fallback_activated"],
            "fallback_provider": fallback_metadata["fallback_provider"],
            "fallback_model": fallback_metadata["fallback_model"],
            "fallback_base_url": fallback_metadata["fallback_base_url"],
            "fallback_api_mode": fallback_metadata["fallback_api_mode"],
            "fallback_error": fallback_metadata["fallback_error"],
            "fallback_result": fallback_metadata["fallback_result"],
            "providers_used_effective": list(fallback_metadata["providers_used_effective"]),
            "raw_metadata": raw_metadata,
        }

    def execute_tool(self, tool_name: str, arguments: Mapping[str, Any] | None = None) -> str:
        args = dict(arguments or {})
        if tool_name not in self._allowed_tool_names():
            raise AIAgentExecutorBridgeError(f"tool_not_allowed:{tool_name}")

        result: dict[str, Any] | None = None
        try:
            if tool_name == "read_file":
                path = self._resolve_workspace_path(str(args.get("path") or ""), allow_missing=False)
                result = {"path": self._relative_path(path), "content": path.read_text(encoding="utf-8")}
            elif tool_name == "find_files":
                result = self._find_files(args)
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
                pytest_request: Any
                if isinstance(args.get("command"), str) and str(args.get("command") or "").strip():
                    pytest_request = str(args.get("command") or "").strip()
                else:
                    pytest_request = args
                summary = run_controlled_tests(
                    allow_test_commands=True,
                    test_workspace=self.workspace_root,
                    tests_payload=[pytest_request],
                    step_kind="engineer",
                    step_subagent_id="hermes_engineer_core",
                    subprocess_runner=self.subprocess_runner,
                )
                result = summary.to_safe_dict()
                if summary.blocked_reason is not None:
                    raise AIAgentExecutorBridgeError(summary.blocked_reason)
            else:
                raise AIAgentExecutorBridgeError(f"tool_not_implemented:{tool_name}")
        except AIAgentExecutorBridgeError as exc:
            self._record_tool_call(
                tool_name,
                args,
                status="failed",
                result=result if tool_name == "pytest" else None,
                error={
                    "kind": str(exc),
                    "message": self._tool_error_message(tool_name, args, str(exc)),
                },
            )
            raise

        self._record_tool_call(tool_name, args, status="succeeded", result=result if tool_name == "pytest" else None)
        return json.dumps(result, ensure_ascii=False)

    def _build_agent(self, runtime_plan: Any) -> Any:
        kwargs = runtime_plan.to_aiagent_kwargs()
        expected_fallback = kwargs.get("fallback_model")
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
        self._validate_agent_fallback_policy(agent, runtime_plan, expected_fallback)
        agent.tools = self._tool_definitions()
        agent.valid_tool_names = set(self._allowed_tool_names())
        agent.enabled_toolsets = []
        agent.disabled_toolsets = list(kwargs["disabled_toolsets"])
        agent._skip_role_model_selection = True
        agent._constructor_provider = str(getattr(runtime_plan, 'constructor_provider', None) or '')
        agent._constructor_model = str(getattr(runtime_plan, 'constructor_model', None) or '')
        agent._constructor_api_mode = str(getattr(runtime_plan, 'constructor_api_mode', None) or '')
        agent._constructor_base_url = str(getattr(runtime_plan, 'constructor_base_url', None) or '')
        # Build the allowed identity set: primary + all configured fallbacks.
        # Each entry is a 4-tuple (provider, model, api_mode, base_url_family).
        # build_api_kwargs validates outbound identity against this set before any call.
        _allowed_ids: list[dict] = []
        if agent._constructor_model and agent._constructor_api_mode and agent._constructor_provider:
            _allowed_ids.append({
                "provider": agent._constructor_provider,
                "model": agent._constructor_model,
                "api_mode": agent._constructor_api_mode,
                "base_url_family": _normalize_base_url_family(
                    agent._constructor_provider,
                    agent._constructor_api_mode,
                    agent._constructor_base_url or None,
                ),
            })
        _fp = getattr(runtime_plan, 'fallback_policy', None)
        if _fp and getattr(_fp, 'provider', None) and getattr(_fp, 'model', None):
            _fb_api_mode = RuntimeFactory._constructor_api_mode(_fp.provider) or ''
            _allowed_ids.append({
                "provider": _fp.provider,
                "model": _fp.model,
                "api_mode": _fb_api_mode,
                "base_url_family": _normalize_base_url_family(
                    _fp.provider,
                    _fb_api_mode,
                    None,  # fallback has no explicit base_url in spec; derive from provider
                ),
            })
        agent._controlled_allowed_request_identities = _allowed_ids
        return agent

    def _validate_agent_fallback_policy(
        self,
        agent: Any,
        runtime_plan: Any,
        expected_fallback: Mapping[str, Any] | None,
    ) -> None:
        if getattr(runtime_plan, "subagent_id", "") != "hermes_engineer_core":
            return
        if not isinstance(expected_fallback, Mapping):
            raise AIAgentExecutorBridgeError("missing_engineer_fallback_policy")
        provider = str(expected_fallback.get("provider") or "").strip()
        model = str(expected_fallback.get("model") or "").strip()
        if not provider or not model:
            raise AIAgentExecutorBridgeError("missing_engineer_fallback_policy")

        fallback_chain = list(getattr(agent, "_fallback_chain", []) or [])
        expected_chain = [{"provider": provider, "model": model}]
        if fallback_chain != expected_chain:
            raise AIAgentExecutorBridgeError(
                "invalid_engineer_fallback_chain:"
                f"expected={expected_chain!r}:actual={fallback_chain!r}"
            )

    def _default_conversation_runner(self, _bridge: "AIAgentSubagentExecutorBridge", agent: Any, request: Any, _runtime_plan: Any) -> Mapping[str, Any]:
        user_message = self._build_user_message(request)
        with self.patched_tool_dispatch():
            return agent.run_conversation(user_message)

    def _normalize_result(self, result: Any) -> dict[str, Any]:
        if isinstance(result, Mapping):
            normalized = dict(result)
            raw_metadata = dict(normalized.get("raw_metadata") or {})
            self._preserve_terminal_aliases(normalized, raw_metadata)
            structured_output, source, parse_error = self._extract_structured_output_candidate(result, raw_metadata)
            normalized["execution_status"] = normalized.get("execution_status") or "completed"
            normalized["completion_reason"] = normalized.get("completion_reason") or self._detect_completion_reason(result, raw_metadata) or "completed"
            normalized["token_usage"] = normalized.get("token_usage") or {}
            if structured_output is not None:
                raw_metadata["structured_output"] = structured_output
            else:
                raw_metadata["structured_output_missing"] = True
                missing_metadata = self._missing_structured_output_metadata(
                    result=result,
                    raw_metadata=raw_metadata,
                    output_text=normalized.get("output_text"),
                    parse_error=parse_error,
                )
                raw_metadata.update(missing_metadata)
                output_text = normalized.get("output_text")
                if (
                    self._supported_subagent_id() == "hermes_engineer_core"
                    and raw_metadata.get("structured_output") is None
                    and self._should_synthesize_blocked_envelope(
                        completion_reason=normalized["completion_reason"],
                        output_text=output_text,
                        missing_metadata=missing_metadata,
                    )
                ):
                    raw_metadata["structured_output"] = self._synthesized_blocked_structured_output(
                        output_text=self._synthesis_summary_text(output_text, missing_metadata),
                        raw_metadata=raw_metadata,
                    )
                    raw_metadata["structured_output_source"] = str(
                        missing_metadata.get("structured_output_source") or "synthesized_plain_text_blocked"
                    )
                    raw_metadata["structured_output_missing_reason"] = str(
                        missing_metadata.get("structured_output_missing_reason") or "engineer_text_response_without_structured_output"
                    )
                    raw_metadata["structured_output_missing_blocked_reason"] = str(
                        missing_metadata.get("structured_output_missing_blocked_reason") or "invalid_engineer_output"
                    )
                    raw_metadata["reason"] = str(
                        missing_metadata.get("reason") or "text_response_without_structured_output"
                    )
                    raw_metadata["repair_attempted"] = False
                    raw_metadata["repair_succeeded"] = False
                    raw_metadata["synthesized_envelope"] = True
                    if isinstance(output_text, str):
                        raw_metadata["original_output_text_length"] = len(output_text)
                        raw_metadata["original_output_text_excerpt"] = output_text.strip()[:500]
            raw_metadata.setdefault("structured_output_source", source)
            if parse_error is not None:
                raw_metadata["structured_output_parse_error"] = parse_error
            normalized["raw_metadata"] = raw_metadata
            return normalized
        if isinstance(result, str):
            structured_output, source, parse_error = self._structured_output_from_output_text(result)
            raw_metadata: dict[str, Any] = {"structured_output_source": source}
            if structured_output is not None:
                raw_metadata["structured_output"] = structured_output
            else:
                raw_metadata["structured_output_missing"] = True
            parse_error = parse_error or self._legacy_string_parse_error(result)
            if parse_error is not None:
                raw_metadata["structured_output_parse_error"] = parse_error
            return {
                "output_text": result,
                "execution_status": "completed",
                "completion_reason": "completed",
                "raw_metadata": raw_metadata,
            }
        raise AIAgentExecutorBridgeError("invalid_agent_result")

    def _preserve_terminal_aliases(self, normalized: dict[str, Any], raw_metadata: dict[str, Any]) -> None:
        completion_reason = self._first_text_value(normalized, ("completion_reason", "turn_exit_reason"))
        if completion_reason is not None and "completion_reason" not in normalized:
            normalized["completion_reason"] = completion_reason
        output_text = self._detect_output_text(normalized)
        if output_text is not None and "output_text" not in normalized:
            normalized["output_text"] = output_text
        for key in ("turn_exit_reason", "final_response", "final_response_text", "response_text", "text"):
            value = normalized.get(key)
            if value is not None:
                raw_metadata.setdefault(key, value)

    def _extract_structured_output_candidate(
        self,
        result: Mapping[str, Any],
        raw_metadata: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, str, str | None]:
        candidate = raw_metadata.get("structured_output")
        if isinstance(candidate, Mapping):
            return dict(candidate), "raw_metadata.structured_output", None

        candidate = result.get("structured_output")
        if isinstance(candidate, Mapping):
            return dict(candidate), "structured_output", None

        final_response = result.get("final_response")
        if isinstance(final_response, Mapping):
            candidate = final_response.get("structured_output")
            if isinstance(candidate, Mapping):
                return dict(candidate), "final_response.structured_output", None
            if self._looks_like_structured_output_mapping(final_response):
                return dict(final_response), "final_response", None

        output_text = self._detect_output_text(result)
        if isinstance(output_text, str):
            return self._structured_output_from_output_text(output_text)

        return None, "none", None

    def _structured_output_from_output_text(self, output_text: str) -> tuple[dict[str, Any] | None, str, str | None]:
        text = output_text.strip()
        if not text:
            return None, "none", None
        if not text.startswith(("{", "[")):
            return None, "none", None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            return None, "none", f"json_decode_error:{exc.msg}"
        if isinstance(parsed, Mapping):
            return dict(parsed), "output_text_json", None
        return None, "none", f"json_not_mapping:{type(parsed).__name__}"

    def _legacy_string_parse_error(self, output_text: str) -> str | None:
        text = output_text.strip()
        if not text:
            return None
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return f"json_decode_error:{exc.msg}"
        return None

    def _looks_like_structured_output_mapping(self, value: Mapping[str, Any]) -> bool:
        return all(field in value for field in ("schema_version", "subagent_id", "role", "status", "summary"))

    def _detect_completion_reason(self, result: Mapping[str, Any], raw_metadata: Mapping[str, Any]) -> str | None:
        for key in ("completion_reason", "turn_exit_reason", "stop_reason", "end_reason", "reason"):
            for payload in (result, raw_metadata):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _detect_output_text(self, payload: Mapping[str, Any]) -> str | None:
        return self._first_text_value(payload, ("output_text", "final_response", "final_response_text", "response_text", "text"))

    def _first_text_value(self, payload: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str):
                return value
        return None

    def _missing_structured_output_metadata(
        self,
        *,
        result: Mapping[str, Any],
        raw_metadata: Mapping[str, Any],
        output_text: Any,
        parse_error: str | None,
    ) -> dict[str, Any]:
        if parse_error is not None:
            diagnostic_text = output_text if isinstance(output_text, str) else ""
            return {
                "structured_output_missing_reason": "malformed_structured_output",
                "structured_output_missing_blocked_reason": "malformed_structured_output",
                "structured_output_source": "synthesized_parse_failure_blocked",
                "reason": "malformed_structured_output",
                "diagnostic_output_text": diagnostic_text,
            }
        if not self._is_max_iterations_result(result, raw_metadata):
            provider_failure = self._provider_failure_metadata(raw_metadata)
            if provider_failure is not None:
                return provider_failure
            empty_output = not isinstance(output_text, str) or not output_text.strip()
            if empty_output:
                return {
                    "structured_output_missing_reason": "engineer_empty_output_without_structured_output",
                    "structured_output_missing_blocked_reason": "empty_output_without_structured_output",
                    "structured_output_source": "synthesized_empty_output_blocked",
                    "reason": "empty_output_without_structured_output",
                }
            return {}
        return {
            "structured_output_missing_reason": "engineer_max_iterations_without_structured_output",
            "structured_output_missing_blocked_reason": "max_iterations_plain_text_output",
            "structured_output_source": "synthesized_max_iterations_blocked",
            "reason": "max_iterations_without_structured_output",
            "diagnostic_output_text": output_text,
        }

    def _provider_failure_metadata(self, raw_metadata: Mapping[str, Any]) -> dict[str, Any] | None:
        if not raw_metadata.get("real_provider_bridge_invoked"):
            return None
        provider_error = raw_metadata.get("provider_error")
        http_status = raw_metadata.get("http_status")
        fallback_status = str(raw_metadata.get("fallback_status") or "").strip().lower()
        fallback_diagnostic = str(raw_metadata.get("fallback_diagnostic") or "").strip()
        if fallback_status in {"exhausted", "unavailable"} or fallback_diagnostic:
            return {
                "structured_output_missing_reason": "engineer_fallback_exhausted_without_structured_output",
                "structured_output_missing_blocked_reason": "fallback_exhausted_without_structured_output",
                "structured_output_source": "synthesized_fallback_exhausted_blocked",
                "reason": "fallback_exhausted_without_structured_output",
                "diagnostic_output_text": fallback_diagnostic or f"Fallback {fallback_status or 'unavailable'} without structured output.",
            }
        if provider_error is not None or http_status is not None:
            diagnostic_parts = []
            if provider_error is not None:
                diagnostic_parts.append(str(provider_error))
            if http_status is not None and str(http_status) not in " ".join(diagnostic_parts):
                diagnostic_parts.append(f"HTTP {http_status}")
            diagnostic_text = " ".join(part for part in diagnostic_parts if part).strip()
            return {
                "structured_output_missing_reason": "engineer_provider_error_without_structured_output",
                "structured_output_missing_blocked_reason": "provider_error_without_structured_output",
                "structured_output_source": "synthesized_provider_error_blocked",
                "reason": "provider_error_without_structured_output",
                "diagnostic_output_text": diagnostic_text or "Provider error without structured output.",
            }
        return None

    def _fallback_metadata(self, normalized: Mapping[str, Any], runtime_plan: Any) -> dict[str, Any]:
        raw_metadata = dict(normalized.get("raw_metadata") or {})
        initial_provider = self._metadata_text({"value": getattr(runtime_plan, "constructor_provider", None)}, "value")
        initial_model = self._metadata_text({"value": getattr(runtime_plan, "constructor_model", None)}, "value")
        effective_provider = self._metadata_text(raw_metadata, "effective_provider") or self._metadata_text(normalized, "provider") or initial_provider
        effective_model = self._metadata_text(raw_metadata, "effective_model") or self._metadata_text(normalized, "model") or initial_model
        fallback_result = self._metadata_text(raw_metadata, "fallback_result") or self._metadata_text(raw_metadata, "fallback_status")
        fallback_error = (
            self._metadata_text(raw_metadata, "fallback_error")
            or self._metadata_text(raw_metadata, "fallback_diagnostic")
            or self._metadata_text(raw_metadata, "provider_error")
        )
        fallback_attempted = bool(raw_metadata.get("fallback_attempted"))
        fallback_activated = bool(raw_metadata.get("fallback_activated"))
        if not fallback_attempted:
            fallback_attempted = fallback_activated or bool(fallback_result) or bool(fallback_error)
            if not fallback_attempted:
                fallback_attempted = effective_provider != initial_provider or effective_model != initial_model
        if not fallback_activated:
            fallback_activated = effective_provider != initial_provider or effective_model != initial_model
        fallback_provider = self._metadata_text(raw_metadata, "fallback_provider")
        fallback_model = self._metadata_text(raw_metadata, "fallback_model")
        fallback_base_url = self._metadata_text(raw_metadata, "fallback_base_url")
        fallback_api_mode = self._metadata_text(raw_metadata, "fallback_api_mode")
        if fallback_activated:
            fallback_provider = fallback_provider or effective_provider
            fallback_model = fallback_model or effective_model
            fallback_base_url = fallback_base_url or self._metadata_text(normalized, "base_url")
            fallback_api_mode = fallback_api_mode or self._metadata_text(normalized, "api_mode")
            fallback_result = fallback_result or "activated"
        elif fallback_attempted:
            fallback_result = fallback_result or "attempted"
        providers_used_effective = raw_metadata.get("providers_used_effective")
        providers: list[str] = []
        for candidate in (initial_provider, effective_provider):
            if candidate and candidate not in providers:
                providers.append(candidate)
        if isinstance(providers_used_effective, list):
            for candidate in providers_used_effective:
                if not isinstance(candidate, str):
                    continue
                text = candidate.strip()
                if text and text not in providers:
                    providers.append(text)
        return {
            "initial_provider": initial_provider,
            "initial_model": initial_model,
            "effective_provider": effective_provider,
            "effective_model": effective_model,
            "fallback_attempted": fallback_attempted,
            "fallback_activated": fallback_activated,
            "fallback_provider": fallback_provider,
            "fallback_model": fallback_model,
            "fallback_base_url": fallback_base_url,
            "fallback_api_mode": fallback_api_mode,
            "fallback_error": fallback_error,
            "fallback_result": fallback_result,
            "providers_used_effective": providers,
        }

    def _metadata_text(self, payload: Mapping[str, Any], key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _should_synthesize_blocked_envelope(
        self,
        *,
        completion_reason: str,
        output_text: Any,
        missing_metadata: Mapping[str, Any],
    ) -> bool:
        if missing_metadata:
            return True
        return isinstance(output_text, str) and output_text.strip() and completion_reason.startswith("text_response")

    def _synthesis_summary_text(self, output_text: Any, missing_metadata: Mapping[str, Any]) -> str:
        if isinstance(output_text, str) and output_text.strip():
            return output_text
        diagnostic_text = missing_metadata.get("diagnostic_output_text")
        if isinstance(diagnostic_text, str) and diagnostic_text.strip():
            return diagnostic_text
        blocked_reason = missing_metadata.get("structured_output_missing_blocked_reason")
        if blocked_reason == "empty_output_without_structured_output":
            return "Engineer bridge returned empty output instead of the required StructuredOutputEnvelope."
        return "Engineer bridge terminated without returning the required StructuredOutputEnvelope."

    def _synthesized_blocked_structured_output(
        self,
        *,
        output_text: str,
        raw_metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized_text = output_text.strip()
        summary = self._plain_text_summary(normalized_text)
        return {
            "schema_version": "v1",
            "subagent_id": "hermes_engineer_core",
            "role": "engineer",
            "status": "blocked",
            "summary": summary,
            "findings": [
                {
                    "code": "missing_structured_output",
                    "summary": "Engineer model returned plain text instead of a StructuredOutputEnvelope.",
                    "details": summary,
                }
            ],
            "changes": [],
            "blockers": ["missing_structured_output"],
            "artifacts": [],
            "confidence": 0.0,
            "requires_review": False,
            "next_action": "retry_with_structured_output",
        }

    def _plain_text_summary(self, output_text: str) -> str:
        first_line = next((line.strip() for line in output_text.splitlines() if line.strip()), "")
        if not first_line:
            return "Engineer model returned plain text instead of the required StructuredOutputEnvelope."
        if len(first_line) <= 220:
            return first_line
        return f"{first_line[:217].rstrip()}..."

    def _is_max_iterations_result(self, result: Mapping[str, Any], raw_metadata: Mapping[str, Any]) -> bool:
        for key in ("completion_reason", "turn_exit_reason", "stop_reason", "end_reason", "reason"):
            for payload in (result, raw_metadata):
                value = payload.get(key)
                if isinstance(value, str) and "max_iterations_reached" in value:
                    return True
        return False

    def _validate_runtime_plan(self, runtime_plan: Any) -> None:
        if getattr(runtime_plan, "subagent_id", None) != self._supported_subagent_id():
            raise AIAgentExecutorBridgeError("unsupported_subagent")
        if getattr(runtime_plan, "actual_runtime_status", None) != "ready_to_construct":
            raise AIAgentExecutorBridgeError("runtime_plan_not_ready")
        if not (self.workspace_root / ".git").exists():
            raise AIAgentExecutorBridgeError("workspace_not_git_repo")

    def _validate_request(self, request: Any, runtime_plan: Any) -> None:
        del request, runtime_plan

    def _supported_subagent_id(self) -> str:
        return "hermes_engineer_core"

    def _bridge_role_id(self, runtime_plan: Any) -> str:
        del runtime_plan
        return "engineer"

    def _allowed_tool_names(self) -> tuple[str, ...]:
        return _ENGINEER_ALLOWED_TOOL_NAMES

    def _build_user_message(self, request: Any) -> str:
        if getattr(request, "input_messages", None):
            first = request.input_messages[0]
            if isinstance(first, Mapping):
                return str(first.get("content") or "")
        return ""

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
        definitions = {
            "read_file": self._tool_definition("read_file", "Read a file inside the controlled workspace using a repo-relative path returned by find_files.", {"path": {"type": "string"}}, ["path"]),
            "find_files": self._tool_definition("find_files", "List filenames inside the controlled workspace. Use this for filename or glob discovery. Returns repo-relative paths only.", {"pattern": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 500}}, []),
            "search_files": self._tool_definition("search_files", "Search file contents inside the controlled workspace. Use this for text or regex content search, not filename globbing. Patterns like \"*.py\" are not useful here; use find_files for filenames.", {"pattern": {"type": "string"}}, ["pattern"]),
            "patch": self._tool_definition("patch", "Replace file content inside the controlled workspace.", {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}, "content": {"type": "string"}}, ["path"]),
            "write_file": self._tool_definition("write_file", "Write a file inside the controlled workspace.", {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
            "git_status": self._tool_definition("git_status", "Show git status for the controlled workspace.", {}, []),
            "git_diff": self._tool_definition("git_diff", "Show git diff for the controlled workspace.", {}, []),
            "pytest": self._tool_definition(
                "pytest",
                "Run allowed pytest targets inside the controlled workspace. The runtime chooses the executable.",
                {
                    "targets": {"type": "array", "items": {"type": "string"}},
                    "quiet": {"type": "boolean"},
                    "maxfail": {"type": "integer"},
                },
                ["targets"],
            ),
        }
        return [definitions[name] for name in self._allowed_tool_names()]

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

    def _find_files(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        pattern = str(arguments.get("pattern") or "**/*").strip() or "**/*"
        requested_max = arguments.get("max_results")
        if requested_max is None:
            max_results = 200
        else:
            try:
                max_results = int(requested_max)
            except (TypeError, ValueError) as exc:
                raise AIAgentExecutorBridgeError("invalid_max_results") from exc
            if max_results < 1 or max_results > 500:
                raise AIAgentExecutorBridgeError("invalid_max_results")

        files: list[str] = []
        truncated = False
        for candidate in sorted(self.workspace_root.rglob("*")):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(self.workspace_root)
            if any(part in {".git", "__pycache__", ".pytest_cache", "venv", ".venv", "node_modules"} for part in relative.parts):
                continue
            relative_path = relative.as_posix()
            if not PurePosixPath(relative_path).match(pattern):
                continue
            files.append(relative_path)
            if len(files) >= max_results:
                truncated = True
                break
        return {"status": "ok", "pattern": pattern, "files": files, "truncated": truncated}

    def _record_tool_call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        status: str,
        result: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "tool_name": tool_name,
            "arguments": self._redacted_arguments(arguments),
            "status": status,
        }
        if result is not None:
            payload["result"] = dict(result)
        if error is not None:
            payload["error"] = dict(error)
        self._tool_calls.append(payload)

    def _tool_error_message(self, tool_name: str, arguments: Mapping[str, Any], reason: str) -> str:
        if tool_name == "read_file" and reason == "path_missing":
            return (
                "read_file expects a repo-relative path inside the controlled workspace. "
                "Do not use absolute host paths like /home/hermes/... Use a path returned by find_files."
            )
        if tool_name == "read_file" and reason == "absolute_path_denied":
            return (
                "read_file absolute paths are denied. Convert the path to a repo-relative path under the controlled workspace "
                "and prefer a path returned by find_files."
            )
        if tool_name == "search_files" and reason == "invalid_search_pattern":
            return "search_files expects a non-empty content pattern. Use find_files for filename discovery."
        del arguments
        return reason

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


class AIAgentReviewerExecutorBridge(AIAgentSubagentExecutorBridge):
    def _supported_subagent_id(self) -> str:
        return "hermes_code_reviewer"

    def _bridge_role_id(self, runtime_plan: Any) -> str:
        del runtime_plan
        return "reviewer"

    def _allowed_tool_names(self) -> tuple[str, ...]:
        return _REVIEWER_ALLOWED_TOOL_NAMES

    def _validate_request(self, request: Any, runtime_plan: Any) -> None:
        del runtime_plan
        metadata = getattr(request, "metadata", None)
        if not isinstance(metadata, Mapping):
            raise AIAgentExecutorBridgeError("reviewer_packet_missing")
        reviewer_packet = metadata.get("reviewer_packet")
        if not isinstance(reviewer_packet, Mapping):
            raise AIAgentExecutorBridgeError("reviewer_packet_missing")
        safe_packet = reviewer_packet.get("safe_packet")
        if not isinstance(safe_packet, Mapping):
            raise AIAgentExecutorBridgeError("reviewer_packet_invalid")
        if reviewer_packet.get("present") is not True:
            raise AIAgentExecutorBridgeError("reviewer_packet_invalid")
        if safe_packet.get("packet_status") != "ready_for_review":
            raise AIAgentExecutorBridgeError("reviewer_packet_invalid")

    def _build_user_message(self, request: Any) -> str:
        user_message = super()._build_user_message(request)
        metadata = getattr(request, "metadata", None)
        reviewer_packet = dict((metadata or {}).get("reviewer_packet") or {})
        safe_packet = dict(reviewer_packet.get("safe_packet") or {})
        packet_json = json.dumps(safe_packet, sort_keys=True, ensure_ascii=False)
        return "\n\n".join(
            [
                user_message,
                "Review the engineer candidate using the attached reviewer packet only.",
                "Reviewer packet:",
                packet_json,
            ]
        )
