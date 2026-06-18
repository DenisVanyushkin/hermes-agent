"""Import-light subagent runner contracts for observe-mode pipeline plans."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import time
from typing import Any, Mapping, Protocol

from hermes_cli.runtime_factory import ControlledRuntime, RuntimeBuildResult, RuntimeFactoryPlan


_READY_RUNTIME_STATUS = "ready_to_construct"
_PROMPT_METADATA_KEYS = ("path", "artifact_id", "sha256")
_SENSITIVE_KEY_PARTS = ("secret", "token", "password", "credential", "api_key", "client", "prompt", "env")
_VALID_EXECUTION_STATUSES = {"completed", "failed", "rejected"}
_VALID_ENVELOPE_STATUSES = {"succeeded", "failed", "blocked", "needs_review", "not_invoked", "disagree_with_reviewer"}


class SubagentRunnerError(Exception):
    """Controlled subagent runner failure."""


class SubagentRunnerStatus(str, Enum):
    PLAN_ONLY = "plan_only"
    NOT_INVOKED = "not_invoked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    INVALID_OUTPUT = "invalid_output"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class SubagentArtifactRef:
    artifact_id: str
    kind: str
    path: str | None = None
    sha256: str | None = None
    redacted: bool = True

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "path": self.path,
            "sha256": self.sha256,
            "redacted": self.redacted,
        }


@dataclass(frozen=True)
class SubagentToolCallSummary:
    tool_name: str
    call_count: int = 0
    status: str = "not_invoked"

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "call_count": self.call_count,
            "status": self.status,
        }


@dataclass(frozen=True)
class SubagentUsageSummary:
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    source: str = "unavailable"

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "source": self.source,
        }


@dataclass(frozen=True)
class SubagentCacheSummary:
    cache_hit: bool | None = None
    cache_write: bool | None = None
    cache_key: str | None = None
    read_hit: bool | None = None
    write: bool | None = None
    source: str = "unavailable"

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "cache_hit": self.cache_hit,
            "cache_write": self.cache_write,
            "cache_key": self.cache_key,
            "read_hit": self.read_hit if self.read_hit is not None else self.cache_hit,
            "write": self.write if self.write is not None else self.cache_write,
            "source": self.source,
        }


@dataclass(frozen=True)
class StructuredOutputEnvelope:
    schema_version: str | None
    subagent_id: str | None
    role: str | None
    status: str | None
    summary: str | None
    findings: list[dict[str, Any]] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    artifacts: list[SubagentArtifactRef] = field(default_factory=list)
    confidence: float | None = None
    requires_review: bool | None = None
    next_action: str | None = None
    mutations: list[dict[str, Any]] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    validation_status: str = "not_applicable"
    validation_errors: list[dict[str, str]] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "subagent_id": self.subagent_id,
            "role": self.role,
            "status": self.status,
            "summary": self.summary,
            "findings": list(self.findings),
            "changes": list(self.changes),
            "blockers": list(self.blockers),
            "artifacts": [artifact.to_safe_dict() for artifact in self.artifacts],
            "confidence": self.confidence,
            "requires_review": self.requires_review,
            "next_action": self.next_action,
            "mutations": [dict(item) for item in self.mutations],
            "tests": list(self.tests),
            "validation_status": self.validation_status,
            "validation_errors": list(self.validation_errors),
        }


@dataclass(frozen=True)
class SubagentRunnerRequest:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    step_id: str
    subagent_id: str
    role_id: str
    runtime_factory_plan_id: str
    runtime_factory_status: str
    execution_mode: str
    prompt_input_hash: str | None
    actual_provider: str | None = None
    actual_model: str | None = None
    actual_model_class: str | None = None
    request_metadata: dict[str, Any] = field(default_factory=dict)
    status: str = SubagentRunnerStatus.PLAN_ONLY.value

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_session_id": self.pipeline_session_id,
            "trace_id": self.trace_id,
            "pipeline_id": self.pipeline_id,
            "step_id": self.step_id,
            "subagent_id": self.subagent_id,
            "role_id": self.role_id,
            "runtime_factory_plan_id": self.runtime_factory_plan_id,
            "runtime_factory_status": self.runtime_factory_status,
            "execution_mode": self.execution_mode,
            "prompt_input_hash": self.prompt_input_hash,
            "actual_provider": self.actual_provider,
            "actual_model": self.actual_model,
            "actual_model_class": self.actual_model_class,
            "request_metadata": dict(self.request_metadata),
            "status": self.status,
        }


@dataclass(frozen=True)
class SubagentRunnerResult:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    step_id: str
    subagent_id: str
    role_id: str
    runtime_factory_plan_id: str
    runtime_factory_status: str
    status: SubagentRunnerStatus
    failure_reason: str | None = None
    actual_provider: str | None = None
    actual_model: str | None = None
    actual_model_class: str | None = None
    input_hash: str | None = None
    prompt_hash: str | None = None
    response_output_hash: str | None = None
    usage_summary: SubagentUsageSummary = field(default_factory=SubagentUsageSummary)
    cache_summary: SubagentCacheSummary = field(default_factory=SubagentCacheSummary)
    tool_call_summaries: list[SubagentToolCallSummary] = field(default_factory=list)
    elapsed_ms: float | None = None
    artifacts_created: list[SubagentArtifactRef] = field(default_factory=list)
    structured_output: StructuredOutputEnvelope | None = None
    error_type: str | None = None
    schema_validation_status: str = "not_applicable"
    raw_output_redacted: bool = True

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_session_id": self.pipeline_session_id,
            "trace_id": self.trace_id,
            "pipeline_id": self.pipeline_id,
            "step_id": self.step_id,
            "subagent_id": self.subagent_id,
            "role_id": self.role_id,
            "runtime_factory_plan_id": self.runtime_factory_plan_id,
            "runtime_factory_status": self.runtime_factory_status,
            "status": self.status.value,
            "failure_reason": self.failure_reason,
            "actual_provider": self.actual_provider,
            "actual_model": self.actual_model,
            "actual_model_class": self.actual_model_class,
            "input_hash": self.input_hash,
            "prompt_hash": self.prompt_hash,
            "response_output_hash": self.response_output_hash,
            "usage_summary": self.usage_summary.to_safe_dict(),
            "cache_summary": self.cache_summary.to_safe_dict(),
            "tool_call_summaries": [item.to_safe_dict() for item in self.tool_call_summaries],
            "elapsed_ms": self.elapsed_ms,
            "artifacts_created": [artifact.to_safe_dict() for artifact in self.artifacts_created],
            "structured_output": self.structured_output.to_safe_dict() if isinstance(self.structured_output, StructuredOutputEnvelope) else self.structured_output,
            "error_type": self.error_type,
            "schema_validation_status": self.schema_validation_status,
            "raw_output_redacted": self.raw_output_redacted,
        }


class ControlledRuntimeRunner:
    def run(
        self,
        runtime: ControlledRuntime,
        *,
        input_messages: list[dict[str, Any]] | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> SubagentRunnerResult:
        start = time.perf_counter()
        payload = {
            "input_messages": list(input_messages or []),
            "request_metadata": dict(request_metadata or {}),
        }
        input_hash = _stable_hash(payload)
        prompt_hash = _stable_hash(
            {
                "system_prompt_source_id": runtime.system_prompt_source_id,
                "system_prompt_path": runtime.system_prompt_path,
            }
        )

        if runtime.runtime_status != "ready" or runtime.invocation_client is None:
            return self._result(
                runtime=runtime,
                status=SubagentRunnerStatus.BLOCKED,
                input_hash=input_hash,
                prompt_hash=prompt_hash,
                failure_reason="runtime_not_ready",
                error_type="runtime_not_ready",
                elapsed_ms=_elapsed_ms(start),
            )

        try:
            raw_result = runtime.invocation_client(runtime, payload)
        except Exception as exc:
            return self._result(
                runtime=runtime,
                status=SubagentRunnerStatus.FAILED,
                input_hash=input_hash,
                prompt_hash=prompt_hash,
                failure_reason="runtime_invocation_failed",
                error_type=type(exc).__name__,
                elapsed_ms=_elapsed_ms(start),
            )

        if not isinstance(raw_result, Mapping):
            return self._result(
                runtime=runtime,
                status=SubagentRunnerStatus.INVALID_OUTPUT,
                input_hash=input_hash,
                prompt_hash=prompt_hash,
                failure_reason="invalid_output",
                error_type="invalid_output",
                elapsed_ms=_elapsed_ms(start),
            )

        reported_provider = _string_or_none(raw_result.get("provider")) or runtime.provider
        reported_model = _string_or_none(raw_result.get("model")) or runtime.model
        if reported_provider != runtime.provider or reported_model != runtime.model:
            return self._result(
                runtime=runtime,
                status=SubagentRunnerStatus.BLOCKED,
                input_hash=input_hash,
                prompt_hash=prompt_hash,
                failure_reason="runtime_contract_mismatch",
                error_type="runtime_contract_mismatch",
                elapsed_ms=_elapsed_ms(start),
            )

        structured_output = raw_result.get("structured_output")
        if structured_output is not None and not isinstance(structured_output, Mapping):
            return self._result(
                runtime=runtime,
                status=SubagentRunnerStatus.INVALID_OUTPUT,
                input_hash=input_hash,
                prompt_hash=prompt_hash,
                failure_reason="invalid_output",
                error_type="invalid_output",
                elapsed_ms=_elapsed_ms(start),
            )

        output_hash = _stable_hash(
            {
                "output_text": _string_or_none(raw_result.get("output_text")),
                "structured_output": dict(structured_output) if isinstance(structured_output, Mapping) else None,
            }
        )
        usage_summary = _usage_summary(raw_result.get("token_usage"))
        cache_summary = _cache_summary(raw_result.get("cache"))
        tool_call_summaries = _tool_call_summaries(raw_result.get("tool_calls"))
        return self._result(
            runtime=runtime,
            status=SubagentRunnerStatus.SUCCEEDED,
            input_hash=input_hash,
            prompt_hash=prompt_hash,
            response_output_hash=output_hash,
            actual_provider=reported_provider,
            actual_model=reported_model,
            usage_summary=usage_summary,
            cache_summary=cache_summary,
            tool_call_summaries=tool_call_summaries,
            structured_output=dict(structured_output) if isinstance(structured_output, Mapping) else {},
            elapsed_ms=_elapsed_ms(start),
        )

    def _result(
        self,
        *,
        runtime: ControlledRuntime,
        status: SubagentRunnerStatus,
        input_hash: str,
        prompt_hash: str,
        elapsed_ms: float,
        failure_reason: str | None = None,
        error_type: str | None = None,
        response_output_hash: str | None = None,
        actual_provider: str | None = None,
        actual_model: str | None = None,
        usage_summary: SubagentUsageSummary | None = None,
        cache_summary: SubagentCacheSummary | None = None,
        tool_call_summaries: list[SubagentToolCallSummary] | None = None,
        structured_output: Any = None,
    ) -> SubagentRunnerResult:
        return SubagentRunnerResult(
            pipeline_session_id=runtime.pipeline_session_id,
            trace_id=runtime.trace_id,
            pipeline_id=runtime.pipeline_id,
            step_id=runtime.role_id,
            subagent_id=runtime.subagent_id,
            role_id=runtime.role_id,
            runtime_factory_plan_id=f"{runtime.pipeline_session_id}:{runtime.role_id}:{runtime.subagent_id}",
            runtime_factory_status=runtime.runtime_status,
            status=status,
            failure_reason=failure_reason,
            actual_provider=actual_provider or runtime.provider,
            actual_model=actual_model or runtime.model,
            actual_model_class=runtime.model_class,
            input_hash=input_hash,
            prompt_hash=prompt_hash,
            response_output_hash=response_output_hash,
            usage_summary=usage_summary or SubagentUsageSummary(input_tokens=0, output_tokens=0, total_tokens=0, source="unavailable"),
            cache_summary=cache_summary or SubagentCacheSummary(source="unavailable"),
            tool_call_summaries=list(tool_call_summaries or []),
            elapsed_ms=elapsed_ms,
            artifacts_created=[],
            structured_output=structured_output,
            error_type=error_type,
            schema_validation_status="not_applicable",
            raw_output_redacted=True,
        )


def build_subagent_runner_request(
    *,
    session: Any,
    planned_step: Any,
    runtime_factory_plan: RuntimeFactoryPlan,
) -> SubagentRunnerRequest:
    return SubagentRunnerRequest(
        pipeline_session_id=str(getattr(session, "pipeline_session_id", "") or ""),
        trace_id=str(getattr(session, "trace_id", "") or ""),
        pipeline_id=str(getattr(session, "pipeline_id", "") or ""),
        step_id=str(getattr(planned_step, "step_kind", "") or ""),
        subagent_id=runtime_factory_plan.subagent_id,
        role_id=runtime_factory_plan.role_id,
        runtime_factory_plan_id=_runtime_factory_plan_id(runtime_factory_plan),
        runtime_factory_status=runtime_factory_plan.status.value,
        execution_mode=runtime_factory_plan.execution_mode,
        prompt_input_hash=str(getattr(session, "user_message_hash", "") or "") or None,
    )


def build_not_invoked_runner_result(
    *,
    request: SubagentRunnerRequest,
    runtime_factory_plan: RuntimeFactoryPlan,
    reason: str = "observe_mode_plan_only",
) -> SubagentRunnerResult:
    return SubagentRunnerResult(
        pipeline_session_id=request.pipeline_session_id,
        trace_id=request.trace_id,
        pipeline_id=request.pipeline_id,
        step_id=request.step_id,
        subagent_id=request.subagent_id,
        role_id=request.role_id,
        runtime_factory_plan_id=request.runtime_factory_plan_id,
        runtime_factory_status=runtime_factory_plan.status.value,
        status=SubagentRunnerStatus.NOT_INVOKED,
        failure_reason=reason,
        schema_validation_status="not_applicable",
        raw_output_redacted=True,
    )


def validate_structured_output_envelope(payload: Any) -> StructuredOutputEnvelope:
    if not isinstance(payload, Mapping):
        return _invalid_envelope("payload", "Structured output payload must be a mapping")

    required_fields = (
        "schema_version",
        "subagent_id",
        "role",
        "status",
        "summary",
        "blockers",
        "artifacts",
        "confidence",
        "requires_review",
        "next_action",
    )
    errors: list[dict[str, str]] = []
    for field_name in required_fields:
        if field_name not in payload:
            errors.append({"field": field_name, "message": "Missing required field"})

    required_strings = (
        "schema_version",
        "subagent_id",
        "role",
        "status",
        "summary",
        "next_action",
    )
    normalized_strings: dict[str, str | None] = {}
    for field_name in required_strings:
        normalized_value, error = _required_string_field(payload, field_name)
        normalized_strings[field_name] = normalized_value
        if error is not None:
            errors.append(error)

    status = normalized_strings["status"]
    if status is not None and status not in _VALID_ENVELOPE_STATUSES:
        errors.append({"field": "status", "message": "Unknown structured output status"})
    if "findings" not in payload and "changes" not in payload:
        errors.append({"field": "findings|changes", "message": "Structured output requires findings or changes"})

    blockers = payload.get("blockers")
    if blockers is not None and not isinstance(blockers, list):
        errors.append({"field": "blockers", "message": "Expected a list"})
    artifacts = payload.get("artifacts")
    if artifacts is not None and not isinstance(artifacts, list):
        errors.append({"field": "artifacts", "message": "Expected a list"})
    confidence = payload.get("confidence")
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            errors.append({"field": "confidence", "message": "Expected a number between 0 and 1"})
        elif not 0.0 <= float(confidence) <= 1.0:
            errors.append({"field": "confidence", "message": "Expected a number between 0 and 1"})
    requires_review = payload.get("requires_review")
    if requires_review is not None and not isinstance(requires_review, bool):
        errors.append({"field": "requires_review", "message": "Expected a boolean"})
    mutations = payload.get("mutations")
    if mutations is not None and not isinstance(mutations, list):
        errors.append({"field": "mutations", "message": "Expected a list"})
    tests = payload.get("tests")
    if tests is not None and not isinstance(tests, list):
        errors.append({"field": "tests", "message": "Expected a list"})

    if errors:
        return StructuredOutputEnvelope(
            schema_version=normalized_strings["schema_version"],
            subagent_id=normalized_strings["subagent_id"],
            role=normalized_strings["role"],
            status=status,
            summary=normalized_strings["summary"],
            validation_status="invalid_structured_output",
            validation_errors=errors,
        )

    findings = _mapping_list(payload.get("findings"))
    changes = _mapping_list(payload.get("changes"))
    return StructuredOutputEnvelope(
        schema_version=normalized_strings["schema_version"],
        subagent_id=normalized_strings["subagent_id"],
        role=normalized_strings["role"],
        status=status,
        summary=normalized_strings["summary"],
        findings=findings,
        changes=changes,
        blockers=[str(item) for item in payload.get("blockers", []) if item is not None],
        artifacts=_artifact_refs(payload.get("artifacts")),
        confidence=float(confidence) if confidence is not None else None,
        requires_review=requires_review,
        next_action=normalized_strings["next_action"],
        mutations=_mapping_list(mutations),
        tests=[str(item) for item in (tests or []) if item is not None],
        validation_status="valid",
    )


def _invalid_envelope(field_name: str, message: str) -> StructuredOutputEnvelope:
    return StructuredOutputEnvelope(
        schema_version=None,
        subagent_id=None,
        role=None,
        status=None,
        summary=None,
        validation_status="invalid_structured_output",
        validation_errors=[{"field": field_name, "message": message}],
    )


def _required_string_field(payload: Mapping[str, Any], field_name: str) -> tuple[str | None, dict[str, str] | None]:
    if field_name not in payload:
        return None, None
    value = payload.get(field_name)
    if not isinstance(value, str):
        return None, {"field": field_name, "message": "Expected a non-empty string"}
    normalized = value.strip()
    if not normalized:
        return None, {"field": field_name, "message": "Expected a non-empty string"}
    return normalized, None


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            items.append({str(key): item[key] for key in item})
    return items


def _artifact_refs(value: Any) -> list[SubagentArtifactRef]:
    if not isinstance(value, list):
        return []
    artifacts: list[SubagentArtifactRef] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        artifact_id = _string_or_none(item.get("artifact_id"))
        kind = _string_or_none(item.get("kind"))
        if artifact_id and kind:
            artifacts.append(
                SubagentArtifactRef(
                    artifact_id=artifact_id,
                    kind=kind,
                    path=_string_or_none(item.get("path")),
                    sha256=_string_or_none(item.get("sha256")),
                    redacted=bool(item.get("redacted", True)),
                )
            )
    return artifacts


def _runtime_factory_plan_id(plan: RuntimeFactoryPlan) -> str:
    return f"{plan.pipeline_session_id}:{plan.role_id}:{plan.subagent_id}"


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


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _usage_summary(value: Any) -> SubagentUsageSummary:
    if not isinstance(value, Mapping):
        return SubagentUsageSummary(input_tokens=0, output_tokens=0, total_tokens=0, source="unavailable")
    input_tokens = int(value.get("input_tokens") or 0)
    output_tokens = int(value.get("output_tokens") or 0)
    reasoning_tokens = int(value.get("reasoning_tokens") or 0) if value.get("reasoning_tokens") is not None else None
    total_tokens = int(value.get("total_tokens") or (input_tokens + output_tokens + (reasoning_tokens or 0)))
    return SubagentUsageSummary(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        source="reported",
    )


def _cache_summary(value: Any) -> SubagentCacheSummary:
    if not isinstance(value, Mapping):
        return SubagentCacheSummary(source="unavailable")
    read_hit = value.get("read_hit")
    write = value.get("write")
    return SubagentCacheSummary(
        cache_hit=read_hit if isinstance(read_hit, bool) else None,
        cache_write=write if isinstance(write, bool) else None,
        read_hit=read_hit if isinstance(read_hit, bool) else None,
        write=write if isinstance(write, bool) else None,
        source="reported",
    )


def _tool_call_summaries(value: Any) -> list[SubagentToolCallSummary]:
    if not isinstance(value, list):
        return []
    summaries: list[SubagentToolCallSummary] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = _string_or_none(item.get("tool_name")) or _string_or_none(item.get("name")) or "unknown"
        summaries.append(
            SubagentToolCallSummary(
                tool_name=name,
                call_count=int(item.get("call_count") or 1),
                status=_string_or_none(item.get("status")) or "reported",
            )
        )
    return summaries
