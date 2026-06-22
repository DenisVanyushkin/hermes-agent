"""Import-light runtime build planning for pipeline subagents."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
from pathlib import Path
from typing import Any, Mapping, Protocol

from hermes_cli.pipeline_specs import LoadedPipelineSpecs


VALID_RUNTIME_STATUSES = {"planned_only", "ready_to_construct", "blocked", "unavailable"}
_PROVIDER_API_MODES = {
    "openai-codex": "codex_responses",
    "openai": "codex_responses",
    "xai-oauth": "codex_responses",
    "openrouter": "chat_completions",
    "nous": "chat_completions",
    "anthropic": "anthropic_messages",
    "bedrock": "bedrock_converse",
}


@dataclass(frozen=True)
class RuntimeFactoryErrorDetail:
    code: str
    message: str
    field_path: str = ""
    file_path: str = ""


class RuntimeFactoryError(Exception):
    def __init__(self, errors: list[RuntimeFactoryErrorDetail]):
        self.errors = errors
        super().__init__("; ".join(error.message for error in errors))


class RuntimeFactoryStatus(str, Enum):
    PLAN_ONLY = "plan_only"
    BLOCKED = "blocked"


class ControlledRuntimeClientProtocol(Protocol):
    def __call__(self, runtime: "ControlledRuntime", payload: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class RealProviderRequest:
    runtime: "ControlledRuntime"
    provider: str
    model: str
    input_messages: list[dict[str, Any]]
    request_metadata: dict[str, Any]


class RealProviderClientProtocol(Protocol):
    def __call__(self, request: RealProviderRequest) -> Mapping[str, Any]:
        ...


class RealProviderClientFactory(Protocol):
    def __call__(self, runtime: "ControlledRuntime") -> RealProviderClientProtocol:
        ...


@dataclass(frozen=True)
class RuntimeToolPolicy:
    read: list[str] = field(default_factory=list)
    write: list[str] = field(default_factory=list)
    execute: list[str] = field(default_factory=list)
    gated: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "read": list(self.read),
            "write": list(self.write),
            "execute": list(self.execute),
            "gated": list(self.gated),
            "forbidden": list(self.forbidden),
        }


@dataclass(frozen=True)
class RuntimeEnvironmentPolicy:
    working_directory_policy: str
    secrets_env_access: str
    can_mutate_files: bool
    can_restart_services: Any
    can_commit: Any
    can_push: Any

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "working_directory_policy": self.working_directory_policy,
            "secrets_env_access": self.secrets_env_access,
            "can_mutate_files": self.can_mutate_files,
            "can_restart_services": self.can_restart_services,
            "can_commit": self.can_commit,
            "can_push": self.can_push,
        }


@dataclass(frozen=True)
class RuntimeFactoryRequest:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    subagent_id: str
    role_id: str
    execution_mode: str
    dry_run: bool = True


@dataclass(frozen=True)
class RuntimeFactoryPlan:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    subagent_id: str
    role_id: str
    status: RuntimeFactoryStatus
    execution_mode: str
    dry_run: bool
    provider: str | None
    model: str | None
    model_class: str | None
    system_prompt_source_id: str | None
    system_prompt_path: str | None
    tool_set: list[str]
    tool_policy: RuntimeToolPolicy
    environment_policy: RuntimeEnvironmentPolicy
    context_window_policy: dict[str, Any]
    prompt_cache_policy: dict[str, Any]
    logging_hooks_policy: dict[str, Any]
    token_accounting_policy: dict[str, Any]
    safety_gates: dict[str, Any]
    errors: list[RuntimeFactoryErrorDetail] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_session_id": self.pipeline_session_id,
            "trace_id": self.trace_id,
            "pipeline_id": self.pipeline_id,
            "subagent_id": self.subagent_id,
            "role_id": self.role_id,
            "status": self.status.value,
            "execution_mode": self.execution_mode,
            "dry_run": self.dry_run,
            "provider": self.provider,
            "model": self.model,
            "model_class": self.model_class,
            "system_prompt_source_id": self.system_prompt_source_id,
            "system_prompt_path": self.system_prompt_path,
            "tool_set": list(self.tool_set),
            "tool_policy": self.tool_policy.to_safe_dict(),
            "environment_policy": self.environment_policy.to_safe_dict(),
            "context_window_policy": dict(self.context_window_policy),
            "prompt_cache_policy": dict(self.prompt_cache_policy),
            "logging_hooks_policy": dict(self.logging_hooks_policy),
            "token_accounting_policy": dict(self.token_accounting_policy),
            "safety_gates": dict(self.safety_gates),
            "errors": [
                {
                    "code": error.code,
                    "message": error.message,
                    "field_path": error.field_path,
                    "file_path": error.file_path,
                }
                for error in self.errors
            ],
        }


@dataclass(frozen=True)
class ControlledRuntime:
    pipeline_session_id: str
    trace_id: str
    pipeline_id: str
    subagent_id: str
    role_id: str
    runtime_status: str
    execution_mode: str
    dry_run: bool
    provider: str | None
    model: str | None
    model_class: str | None
    system_prompt_source_id: str | None
    system_prompt_path: str | None
    tool_set: list[str]
    tool_policy: RuntimeToolPolicy
    environment_policy: RuntimeEnvironmentPolicy
    context_window_policy: dict[str, Any]
    prompt_cache_policy: dict[str, Any]
    logging_hooks_policy: dict[str, Any]
    token_accounting_policy: dict[str, Any]
    safety_gates: dict[str, Any]
    runtime_mode: str = "fake"
    real_provider_allowed: bool = False
    provider_policy_status: str = "not_requested"
    working_directory: str | None = None
    invocation_client: ControlledRuntimeClientProtocol | None = field(default=None, repr=False, compare=False)
    errors: list[RuntimeFactoryErrorDetail] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "pipeline_session_id": self.pipeline_session_id,
            "trace_id": self.trace_id,
            "pipeline_id": self.pipeline_id,
            "subagent_id": self.subagent_id,
            "role_id": self.role_id,
            "runtime_status": self.runtime_status,
            "execution_mode": self.execution_mode,
            "dry_run": self.dry_run,
            "provider": self.provider,
            "model": self.model,
            "model_class": self.model_class,
            "system_prompt_source_id": self.system_prompt_source_id,
            "system_prompt_path": self.system_prompt_path,
            "tool_set": list(self.tool_set),
            "tool_policy": self.tool_policy.to_safe_dict(),
            "environment_policy": self.environment_policy.to_safe_dict(),
            "context_window_policy": dict(self.context_window_policy),
            "prompt_cache_policy": dict(self.prompt_cache_policy),
            "logging_hooks_policy": dict(self.logging_hooks_policy),
            "token_accounting_policy": dict(self.token_accounting_policy),
            "safety_gates": dict(self.safety_gates),
            "runtime_mode": self.runtime_mode,
            "real_provider_allowed": self.real_provider_allowed,
            "provider_policy_status": self.provider_policy_status,
            "working_directory": self.working_directory,
            "errors": [
                {
                    "code": error.code,
                    "message": error.message,
                    "field_path": error.field_path,
                    "file_path": error.file_path,
                }
                for error in self.errors
            ],
        }


def build_runtime_factory_plan(
    *,
    session: Any,
    planned_step: Any,
    subagent_spec: dict[str, Any] | None,
    config: dict[str, Any] | None,
) -> RuntimeFactoryPlan:
    """Build a metadata-only runtime contract; never constructs clients or runs tools."""

    errors: list[RuntimeFactoryErrorDetail] = []
    subagent_id = str(getattr(planned_step, "subagent_id", "") or "")
    role_id = str(getattr(planned_step, "step_kind", "") or "")
    pipeline_session_id = str(getattr(session, "pipeline_session_id", "") or "")
    trace_id = str(getattr(session, "trace_id", "") or pipeline_session_id)
    pipeline_id = str(getattr(session, "pipeline_id", "") or "")

    if not isinstance(subagent_spec, dict):
        errors.append(
            RuntimeFactoryErrorDetail(
                code="unknown_subagent",
                message=f"Unknown subagent_id {subagent_id!r}",
                field_path="subagent_id",
            )
        )
        return _runtime_contract_blocked_plan(
            pipeline_session_id=pipeline_session_id,
            trace_id=trace_id,
            pipeline_id=pipeline_id,
            subagent_id=subagent_id,
            role_id=role_id,
            errors=errors,
        )

    spec_id = _contract_str(subagent_spec.get("id"))
    if spec_id != subagent_id:
        errors.append(
            RuntimeFactoryErrorDetail(
                code="subagent_spec_mismatch",
                message=f"Planned subagent {subagent_id!r} does not match spec id {spec_id!r}",
                field_path="id",
            )
        )

    model_choice = _contract_mapping(_contract_nested(subagent_spec, ("models", "default")))
    provider = _contract_str(model_choice.get("provider"))
    model = _contract_str(model_choice.get("model"))
    model_class = _contract_str(model_choice.get("class"))
    if not provider:
        errors.append(RuntimeFactoryErrorDetail(code="missing_model_field", message="Subagent spec must define models.default.provider", field_path="models.default.provider"))
    if not model:
        errors.append(RuntimeFactoryErrorDetail(code="missing_model_field", message="Subagent spec must define models.default.model", field_path="models.default.model"))

    prompt_path = _contract_str(_contract_nested(subagent_spec, ("system_prompt", "path")))
    if not prompt_path:
        errors.append(RuntimeFactoryErrorDetail(code="missing_prompt_path", message="Subagent spec must define system_prompt.path", field_path="system_prompt.path"))

    tool_policy = _build_runtime_tool_policy(subagent_spec, errors)
    permissions = _contract_mapping(subagent_spec.get("permissions"))
    environment_policy = RuntimeEnvironmentPolicy(
        working_directory_policy="pipeline_session_workspace",
        secrets_env_access="not_granted",
        can_mutate_files=bool(permissions.get("can_mutate_files")),
        can_restart_services=permissions.get("can_restart_services", False),
        can_commit=permissions.get("can_commit", False),
        can_push=permissions.get("can_push", False),
    )
    observability = _contract_mapping(subagent_spec.get("observability"))

    status = RuntimeFactoryStatus.BLOCKED if errors else RuntimeFactoryStatus.PLAN_ONLY
    return RuntimeFactoryPlan(
        pipeline_session_id=pipeline_session_id,
        trace_id=trace_id,
        pipeline_id=pipeline_id,
        subagent_id=subagent_id,
        role_id=role_id,
        status=status,
        execution_mode="observe_plan_only",
        dry_run=True,
        provider=provider,
        model=model,
        model_class=model_class,
        system_prompt_source_id=f"prompt:{subagent_id}" if prompt_path else None,
        system_prompt_path=prompt_path,
        tool_set=tool_policy.read + tool_policy.write + tool_policy.execute,
        tool_policy=tool_policy,
        environment_policy=environment_policy,
        context_window_policy={"source": "not_wired", "mode": "metadata_only"},
        prompt_cache_policy=_contract_mapping(subagent_spec.get("prompt_cache_policy")),
        logging_hooks_policy={
            "provider_model_selection": bool(observability.get("log_selected_provider_model")),
            "provider_model_actual": bool(observability.get("log_actual_provider_model")),
            "tool_calls": bool(observability.get("log_tool_calls")),
        },
        token_accounting_policy={"token_usage": bool(observability.get("log_token_usage"))},
        safety_gates={
            "failure_policy": _contract_mapping(subagent_spec.get("failure_policy")),
            "pipeline_loop_policy": _contract_mapping((config or {}).get("loop_policy")),
            "mode": "fail_closed",
        },
        errors=errors,
    )


def build_controlled_runtime(
    *,
    plan: RuntimeFactoryPlan,
    invocation_client: ControlledRuntimeClientProtocol | None,
    request_real_provider_execution: bool = False,
    allow_real_provider_execution: bool = False,
    allowed_real_providers: tuple[str, ...] = (),
    allowed_real_models: tuple[str, ...] = (),
    allowed_real_providers_by_role: dict[str, tuple[str, ...]] | None = None,
    allowed_real_models_by_role: dict[str, tuple[str, ...]] | None = None,
    allowed_real_providers_by_subagent: dict[str, tuple[str, ...]] | None = None,
    allowed_real_models_by_subagent: dict[str, tuple[str, ...]] | None = None,
    real_provider_client_factory: RealProviderClientFactory | None = None,
    working_directory: str | None = None,
) -> ControlledRuntime:
    errors = list(plan.errors)
    provider = plan.provider
    model = plan.model
    model_class = plan.model_class
    provider_policy_status = "not_requested"
    real_provider_allowed = False
    runtime_mode = "fake"
    selected_invocation_client = invocation_client

    ready = plan.status == RuntimeFactoryStatus.PLAN_ONLY and bool(provider) and bool(model)
    if request_real_provider_execution:
        runtime_mode = "blocked"
        provider_policy_status = "blocked"
        selected_invocation_client = None
        if not allow_real_provider_execution:
            errors.append(
                RuntimeFactoryErrorDetail(
                    code="real_provider_execution_disabled",
                    message="Real provider execution requires an explicit allow_real_provider_execution gate",
                    field_path="allow_real_provider_execution",
                )
            )
            ready = False
        elif provider not in allowed_real_providers:
            errors.append(
                RuntimeFactoryErrorDetail(
                    code="real_provider_provider_not_allowed",
                    message=f"Provider {provider!r} is not allowlisted for real provider execution",
                    field_path="allowed_real_providers",
                )
            )
            ready = False
        elif model not in allowed_real_models:
            errors.append(
                RuntimeFactoryErrorDetail(
                    code="real_provider_model_not_allowed",
                    message=f"Model {model!r} is not allowlisted for real provider execution",
                    field_path="allowed_real_models",
                )
            )
            ready = False
        elif not _policy_allows_identity(
            policy_by_identity=allowed_real_providers_by_role,
            identity=plan.role_id,
            candidate=provider,
            missing_code="real_provider_role_policy_missing",
            mismatch_code="real_provider_role_provider_not_allowed",
            field_path="allowed_real_providers_by_role",
            errors=errors,
        ):
            ready = False
        elif not _policy_allows_identity(
            policy_by_identity=allowed_real_models_by_role,
            identity=plan.role_id,
            candidate=model,
            missing_code="real_provider_role_policy_missing",
            mismatch_code="real_provider_role_model_not_allowed",
            field_path="allowed_real_models_by_role",
            errors=errors,
        ):
            ready = False
        elif not _policy_allows_identity(
            policy_by_identity=allowed_real_providers_by_subagent,
            identity=plan.subagent_id,
            candidate=provider,
            missing_code="real_provider_subagent_policy_missing",
            mismatch_code="real_provider_subagent_provider_not_allowed",
            field_path="allowed_real_providers_by_subagent",
            errors=errors,
        ):
            ready = False
        elif not _policy_allows_identity(
            policy_by_identity=allowed_real_models_by_subagent,
            identity=plan.subagent_id,
            candidate=model,
            missing_code="real_provider_subagent_policy_missing",
            mismatch_code="real_provider_subagent_model_not_allowed",
            field_path="allowed_real_models_by_subagent",
            errors=errors,
        ):
            ready = False
        elif real_provider_client_factory is None:
            errors.append(
                RuntimeFactoryErrorDetail(
                    code="real_provider_client_factory_missing",
                    message="Real provider execution requires an injected client factory",
                    field_path="real_provider_client_factory",
                )
            )
            ready = False
        elif not callable(real_provider_client_factory):
            errors.append(
                RuntimeFactoryErrorDetail(
                    code="real_provider_client_factory_invalid",
                    message="Real provider execution requires a callable injected client factory",
                    field_path="real_provider_client_factory",
                )
            )
            ready = False
        else:
            real_provider_allowed = True
            provider_policy_status = "allowed"
            runtime_mode = "real_provider"
    elif invocation_client is None:
        runtime_mode = "blocked"
        ready = False
        errors.append(
            RuntimeFactoryErrorDetail(
                code="controlled_runtime_invocation_client_missing",
                message="Controlled runtime requires an injected invocation client",
                field_path="invocation_client",
            )
        )

    real_provider_allowed = bool(ready and provider_policy_status == "allowed")

    provider = provider if ready else None
    model = model if ready else None
    model_class = model_class if ready else None
    runtime = ControlledRuntime(
        pipeline_session_id=plan.pipeline_session_id,
        trace_id=plan.trace_id,
        pipeline_id=plan.pipeline_id,
        subagent_id=plan.subagent_id,
        role_id=plan.role_id,
        runtime_status="ready" if ready else "blocked",
        execution_mode=plan.execution_mode,
        dry_run=plan.dry_run,
        provider=provider,
        model=model,
        model_class=model_class,
        system_prompt_source_id=plan.system_prompt_source_id,
        system_prompt_path=plan.system_prompt_path,
        tool_set=list(plan.tool_set),
        tool_policy=plan.tool_policy,
        environment_policy=plan.environment_policy,
        context_window_policy=dict(plan.context_window_policy),
        prompt_cache_policy=dict(plan.prompt_cache_policy),
        logging_hooks_policy=dict(plan.logging_hooks_policy),
        token_accounting_policy=dict(plan.token_accounting_policy),
        safety_gates=dict(plan.safety_gates),
        runtime_mode=runtime_mode if ready else "blocked",
        real_provider_allowed=real_provider_allowed,
        provider_policy_status=provider_policy_status,
        working_directory=working_directory,
        invocation_client=None,
        errors=errors,
    )
    if ready and real_provider_allowed and real_provider_client_factory is not None:
        selected_invocation_client = _build_real_provider_invocation_client(runtime, real_provider_client_factory)
    runtime = ControlledRuntime(
        **{
            **runtime.__dict__,
            "invocation_client": selected_invocation_client,
        }
    )
    return runtime


def _build_real_provider_invocation_client(
    runtime: ControlledRuntime,
    real_provider_client_factory: RealProviderClientFactory,
) -> ControlledRuntimeClientProtocol:
    def _invoke(_runtime: ControlledRuntime, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        input_messages = payload.get("input_messages")
        normalized_messages = _validated_input_messages(input_messages)
        client = real_provider_client_factory(runtime)
        request = RealProviderRequest(
            runtime=runtime,
            provider=str(runtime.provider or ""),
            model=str(runtime.model or ""),
            input_messages=normalized_messages,
            request_metadata=dict(payload.get("request_metadata") or {}),
        )
        return client(request)

    return _invoke


def _validated_input_messages(value: Any) -> list[dict[str, Any]]:
    messages = list(value or [])
    normalized: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, Mapping):
            raise ValueError("real_provider_invalid_input_messages")
        normalized.append(dict(item))
    return normalized


def _policy_allows_identity(
    *,
    policy_by_identity: dict[str, tuple[str, ...]] | None,
    identity: str,
    candidate: str | None,
    missing_code: str,
    mismatch_code: str,
    field_path: str,
    errors: list[RuntimeFactoryErrorDetail],
) -> bool:
    if policy_by_identity is None:
        return True
    allowed = tuple(policy_by_identity.get(identity) or ())
    if not allowed:
        errors.append(
            RuntimeFactoryErrorDetail(
                code=missing_code,
                message=f"Real provider execution requires explicit policy for {identity!r}",
                field_path=field_path,
            )
        )
        return False
    if candidate not in allowed:
        errors.append(
            RuntimeFactoryErrorDetail(
                code=mismatch_code,
                message=f"Requested runtime value is not allowed for {identity!r}",
                field_path=field_path,
            )
        )
        return False
    return True


def _runtime_contract_blocked_plan(
    *,
    pipeline_session_id: str,
    trace_id: str,
    pipeline_id: str,
    subagent_id: str,
    role_id: str,
    errors: list[RuntimeFactoryErrorDetail],
) -> RuntimeFactoryPlan:
    return RuntimeFactoryPlan(
        pipeline_session_id=pipeline_session_id,
        trace_id=trace_id,
        pipeline_id=pipeline_id,
        subagent_id=subagent_id,
        role_id=role_id,
        status=RuntimeFactoryStatus.BLOCKED,
        execution_mode="observe_plan_only",
        dry_run=True,
        provider=None,
        model=None,
        model_class=None,
        system_prompt_source_id=None,
        system_prompt_path=None,
        tool_set=[],
        tool_policy=RuntimeToolPolicy(),
        environment_policy=RuntimeEnvironmentPolicy(
            working_directory_policy="pipeline_session_workspace",
            secrets_env_access="not_granted",
            can_mutate_files=False,
            can_restart_services=False,
            can_commit=False,
            can_push=False,
        ),
        context_window_policy={"source": "not_wired", "mode": "metadata_only"},
        prompt_cache_policy={},
        logging_hooks_policy={},
        token_accounting_policy={},
        safety_gates={"mode": "fail_closed"},
        errors=errors,
    )


def _build_runtime_tool_policy(
    subagent_spec: dict[str, Any],
    errors: list[RuntimeFactoryErrorDetail],
) -> RuntimeToolPolicy:
    tools = _contract_mapping(subagent_spec.get("tools"))
    buckets: dict[str, list[str]] = {}
    for key in ("read", "write", "execute", "gated", "forbidden"):
        value = tools.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            errors.append(
                RuntimeFactoryErrorDetail(
                    code="malformed_tool_permissions",
                    message=f"tools.{key} must be a list of strings",
                    field_path=f"tools.{key}",
                )
            )
            buckets[key] = []
            continue
        buckets[key] = list(value)
    return RuntimeToolPolicy(
        read=buckets["read"],
        write=buckets["write"],
        execute=buckets["execute"],
        gated=buckets["gated"],
        forbidden=buckets["forbidden"],
    )


def _contract_nested(container: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = container
    for part in path:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _contract_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _contract_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


@dataclass(frozen=True)
class PromptArtifactRecord:
    path: str
    exists: bool
    sha256: str | None
    size_bytes: int
    artifact_id: str | None
    full_text_loaded: bool = False

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "sha256": self.sha256,
            "artifact_id": self.artifact_id,
            "size_bytes": self.size_bytes,
            "full_text_loaded": self.full_text_loaded,
        }


@dataclass(frozen=True)
class ToolPermissionPlan:
    read: list[str] = field(default_factory=list)
    write: list[str] = field(default_factory=list)
    execute: list[str] = field(default_factory=list)
    gated: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    unknown_permissions: list[str] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "read": list(self.read),
            "write": list(self.write),
            "execute": list(self.execute),
            "gated": list(self.gated),
            "forbidden": list(self.forbidden),
            "unknown_permissions": list(self.unknown_permissions),
        }


@dataclass(frozen=True)
class RuntimeSelectionRecord:
    selected_provider: str
    selected_model: str
    selected_model_class: str | None
    fallback_mode: str | None
    fallback_reason: str | None
    requested_model_class: str | None
    available_model_classes: list[str]
    escalation_allowed: bool
    escalation_targets: list[str]

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "selected_provider": self.selected_provider,
            "selected_model": self.selected_model,
            "selected_model_class": self.selected_model_class,
            "fallback_mode": self.fallback_mode,
            "fallback_reason": self.fallback_reason,
            "requested_model_class": self.requested_model_class,
            "available_model_classes": list(self.available_model_classes),
            "escalation_allowed": self.escalation_allowed,
            "escalation_targets": list(self.escalation_targets),
        }


@dataclass(frozen=True)
class FallbackPolicyRecord:
    mode: str | None
    reason: str | None

    def to_safe_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "reason": self.reason}


@dataclass(frozen=True)
class RuntimeBuildRequest:
    loaded_specs: LoadedPipelineSpecs
    subagent_id: str
    pipeline_session_id: str
    invocation_id: str | None = None
    platform_context: dict[str, Any] | None = None
    session_context: dict[str, Any] | None = None
    requested_model_class: str | None = None
    current_session_provider: str | None = None
    current_session_model: str | None = None


@dataclass(frozen=True)
class RuntimeBuildResult:
    subagent_id: str
    pipeline_session_id: str
    invocation_id: str | None
    actual_runtime_status: str
    selection: RuntimeSelectionRecord | None
    prompt: PromptArtifactRecord | None
    tool_permission_plan: ToolPermissionPlan | None
    constructor_provider: str | None
    constructor_model: str | None
    constructor_api_mode: str | None
    constructor_base_url: str | None
    current_session_provider: str | None
    current_session_model: str | None
    selected_runtime_differs_from_session_default: bool
    fallback_policy: FallbackPolicyRecord | None
    runtime_mode: str = "fake"
    real_provider_allowed: bool = False
    provider_policy_status: str = "not_requested"
    errors: list[RuntimeFactoryErrorDetail] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "subagent_id": self.subagent_id,
            "pipeline_session_id": self.pipeline_session_id,
            "invocation_id": self.invocation_id,
            "actual_runtime_status": self.actual_runtime_status,
            "selected_provider": self.selection.selected_provider if self.selection else None,
            "selected_model": self.selection.selected_model if self.selection else None,
            "selected_model_class": self.selection.selected_model_class if self.selection else None,
            "constructor_provider": self.constructor_provider,
            "constructor_model": self.constructor_model,
            "constructor_api_mode": self.constructor_api_mode,
            "constructor_base_url": self.constructor_base_url,
            "runtime_mode": self.runtime_mode,
            "real_provider_allowed": self.real_provider_allowed,
            "provider_policy_status": self.provider_policy_status,
            "session_default_provider": self.current_session_provider,
            "session_default_model": self.current_session_model,
            "session_default_mismatch": self.selected_runtime_differs_from_session_default,
            "prompt": self.prompt.to_safe_dict() if self.prompt else None,
            "tool_permission_plan": self.tool_permission_plan.to_safe_dict() if self.tool_permission_plan else None,
            "fallback_policy": self.fallback_policy.to_safe_dict() if self.fallback_policy else None,
            "errors": [
                {
                    "code": error.code,
                    "message": error.message,
                    "field_path": error.field_path,
                    "file_path": error.file_path,
                }
                for error in self.errors
            ],
        }

    def to_aiagent_kwargs(self) -> dict[str, Any]:
        if self.actual_runtime_status != "ready_to_construct":
            raise RuntimeError("Runtime construction inputs are not ready")
        if not self.constructor_provider or not self.constructor_model:
            raise RuntimeError("Runtime construction inputs are incomplete")

        kwargs = {
            "provider": self.constructor_provider,
            "model": self.constructor_model,
        }
        if self.constructor_api_mode:
            kwargs["api_mode"] = self.constructor_api_mode
        if self.constructor_base_url:
            kwargs["base_url"] = self.constructor_base_url
        return kwargs


class RuntimeFactory:
    def __init__(self, repo_root: Path | str | None = None):
        self.repo_root = Path(repo_root) if repo_root is not None else None

    def build(self, request: RuntimeBuildRequest) -> RuntimeBuildResult:
        errors: list[RuntimeFactoryErrorDetail] = []
        spec = request.loaded_specs.subagent_specs.get(request.subagent_id)
        if spec is None:
            return self._blocked_result(
                request,
                [RuntimeFactoryErrorDetail(code="unknown_subagent", field_path="subagent_id", message=f"Unknown subagent_id {request.subagent_id!r}")],
            )

        repo_root = self.repo_root or request.loaded_specs.repo_root
        model_choice = self._resolve_model_choice(spec, request, errors)
        prompt = self._build_prompt_record(spec, repo_root, errors)
        tool_permission_plan = self._build_tool_permission_plan(spec, errors)
        fallback_policy = FallbackPolicyRecord(
            mode=self._nested_str(spec, ("models", "fallback", "mode")),
            reason=self._nested_str(spec, ("models", "fallback", "reason")),
        )

        if errors:
            return self._blocked_result(request, errors)

        assert model_choice is not None
        selection = RuntimeSelectionRecord(
            selected_provider=str(model_choice.get("provider")),
            selected_model=str(model_choice.get("model")),
            selected_model_class=self._string_or_none(model_choice.get("class")),
            fallback_mode=fallback_policy.mode,
            fallback_reason=fallback_policy.reason,
            requested_model_class=self._string_or_none(request.requested_model_class),
            available_model_classes=self._available_model_classes(spec),
            escalation_allowed=bool(self._nested(spec, ("models", "escalation", "allowed"))),
            escalation_targets=self._escalation_target_classes(spec),
        )
        differs = bool(
            request.current_session_provider
            and request.current_session_model
            and (
                request.current_session_provider != selection.selected_provider
                or request.current_session_model != selection.selected_model
            )
        )
        constructor_provider = selection.selected_provider
        constructor_model = selection.selected_model
        constructor_api_mode = self._constructor_api_mode(constructor_provider)
        constructor_base_url = None
        status = "ready_to_construct" if constructor_provider and constructor_model else "planned_only"

        return RuntimeBuildResult(
            subagent_id=request.subagent_id,
            pipeline_session_id=request.pipeline_session_id,
            invocation_id=request.invocation_id,
            actual_runtime_status=status,
            selection=selection,
            prompt=prompt,
            tool_permission_plan=tool_permission_plan,
            constructor_provider=constructor_provider,
            constructor_model=constructor_model,
            constructor_api_mode=constructor_api_mode,
            constructor_base_url=constructor_base_url,
            runtime_mode="bridge_executor" if status == "ready_to_construct" else "fake",
            real_provider_allowed=bool(status == "ready_to_construct" and constructor_provider and constructor_model),
            provider_policy_status="ready_to_construct" if status == "ready_to_construct" else "not_requested",
            current_session_provider=request.current_session_provider,
            current_session_model=request.current_session_model,
            selected_runtime_differs_from_session_default=differs,
            fallback_policy=fallback_policy,
            errors=[],
        )

    def _resolve_model_choice(
        self,
        spec: dict[str, Any],
        request: RuntimeBuildRequest,
        errors: list[RuntimeFactoryErrorDetail],
    ) -> dict[str, Any] | None:
        default_choice = self._nested(spec, ("models", "default"))
        if not isinstance(default_choice, dict):
            errors.append(RuntimeFactoryErrorDetail(code="missing_default_model", field_path="models.default", message="Subagent spec must define models.default"))
            return None

        requested_class = self._string_or_none(request.requested_model_class)
        if not requested_class:
            self._require_model_fields(default_choice, "models.default", errors)
            return default_choice

        allowed_models = self._nested(spec, ("models", "allowed"))
        if not isinstance(allowed_models, list):
            errors.append(RuntimeFactoryErrorDetail(code="malformed_allowed_models", field_path="models.allowed", message="Subagent spec must define models.allowed as a list"))
            return None

        for entry in allowed_models:
            if isinstance(entry, dict) and self._string_or_none(entry.get("class")) == requested_class:
                self._require_model_fields(entry, "models.allowed", errors)
                return entry

        errors.append(
            RuntimeFactoryErrorDetail(
                code="unsupported_model_class",
                field_path="requested_model_class",
                message=f"Requested model class {requested_class!r} is not allowed for subagent {spec.get('id')!r}",
            )
        )
        return None

    def _build_prompt_record(
        self,
        spec: dict[str, Any],
        repo_root: Path,
        errors: list[RuntimeFactoryErrorDetail],
    ) -> PromptArtifactRecord | None:
        prompt_path = self._nested_str(spec, ("system_prompt", "path"))
        if not prompt_path:
            errors.append(RuntimeFactoryErrorDetail(code="missing_prompt_path", field_path="system_prompt.path", message="Subagent spec must define system_prompt.path"))
            return None
        prompt_file = repo_root / prompt_path
        if not prompt_file.exists():
            errors.append(
                RuntimeFactoryErrorDetail(
                    code="missing_prompt_file",
                    field_path="system_prompt.path",
                    file_path=prompt_path,
                    message="Referenced prompt file does not exist",
                )
            )
            return PromptArtifactRecord(path=prompt_path, exists=False, sha256=None, size_bytes=0, artifact_id=None)

        payload = prompt_file.read_bytes()
        prompt_hash = hashlib.sha256(payload).hexdigest()
        return PromptArtifactRecord(
            path=prompt_path,
            exists=True,
            sha256=prompt_hash,
            size_bytes=len(payload),
            artifact_id=prompt_hash,
            full_text_loaded=False,
        )

    def _build_tool_permission_plan(
        self,
        spec: dict[str, Any],
        errors: list[RuntimeFactoryErrorDetail],
    ) -> ToolPermissionPlan | None:
        tools = self._nested(spec, ("tools",))
        if not isinstance(tools, dict):
            errors.append(RuntimeFactoryErrorDetail(code="missing_tools", field_path="tools", message="Subagent spec must define tools"))
            return None

        buckets: dict[str, list[str]] = {}
        unknown_permissions: list[str] = []
        for key in ("read", "write", "execute", "gated", "forbidden"):
            value = tools.get(key, [])
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                errors.append(
                    RuntimeFactoryErrorDetail(
                        code="malformed_tool_permissions",
                        field_path=f"tools.{key}",
                        message=f"tools.{key} must be a list of strings",
                    )
                )
                return None
            buckets[key] = list(value)

        for key in tools:
            if key not in buckets:
                unknown_permissions.append(str(key))

        return ToolPermissionPlan(
            read=buckets["read"],
            write=buckets["write"],
            execute=buckets["execute"],
            gated=buckets["gated"],
            forbidden=buckets["forbidden"],
            unknown_permissions=unknown_permissions,
        )

    def _blocked_result(
        self,
        request: RuntimeBuildRequest,
        errors: list[RuntimeFactoryErrorDetail],
    ) -> RuntimeBuildResult:
        return RuntimeBuildResult(
            subagent_id=request.subagent_id,
            pipeline_session_id=request.pipeline_session_id,
            invocation_id=request.invocation_id,
            actual_runtime_status="blocked",
            selection=None,
            prompt=None,
            tool_permission_plan=None,
            constructor_provider=None,
            constructor_model=None,
            constructor_api_mode=None,
            constructor_base_url=None,
            runtime_mode="blocked",
            real_provider_allowed=False,
            provider_policy_status="blocked",
            current_session_provider=request.current_session_provider,
            current_session_model=request.current_session_model,
            selected_runtime_differs_from_session_default=False,
            fallback_policy=None,
            errors=errors,
        )

    @staticmethod
    def _require_model_fields(choice: dict[str, Any], field_path: str, errors: list[RuntimeFactoryErrorDetail]) -> None:
        for key in ("provider", "model"):
            value = choice.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(
                    RuntimeFactoryErrorDetail(
                        code="missing_model_field",
                        field_path=f"{field_path}.{key}",
                        message=f"Subagent spec must define {field_path}.{key}",
                    )
                )

    @staticmethod
    def _available_model_classes(spec: dict[str, Any]) -> list[str]:
        classes: list[str] = []
        allowed_models = spec.get("models", {}).get("allowed", [])
        if not isinstance(allowed_models, list):
            return classes
        for entry in allowed_models:
            if isinstance(entry, dict):
                model_class = entry.get("class")
                if isinstance(model_class, str) and model_class not in classes:
                    classes.append(model_class)
        return classes

    @staticmethod
    def _escalation_target_classes(spec: dict[str, Any]) -> list[str]:
        targets: list[str] = []
        rules = spec.get("models", {}).get("escalation", {}).get("rules", [])
        if not isinstance(rules, list):
            return targets
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            target = rule.get("escalate_to")
            if not isinstance(target, dict):
                continue
            model_class = target.get("class")
            if isinstance(model_class, str) and model_class not in targets:
                targets.append(model_class)
        return targets

    @staticmethod
    def _constructor_api_mode(provider: str | None) -> str | None:
        normalized = (provider or "").strip().lower()
        return _PROVIDER_API_MODES.get(normalized)

    @staticmethod
    def _nested(container: dict[str, Any], path: tuple[str, ...]) -> Any:
        value: Any = container
        for part in path:
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    @classmethod
    def _nested_str(cls, container: dict[str, Any], path: tuple[str, ...]) -> str | None:
        value = cls._nested(container, path)
        return cls._string_or_none(value)

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
