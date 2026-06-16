"""Metadata-only pipeline session models for orchestrator observe mode."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from hermes_cli.pipeline_router import DEFAULT_PIPELINE_ID, RouterDecision


ENGINEERING_PIPELINE_ID = "engineering_review_pipeline"


class PipelineSessionStatus(str, Enum):
    CREATED = "created"
    PLANNED = "planned"
    PREFLIGHT_BLOCKED = "preflight_blocked"


@dataclass(frozen=True)
class PipelineStepPlan:
    step_kind: str
    subagent_id: str
    condition: str | None
    execution_status: str = "planned"
    planning_mode: str = "metadata_only"
    runtime_factory_plan: dict[str, object] | None = None
    runner_request: dict[str, object] | None = None
    runner_result: dict[str, object] | None = None
    evaluation_result: dict[str, object] | None = None


@dataclass(frozen=True)
class PipelineSessionRequest:
    router_decision: RouterDecision | None
    execution_mode: str
    user_message: str
    session_id: str | None = None
    session_key: str | None = None
    platform: str | None = None
    chat_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None
    created_at: str | None = None
    trace_id: str | None = None


@dataclass(frozen=True)
class PipelineSession:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    router_status: str
    router_confidence: float
    platform: str | None
    session_key: str | None
    session_id: str | None
    chat_id: str | None
    thread_id: str | None
    user_id: str | None
    created_at: str
    user_message_hash: str
    mode: str
    current_state: str
    status: PipelineSessionStatus
    planned_steps: list[PipelineStepPlan] = field(default_factory=list)
    selected_subagent_ids: list[str] = field(default_factory=list)
    reviewer_condition: str | None = None


def create_pipeline_session(*, request: PipelineSessionRequest) -> PipelineSession:
    router = request.router_decision
    pipeline_session_id = (
        router.pipeline_session_id
        if router is not None and router.pipeline_session_id
        else uuid.uuid4().hex
    )
    pipeline_id = _effective_pipeline_id(router)
    planned_steps = _planned_steps(pipeline_id)
    created_at = request.created_at or datetime.now(timezone.utc).isoformat()
    return PipelineSession(
        pipeline_session_id=pipeline_session_id,
        trace_id=request.trace_id or pipeline_session_id,
        pipeline_id=pipeline_id,
        router_status=(router.status if router is not None and router.status else "unavailable"),
        router_confidence=float(router.confidence) if router is not None else 0.0,
        platform=request.platform,
        session_key=request.session_key,
        session_id=request.session_id,
        chat_id=request.chat_id,
        thread_id=request.thread_id,
        user_id=request.user_id,
        created_at=created_at,
        user_message_hash=_hash_user_message(request.user_message),
        mode=request.execution_mode,
        current_state="task_received",
        status=PipelineSessionStatus.CREATED,
        planned_steps=planned_steps,
        selected_subagent_ids=[step.subagent_id for step in planned_steps],
        reviewer_condition=next((step.condition for step in planned_steps if step.step_kind == "reviewer"), None),
    )


def _effective_pipeline_id(router: RouterDecision | None) -> str:
    if router is None:
        return DEFAULT_PIPELINE_ID
    return router.selected_pipeline_id or router.fallback_pipeline_id or DEFAULT_PIPELINE_ID


def _planned_steps(pipeline_id: str) -> list[PipelineStepPlan]:
    if pipeline_id == ENGINEERING_PIPELINE_ID:
        return [
            PipelineStepPlan(step_kind="engineer", subagent_id="hermes_engineer_core", condition=None),
            PipelineStepPlan(
                step_kind="reviewer",
                subagent_id="hermes_code_reviewer",
                condition="code_changes_require_review",
            ),
        ]
    return [PipelineStepPlan(step_kind="response", subagent_id="general_operator", condition=None)]


def _hash_user_message(user_message: str) -> str:
    return hashlib.sha256((user_message or "").encode("utf-8")).hexdigest()[:16]
