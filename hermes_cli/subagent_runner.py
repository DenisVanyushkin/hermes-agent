"""Import-light subagent execution skeleton for pipeline runtime plans."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Mapping, Protocol

from hermes_cli.runtime_factory import RuntimeBuildResult


_READY_RUNTIME_STATUS = "ready_to_construct"
_PROMPT_METADATA_KEYS = ("path", "artifact_id", "sha256")
_SENSITIVE_KEY_PARTS = ("secret", "token", "password", "credential", "api_key", "client", "prompt", "env")
_VALID_EXECUTION_STATUSES = {"completed", "failed", "rejected"}


class SubagentRunnerError(Exception):
    """Controlled subagent runner failure."""


class SubagentExecutorProtocol(Protocol):
    def __call__(
        self,
        request: "SubagentInvocationRequest",
        runtime_plan: RuntimeBuildResult,
    ) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class SubagentInvocationRequest:
    subagent_id: str
    pipeline_session_id: str
    invocation_id: str
    input_messages: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubagentExecutionRecord:
    pipeline_session_id: str
    invocation_id: str
    subagent_id: str
    constructor_provider: str | None
    constructor_model: str | None
    selected_model_class: str | None
    prompt_artifact: dict[str, Any]
    tool_permission_plan_summary: dict[str, Any]
    execution_status: str
    completion_reason: str | None
    elapsed_ms: float
    safe_output_summary: str
    tool_intents_count: int
    requires_tool_gate: bool

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_session_id": self.pipeline_session_id,
            "invocation_id": self.invocation_id,
            "subagent_id": self.subagent_id,
            "constructor_provider": self.constructor_provider,
            "constructor_model": self.constructor_model,
            "selected_model_class": self.selected_model_class,
            "prompt_artifact": dict(self.prompt_artifact),
            "tool_permission_plan_summary": dict(self.tool_permission_plan_summary),
            "execution_status": self.execution_status,
            "completion_reason": self.completion_reason,
            "elapsed_ms": self.elapsed_ms,
            "safe_output_summary": self.safe_output_summary,
            "tool_intents_count": self.tool_intents_count,
            "requires_tool_gate": self.requires_tool_gate,
        }

    def as_log_payload(self) -> dict[str, Any]:
        return self.to_safe_dict()


@dataclass(frozen=True)
class SubagentInvocationResult:
    ok: bool
    execution_status: str
    completion_reason: str | None
    record: SubagentExecutionRecord
    output_text: str | None = None
    stop_reason: str | None = None
    token_usage: dict[str, Any] | None = None
    tool_intents: list[dict[str, Any]] = field(default_factory=list)
    tool_intents_count: int = 0
    requires_tool_gate: bool = False
    error_code: str | None = None
    error_message: str | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "execution_status": self.execution_status,
            "completion_reason": self.completion_reason,
            "record": self.record.to_safe_dict(),
            "stop_reason": self.stop_reason,
            "token_usage": _safe_mapping(self.token_usage),
            "tool_intents": [_safe_tool_intent_summary(intent) for intent in self.tool_intents],
            "tool_intents_count": self.tool_intents_count,
            "requires_tool_gate": self.requires_tool_gate,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "raw_metadata": _sanitize_metadata(self.raw_metadata),
        }

    def as_log_payload(self) -> dict[str, Any]:
        return self.to_safe_dict()


class SubagentRunner:
    def __init__(self, executor: SubagentExecutorProtocol | None):
        self.executor = executor

    def run(
        self,
        runtime_plan: RuntimeBuildResult,
        request: SubagentInvocationRequest,
    ) -> SubagentInvocationResult:
        start = time.perf_counter()

        validation_failure = self._validate(runtime_plan, request, start)
        if validation_failure is not None:
            return validation_failure

        assert self.executor is not None
        try:
            executor_result = self.executor(request, runtime_plan)
        except Exception as exc:
            return self._failure_result(
                runtime_plan=runtime_plan,
                request=request,
                execution_status="failed",
                completion_reason="executor_exception",
                error_code="executor_exception",
                error_message=str(exc),
                elapsed_ms=_elapsed_ms(start),
            )

        if not isinstance(executor_result, Mapping):
            return self._failure_result(
                runtime_plan=runtime_plan,
                request=request,
                execution_status="failed",
                completion_reason="malformed_executor_result",
                error_code="malformed_executor_result",
                error_message="Executor result must be a mapping",
                elapsed_ms=_elapsed_ms(start),
            )

        schema_error = _validate_executor_result_schema(executor_result)
        if schema_error is not None:
            return self._failure_result(
                runtime_plan=runtime_plan,
                request=request,
                execution_status="failed",
                completion_reason="malformed_executor_result",
                error_code="malformed_executor_result",
                error_message=schema_error,
                elapsed_ms=_elapsed_ms(start),
            )

        output_text = executor_result.get("output_text")
        completion_reason = executor_result.get("completion_reason") or "completed"
        stop_reason = _string_or_none(executor_result.get("stop_reason"))
        token_usage = _safe_mapping(executor_result.get("token_usage"))
        tool_intents = _normalize_tool_intents(executor_result.get("tool_intents"))
        raw_metadata = _safe_mapping(executor_result.get("raw_metadata"))
        execution_status = executor_result.get("execution_status") or "completed"
        requires_tool_gate = bool(tool_intents)

        record = self._build_record(
            runtime_plan=runtime_plan,
            request=request,
            execution_status=execution_status,
            completion_reason=completion_reason,
            elapsed_ms=_elapsed_ms(start),
            output_text=output_text,
            tool_intents=tool_intents,
            requires_tool_gate=requires_tool_gate,
        )
        return SubagentInvocationResult(
            ok=True,
            execution_status=execution_status,
            completion_reason=completion_reason,
            record=record,
            output_text=output_text,
            stop_reason=stop_reason,
            token_usage=token_usage,
            tool_intents=tool_intents,
            tool_intents_count=len(tool_intents),
            requires_tool_gate=requires_tool_gate,
            raw_metadata=raw_metadata,
        )

    def _validate(
        self,
        runtime_plan: RuntimeBuildResult,
        request: SubagentInvocationRequest,
        start: float,
    ) -> SubagentInvocationResult | None:
        if runtime_plan.subagent_id != request.subagent_id:
            return self._failure_result(
                runtime_plan=runtime_plan,
                request=request,
                execution_status="failed",
                completion_reason="subagent_id_mismatch",
                error_code="subagent_id_mismatch",
                error_message="Invocation request subagent_id does not match runtime plan",
                elapsed_ms=_elapsed_ms(start),
            )
        if self.executor is None:
            return self._failure_result(
                runtime_plan=runtime_plan,
                request=request,
                execution_status="failed",
                completion_reason="missing_executor",
                error_code="missing_executor",
                error_message="Subagent runner requires an injected executor",
                elapsed_ms=_elapsed_ms(start),
            )
        if runtime_plan.actual_runtime_status != _READY_RUNTIME_STATUS:
            return self._failure_result(
                runtime_plan=runtime_plan,
                request=request,
                execution_status="rejected",
                completion_reason="runtime_plan_not_ready",
                error_code="runtime_plan_not_ready",
                error_message=f"Runtime plan status {runtime_plan.actual_runtime_status!r} is not executable",
                elapsed_ms=_elapsed_ms(start),
            )
        if not runtime_plan.constructor_provider or not runtime_plan.constructor_model:
            return self._failure_result(
                runtime_plan=runtime_plan,
                request=request,
                execution_status="rejected",
                completion_reason="runtime_plan_incomplete",
                error_code="runtime_plan_incomplete",
                error_message="Runtime plan is missing constructor provider/model",
                elapsed_ms=_elapsed_ms(start),
            )
        return None

    def _failure_result(
        self,
        *,
        runtime_plan: RuntimeBuildResult,
        request: SubagentInvocationRequest,
        execution_status: str,
        completion_reason: str,
        error_code: str,
        error_message: str,
        elapsed_ms: float,
    ) -> SubagentInvocationResult:
        record = self._build_record(
            runtime_plan=runtime_plan,
            request=request,
            execution_status=execution_status,
            completion_reason=completion_reason,
            elapsed_ms=elapsed_ms,
            output_text=None,
            tool_intents=[],
            requires_tool_gate=False,
        )
        return SubagentInvocationResult(
            ok=False,
            execution_status=execution_status,
            completion_reason=completion_reason,
            record=record,
            error_code=error_code,
            error_message=error_message,
        )

    def _build_record(
        self,
        *,
        runtime_plan: RuntimeBuildResult,
        request: SubagentInvocationRequest,
        execution_status: str,
        completion_reason: str | None,
        elapsed_ms: float,
        output_text: str | None,
        tool_intents: list[dict[str, Any]],
        requires_tool_gate: bool,
    ) -> SubagentExecutionRecord:
        prompt = runtime_plan.prompt
        prompt_artifact = {
            key: getattr(prompt, key)
            for key in _PROMPT_METADATA_KEYS
            if prompt is not None and getattr(prompt, key) is not None
        }
        return SubagentExecutionRecord(
            pipeline_session_id=request.pipeline_session_id,
            invocation_id=request.invocation_id,
            subagent_id=request.subagent_id,
            constructor_provider=runtime_plan.constructor_provider,
            constructor_model=runtime_plan.constructor_model,
            selected_model_class=runtime_plan.selection.selected_model_class if runtime_plan.selection else None,
            prompt_artifact=prompt_artifact,
            tool_permission_plan_summary=_summarize_tool_permission_plan(runtime_plan),
            execution_status=execution_status,
            completion_reason=completion_reason,
            elapsed_ms=elapsed_ms,
            safe_output_summary=_safe_output_summary(output_text),
            tool_intents_count=len(tool_intents),
            requires_tool_gate=requires_tool_gate,
        )


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 3)


def _string_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _safe_mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): value[key] for key in value}


def _normalize_tool_intents(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    intents: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            intents.append({str(key): item[key] for key in item})
    return intents


def _validate_executor_result_schema(result: Mapping[str, Any]) -> str | None:
    output_text = result.get("output_text")
    if output_text is not None and not isinstance(output_text, str):
        return "Executor field 'output_text' must be a string when present"

    execution_status = result.get("execution_status")
    if execution_status is not None:
        normalized_status = _string_or_none(execution_status)
        if normalized_status is None or normalized_status not in _VALID_EXECUTION_STATUSES:
            return "Executor field 'execution_status' must be a known string status"

    completion_reason = result.get("completion_reason")
    if completion_reason is not None and _string_or_none(completion_reason) is None:
        return "Executor field 'completion_reason' must be a string when present"

    tool_intents = result.get("tool_intents")
    if tool_intents is None:
        return None
    if not isinstance(tool_intents, list):
        return "Executor field 'tool_intents' must be a list when present"
    for intent in tool_intents:
        if not isinstance(intent, Mapping):
            return "Each tool intent must be a mapping"
        name = intent.get("name")
        if name is not None and _string_or_none(name) is None:
            return "Tool intent field 'name' must be a string when present"
        arguments = intent.get("arguments")
        if arguments is not None and not isinstance(arguments, Mapping):
            return "Tool intent field 'arguments' must be a mapping when present"
    return None


def _summarize_tool_permission_plan(runtime_plan: RuntimeBuildResult) -> dict[str, Any]:
    plan = runtime_plan.tool_permission_plan
    if plan is None:
        return {}
    return {
        "read": len(plan.read),
        "write": len(plan.write),
        "execute": len(plan.execute),
        "gated": len(plan.gated),
        "forbidden": len(plan.forbidden),
        "unknown_permissions": len(plan.unknown_permissions),
    }


def _safe_output_summary(output_text: str | None) -> str:
    text = (output_text or "").strip()
    if not text:
        return ""
    if len(text) <= 240:
        return text
    return f"{text[:237]}..."


def _safe_tool_intent_summary(intent: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(intent, dict):
        return {}
    arguments = intent.get("arguments")
    argument_keys: list[str] = []
    argument_count = 0
    if isinstance(arguments, Mapping):
        argument_keys = sorted(str(key) for key in arguments)
        argument_count = len(arguments)
    summary = {
        "name": _string_or_none(intent.get("name")),
        "intent_type": _string_or_none(intent.get("intent_type")),
        "requires_gate": True,
        "argument_keys": argument_keys,
        "argument_count": argument_count,
        "redacted_arguments": bool(argument_keys),
    }
    return {key: value for key, value in summary.items() if value is not None}


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        key_text = str(key)
        lowered = key_text.lower()
        if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
            continue
        safe_value = _sanitize_value(value)
        if safe_value is not None:
            sanitized[key_text] = safe_value
    return sanitized


def _sanitize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:240]
    if isinstance(value, Mapping):
        nested: dict[str, Any] = {}
        for key, inner in value.items():
            key_text = str(key)
            if any(part in key_text.lower() for part in _SENSITIVE_KEY_PARTS):
                continue
            safe_inner = _sanitize_value(inner)
            if safe_inner is not None:
                nested[key_text] = safe_inner
        return nested
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value[:20]]
    return None
