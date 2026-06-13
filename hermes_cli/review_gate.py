from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any

import yaml

from agent.tool_result_classification import file_mutation_result_landed
from hermes_cli.config import load_config_readonly
from hermes_cli.profile_execution import RoleExecutionPlan
from hermes_cli.profile_validation import DEFAULT_MODEL_POLICY_PATH, MODEL_TIERS


VALID_REVIEW_GATE_MODES = frozenset({"disabled", "observe", "enforce"})
VALID_REVIEW_GATE_VERDICTS = frozenset(
    {"pending", "approved", "changes_requested", "blocked", "waived", "not_required"}
)
VALID_REVIEWER_TIERS = frozenset(MODEL_TIERS | {"code_review"})
_MATERIAL_PATH_HINTS = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".swift",
    ".sql",
    ".sh",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
)


@dataclass(frozen=True)
class ReviewGateDecision:
    mode: str
    status: str
    review_required: bool
    blocking: bool
    material_change_detected: bool
    reviewer_tier: str
    reviewer_provider: str
    reviewer_model: str
    changed_paths: list[str]
    changed_path_count: int
    packet: dict[str, Any]
    warning: str = ""


def load_review_gate_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else load_config_readonly()
    raw = cfg.get("review_gate")
    if not isinstance(raw, dict):
        raw = {}
    mode = str(raw.get("mode") or "observe").strip().lower()
    if mode not in VALID_REVIEW_GATE_MODES:
        mode = "observe"
    reviewer_tier = str(raw.get("reviewer_tier") or "code_review").strip().lower()
    if reviewer_tier not in VALID_REVIEWER_TIERS:
        reviewer_tier = "code_review"
    return {
        "mode": mode,
        "reviewer_tier": reviewer_tier,
    }


