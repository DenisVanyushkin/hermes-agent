"""Thin activation adapter for future pipeline runtime wiring."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from hermes_cli.pipeline_gate import PipelineGateDecision
    from hermes_cli.pipeline_router import RouterDecision


class PipelineActivationStatus(str, Enum):
    BLOCKED = "blocked"
    NOT_WIRED = "not_wired"


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
    router_decision: RouterDecision | None
    preflight_decision: PipelineGateDecision | None
    executor: Callable[[], "PipelineExecutionResult"] | None = None


@dataclass(frozen=True)
class PipelineActivationResult:
    pipeline_id: str | None
    pipeline_session_id: str | None
    activation_status: PipelineActivationStatus
    activation_reason: str
    would_execute: bool
    executed: bool
    requirements_met: list[str] = field(default_factory=list)
    requirements_failed: list[str] = field(default_factory=list)
    error: PipelineActivationError | None = None
    elapsed_ms: float = 0.0

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "pipeline_session_id": self.pipeline_session_id,
            "activation_status": self.activation_status.value,
            "activation_reason": self.activation_reason,
            "would_execute": self.would_execute,
            "executed": self.executed,
            "requirements_met": list(self.requirements_met),
            "requirements_failed": list(self.requirements_failed),
            "error": self.error.to_safe_dict() if self.error else None,
            "elapsed_ms": self.elapsed_ms,
        }


class PipelineActivationCoordinator:
    def run(self, request: PipelineActivationRequest) -> PipelineActivationResult:
        started = time.perf_counter()
        requirements_met: list[str] = []
        requirements_failed: list[str] = []
        preflight = request.preflight_decision
        router = request.router_decision
        pipeline_id = getattr(preflight, "selected_pipeline_id", None) or getattr(router, "selected_pipeline_id", None)
        pipeline_session_id = getattr(preflight, "pipeline_session_id", None) or getattr(router, "pipeline_session_id", None)

        def finish(
            status: PipelineActivationStatus,
            reason: str,
            *,
            would_execute: bool,
            executed: bool,
            error: PipelineActivationError | None = None,
        ) -> PipelineActivationResult:
            return PipelineActivationResult(
                pipeline_id=pipeline_id,
                pipeline_session_id=pipeline_session_id,
                activation_status=status,
                activation_reason=reason,
                would_execute=would_execute,
                executed=executed,
                requirements_met=requirements_met,
                requirements_failed=requirements_failed,
                error=error,
                elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
            )

        if preflight is None:
            requirements_failed.append("preflight_decision_present")
            return finish(PipelineActivationStatus.BLOCKED, "missing_preflight_decision", would_execute=False, executed=False)
        requirements_met.append("preflight_decision_present")

        if not preflight.allowed:
            requirements_failed.append("preflight_allowed")
            return finish(PipelineActivationStatus.BLOCKED, preflight.reason_code, would_execute=False, executed=False)
        requirements_met.append("preflight_allowed")

        if router is not None and getattr(router, "selected_pipeline_id", None) not in (None, pipeline_id):
            requirements_failed.append("pipeline_id_matches_router")
            return finish(PipelineActivationStatus.BLOCKED, "pipeline_id_mismatch", would_execute=False, executed=False)
        requirements_met.append("pipeline_id_matches_router")

        if router is not None and getattr(router, "pipeline_session_id", None) not in (None, pipeline_session_id):
            requirements_failed.append("pipeline_session_matches_router")
            return finish(PipelineActivationStatus.BLOCKED, "pipeline_session_id_mismatch", would_execute=False, executed=False)
        requirements_met.append("pipeline_session_matches_router")

        requirements_failed.append("activation_path_wired")
        return finish(PipelineActivationStatus.NOT_WIRED, "activation_not_wired", would_execute=True, executed=False)
