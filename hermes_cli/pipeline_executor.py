"""Import-light engineering review pipeline executor skeleton."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Mapping

from hermes_cli.pipeline_specs import LoadedPipelineSpecs
from hermes_cli.runtime_factory import RuntimeBuildRequest, RuntimeBuildResult, RuntimeFactory
from hermes_cli.subagent_runner import SubagentInvocationRequest, SubagentInvocationResult, SubagentRunner


ENGINEERING_PIPELINE_ID = "engineering_review_pipeline"
_SENSITIVE_KEY_PARTS = ("secret", "token", "password", "credential", "api_key", "client", "prompt", "env")


class PipelineExecutorError(Exception):
    """Controlled pipeline executor failure."""


class PipelineExecutorStatus(str, Enum):
    APPROVED = "approved"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class PipelineExecutionRequest:
    loaded_specs: LoadedPipelineSpecs
    pipeline_session_id: str
    task_summary: str
    repo_path: str
    pipeline_id: str = ENGINEERING_PIPELINE_ID
    max_iterations: int = 2
    current_session_provider: str | None = None
    current_session_model: str | None = None


@dataclass(frozen=True)
class PipelineStepRecord:
    iteration: int
    step_kind: str
    subagent_id: str
    invocation_id: str
    execution_status: str
    completion_reason: str | None
    constructor_provider: str | None
    constructor_model: str | None
    selected_model_class: str | None
    elapsed_ms: float
    safe_output_summary: str
    metadata_summary: dict[str, Any] = field(default_factory=dict)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "step_kind": self.step_kind,
            "subagent_id": self.subagent_id,
            "invocation_id": self.invocation_id,
            "execution_status": self.execution_status,
            "completion_reason": self.completion_reason,
            "constructor_provider": self.constructor_provider,
            "constructor_model": self.constructor_model,
            "selected_model_class": self.selected_model_class,
            "elapsed_ms": self.elapsed_ms,
            "safe_output_summary": _sanitize_payload(self.safe_output_summary),
            "metadata_summary": _sanitize_payload(self.metadata_summary),
        }

    def as_log_payload(self) -> dict[str, Any]:
        return self.to_safe_dict()


@dataclass(frozen=True)
class PipelineIterationRecord:
    iteration: int
    engineer: PipelineStepRecord
    reviewer: PipelineStepRecord | None
    reviewer_required: bool
    reviewer_ran: bool
    blocking_findings_count: int
    final_iteration_status: str

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "engineer": self.engineer.to_safe_dict(),
            "reviewer": self.reviewer.to_safe_dict() if self.reviewer else None,
            "reviewer_required": self.reviewer_required,
            "reviewer_ran": self.reviewer_ran,
            "blocking_findings_count": self.blocking_findings_count,
            "final_iteration_status": self.final_iteration_status,
        }

    def as_log_payload(self) -> dict[str, Any]:
        return self.to_safe_dict()


@dataclass(frozen=True)
class PipelineExecutionResult:
    pipeline_id: str
    pipeline_session_id: str
    status: PipelineExecutorStatus
    completion_reason: str
    iterations: list[PipelineIterationRecord]
    step_records: list[PipelineStepRecord]
    reviewer_required: bool
    reviewer_ran: bool
    blocking_findings_count: int
    final_approval_status: str
    elapsed_ms: float
    safe_summary: str
    error_code: str | None = None
    error_message: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "pipeline_session_id": self.pipeline_session_id,
            "status": self.status.value,
            "completion_reason": self.completion_reason,
            "iterations": [item.to_safe_dict() for item in self.iterations],
            "step_records": [item.to_safe_dict() for item in self.step_records],
            "reviewer_required": self.reviewer_required,
            "reviewer_ran": self.reviewer_ran,
            "blocking_findings_count": self.blocking_findings_count,
            "final_approval_status": self.final_approval_status,
            "elapsed_ms": self.elapsed_ms,
            "safe_summary": self.safe_summary,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }

    def as_log_payload(self) -> dict[str, Any]:
        return self.to_safe_dict()


class EngineeringReviewPipelineExecutor:
    def __init__(
        self,
        *,
        runtime_factory: RuntimeFactory,
        engineer_runner: SubagentRunner,
        reviewer_runner: SubagentRunner,
    ):
        self.runtime_factory = runtime_factory
        self.engineer_runner = engineer_runner
        self.reviewer_runner = reviewer_runner

    def execute(self, request: PipelineExecutionRequest) -> PipelineExecutionResult:
        started = time.perf_counter()
        specs_error = self._validate_specs(request.loaded_specs, request.pipeline_id)
        if specs_error is not None:
            return self._failure_result(
                request=request,
                completion_reason=specs_error[0],
                error_code=specs_error[0],
                error_message=specs_error[1],
                elapsed_ms=_elapsed_ms(started),
            )

        pipeline_spec = request.loaded_specs.pipeline_specs[request.pipeline_id]
        engineer_id = str(pipeline_spec.get("subagents", {}).get("engineer"))
        reviewer_id = str(pipeline_spec.get("subagents", {}).get("reviewer"))
        max_iterations = max(1, int(request.max_iterations or 1))

        reviewer_required = False
        reviewer_ran = False
        blocking_findings_count = 0
        step_records: list[PipelineStepRecord] = []
        iterations: list[PipelineIterationRecord] = []
        reviewer_feedback: list[dict[str, Any]] = []

        for iteration in range(1, max_iterations + 1):
            engineer_plan = self._build_runtime_plan(
                request=request,
                subagent_id=engineer_id,
                invocation_id=f"{request.pipeline_session_id}:engineer:{iteration}",
                elapsed_ms=_elapsed_ms(started),
            )
            if isinstance(engineer_plan, PipelineExecutionResult):
                return engineer_plan

            engineer_result = self.engineer_runner.run(
                engineer_plan,
                SubagentInvocationRequest(
                    subagent_id=engineer_id,
                    pipeline_session_id=request.pipeline_session_id,
                    invocation_id=f"{request.pipeline_session_id}:engineer:{iteration}",
                    input_messages=[{"role": "user", "content": request.task_summary}],
                    metadata={"reviewer_feedback": reviewer_feedback},
                ),
            )
            if not engineer_result.ok:
                return self._subagent_failure_result(
                    request=request,
                    result=engineer_result,
                    elapsed_ms=_elapsed_ms(started),
                )

            engineer_metadata = _validate_engineer_metadata(engineer_result.raw_metadata)
            if isinstance(engineer_metadata, str):
                return self._failure_result(
                    request=request,
                    completion_reason="malformed_engineer_metadata",
                    error_code="malformed_engineer_metadata",
                    error_message=engineer_metadata,
                    elapsed_ms=_elapsed_ms(started),
                )

            engineer_step = self._step_record(
                iteration=iteration,
                step_kind="engineer",
                runtime_plan=engineer_plan,
                invocation_result=engineer_result,
                metadata_summary={
                    "code_changed": engineer_metadata["code_changed"],
                    "files_changed": list(engineer_metadata["files_changed"]),
                    "needs_review": engineer_metadata["needs_review"],
                    "change_summary": engineer_metadata["change_summary"],
                },
            )
            step_records.append(engineer_step)

            if not engineer_metadata["code_changed"]:
                iterations.append(
                    PipelineIterationRecord(
                        iteration=iteration,
                        engineer=engineer_step,
                        reviewer=None,
                        reviewer_required=False,
                        reviewer_ran=False,
                        blocking_findings_count=0,
                        final_iteration_status="no_code_changes",
                    )
                )
                return self._success_result(
                    request=request,
                    status=PipelineExecutorStatus.APPROVED,
                    completion_reason="no_code_changes",
                    iterations=iterations,
                    step_records=step_records,
                    reviewer_required=False,
                    reviewer_ran=False,
                    blocking_findings_count=0,
                    final_approval_status="not_required",
                    elapsed_ms=_elapsed_ms(started),
                    safe_summary="Engineer reported no code changes; reviewer skipped.",
                )

            reviewer_required = bool(engineer_metadata["needs_review"])
            if not reviewer_required:
                iterations.append(
                    PipelineIterationRecord(
                        iteration=iteration,
                        engineer=engineer_step,
                        reviewer=None,
                        reviewer_required=False,
                        reviewer_ran=False,
                        blocking_findings_count=0,
                        final_iteration_status="review_not_required",
                    )
                )
                return self._success_result(
                    request=request,
                    status=PipelineExecutorStatus.APPROVED,
                    completion_reason="review_not_required",
                    iterations=iterations,
                    step_records=step_records,
                    reviewer_required=False,
                    reviewer_ran=False,
                    blocking_findings_count=0,
                    final_approval_status="not_required",
                    elapsed_ms=_elapsed_ms(started),
                    safe_summary="Engineer reported changes but review was not required.",
                )

            reviewer_plan = self._build_runtime_plan(
                request=request,
                subagent_id=reviewer_id,
                invocation_id=f"{request.pipeline_session_id}:reviewer:{iteration}",
                elapsed_ms=_elapsed_ms(started),
            )
            if isinstance(reviewer_plan, PipelineExecutionResult):
                return reviewer_plan

            reviewer_result = self.reviewer_runner.run(
                reviewer_plan,
                SubagentInvocationRequest(
                    subagent_id=reviewer_id,
                    pipeline_session_id=request.pipeline_session_id,
                    invocation_id=f"{request.pipeline_session_id}:reviewer:{iteration}",
                    input_messages=[{"role": "user", "content": "Review engineering changes"}],
                    metadata={
                        "engineer_summary": engineer_metadata["change_summary"],
                        "files_changed": engineer_metadata["files_changed"],
                    },
                ),
            )
            reviewer_ran = True
            if not reviewer_result.ok:
                return self._subagent_failure_result(
                    request=request,
                    result=reviewer_result,
                    elapsed_ms=_elapsed_ms(started),
                )

            reviewer_metadata = _validate_reviewer_metadata(reviewer_result.raw_metadata)
            if isinstance(reviewer_metadata, str):
                return self._failure_result(
                    request=request,
                    completion_reason="malformed_reviewer_metadata",
                    error_code="malformed_reviewer_metadata",
                    error_message=reviewer_metadata,
                    elapsed_ms=_elapsed_ms(started),
                )

            blocking_findings_count = len(reviewer_metadata["blocking_findings"])
            reviewer_step = self._step_record(
                iteration=iteration,
                step_kind="reviewer",
                runtime_plan=reviewer_plan,
                invocation_result=reviewer_result,
                metadata_summary={
                    "approved": reviewer_metadata["approved"],
                    "blocking_findings_count": blocking_findings_count,
                    "nonblocking_findings_count": len(reviewer_metadata["nonblocking_findings"]),
                },
            )
            step_records.append(reviewer_step)
            iterations.append(
                PipelineIterationRecord(
                    iteration=iteration,
                    engineer=engineer_step,
                    reviewer=reviewer_step,
                    reviewer_required=True,
                    reviewer_ran=True,
                    blocking_findings_count=blocking_findings_count,
                    final_iteration_status="approved" if blocking_findings_count == 0 and reviewer_metadata["approved"] else "changes_requested",
                )
            )

            if blocking_findings_count == 0 and reviewer_metadata["approved"]:
                return self._success_result(
                    request=request,
                    status=PipelineExecutorStatus.APPROVED,
                    completion_reason="review_approved",
                    iterations=iterations,
                    step_records=step_records,
                    reviewer_required=reviewer_required,
                    reviewer_ran=reviewer_ran,
                    blocking_findings_count=0,
                    final_approval_status="approved",
                    elapsed_ms=_elapsed_ms(started),
                    safe_summary="Reviewer approved engineering changes.",
                )

            reviewer_feedback = list(reviewer_metadata["blocking_findings"])
            if iteration >= max_iterations:
                return self._success_result(
                    request=request,
                    status=PipelineExecutorStatus.BLOCKED,
                    completion_reason="max_iterations_reached",
                    iterations=iterations,
                    step_records=step_records,
                    reviewer_required=reviewer_required,
                    reviewer_ran=reviewer_ran,
                    blocking_findings_count=blocking_findings_count,
                    final_approval_status="blocked",
                    elapsed_ms=_elapsed_ms(started),
                    safe_summary="Blocking review findings remained after the allowed iterations.",
                )

        return self._failure_result(
            request=request,
            completion_reason="pipeline_unreachable",
            error_code="pipeline_unreachable",
            error_message="Pipeline exited unexpectedly",
            elapsed_ms=_elapsed_ms(started),
        )

    def _validate_specs(self, loaded_specs: LoadedPipelineSpecs, pipeline_id: str) -> tuple[str, str] | None:
        pipeline_spec = loaded_specs.pipeline_specs.get(pipeline_id)
        if pipeline_spec is None:
            return ("missing_pipeline_spec", f"Missing pipeline spec {pipeline_id!r}")
        subagents = pipeline_spec.get("subagents") or {}
        engineer_id = subagents.get("engineer")
        reviewer_id = subagents.get("reviewer")
        if not isinstance(engineer_id, str) or not engineer_id:
            return ("missing_engineer_subagent_spec", "Engineering pipeline is missing engineer subagent")
        if not isinstance(reviewer_id, str) or not reviewer_id:
            return ("missing_reviewer_subagent_spec", "Engineering pipeline is missing reviewer subagent")
        if engineer_id not in loaded_specs.subagent_specs:
            return ("missing_engineer_subagent_spec", f"Missing engineer subagent spec {engineer_id!r}")
        if reviewer_id not in loaded_specs.subagent_specs:
            return ("missing_reviewer_subagent_spec", f"Missing reviewer subagent spec {reviewer_id!r}")
        return None

    def _build_runtime_plan(
        self,
        *,
        request: PipelineExecutionRequest,
        subagent_id: str,
        invocation_id: str,
        elapsed_ms: float,
    ) -> RuntimeBuildResult | PipelineExecutionResult:
        plan = self.runtime_factory.build(
            RuntimeBuildRequest(
                loaded_specs=request.loaded_specs,
                subagent_id=subagent_id,
                pipeline_session_id=request.pipeline_session_id,
                invocation_id=invocation_id,
                current_session_provider=request.current_session_provider,
                current_session_model=request.current_session_model,
            )
        )
        if plan.actual_runtime_status != "ready_to_construct":
            return self._failure_result(
                request=request,
                completion_reason="runtime_plan_failed",
                error_code="runtime_plan_failed",
                error_message=f"Runtime plan for {subagent_id} is {plan.actual_runtime_status}",
                elapsed_ms=elapsed_ms,
            )
        return plan

    def _step_record(
        self,
        *,
        iteration: int,
        step_kind: str,
        runtime_plan: RuntimeBuildResult,
        invocation_result: SubagentInvocationResult,
        metadata_summary: dict[str, Any],
    ) -> PipelineStepRecord:
        return PipelineStepRecord(
            iteration=iteration,
            step_kind=step_kind,
            subagent_id=runtime_plan.subagent_id,
            invocation_id=invocation_result.record.invocation_id,
            execution_status=invocation_result.execution_status,
            completion_reason=invocation_result.completion_reason,
            constructor_provider=runtime_plan.constructor_provider,
            constructor_model=runtime_plan.constructor_model,
            selected_model_class=runtime_plan.selection.selected_model_class if runtime_plan.selection else None,
            elapsed_ms=invocation_result.record.elapsed_ms,
            safe_output_summary=invocation_result.record.safe_output_summary,
            metadata_summary=metadata_summary,
        )

    def _subagent_failure_result(
        self,
        *,
        request: PipelineExecutionRequest,
        result: SubagentInvocationResult,
        elapsed_ms: float,
    ) -> PipelineExecutionResult:
        return self._failure_result(
            request=request,
            completion_reason="subagent_execution_failed",
            error_code="subagent_execution_failed",
            error_message=result.error_message or result.completion_reason or "Subagent execution failed",
            elapsed_ms=elapsed_ms,
        )

    def _success_result(
        self,
        *,
        request: PipelineExecutionRequest,
        status: PipelineExecutorStatus,
        completion_reason: str,
        iterations: list[PipelineIterationRecord],
        step_records: list[PipelineStepRecord],
        reviewer_required: bool,
        reviewer_ran: bool,
        blocking_findings_count: int,
        final_approval_status: str,
        elapsed_ms: float,
        safe_summary: str,
    ) -> PipelineExecutionResult:
        return PipelineExecutionResult(
            pipeline_id=request.pipeline_id,
            pipeline_session_id=request.pipeline_session_id,
            status=status,
            completion_reason=completion_reason,
            iterations=list(iterations),
            step_records=list(step_records),
            reviewer_required=reviewer_required,
            reviewer_ran=reviewer_ran,
            blocking_findings_count=blocking_findings_count,
            final_approval_status=final_approval_status,
            elapsed_ms=elapsed_ms,
            safe_summary=safe_summary,
        )

    def _failure_result(
        self,
        *,
        request: PipelineExecutionRequest,
        completion_reason: str,
        error_code: str,
        error_message: str,
        elapsed_ms: float,
    ) -> PipelineExecutionResult:
        return PipelineExecutionResult(
            pipeline_id=request.pipeline_id,
            pipeline_session_id=request.pipeline_session_id,
            status=PipelineExecutorStatus.FAILED,
            completion_reason=completion_reason,
            iterations=[],
            step_records=[],
            reviewer_required=False,
            reviewer_ran=False,
            blocking_findings_count=0,
            final_approval_status="failed",
            elapsed_ms=elapsed_ms,
            safe_summary="Engineering review pipeline execution failed.",
            error_code=error_code,
            error_message=error_message,
        )


def _validate_engineer_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any] | str:
    if not isinstance(metadata, Mapping):
        return "Engineer metadata must be a mapping"
    required_fields = ("code_changed", "change_summary", "files_changed", "needs_review")
    missing_fields = [field for field in required_fields if field not in metadata]
    if missing_fields:
        return f"Engineer metadata is missing required fields: {', '.join(missing_fields)}"
    code_changed = metadata.get("code_changed")
    if not isinstance(code_changed, bool):
        return "Engineer metadata field 'code_changed' must be a boolean"
    change_summary = metadata.get("change_summary")
    if not isinstance(change_summary, str):
        return "Engineer metadata field 'change_summary' must be a string"
    files_changed = metadata.get("files_changed")
    if not isinstance(files_changed, list) or any(not isinstance(item, str) for item in files_changed):
        return "Engineer metadata field 'files_changed' must be a list[str]"
    needs_review = metadata.get("needs_review")
    if not isinstance(needs_review, bool):
        return "Engineer metadata field 'needs_review' must be a boolean"
    return {
        "code_changed": code_changed,
        "change_summary": change_summary,
        "files_changed": list(files_changed),
        "needs_review": needs_review,
    }


def _validate_reviewer_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any] | str:
    if not isinstance(metadata, Mapping):
        return "Reviewer metadata must be a mapping"
    required_fields = ("blocking_findings", "nonblocking_findings", "approved")
    missing_fields = [field for field in required_fields if field not in metadata]
    if missing_fields:
        return f"Reviewer metadata is missing required fields: {', '.join(missing_fields)}"
    blocking_findings = metadata.get("blocking_findings")
    nonblocking_findings = metadata.get("nonblocking_findings")
    approved = metadata.get("approved")
    if not isinstance(blocking_findings, list) or any(not isinstance(item, Mapping) for item in blocking_findings):
        return "Reviewer metadata field 'blocking_findings' must be a list[dict]"
    if not isinstance(nonblocking_findings, list) or any(not isinstance(item, Mapping) for item in nonblocking_findings):
        return "Reviewer metadata field 'nonblocking_findings' must be a list[dict]"
    if not isinstance(approved, bool):
        return "Reviewer metadata field 'approved' must be a boolean"
    return {
        "blocking_findings": [dict(item) for item in blocking_findings],
        "nonblocking_findings": [dict(item) for item in nonblocking_findings],
        "approved": approved,
    }


def _sanitize_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(part in key_text.lower() for part in _SENSITIVE_KEY_PARTS):
                sanitized[key_text] = "[redacted]"
                continue
            sanitized[key_text] = _sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_payload(item) for item in value[:10]]
    if isinstance(value, str):
        lowered = value.lower()
        if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
            return "[redacted]"
        return value[:240]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return repr(value)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)
