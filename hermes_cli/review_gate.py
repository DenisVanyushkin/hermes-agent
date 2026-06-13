from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import re
import subprocess
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
    packet_hash: str = ""
    automatic_review_invoked: bool = False
    automatic_review_verdict: str = "pending"
    reviewer_summary: str = ""
    reviewer_findings: list[str] = field(default_factory=list)
    required_changes: list[str] = field(default_factory=list)
    tests_required: list[str] = field(default_factory=list)
    approval_sensitive: bool = False
    user_override: bool = False
    review_error: str = ""
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
    auto_review_in_observe = bool(raw.get("auto_review_in_observe", False))
    return {
        "mode": mode,
        "reviewer_tier": reviewer_tier,
        "auto_review_in_observe": auto_review_in_observe,
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
    provider = str(requested.get("provider") or "openai-codex")
    model = str(requested.get("model") or "gpt-5.5")
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
        "task": plan.task,
        "operation_category": plan.operation_category,
        "reviewer_tier": reviewer_tier,
        "reviewer_provider": reviewer_provider,
        "reviewer_model": reviewer_model,
        "changed_paths": list(changed_paths),
        "changed_path_count": len(changed_paths),
        "requires_review": True,
        "status": "pending",
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _normalize_repo_paths(paths: list[str], repo_root: Path) -> list[str]:
    normalized: list[str] = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            continue
        candidate = raw.strip()
        try:
            path_obj = Path(candidate)
            if path_obj.is_absolute():
                candidate = str(path_obj.resolve().relative_to(repo_root))
        except Exception:
            pass
        if candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _run_git_diff(command: list[str], repo_root: Path) -> str:
    try:
        proc = subprocess.run(
            command,
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    return output.strip()


def _redact_secret_like_values(text: str) -> str:
    if not text:
        return ""
    redacted = text
    redacted = re.sub(
        r"(?i)(api[_-]?key|secret|token|password|authorization)\s*[:=]\s*(['\"]?)[^\s'\"\n]+\2",
        r"\1=[REDACTED]",
        redacted,
    )
    redacted = re.sub(r"sk-[A-Za-z0-9]{16,}", "[REDACTED]", redacted)
    redacted = re.sub(r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer [REDACTED]", redacted)
    return redacted


def _truncate(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... [truncated]"


def _sha256_short(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _collect_test_evidence(messages: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    tests_run: list[str] = []
    test_results: list[str] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "assistant":
            for tool_call in msg.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
                name = str(function.get("name") or "")
                args = _parse_tool_args(tool_call)
                command = str(args.get("command") or args.get("args") or "")
                if name == "terminal" and command and any(marker in command for marker in ("pytest", "validate_profile_architecture.py", "python scripts/")):
                    tests_run.append(command.strip())
        if msg.get("role") == "tool":
            content = str(msg.get("content") or "")
            if any(marker in content.lower() for marker in ("passed", "failed", "error", "errors", "assert", "collected")):
                test_results.append(_truncate(_redact_secret_like_values(content), 2000))
    return tests_run, test_results


def _build_review_prompt(
    *,
    plan: RoleExecutionPlan,
    review_packet: dict[str, Any],
    diff_stat: str,
    focused_diff: str,
    tests_run: list[str],
    test_results: list[str],
    rollback_notes: str,
    risk_notes: str,
) -> str:
    payload = {
        "task_goal": plan.task,
        "selected_role": plan.selected_role,
        "implementation_model": review_packet.get("reviewer_model"),
        "implementation_provider": review_packet.get("reviewer_provider"),
        "implementation_tier": review_packet.get("reviewer_tier"),
        "changed_files": review_packet.get("changed_paths", []),
        "diff_stat": diff_stat,
        "focused_diff": focused_diff,
        "tests_run": tests_run,
        "test_results": test_results,
        "risk_notes": risk_notes,
        "rollback_notes": rollback_notes,
        "approval_sensitive_changes": bool(plan.critical_approval_required or plan.operation_category in {"repo_mutation", "git_remote_mutation", "security_critical_mutation"}),
        "review_packet": review_packet,
    }
    schema = {
        "verdict": "approved | changes_requested | blocked",
        "summary": "Short explanation",
        "findings": ["string"],
        "required_changes": ["string"],
        "risk_level": "low | medium | high",
        "tests_required": ["string"],
        "approval_sensitive": False,
    }
    return "\n".join(
        [
            "You are Hermes' automatic code reviewer.",
            "Return JSON only. No markdown. No extra prose.",
            f"Expected schema: {json.dumps(schema, ensure_ascii=False)}",
            f"Review packet: {json.dumps(payload, ensure_ascii=False, sort_keys=True)}",
        ]
    )


class ReviewInvocationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReviewVerdict:
    verdict: str
    summary: str
    findings: list[str]
    required_changes: list[str]
    risk_level: str
    tests_required: list[str]
    approval_sensitive: bool
    raw: dict[str, Any]


def run_code_review(
    review_packet: dict[str, Any],
    *,
    plan: RoleExecutionPlan,
    reviewer_tier: str = "code_review",
    policy_path: Path | str = DEFAULT_MODEL_POLICY_PATH,
    messages: list[dict[str, Any]] | None = None,
) -> tuple[ReviewVerdict, dict[str, Any]]:
    reviewer = resolve_reviewer_model(reviewer_tier, policy_path=policy_path)
    repo_root = _repo_root()
    changed_paths = _normalize_repo_paths(list(review_packet.get("changed_paths") or []), repo_root)
    git_args = ["git", "diff", "HEAD", "--stat", "--", *changed_paths] if changed_paths else ["git", "diff", "HEAD", "--stat"]
    diff_stat = _truncate(_redact_secret_like_values(_run_git_diff(git_args, repo_root)), 4000)
    git_diff_args = ["git", "diff", "HEAD", "--", *changed_paths] if changed_paths else ["git", "diff", "HEAD", "--"]
    focused_diff = _truncate(_redact_secret_like_values(_run_git_diff(git_diff_args, repo_root)), 12000)
    tests_run, test_results = _collect_test_evidence(messages or [])
    risk_notes = (
        "Material repository mutation; verify tests and rollback path." if plan.operation_category in {"repo_mutation", "git_remote_mutation"}
        else "Review for correctness and safety."
    )
    rollback_notes = (
        "Revert the commit or restore the touched files to HEAD; for remote pushes, use a follow-up revert."
    )
    prompt = _build_review_prompt(
        plan=plan,
        review_packet=review_packet,
        diff_stat=diff_stat,
        focused_diff=focused_diff,
        tests_run=tests_run,
        test_results=test_results,
        rollback_notes=rollback_notes,
        risk_notes=risk_notes,
    )
    try:
        from agent.auxiliary_client import resolve_provider_client

        client, resolved_model = resolve_provider_client(
            reviewer["provider"],
            reviewer["model"],
            raw_codex=False,
            async_mode=False,
        )
        if client is None:
            raise ReviewInvocationError(
                f"reviewer_unavailable: unable to resolve client for {reviewer['provider']} / {reviewer['model']}"
            )
        response = client.chat.completions.create(
            model=resolved_model or reviewer["model"],
            messages=[
                {"role": "system", "content": "You are a strict code reviewer. Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
    except ReviewInvocationError:
        raise
    except Exception as exc:
        raise ReviewInvocationError(f"reviewer_error: {exc}") from exc

    content = ""
    try:
        choice = response.choices[0]
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None) if message is not None else None
        if not isinstance(content, str):
            content = str(content or "")
    except Exception as exc:
        raise ReviewInvocationError(f"invalid_review_verdict: unable to read reviewer response ({exc})") from exc

    try:
        data = json.loads(content)
    except Exception as exc:
        raise ReviewInvocationError(f"invalid_review_verdict: reviewer did not return valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ReviewInvocationError("invalid_review_verdict: reviewer JSON must be an object")

    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in {"approved", "changes_requested", "blocked"}:
        raise ReviewInvocationError(f"invalid_review_verdict: unsupported verdict {verdict!r}")

    summary = str(data.get("summary") or "").strip()
    findings = data.get("findings") if isinstance(data.get("findings"), list) else []
    required_changes = data.get("required_changes") if isinstance(data.get("required_changes"), list) else []
    tests_required = data.get("tests_required") if isinstance(data.get("tests_required"), list) else []
    risk_level = str(data.get("risk_level") or "unknown").strip().lower()
    approval_sensitive = bool(data.get("approval_sensitive", bool(plan.critical_approval_required)))
    verdict_obj = ReviewVerdict(
        verdict=verdict,
        summary=summary,
        findings=[str(item) for item in findings if str(item).strip()],
        required_changes=[str(item) for item in required_changes if str(item).strip()],
        risk_level=risk_level,
        tests_required=[str(item) for item in tests_required if str(item).strip()],
        approval_sensitive=approval_sensitive,
        raw=data,
    )
    review_packet_out = dict(review_packet)
    review_packet_out.update(
        {
            "packet_hash": _sha256_short(review_packet),
            "automatic_review_invoked": True,
            "automatic_review_verdict": verdict,
            "reviewer_summary": summary,
            "reviewer_findings": verdict_obj.findings,
            "required_changes": verdict_obj.required_changes,
            "tests_required": verdict_obj.tests_required,
            "approval_sensitive": verdict_obj.approval_sensitive,
            "reviewer_provider": reviewer["provider"],
            "reviewer_model": reviewer["model"],
            "reviewer_tier": reviewer["reviewer_tier"],
            "reviewer_risk_level": risk_level,
        }
    )
    return verdict_obj, review_packet_out


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
    auto_review_in_observe = bool(gate_cfg.get("auto_review_in_observe"))
    reviewer = resolve_reviewer_model(gate_cfg["reviewer_tier"], policy_path=policy_path)
    material_change_detected, discovered_paths = detect_material_engineering_change(
        plan,
        messages,
        changed_paths=changed_paths,
    )
    review_required = material_change_detected
    packet = build_review_packet(
        plan,
        changed_paths=discovered_paths,
        reviewer_tier=reviewer["reviewer_tier"],
        reviewer_provider=reviewer["provider"],
        reviewer_model=reviewer["model"],
    ) if review_required else {}
    packet_hash = _sha256_short(packet) if packet else ""
    status = str(verdict or ("pending" if review_required else "not_required")).strip().lower()
    if status not in VALID_REVIEW_GATE_VERDICTS:
        status = "pending" if review_required else "not_required"
    automatic_review_invoked = False
    automatic_review_verdict = "pending"
    reviewer_summary = ""
    reviewer_findings: list[str] = []
    required_changes: list[str] = []
    tests_required: list[str] = []
    approval_sensitive = bool(
        plan.critical_approval_required
        or plan.operation_category in {"repo_mutation", "git_remote_mutation", "security_critical_mutation"}
    )
    review_error = ""
    user_override = bool(verdict in {"approved", "waived", "changes_requested", "blocked"})
    blocking = bool(
        mode == "enforce"
        and review_required
        and status not in {"approved", "waived"}
    )
    warning = ""
    if review_required:
        if verdict in {"approved", "waived", "changes_requested", "blocked"}:
            status = verdict
            blocking = bool(mode == "enforce" and verdict not in {"approved", "waived"})
            automatic_review_verdict = verdict
            warning = ""
        else:
            should_auto_review = mode == "enforce" or auto_review_in_observe
            if should_auto_review:
                try:
                    verdict_obj, reviewed_packet = run_code_review(
                        packet,
                        plan=plan,
                        reviewer_tier=reviewer["reviewer_tier"],
                        policy_path=policy_path,
                        messages=messages,
                    )
                    automatic_review_invoked = True
                    automatic_review_verdict = verdict_obj.verdict
                    reviewer_summary = verdict_obj.summary
                    reviewer_findings = verdict_obj.findings
                    required_changes = verdict_obj.required_changes
                    tests_required = verdict_obj.tests_required
                    approval_sensitive = verdict_obj.approval_sensitive
                    packet = reviewed_packet
                    packet_hash = reviewed_packet.get("packet_hash", packet_hash)
                    status = verdict_obj.verdict
                    blocking = bool(mode == "enforce" and verdict_obj.verdict != "approved")
                    if mode == "observe":
                        warning = (
                            f"automatic reviewer returned {verdict_obj.verdict}; "
                            f"would_block={verdict_obj.verdict != 'approved'}"
                        )
                except ReviewInvocationError as exc:
                    automatic_review_invoked = True
                    automatic_review_verdict = "blocked"
                    review_error = str(exc)
                    status = "blocked" if mode == "enforce" else "pending"
                    blocking = mode == "enforce"
                    warning = f"reviewer unavailable: {exc}"
            elif mode == "observe" and review_required:
                warning = "review would be required before marking this engineering change done"
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
        packet_hash=packet_hash,
        automatic_review_invoked=automatic_review_invoked,
        automatic_review_verdict=automatic_review_verdict,
        reviewer_summary=reviewer_summary,
        reviewer_findings=reviewer_findings,
        required_changes=required_changes,
        tests_required=tests_required,
        approval_sensitive=approval_sensitive,
        user_override=user_override,
        review_error=review_error,
        warning=warning,
    )


def decision_to_dict(decision: ReviewGateDecision | dict[str, Any]) -> dict[str, Any]:
    if isinstance(decision, dict):
        return dict(decision)
    if not isinstance(decision, ReviewGateDecision):
        raise TypeError("decision_to_dict expects ReviewGateDecision or dict")
    return asdict(decision)


def build_review_gate_startup_log_fields(
    config: dict[str, Any] | None = None,
    *,
    config_path: str | Path = "",
    config_loaded_ok: bool = True,
    fallback_config_used: bool = False,
) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else load_config_readonly()
    gate = load_review_gate_config(cfg)
    return {
        "config_path": str(config_path),
        "config_loaded_ok": bool(config_loaded_ok),
        "fallback_config_used": bool(fallback_config_used),
        "review_gate.mode": gate["mode"],
        "review_gate.reviewer_tier": gate["reviewer_tier"],
        "review_gate.auto_review_in_observe": gate["auto_review_in_observe"],
    }


def build_review_gate_evaluation_log_fields(decision: ReviewGateDecision) -> dict[str, Any]:
    return {
        "review_gate.mode": decision.mode,
        "review_gate.reviewer_tier": decision.reviewer_tier,
        "automatic_review_invoked": decision.automatic_review_invoked,
        "automatic_review_verdict": decision.automatic_review_verdict,
        "reviewer_provider": decision.reviewer_provider,
        "reviewer_model": decision.reviewer_model,
        "changed_paths_count": decision.changed_path_count,
        "blocking": decision.blocking,
        "status": decision.status,
    }


def _review_gate_task_summary(decision: ReviewGateDecision) -> str:
    packet = decision.packet if isinstance(decision.packet, dict) else {}
    task = str(packet.get("task") or packet.get("task_summary") or "").strip()
    if task:
        return task
    operation = str(packet.get("operation_category") or "").strip()
    if operation:
        return operation.replace("_", " ")
    return "n/a"


def _review_gate_diff_stat(changed_paths: list[str]) -> str:
    if not changed_paths:
        return "n/a"
    repo_root = _repo_root()
    args = ["git", "diff", "HEAD", "--stat", "--", *changed_paths]
    try:
        completed = subprocess.run(
            args,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return f"unavailable ({exc})"
    output = (completed.stdout or completed.stderr or "").strip()
    if not output:
        return "n/a"
    return _truncate(output, 800)


def render_review_gate_block_message(decision: ReviewGateDecision) -> str:
    changed_paths = decision.changed_paths[:10]
    packet = decision.packet if isinstance(decision.packet, dict) else {}
    task_summary = _review_gate_task_summary(decision)
    operation_category = str(packet.get("operation_category") or "").strip()
    planned_action = str(packet.get("task") or packet.get("planned_action") or "").strip()
    approval_scope = "production/security approval" if decision.approval_sensitive else "code review approval"
    if decision.review_error:
        title = "Final completion is blocked because automatic reviewer failed."
    elif decision.automatic_review_invoked:
        if decision.automatic_review_verdict == "approved":
            title = "Automatic review approved; final completion should not be blocked."
        else:
            title = "Final completion is blocked by automatic review verdict."
    elif decision.approval_sensitive:
        title = "Explicit approval is required for this production/security-sensitive change."
    else:
        title = "Final completion is blocked pending code review approval."

    lines = [
        title,
        "",
        f"Task summary: {task_summary}",
        f"Review gate mode: {decision.mode}",
        f"Approval scope: {approval_scope}",
        f"Reviewer tier: {decision.reviewer_tier}",
        f"Reviewer model: {decision.reviewer_provider} / {decision.reviewer_model}",
        f"Automatic review invoked: {'yes' if decision.automatic_review_invoked else 'no'}",
        f"Automatic review verdict: {decision.automatic_review_verdict}",
    ]
    if operation_category:
        lines.append(f"Operation category: {operation_category}")
    if planned_action:
        lines.append(f"Planned action: {planned_action}")
    if decision.reviewer_summary:
        lines.append(f"Reviewer summary: {decision.reviewer_summary}")
    else:
        lines.append("Reviewer summary: n/a")
    if decision.reviewer_findings:
        lines.append("Reviewer findings:")
        lines.extend(f"- {finding}" for finding in decision.reviewer_findings)
    else:
        lines.append("Reviewer findings: none")
    if decision.required_changes:
        lines.append("Required changes:")
        lines.extend(f"- {change}" for change in decision.required_changes)
    else:
        lines.append("Required changes: none")
    if decision.review_error:
        lines.append(f"Reviewer error: {decision.review_error}")
    lines.append(f"Why user action is required: {decision.warning or ('automatic review did not approve this change' if decision.automatic_review_verdict != 'approved' else 'production/security approval is still required')}")
    lines.append(f"Diff stat: {_review_gate_diff_stat(changed_paths)}")
    if changed_paths:
        lines.append("Changed files:")
        lines.extend(f"- {path}" for path in changed_paths)
    else:
        lines.append("Changed files: none detected")
    lines.extend(
        [
            "",
            "Exact allowed replies:",
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
