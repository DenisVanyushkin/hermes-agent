"""Test-only guarded handoff contract between pipeline gate and executor."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import time
from typing import TYPE_CHECKING, Any

from hermes_cli.pipeline_executor import EngineeringReviewPipelineExecutor
from hermes_cli.pipeline_gate import PipelineGateDecision, PipelineGateMode

if TYPE_CHECKING:
    from hermes_cli.pipeline_executor import PipelineExecutionRequest, PipelineExecutionResult
    from hermes_cli.pipeline_router import RouterDecision
    from hermes_cli.subagent_runner import SubagentExecutorProtocol


class PipelineHandoffMode(str, Enum):
    DISABLED = "disabled"
    OBSERVE_ONLY = "observe_only"
    PLAN_ONLY = "plan_only"
    TEST_EXECUTE = "test_execute"


class PipelineHandoffStatus(str, Enum):
    DENIED = "denied"
    BLOCKED = "blocked"
    EXECUTED = "executed"
    FAILED = "failed"


@dataclass(frozen=True)
class PipelineHandoffError:
    code: str
    exception_type: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code}
        if self.exception_type:
            payload["exception_type"] = self.exception_type
        return payload


@dataclass(frozen=True)
class PipelineHandoffDecision:
    pipeline_id: str
    pipeline_session_id: str
    gate_allowed: bool
    gate_reason_code: str
    handoff_status: PipelineHandoffStatus
    handoff_reason: str
    execution_mode: PipelineHandoffMode
    would_execute: bool
    executed: bool
    pipeline_executor_status: str | None = None
    safe_summary: str = ""
    elapsed_ms: float = 0.0
    error: PipelineHandoffError | None = None
    gate_payload: dict[str, Any] = field(default_factory=dict)
    pipeline_executor_result: dict[str, Any] | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "pipeline_session_id": self.pipeline_session_id,
            "gate_allowed": self.gate_allowed,
            "gate_reason_code": self.gate_reason_code,
            "handoff_status": self.handoff_status.value,
            "handoff_reason": self.handoff_reason,
            "execution_mode": self.execution_mode.value,
            "would_execute": self.would_execute,
            "executed": self.executed,
            "pipeline_executor_status": self.pipeline_executor_status,
            "safe_summary": self.safe_summary,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error.to_safe_dict() if self.error else None,
            "gate_payload": dict(self.gate_payload),
            "pipeline_executor_result": self.pipeline_executor_result,
        }

    def as_log_payload(self) -> dict[str, Any]:
        return self.to_safe_dict()


@dataclass(frozen=True)
class PipelineHandoffResult(PipelineHandoffDecision):
    pass


@dataclass(frozen=True)
class PipelineHandoffRequest:
    pipeline_id: str
    pipeline_session_id: str
    router_decision: RouterDecision | None
    execution_request: PipelineExecutionRequest
    gate_decision: PipelineGateDecision | None = None
    mode: PipelineHandoffMode = PipelineHandoffMode.DISABLED
    allow_test_execution: bool = False
    engineer_executor: SubagentExecutorProtocol | None = None
    reviewer_executor: SubagentExecutorProtocol | None = None


class PipelineHandoffCoordinator:
    def run(self, request: PipelineHandoffRequest) -> PipelineHandoffResult:
        started = time.perf_counter()
        gate = request.gate_decision

        if gate is None:
            return self._result(
                request=request,
                handoff_status=PipelineHandoffStatus.DENIED,
                handoff_reason="missing_gate_decision",
                gate_allowed=False,
                gate_reason_code="missing_gate_decision",
                would_execute=False,
                executed=False,
                safe_summary="Pipeline handoff denied because no gate decision was provided.",
                started=started,
            )

        gate_allowed = bool(gate.allowed)
        gate_reason_code = gate.reason_code

        if not gate_allowed:
            return self._result(
                request=request,
                handoff_status=PipelineHandoffStatus.DENIED,
                handoff_reason=gate.reason,
                gate_allowed=False,
                gate_reason_code=gate_reason_code,
                would_execute=False,
                executed=False,
                safe_summary="Pipeline handoff denied by the execution gate.",
                started=started,
                gate=gate,
            )

        consistency_error = self._validate_consistency(request, gate)
        if consistency_error is not None:
            return self._result(
                request=request,
                handoff_status=PipelineHandoffStatus.FAILED,
                handoff_reason=consistency_error.code,
                gate_allowed=True,
                gate_reason_code=gate_reason_code,
                would_execute=False,
                executed=False,
                safe_summary="Pipeline handoff failed closed due to inconsistent gate metadata.",
                started=started,
                gate=gate,
                error=consistency_error,
            )

        would_execute = request.mode == PipelineHandoffMode.TEST_EXECUTE
        if request.mode != PipelineHandoffMode.TEST_EXECUTE:
            return self._result(
                request=request,
                handoff_status=PipelineHandoffStatus.BLOCKED,
                handoff_reason="test_execute_mode_required",
                gate_allowed=True,
                gate_reason_code=gate_reason_code,
                would_execute=would_execute,
                executed=False,
                safe_summary="Pipeline handoff remained non-executing because only test_execute can run.",
                started=started,
                gate=gate,
            )

        if not request.allow_test_execution:
            return self._result(
                request=request,
                handoff_status=PipelineHandoffStatus.BLOCKED,
                handoff_reason="test_execution_not_enabled",
                gate_allowed=True,
                gate_reason_code=gate_reason_code,
                would_execute=True,
                executed=False,
                safe_summary="Pipeline handoff blocked because test execution was not explicitly enabled.",
                started=started,
                gate=gate,
            )

        if request.engineer_executor is None or request.reviewer_executor is None:
            return self._result(
                request=request,
                handoff_status=PipelineHandoffStatus.BLOCKED,
                handoff_reason="missing_fake_executors",
                gate_allowed=True,
                gate_reason_code=gate_reason_code,
                would_execute=True,
                executed=False,
                safe_summary="Pipeline handoff blocked because fake executors were not injected.",
                started=started,
                gate=gate,
            )

        try:
            executor_result = self._execute_test_only(request)
        except Exception as exc:
            return self._result(
                request=request,
                handoff_status=PipelineHandoffStatus.FAILED,
                handoff_reason="pipeline_executor_failure",
                gate_allowed=True,
                gate_reason_code=gate_reason_code,
                would_execute=True,
                executed=False,
                safe_summary="Pipeline handoff failed while invoking the test-only executor.",
                started=started,
                gate=gate,
                error=PipelineHandoffError(
                    "pipeline_executor_failure",
                    exception_type=type(exc).__name__,
                ),
            )

        from hermes_cli.pipeline_executor import PipelineExecutionResult

        if not isinstance(executor_result, PipelineExecutionResult):
            return self._result(
                request=request,
                handoff_status=PipelineHandoffStatus.FAILED,
                handoff_reason="malformed_pipeline_execution_result",
                gate_allowed=True,
                gate_reason_code=gate_reason_code,
                would_execute=True,
                executed=False,
                safe_summary="Pipeline handoff failed because the executor returned an invalid result type.",
                started=started,
                gate=gate,
                error=PipelineHandoffError(
                    "malformed_pipeline_execution_result",
                    exception_type=type(executor_result).__name__,
                ),
            )

        return self._result(
            request=request,
            handoff_status=PipelineHandoffStatus.EXECUTED,
            handoff_reason=executor_result.completion_reason,
            gate_allowed=True,
            gate_reason_code=gate_reason_code,
            would_execute=True,
            executed=True,
            pipeline_executor_status=executor_result.status.value,
            safe_summary=executor_result.safe_summary,
            pipeline_executor_result=executor_result.to_safe_dict(),
            started=started,
            gate=gate,
        )

    def _validate_consistency(
        self,
        request: PipelineHandoffRequest,
        gate: PipelineGateDecision,
    ) -> PipelineHandoffError | None:
        if gate.pipeline_id and gate.pipeline_id != request.pipeline_id:
            return PipelineHandoffError("pipeline_id_mismatch")
        if gate.pipeline_session_id and gate.pipeline_session_id != request.pipeline_session_id:
            return PipelineHandoffError("pipeline_session_id_mismatch")
        if gate.mode != PipelineGateMode.EXECUTE:
            return PipelineHandoffError("gate_execute_mode_required")
        return None

    def _execute_test_only(self, request: PipelineHandoffRequest) -> PipelineExecutionResult:
        from hermes_cli.runtime_factory import RuntimeFactory
        from hermes_cli.subagent_runner import SubagentRunner

        executor = EngineeringReviewPipelineExecutor(
            runtime_factory=RuntimeFactory(repo_root=request.execution_request.repo_path),
            engineer_runner=SubagentRunner(executor=request.engineer_executor),
            reviewer_runner=SubagentRunner(executor=request.reviewer_executor),
        )
        return executor.execute(request.execution_request)

    def _result(
        self,
        *,
        request: PipelineHandoffRequest,
        handoff_status: PipelineHandoffStatus,
        handoff_reason: str,
        gate_allowed: bool,
        gate_reason_code: str,
        would_execute: bool,
        executed: bool,
        safe_summary: str,
        started: float,
        gate: PipelineGateDecision | None = None,
        error: PipelineHandoffError | None = None,
        pipeline_executor_status: str | None = None,
        pipeline_executor_result: dict[str, Any] | None = None,
    ) -> PipelineHandoffResult:
        return PipelineHandoffResult(
            pipeline_id=request.pipeline_id,
            pipeline_session_id=request.pipeline_session_id,
            gate_allowed=gate_allowed,
            gate_reason_code=gate_reason_code,
            handoff_status=handoff_status,
            handoff_reason=handoff_reason,
            execution_mode=request.mode,
            would_execute=would_execute,
            executed=executed,
            pipeline_executor_status=pipeline_executor_status,
            safe_summary=safe_summary,
            elapsed_ms=_elapsed_ms(started),
            error=error,
            gate_payload=gate.to_safe_dict() if gate else {},
            pipeline_executor_result=_sanitize_execution_result(pipeline_executor_result),
        )


def _sanitize_execution_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return _sanitize_value(result)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value[:10]]
    if isinstance(value, str):
        lowered = value.lower()
        if "secret" in lowered or "token" in lowered or "password" in lowered or "prompt" in lowered:
            return "[redacted]"
        if value.startswith("/"):
            return _hash_text(value)
        return value[:240]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return repr(value)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)
