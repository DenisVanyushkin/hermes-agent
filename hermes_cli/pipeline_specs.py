"""Read-only loaders and validators for Hermes pipeline architecture specs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY_PATH = REPO_ROOT / "config" / "pipelines" / "registry.yaml"
ROUTER_SPEC_PATH = Path("config/subagents/hermes_pipeline_router.yaml")

VALID_ROUTER_STATUSES = {
    "selected",
    "no_specialized_pipeline",
    "needs_clarification",
    "blocked_by_policy",
    "routing_failed",
}
REQUIRED_REPORTING_FIELDS = {
    "selected_provider_model_per_invocation",
    "actual_provider_model_per_invocation",
}


@dataclass(frozen=True)
class SpecValidationErrorDetail:
    file_path: str
    field_path: str
    message: str
    severity: str = "error"


class PipelineSpecValidationError(Exception):
    """Raised when pipeline architecture specs fail validation."""

    def __init__(self, errors: list[SpecValidationErrorDetail]):
        self.errors = errors
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        return "; ".join(
            f"{error.file_path}:{error.field_path or '<root>'}: {error.message}"
            for error in self.errors
        )


@dataclass(frozen=True)
class LoadedPipelineSpecs:
    repo_root: Path
    registry_path: Path
    registry: dict[str, Any]
    pipeline_specs: dict[str, dict[str, Any]]
    subagent_specs: dict[str, dict[str, Any]]


def load_pipeline_specs(
    repo_root: Path | str | None = None,
    registry_path: Path | str | None = None,
) -> LoadedPipelineSpecs:
    repo = Path(repo_root) if repo_root is not None else REPO_ROOT
    registry_file = Path(registry_path) if registry_path is not None else Path("config/pipelines/registry.yaml")
    if not registry_file.is_absolute():
        registry_file = repo / registry_file

    errors: list[SpecValidationErrorDetail] = []
    registry = _load_yaml_mapping(registry_file, repo, errors)
    registry_entries = _expect_list(
        registry,
        key="registry",
        file_path=registry_file,
        repo_root=repo,
        errors=errors,
    )

    router_spec = _load_yaml_mapping(repo / ROUTER_SPEC_PATH, repo, errors)
    router_statuses = set(_nested_list(router_spec, ("output_schema", "status", "enum")))
    if router_statuses:
        unknown_router_statuses = router_statuses - VALID_ROUTER_STATUSES
        for status in sorted(unknown_router_statuses):
            errors.append(_error(ROUTER_SPEC_PATH, "output_schema.status.enum", f"Unknown router status {status!r}"))

    pipeline_specs_by_registry_id: dict[str, dict[str, Any]] = {}
    pipeline_id_to_path: dict[str, Path] = {}
    loaded_subagent_paths: set[Path] = {ROUTER_SPEC_PATH}

    default_registry_entry_found = False

    for index, entry in enumerate(registry_entries):
        field_prefix = f"registry[{index}]"
        if not isinstance(entry, dict):
            errors.append(_error(registry_file, field_prefix, "Registry entry must be a mapping"))
            continue

        registry_id = entry.get("id")
        if isinstance(registry_id, str) and registry_id == "default_conversation_pipeline":
            default_registry_entry_found = True

        config_path = entry.get("config_path")
        if not isinstance(config_path, str) or not config_path:
            errors.append(_error(registry_file, f"{field_prefix}.config_path", "Registry entry must define config_path"))
            continue

        pipeline_path = repo / config_path
        if not pipeline_path.exists():
            errors.append(_error(config_path, "config_path", "Referenced pipeline spec does not exist"))
            continue

        pipeline_spec = _load_yaml_mapping(pipeline_path, repo, errors)
        pipeline_id = pipeline_spec.get("id")
        if registry_id != pipeline_id:
            errors.append(
                _error(
                    config_path,
                    "id",
                    f"Pipeline spec id {pipeline_id!r} does not match registry id {registry_id!r}",
                )
            )

        if isinstance(registry_id, str):
            if registry_id in pipeline_specs_by_registry_id:
                errors.append(_error(registry_file, f"{field_prefix}.id", f"Duplicate pipeline id {registry_id!r}"))
            else:
                pipeline_specs_by_registry_id[registry_id] = pipeline_spec

        if isinstance(pipeline_id, str):
            existing_path = pipeline_id_to_path.get(pipeline_id)
            if existing_path is not None:
                errors.append(_error(config_path, "id", f"Duplicate pipeline spec id {pipeline_id!r}"))
            else:
                pipeline_id_to_path[pipeline_id] = Path(config_path)

        for subagent_id in _collect_pipeline_subagent_ids(pipeline_spec):
            loaded_subagent_paths.add(Path("config/subagents") / f"{subagent_id}.yaml")

        _validate_registry_entry(
            entry=entry,
            pipeline_spec=pipeline_spec,
            router_statuses=router_statuses,
            file_path=registry_file,
            entry_prefix=field_prefix,
            errors=errors,
        )

    if not default_registry_entry_found:
        errors.append(_error(registry_file, "registry", "Registry must include default_conversation_pipeline"))

    subagent_specs: dict[str, dict[str, Any]] = {}
    subagent_id_to_path: dict[str, Path] = {}
    for subagent_rel_path in sorted(loaded_subagent_paths):
        subagent_path = repo / subagent_rel_path
        if not subagent_path.exists():
            errors.append(_error(subagent_rel_path, "id", "Referenced subagent spec does not exist"))
            continue
        subagent_spec = _load_yaml_mapping(subagent_path, repo, errors)
        subagent_id = subagent_spec.get("id")
        if not isinstance(subagent_id, str) or not subagent_id:
            errors.append(_error(subagent_rel_path, "id", "Subagent spec must define a non-empty id"))
            continue
        if subagent_id in subagent_specs:
            errors.append(_error(subagent_rel_path, "id", f"Duplicate subagent id {subagent_id!r}"))
        else:
            subagent_specs[subagent_id] = subagent_spec
        previous_path = subagent_id_to_path.get(subagent_id)
        if previous_path is not None:
            errors.append(_error(subagent_rel_path, "id", f"Duplicate subagent spec id {subagent_id!r}"))
        else:
            subagent_id_to_path[subagent_id] = subagent_rel_path

        prompt_path = _nested_str(subagent_spec, ("system_prompt", "path"))
        if prompt_path:
            prompt_file = repo / prompt_path
            if not prompt_file.exists():
                errors.append(_error(prompt_path, "system_prompt.path", "Referenced prompt file does not exist"))

    _validate_pipeline_semantics(
        repo_root=repo,
        pipelines=pipeline_specs_by_registry_id,
        subagents=subagent_specs,
        router_statuses=router_statuses,
        errors=errors,
    )

    if errors:
        raise PipelineSpecValidationError(errors)

    return LoadedPipelineSpecs(
        repo_root=repo,
        registry_path=registry_file,
        registry=registry,
        pipeline_specs=pipeline_specs_by_registry_id,
        subagent_specs=subagent_specs,
    )


def validate_pipeline_specs(
    repo_root: Path | str | None = None,
    registry_path: Path | str | None = None,
) -> list[SpecValidationErrorDetail]:
    try:
        load_pipeline_specs(repo_root=repo_root, registry_path=registry_path)
    except PipelineSpecValidationError as exc:
        return exc.errors
    return []


def _validate_registry_entry(
    entry: dict[str, Any],
    pipeline_spec: dict[str, Any],
    router_statuses: set[str],
    file_path: Path,
    entry_prefix: str,
    errors: list[SpecValidationErrorDetail],
) -> None:
    allowed_statuses = entry.get("allowed_router_statuses")
    if not isinstance(allowed_statuses, list) or not allowed_statuses:
        errors.append(_error(file_path, f"{entry_prefix}.allowed_router_statuses", "allowed_router_statuses must be a non-empty list"))
    else:
        for status in allowed_statuses:
            if status not in router_statuses:
                errors.append(
                    _error(
                        file_path,
                        f"{entry_prefix}.allowed_router_statuses",
                        f"Router status {status!r} is not declared by hermes_pipeline_router",
                    )
                )

    fallback_eligible = entry.get("fallback_eligible")
    if not isinstance(fallback_eligible, bool):
        errors.append(_error(file_path, f"{entry_prefix}.fallback_eligible", "fallback_eligible must be a boolean"))
    elif fallback_eligible and "routing_failed" not in (allowed_statuses or []):
        errors.append(
            _error(
                file_path,
                f"{entry_prefix}.fallback_eligible",
                "fallback_eligible pipelines must allow router status 'routing_failed'",
            )
        )

    entry_id = entry.get("id")
    if entry_id == "default_conversation_pipeline":
        required_subagents = set(entry.get("required_subagents") or [])
        if "general_operator" not in required_subagents:
            errors.append(_error(file_path, f"{entry_prefix}.required_subagents", "Default pipeline registry entry must require general_operator"))

    if entry_id == "engineering_review_pipeline":
        required_subagents = set(entry.get("required_subagents") or [])
        for subagent_id in ("hermes_engineer_core", "hermes_code_reviewer"):
            if subagent_id not in required_subagents:
                errors.append(
                    _error(
                        file_path,
                        f"{entry_prefix}.required_subagents",
                        f"Engineering pipeline registry entry must require {subagent_id}",
                    )
                )


def _validate_pipeline_semantics(
    repo_root: Path,
    pipelines: dict[str, dict[str, Any]],
    subagents: dict[str, dict[str, Any]],
    router_statuses: set[str],
    errors: list[SpecValidationErrorDetail],
) -> None:
    default_pipeline = pipelines.get("default_conversation_pipeline")
    if default_pipeline is None:
        errors.append(_error(DEFAULT_REGISTRY_PATH, "registry", "Default pipeline spec could not be loaded"))
    else:
        primary = _nested_str(default_pipeline, ("subagents", "primary"))
        if primary != "general_operator":
            errors.append(
                _error(
                    Path("config/pipelines/default_conversation_pipeline.yaml"),
                    "subagents.primary",
                    "Default pipeline must reference general_operator as primary",
                )
            )

    engineering_pipeline = pipelines.get("engineering_review_pipeline")
    if engineering_pipeline is not None:
        for key, subagent_id in (("engineer", "hermes_engineer_core"), ("reviewer", "hermes_code_reviewer")):
            actual = _nested_str(engineering_pipeline, ("subagents", key))
            if actual != subagent_id:
                errors.append(
                    _error(
                        Path("config/pipelines/engineering_review_pipeline.yaml"),
                        f"subagents.{key}",
                        f"Engineering pipeline must reference {subagent_id}",
                    )
                )

    for pipeline_id, pipeline_spec in pipelines.items():
        pipeline_file = repo_root / "config" / "pipelines" / f"{pipeline_id}.yaml"
        for subagent_id in _collect_pipeline_subagent_ids(pipeline_spec):
            if subagent_id not in subagents:
                errors.append(
                    _error(
                        pipeline_file.relative_to(repo_root),
                        "subagents",
                        f"Referenced subagent {subagent_id!r} does not exist",
                    )
                )

        _validate_reporting_contract(pipeline_id, pipeline_spec, repo_root, errors)
        _validate_cyclic_pipeline_requirements(pipeline_id, pipeline_spec, subagents, repo_root, errors)

    router_spec = subagents.get("hermes_pipeline_router")
    if router_spec is not None:
        router_enum = set(_nested_list(router_spec, ("output_schema", "status", "enum")))
        for status in sorted(router_enum - VALID_ROUTER_STATUSES):
            errors.append(_error(ROUTER_SPEC_PATH, "output_schema.status.enum", f"Unknown router status {status!r}"))


def _validate_reporting_contract(
    pipeline_id: str,
    pipeline_spec: dict[str, Any],
    repo_root: Path,
    errors: list[SpecValidationErrorDetail],
) -> None:
    include_fields = set(_nested_list(pipeline_spec, ("reporting", "include")))
    for field_name in REQUIRED_REPORTING_FIELDS:
        if field_name not in include_fields:
            errors.append(
                _error(
                    Path("config/pipelines") / f"{pipeline_id}.yaml",
                    "reporting.include",
                    f"Reporting contract must include {field_name}",
                )
            )


def _validate_cyclic_pipeline_requirements(
    pipeline_id: str,
    pipeline_spec: dict[str, Any],
    subagents: dict[str, dict[str, Any]],
    repo_root: Path,
    errors: list[SpecValidationErrorDetail],
) -> None:
    communication_enabled = bool(_nested_value(pipeline_spec, ("communication_policy", "subagents_may_exchange_messages")))
    disagreement_enabled = bool(_nested_value(pipeline_spec, ("disagreement_policy", "enabled")))
    pipeline_file = Path("config/pipelines") / f"{pipeline_id}.yaml"

    if not (communication_enabled or disagreement_enabled):
        return

    loop_policy = _nested_mapping(pipeline_spec, ("loop_policy",))
    required_loop_keys = (
        "max_invalid_output_retries",
        "max_tool_retries",
        "max_model_escalations",
    )
    for key in required_loop_keys:
        if key not in loop_policy:
            errors.append(_error(pipeline_file, f"loop_policy.{key}", "Cyclic pipeline must define loop limits"))

    if communication_enabled:
        for key in ("max_review_iterations", "max_peer_discussion_rounds_per_iteration", "max_disagreement_rounds_per_iteration"):
            if key not in loop_policy:
                errors.append(_error(pipeline_file, f"loop_policy.{key}", "Engineering/cyclic pipeline must define loop limits"))

    decisive_subagent = _nested_str(pipeline_spec, ("disagreement_policy", "decisive_subagent"))
    arbitrator_subagent = _nested_str(pipeline_spec, ("disagreement_policy", "arbitrator_subagent"))
    if communication_enabled or disagreement_enabled:
        if not decisive_subagent and not arbitrator_subagent:
            errors.append(
                _error(
                    pipeline_file,
                    "disagreement_policy",
                    "Pipelines with peer communication or disagreement handling must define decisive_subagent or arbitrator_subagent",
                )
            )

    allowed_model_classes_by_subagent = {
        subagent_id: {entry.get("class") for entry in _nested_list(spec, ("models", "allowed")) if isinstance(entry, dict)}
        for subagent_id, spec in subagents.items()
    }
    pipeline_role_targets = {
        key: value
        for key, value in _nested_mapping(pipeline_spec, ("subagents",)).items()
        if isinstance(value, str)
    }

    for index, rule in enumerate(_nested_list(pipeline_spec, ("model_escalation_policy", "rules"))):
        if not isinstance(rule, dict):
            continue
        target_subagent = rule.get("target_subagent")
        model_class = rule.get("escalate_to_model_class")
        if not isinstance(target_subagent, str) or not isinstance(model_class, str):
            continue
        if target_subagent == "decisive_subagent_or_arbitrator":
            candidate_ids = {value for value in (decisive_subagent, arbitrator_subagent) if value}
        else:
            resolved_target = pipeline_role_targets.get(target_subagent, target_subagent)
            candidate_ids = {resolved_target}
        for candidate_id in candidate_ids:
            if candidate_id not in subagents:
                errors.append(
                    _error(
                        pipeline_file,
                        f"model_escalation_policy.rules[{index}].target_subagent",
                        f"Escalation target subagent {candidate_id!r} does not exist",
                    )
                )
                continue
            allowed_classes = allowed_model_classes_by_subagent.get(candidate_id, set())
            if model_class not in allowed_classes:
                errors.append(
                    _error(
                        pipeline_file,
                        f"model_escalation_policy.rules[{index}].escalate_to_model_class",
                        f"Escalation model class {model_class!r} is not allowed for subagent {candidate_id!r}",
                    )
                )

    pipeline_prompt_cache_policy = _nested_mapping(pipeline_spec, ("prompt_cache_policy",))
    for subagent_id in _collect_pipeline_subagent_ids(pipeline_spec):
        subagent_spec = subagents.get(subagent_id)
        if subagent_spec is None:
            continue
        subagent_prompt_cache = _nested_mapping(subagent_spec, ("prompt_cache_policy",))
        if not subagent_prompt_cache and not pipeline_prompt_cache_policy:
            errors.append(
                _error(
                    pipeline_file,
                    "prompt_cache_policy",
                    f"Cyclic pipeline subagent {subagent_id!r} must define prompt-cache policy or inherit one from the pipeline",
                )
            )


def _collect_pipeline_subagent_ids(pipeline_spec: dict[str, Any]) -> set[str]:
    subagents = pipeline_spec.get("subagents")
    if not isinstance(subagents, dict):
        return set()

    collected: set[str] = set()
    for value in subagents.values():
        if isinstance(value, str):
            collected.add(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    collected.add(item)
    for key in ("decisive_subagent", "arbitrator_subagent"):
        value = _nested_value(pipeline_spec, ("disagreement_policy", key))
        if isinstance(value, str) and value:
            collected.add(value)
    return collected


def _load_yaml_mapping(
    path: Path,
    repo_root: Path,
    errors: list[SpecValidationErrorDetail],
) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        errors.append(_error(path, "", "File does not exist"))
        return {}
    except yaml.YAMLError as exc:
        errors.append(_error(path, "", f"Invalid YAML: {exc}"))
        return {}

    if not isinstance(data, dict):
        errors.append(_error(path, "", "Top-level YAML value must be a mapping"))
        return {}
    return data


def _expect_list(
    data: dict[str, Any],
    key: str,
    file_path: Path,
    repo_root: Path,
    errors: list[SpecValidationErrorDetail],
) -> list[Any]:
    value = data.get(key)
    if isinstance(value, list):
        return value
    errors.append(_error(file_path, key, f"{key} must be a list"))
    return []


def _nested_value(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _nested_mapping(data: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    value = _nested_value(data, path)
    if isinstance(value, dict):
        return value
    return {}


def _nested_list(data: dict[str, Any], path: tuple[str, ...]) -> list[Any]:
    value = _nested_value(data, path)
    if isinstance(value, list):
        return value
    return []


def _nested_str(data: dict[str, Any], path: tuple[str, ...]) -> str | None:
    value = _nested_value(data, path)
    if isinstance(value, str) and value:
        return value
    return None


def _error(file_path: Path | str, field_path: str, message: str, severity: str = "error") -> SpecValidationErrorDetail:
    return SpecValidationErrorDetail(
        file_path=str(file_path),
        field_path=field_path,
        message=message,
        severity=severity,
    )
