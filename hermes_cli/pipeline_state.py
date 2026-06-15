"""Shared pipeline session/state/report dataclasses for the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PipelineSession:
    pipeline_session_id: str
    platform: str | None
    session_key: str | None
    session_id: str | None
    chat_id: str | None
    thread_id: str | None
    user_id: str | None
    created_at: str
    user_message_hash: str
    mode: str


@dataclass(frozen=True)
class PipelineState:
    pipeline_session_id: str
    pipeline_id: str
    state: str
    mode: str
    router_status: str
    selected_pipeline_id: str | None
    fallback_pipeline_id: str | None
    completion_allowed: bool
    completion_blocked_reason: str | None
    final_verdict: str


@dataclass(frozen=True)
class ExecutionReport:
    pipeline_session_id: str
    pipeline_id: str
    router_status: str
    selected_pipeline_id: str | None
    fallback_pipeline_id: str | None
    completion_allowed: bool
    completion_reason: str
    executed: bool
    would_execute: bool
    execution_mode: str
    runtime_status: str
    token_usage: dict[str, Any] | str | None = None
    cache_usage: dict[str, Any] | str | None = None
    tool_call_summary: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_ms: float | None = None


@dataclass(frozen=True)
class OrchestratorObserveReport:
    session: PipelineSession
    state: PipelineState
    execution_report: ExecutionReport
