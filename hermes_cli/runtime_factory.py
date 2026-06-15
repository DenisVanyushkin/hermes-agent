"""Import-light runtime build planning for pipeline subagents."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Any

from hermes_cli.pipeline_specs import LoadedPipelineSpecs


VALID_RUNTIME_STATUSES = {"planned_only", "ready_to_build", "blocked", "unavailable"}


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


@dataclass(frozen=True)
class PromptArtifactRecord:
    path: str
    exists: bool
    sha256: str | None
    size_bytes: int


@dataclass(frozen=True)
class ToolPermissionPlan:
    read: list[str] = field(default_factory=list)
    write: list[str] = field(default_factory=list)
    execute: list[str] = field(default_factory=list)
    gated: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    unknown_permissions: list[str] = field(default_factory=list)


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
    actual_provider: str | None
    actual_model: str | None
    current_session_provider: str | None
    current_session_model: str | None
    selected_runtime_differs_from_session_default: bool
    errors: list[RuntimeFactoryErrorDetail] = field(default_factory=list)


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

        if errors:
            return self._blocked_result(request, errors)

        assert model_choice is not None
        selection = RuntimeSelectionRecord(
            selected_provider=str(model_choice.get("provider")),
            selected_model=str(model_choice.get("model")),
            selected_model_class=self._string_or_none(model_choice.get("class")),
            fallback_mode=self._nested_str(spec, ("models", "fallback", "mode")),
            fallback_reason=self._nested_str(spec, ("models", "fallback", "reason")),
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
        return RuntimeBuildResult(
            subagent_id=request.subagent_id,
            pipeline_session_id=request.pipeline_session_id,
            invocation_id=request.invocation_id,
            actual_runtime_status="planned_only",
            selection=selection,
            prompt=prompt,
            tool_permission_plan=tool_permission_plan,
            actual_provider=None,
            actual_model=None,
            current_session_provider=request.current_session_provider,
            current_session_model=request.current_session_model,
            selected_runtime_differs_from_session_default=differs,
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
            return PromptArtifactRecord(path=prompt_path, exists=False, sha256=None, size_bytes=0)

        payload = prompt_file.read_bytes()
        return PromptArtifactRecord(
            path=prompt_path,
            exists=True,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
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
            actual_provider=None,
            actual_model=None,
            current_session_provider=request.current_session_provider,
            current_session_model=request.current_session_model,
            selected_runtime_differs_from_session_default=False,
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
