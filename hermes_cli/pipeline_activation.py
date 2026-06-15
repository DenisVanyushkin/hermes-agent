"""Import-light guarded activation boundary for test-only pipeline execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import TYPE_CHECKING, Any, Callable, Mapping

from hermes_cli.config import cfg_get

if TYPE_CHECKING:
    from hermes_cli.pipeline_executor import PipelineExecutionResult
    from hermes_cli.pipeline_gate import PipelineGateDecision
    from hermes_cli.pipeline_handoff import PipelineHandoffDecision
    from hermes_cli.pipeline_router import RouterDecision


class PipelineActivationStatus(str, Enum):
    DISABLED = "disabled"
    BLOCKED = "blocked"
    EXECUTED = "executed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class PipelineActivationError:
    code: str
    exception_type: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        payload = {"code": self.code}
        if self.exception_type:
            payload["exception_type"] = self.exception_type
        return payload


@dataclass(frozen=True)
class PipelineActivationRequest:
    config: Mapping[str, Any] | None
    router_decision: RouterDecision | None
    pipeline_id: str | None
    pipeline_session_id: str | None
    gate_decision: PipelineGateDecision | None
    handoff_decision: PipelineHandoffDecision | None
    executor: Callable[[], "PipelineExecutionResult"] | None = None
    platform: str | None = None
    platform_allowed: bool | None = None
    destructive_task: bool | None = None
    explicit_approval: bool | None = None
    allow_test_execution: bool = False


@dataclass(frozen=True)
class PipelineActivationResult:
    pipeline_id: str | None
    pipeline_session_id: str | None
    activation_status: PipelineActivationStatus
    activation_reason: str
    would_execute: bool
    executed: bool
    gate_allowed: bool
    handoff_would_execute: bool
    handoff_executed: bool
    execution_mode: str
    requirements_met: list[str] = field(default_factory=list)
    requirements_failed: list[str] = field(default_factory=list)
    error: PipelineActivationError | None = None
    pipeline_executor_status: str | None = None
    pipeline_executor_result: dict[str, Any] | None = None
    elapsed_ms: float = 0.0

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "pipeline_session_id": self.pipeline_session_id,
            "activation_status": self.activation_status.value,
            "activation_reason": self.activation_reason,
            "would_execute": self.would_execute,
            "executed": self.executed,
            "gate_allowed": self.gate_allowed,
            "handoff_would_execute": self.handoff_would_execute,
            "handoff_executed": self.handoff_executed,
            "execution_mode": self.execution_mode,
            "requirements_met": list(self.requirements_met),
            "requirements_failed": list(self.requirements_failed),
            "error": self.error.to_safe_dict() if self.error else None,
            "pipeline_executor_status": self.pipeline_executor_status,
            "pipeline_executor_result": _sanitize_execution_result(self.pipeline_executor_result),
            "elapsed_ms": self.elapsed_ms,
        }


class PipelineActivationCoordinator:
    def run(self, request: PipelineActivationRequest) -> PipelineActivationResult:
        started = time.perf_counter()
        requirements_met: list[str] = []
        requirements_failed: list[str] = []

        gate = request.gate_decision
        handoff = request.handoff_decision
        pipeline_id = request.pipeline_id or getattr(request.router_decision, "selected_pipeline_id", None)
        pipeline_session_id = request.pipeline_session_id or getattr(request.router_decision, "pipeline_session_id", None)
        execution_mode = _execution_mode(request.config)
        gate_allowed = bool(getattr(gate, "allowed", False))
        handoff_would_execute = bool(getattr(handoff, "would_execute", False))
        handoff_executed = bool(getattr(handoff, "executed", False))

        def finish(
            status: PipelineActivationStatus,
            reason: str,
            *,
            error: PipelineActivationError | None = None,
            pipeline_executor_status: str | None = None,
            pipeline_executor_result: dict[str, Any] | None = None,
            executed: bool = False,
            would_execute: bool = False,
        ) -> PipelineActivationResult:
            return PipelineActivationResult(
                pipeline_id=pipeline_id,
                pipeline_session_id=pipeline_session_id,
                activation_status=status,
                activation_reason=reason,
                would_execute=would_execute,
                executed=executed,
                gate_allowed=gate_allowed,
                handoff_would_execute=handoff_would_execute,
                handoff_executed=handoff_executed,
                execution_mode=execution_mode,
                requirements_met=requirements_met,
                requirements_failed=requirements_failed,
                error=error,
                pipeline_executor_status=pipeline_executor_status,
                pipeline_executor_result=pipeline_executor_result,
                elapsed_ms=_elapsed_ms(started),
            )

        if not bool(cfg_get(request.config, "pipelines", "enabled", default=False)):
            requirements_failed.append("pipelines_enabled")
            return finish(PipelineActivationStatus.DISABLED, "pipelines_disabled")
        requirements_met.append("pipelines_enabled")

        if execution_mode != "execute":
            requirements_failed.append("execution_mode_execute")
            status = PipelineActivationStatus.DISABLED if execution_mode == "disabled" else PipelineActivationStatus.BLOCKED
            return finish(status, f"{execution_mode}_mode_blocks_activation")
        requirements_met.append("execution_mode_execute")

        router = request.router_decision
        if router is None or getattr(router, "status", None) != "selected":
            requirements_failed.append("router_selected")
            return finish(PipelineActivationStatus.BLOCKED, "router_not_selected")
        requirements_met.append("router_selected")

        selected_pipeline_id = getattr(router, "selected_pipeline_id", None)
        if not selected_pipeline_id:
            requirements_failed.append("specialized_pipeline_selected")
            return finish(PipelineActivationStatus.BLOCKED, "pipeline_not_selected")
        requirements_met.append("specialized_pipeline_selected")

        allow_pipelines = _allowlisted_pipelines(request.config)
        if selected_pipeline_id not in allow_pipelines:
            requirements_failed.append("pipeline_allowlisted")
            return finish(PipelineActivationStatus.BLOCKED, "pipeline_not_allowlisted")
        requirements_met.append("pipeline_allowlisted")

        if gate is None:
            requirements_failed.append("gate_decision_present")
            return finish(PipelineActivationStatus.BLOCKED, "missing_gate_decision")
        requirements_met.append("gate_decision_present")

        if not gate_allowed:
            requirements_failed.append("gate_allowed")
            return finish(PipelineActivationStatus.BLOCKED, getattr(gate, "reason_code", "gate_denied"))
        requirements_met.append("gate_allowed")

        if handoff is None:
            requirements_failed.append("handoff_decision_present")
            return finish(PipelineActivationStatus.BLOCKED, "missing_handoff_decision")
        requirements_met.append("handoff_decision_present")

        if not handoff_would_execute:
            requirements_failed.append("handoff_would_execute")
            return finish(PipelineActivationStatus.BLOCKED, getattr(handoff, "handoff_reason", "handoff_denied"))
        requirements_met.append("handoff_would_execute")

        if handoff_executed:
            requirements_failed.append("handoff_pre_activation_only")
            return finish(PipelineActivationStatus.BLOCKED, "handoff_already_executed")
        requirements_met.append("handoff_pre_activation_only")

        if getattr(gate, "pipeline_id", None) not in (None, selected_pipeline_id):
            requirements_failed.append("gate_pipeline_id_matches")
            return finish(PipelineActivationStatus.BLOCKED, "gate_pipeline_id_mismatch")
        requirements_met.append("gate_pipeline_id_matches")

        if getattr(handoff, "pipeline_id", None) not in (None, selected_pipeline_id):
            requirements_failed.append("handoff_pipeline_id_matches")
            return finish(PipelineActivationStatus.BLOCKED, "handoff_pipeline_id_mismatch")
        requirements_met.append("handoff_pipeline_id_matches")

        if pipeline_id != selected_pipeline_id:
            requirements_failed.append("pipeline_id_matches")
            return finish(PipelineActivationStatus.BLOCKED, "pipeline_id_mismatch")
        requirements_met.append("pipeline_id_matches")

        router_session_id = getattr(router, "pipeline_session_id", None)
        if getattr(gate, "pipeline_session_id", None) not in (None, router_session_id):
            requirements_failed.append("gate_session_matches")
            return finish(PipelineActivationStatus.BLOCKED, "gate_pipeline_session_id_mismatch")
        requirements_met.append("gate_session_matches")

        if getattr(handoff, "pipeline_session_id", None) not in (None, router_session_id):
            requirements_failed.append("handoff_session_matches")
            return finish(PipelineActivationStatus.BLOCKED, "handoff_pipeline_session_id_mismatch")
        requirements_met.append("handoff_session_matches")

        if pipeline_session_id != router_session_id:
            requirements_failed.append("pipeline_session_matches")
            return finish(PipelineActivationStatus.BLOCKED, "pipeline_session_id_mismatch")
        requirements_met.append("pipeline_session_matches")

        if request.platform_allowed is not True:
            requirements_failed.append("platform_allowed")
            return finish(PipelineActivationStatus.BLOCKED, "unsafe_platform")
        requirements_met.append("platform_allowed")

        if request.destructive_task and not request.explicit_approval:
            requirements_failed.append("destructive_task_approved")
            return finish(PipelineActivationStatus.BLOCKED, "destructive_task_requires_approval")
        requirements_met.append("destructive_task_approved")

        if not request.allow_test_execution:
            requirements_failed.append("test_execution_enabled")
            return finish(PipelineActivationStatus.BLOCKED, "test_execution_not_enabled")
        requirements_met.append("test_execution_enabled")

        if request.executor is None:
            requirements_failed.append("executor_boundary_present")
            return finish(PipelineActivationStatus.BLOCKED, "missing_executor_boundary")
        requirements_met.append("executor_boundary_present")

        try:
            result = request.executor()
        except Exception as exc:
            return finish(
                PipelineActivationStatus.FAILED,
                "activation_executor_failed",
                error=PipelineActivationError("activation_executor_failed", type(exc).__name__),
            )

        from hermes_cli.pipeline_executor import PipelineExecutionResult

        if not isinstance(result, PipelineExecutionResult):
            return finish(
                PipelineActivationStatus.FAILED,
                "malformed_pipeline_execution_result",
                error=PipelineActivationError("malformed_pipeline_execution_result", type(result).__name__),
            )

        return finish(
            PipelineActivationStatus.EXECUTED,
            result.completion_reason,
            executed=True,
            would_execute=True,
            pipeline_executor_status=result.status.value,
            pipeline_executor_result=result.to_safe_dict(),
        )


def _execution_mode(config: Mapping[str, Any] | None) -> str:
    raw = cfg_get(config, "pipelines", "execution", "mode", default="disabled")
    if not isinstance(raw, str):
        return "disabled"
    value = raw.strip().lower() or "disabled"
    return value if value in {"disabled", "observe", "plan_only", "execute"} else "disabled"


def _allowlisted_pipelines(config: Mapping[str, Any] | None) -> set[str]:
    raw = cfg_get(config, "pipelines", "execution", "allow_pipelines", default=[])
    if isinstance(raw, (list, tuple)):
        return {str(item) for item in raw if str(item).strip()}
    return set()


def _sanitize_execution_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    if isinstance(result, dict):
        return {str(key): _sanitize_execution_result(value) for key, value in result.items()}
    if isinstance(result, list):
        return [_sanitize_execution_result(value) for value in result[:10]]
    if isinstance(result, str):
        lowered = result.lower()
        if "secret" in lowered or "token" in lowered or "password" in lowered or "prompt" in lowered:
            return "[redacted]"
        if result.startswith("/"):
            return "[redacted]"
        return result[:240]
    if isinstance(result, (int, float, bool)) or result is None:
        return result
    return repr(result)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)