def _load_model_policy(policy_path: Path | str = DEFAULT_MODEL_POLICY_PATH) -> dict[str, Any]:
    data = yaml.safe_load(Path(policy_path).read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def resolve_reviewer_model(
    reviewer_tier: str,
    *,
    policy_path: Path | str = DEFAULT_MODEL_POLICY_PATH,
) -> dict[str, str]:
    policy = _load_model_policy(policy_path)
    tiers = policy.get("tiers") if isinstance(policy.get("tiers"), dict) else {}
    requested = tiers.get(reviewer_tier) if isinstance(tiers, dict) else None
    if not isinstance(requested, dict) and reviewer_tier == "code_review":
        requested = tiers.get("critical") if isinstance(tiers, dict) else None
        reviewer_tier = "critical"
    if not isinstance(requested, dict):
        requested = {}
    provider = str(requested.get("provider") or "openrouter")
    model = str(requested.get("model") or "anthropic/claude-opus-4.6")
    return {
        "reviewer_tier": reviewer_tier,
        "provider": provider,
        "model": model,
    }


def _parse_tool_args(tool_call: dict[str, Any]) -> dict[str, Any]:
    function = tool_call.get("function") if isinstance(tool_call, dict) else {}
    arguments = function.get("arguments") if isinstance(function, dict) else {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def collect_changed_paths(messages: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for msg in messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for tool_call in msg.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
            name = str(function.get("name") or "")
            if name not in {"write_file", "patch"}:
                continue
            args = _parse_tool_args(tool_call)
            if name == "write_file":
                path = args.get("path")
                if isinstance(path, str) and path.strip():
                    paths.append(path.strip())
            else:
                for key in ("path", "file_path"):
                    path = args.get(key)
                    if isinstance(path, str) and path.strip():
                        paths.append(path.strip())
    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped


def detect_material_engineering_change(
    plan: RoleExecutionPlan,
    messages: list[dict[str, Any]],
    *,
    changed_paths: list[str] | None = None,
) -> tuple[bool, list[str]]:
    if not isinstance(plan, RoleExecutionPlan) or plan.selected_role != "engineer":
        return False, []
    if plan.operation_category == "read_only_investigation":
        return False, []

    material_categories = {
        "repo_mutation",
        "git_remote_mutation",
        "normal_operational_mutation",
        "security_critical_mutation",
    }
    material_paths = list(changed_paths) if changed_paths is not None else collect_changed_paths(messages)
    if plan.operation_category in material_categories:
        return True, material_paths

    has_material_path = any(
        isinstance(path, str) and path.strip() and path.strip().endswith(_MATERIAL_PATH_HINTS)
        for path in material_paths
    )
    if not has_material_path:
        return False, material_paths

    assistant_tool_turns = [
        msg for msg in messages or []
        if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("tool_calls")
    ]
    tool_results = [msg for msg in messages or [] if isinstance(msg, dict) and msg.get("role") == "tool"]
    tool_result_idx = 0
    landed = False
    for assistant in assistant_tool_turns:
        for tool_call in assistant.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
            name = str(function.get("name") or "")
            if name not in {"write_file", "patch"}:
                continue
            while tool_result_idx < len(tool_results):
                result_msg = tool_results[tool_result_idx]
                tool_result_idx += 1
                if file_mutation_result_landed(name, result_msg.get("content")):
                    landed = True
                    break
        if landed:
            break
    return landed, material_paths


def build_review_packet(
    plan: RoleExecutionPlan,
    *,
    changed_paths: list[str],
    reviewer_tier: str,
    reviewer_provider: str,
    reviewer_model: str,
) -> dict[str, Any]:
    return {
        "selected_role": plan.selected_role,
        "operation_category": plan.operation_category,
        "reviewer_tier": reviewer_tier,
        "reviewer_provider": reviewer_provider,
        "reviewer_model": reviewer_model,
        "changed_paths": list(changed_paths),
        "changed_path_count": len(changed_paths),
        "requires_review": True,
        "status": "pending",
    }


def evaluate_review_gate(
    plan: RoleExecutionPlan,
    messages: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
    changed_paths: list[str] | None = None,
    verdict: str | None = None,
    policy_path: Path | str = DEFAULT_MODEL_POLICY_PATH,
) -> ReviewGateDecision:
    gate_cfg = load_review_gate_config(config)
    mode = gate_cfg["mode"]
    reviewer = resolve_reviewer_model(gate_cfg["reviewer_tier"], policy_path=policy_path)
    material_change_detected, discovered_paths = detect_material_engineering_change(
        plan,
        messages,
        changed_paths=changed_paths,
    )
    review_required = material_change_detected
    status = str(verdict or ("pending" if review_required else "not_required")).strip().lower()
    if status not in VALID_REVIEW_GATE_VERDICTS:
        status = "pending" if review_required else "not_required"
    blocking = bool(
        mode == "enforce"
        and review_required
        and status not in {"approved", "waived"}
    )
    warning = ""
    if mode == "observe" and review_required:
        warning = "review would be required before marking this engineering change done"
    packet = build_review_packet(
        plan,
        changed_paths=discovered_paths,
        reviewer_tier=reviewer["reviewer_tier"],
        reviewer_provider=reviewer["provider"],
        reviewer_model=reviewer["model"],
    ) if review_required else {}
    return ReviewGateDecision(
        mode=mode,
        status=status,
        review_required=review_required,
        blocking=blocking,
        material_change_detected=material_change_detected,
        reviewer_tier=reviewer["reviewer_tier"],
        reviewer_provider=reviewer["provider"],
        reviewer_model=reviewer["model"],
        changed_paths=discovered_paths,
        changed_path_count=len(discovered_paths),
        packet=packet,
        warning=warning,
    )


def decision_to_dict(decision: ReviewGateDecision | dict[str, Any]) -> dict[str, Any]:
    if isinstance(decision, dict):
        return dict(decision)
    if not isinstance(decision, ReviewGateDecision):
        raise TypeError("decision_to_dict expects ReviewGateDecision or dict")
    return asdict(decision)


def render_review_gate_block_message(decision: ReviewGateDecision) -> str:
    changed_paths = decision.changed_paths[:5]
    lines = [
        "Final completion is blocked pending code review approval.",
        "",
        f"Review gate mode: {decision.mode}",
        f"Reviewer tier: {decision.reviewer_tier}",
        f"Reviewer model: {decision.reviewer_provider} / {decision.reviewer_model}",
    ]
    if changed_paths:
        lines.extend(["", "Material changes detected:"])
        lines.extend(f"- {path}" for path in changed_paths)
    lines.extend(
        [
            "",
            "Reply with one of:",
            "- review approved",
            "- review waived",
            "- review changes requested",
            "- review blocked",
        ]
    )
    return "\n".join(lines)


def parse_review_verdict_intent(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not normalized or "review" not in normalized:
        return None
    if "changes requested" in normalized:
        return "changes_requested"
    if re.search(r"\breview blocked\b", normalized):
        return "blocked"
    if re.search(r"\breview approved\b", normalized) or re.search(r"\breview approve\b", normalized):
        return "approved"
    if re.search(r"\breview waived\b", normalized) or re.search(r"\breview waive\b", normalized):
        return "waived"
    return None


def latest_pending_review_gate(messages: list[dict[str, Any]], current_turn_user_idx: int) -> dict[str, Any] | None:
    for idx in range(current_turn_user_idx - 1, -1, -1):
        msg = messages[idx]
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        gate = msg.get("_review_gate")
        if isinstance(gate, dict) and gate.get("required") and gate.get("status") == "pending":
            return gate
    return None
