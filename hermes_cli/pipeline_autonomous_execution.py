"""Autonomous engineering pipeline runtime context construction."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from hermes_cli.baseline_git import classify_dirty
from hermes_cli.pipeline_change_artifacts import is_verified_change_artifact
from hermes_cli.pipeline_aiagent_executor import AIAgentReviewerExecutorBridge, AIAgentSubagentExecutorBridge
from hermes_cli.pipeline_specs import load_pipeline_specs
from hermes_cli.runtime_factory import RuntimeFactory, build_runtime_factory_plan
from hermes_cli.subagent_runner import SubagentRunner


ENGINEER_SUBAGENT_ID = "hermes_engineer_core"
REVIEWER_SUBAGENT_ID = "hermes_code_reviewer"
ENGINEERING_PIPELINE_ID = "engineering_review_pipeline"
AUTONOMOUS_MODE = "autonomous"
AUTONOMOUS_WORKSPACE_ROOT = Path("/tmp/hermes-gateway-autonomous-runs")


def build_autonomous_helper_context(
    *,
    config: dict[str, Any] | None,
    user_message: str,
    session_id: str | None,
    pipeline_session_id: str | None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    inferred_repo_root = Path(__file__).resolve().parent.parent
    base_repo_root = inferred_repo_root if repo_root is None else Path(repo_root)
    workspace = base_repo_root.resolve()
    canonical_repo_root = _repo_root_of(workspace) or workspace
    runtime_context = _default_runtime_context(workspace)
    helper_context = {
        "runtime_factory": RuntimeFactory(repo_root=base_repo_root),
        "runner": SubagentRunner(executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be used"))),
        "user_message": user_message,
        "repo_path": str(workspace),
        "canonical_repo_path": str(canonical_repo_root.resolve()),
        "allow_completion_after_review": True,
        "controlled_runtime_context": runtime_context,
    }
    if not _allow_real_provider_execution(config):
        return helper_context

    runtime_context.update(
        allow_real_provider_execution=True,
        request_real_provider_execution=True,
        allow_mutations=True,
        allow_test_commands=True,
        allow_model_escalation=True,
    )
    # The run gets its own worktree, cut from the repo HEAD commit. It is clean by
    # construction, so dirt in the operator's tree neither blocks the run nor gets
    # stashed out from under them -- and root-owned sandbox leftovers, which
    # `git stash` cannot move, are simply not present in a fresh checkout.
    try:
        _validate_repo_root(
            repo_root=base_repo_root.resolve(),
            expected_repo_root=inferred_repo_root if repo_root is None else None,
        )
    except ValueError as exc:
        runtime_context["blocked_reason"] = str(exc)
        _fail_closed(runtime_context)
        return helper_context

    try:
        run_worktree = prepare_run_worktree(
            repo_root=base_repo_root,
            workspace=autonomous_workspace(
                session_id=session_id, pipeline_session_id=pipeline_session_id
            ),
            run_id=pipeline_session_id or session_id or "autonomous",
        )
    except (ValueError, OSError):
        # Isolation is not optional: if we cannot get it, stop rather than fall
        # back to mutating the live repository.
        runtime_context["blocked_reason"] = "workspace_worktree_failed"
        _fail_closed(runtime_context)
        return helper_context

    workspace = run_worktree.path
    runtime_context["run_branch"] = run_worktree.branch

    helper_context["repo_path"] = str(workspace)
    helper_context["canonical_repo_path"] = str(canonical_repo_root.resolve())
    runtime_context["mutation_workspace"] = str(workspace)
    runtime_context["test_workspace"] = str(workspace)
    runtime_context["workspace_baseline_head"] = _git_stdout(workspace, "rev-parse", "HEAD")
    loaded_specs = load_pipeline_specs(repo_root=base_repo_root)
    plans = _build_bridge_runtime_plans(
        loaded_specs=loaded_specs,
        pipeline_session_id=pipeline_session_id,
        user_message=user_message,
        config=config,
    )
    runtime_context["bridge_runtime_plans"] = {key: value.to_safe_dict() for key, value in plans.items()}
    for subagent_id in (ENGINEER_SUBAGENT_ID, REVIEWER_SUBAGENT_ID):
        if plans[subagent_id].errors:
            runtime_context["blocked_reason"] = f"runtime_plan_blocked:{subagent_id}"
            return helper_context

    runtime_context["executor_bridge"] = {
        ENGINEER_SUBAGENT_ID: AIAgentSubagentExecutorBridge(workspace_root=workspace, repo_root=base_repo_root),
        REVIEWER_SUBAGENT_ID: AIAgentReviewerExecutorBridge(workspace_root=workspace, repo_root=base_repo_root),
    }
    runtime_context["real_executor_ready"] = True
    runtime_context["blocked_reason"] = None
    return helper_context


def _validate_repo_root(*, repo_root: Path, expected_repo_root: Path | None) -> None:
    """Check the repo we cut the run worktree from -- without judging its dirt.

    The dirty-baseline veto used to live here. It is gone from this path on
    purpose: the run no longer executes in this tree, so its cleanliness is the
    operator's business, not a precondition for the pipeline.
    """
    if not repo_root.exists() or not repo_root.is_dir():
        raise ValueError("workspace_repo_missing")
    if expected_repo_root is not None and repo_root != expected_repo_root.resolve():
        raise ValueError("workspace_repo_root_mismatch")
    if not (repo_root / ".git").exists():
        raise ValueError("workspace_not_git_repo")
    try:
        _git_stdout(repo_root, "rev-parse", "--is-inside-work-tree")
        _git_stdout(repo_root, "rev-parse", "HEAD")
    except ValueError as exc:
        raise ValueError("workspace_not_git_repo") from exc


def _fail_closed(runtime_context: dict[str, Any]) -> None:
    """Never let a run that failed to get isolation touch the live tree."""
    runtime_context["real_executor_ready"] = False
    runtime_context["allow_mutations"] = False
    runtime_context["allow_test_commands"] = False
    runtime_context["mutation_workspace"] = ""
    runtime_context["test_workspace"] = ""


def autonomous_workspace(*, session_id: str | None, pipeline_session_id: str | None) -> Path:
    slug = pipeline_session_id or session_id or "autonomous"
    slug = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in str(slug)).strip("-_") or "autonomous"
    return (AUTONOMOUS_WORKSPACE_ROOT / slug).resolve()


RUN_BRANCH_PREFIX = "hermes-run/"


class RunWorktree(SimpleNamespace):
    """Where one autonomous run does its work.

    ``path`` -- the worktree directory; ``branch`` -- its per-run branch;
    ``head`` -- the commit it was cut from; ``created`` -- False when an existing
    worktree for this run was reused.
    """


def _run_slug(run_id: str | None) -> str:
    slug = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in str(run_id or "")
    ).strip("-_")
    return slug or "autonomous"


def prepare_run_worktree(*, repo_root: Path, workspace: Path, run_id: str) -> RunWorktree:
    """Cut a clean, private worktree for one autonomous run.

    The point is that a worktree is created from a *commit*, so it is clean by
    construction no matter what state the repo root is in. That removes the
    dirty-baseline question instead of classifying it, and -- unlike
    the stash-based self-heal it replaces -- it never touches the operator's working
    tree: no stash, no silent revert.

    It also covers the cases auto-heal refuses by design. Sandbox containers
    leave root-owned files around the repo, and ``git stash`` cannot move those
    because the pipeline does not run as root; a fresh worktree simply does not
    contain them.

    Each run gets branch ``hermes-run/<run_id>``. A per-run branch is not
    optional: git refuses to check out one branch in two worktrees, so the run
    cannot share the live branch even if we wanted it to. Landing that branch on
    the mainline stays gated on an approving reviewer verdict.

    Idempotent -- a retried turn within the same run reuses its worktree rather
    than orphaning it.
    """
    repo_root = repo_root.resolve()
    branch = f"{RUN_BRANCH_PREFIX}{_run_slug(run_id)}"
    head = _git_stdout(repo_root, "rev-parse", "HEAD")

    if workspace.exists():
        if not workspace.is_dir() or not (workspace / ".git").exists():
            raise ValueError("workspace_not_git_repo")
        resolved = workspace.resolve()
        _ensure_workspace_layout(workspace=resolved, repo_root=repo_root)
        return RunWorktree(
            path=resolved,
            branch=_git_stdout(resolved, "rev-parse", "--abbrev-ref", "HEAD"),
            head=_git_stdout(resolved, "rev-parse", "HEAD"),
            created=False,
        )

    workspace.parent.mkdir(parents=True, exist_ok=True)
    # Reuse the branch if a previous worktree for this run was already removed;
    # `worktree add -b` would fail on an existing branch name.
    existing = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_root, text=True, encoding="utf-8", capture_output=True, check=False,
    ).returncode == 0
    if existing:
        _git(repo_root, "worktree", "add", str(workspace), branch)
    else:
        _git(repo_root, "worktree", "add", "-b", branch, str(workspace), head)

    resolved = workspace.resolve()
    _ensure_workspace_layout(workspace=resolved, repo_root=repo_root)
    return RunWorktree(path=resolved, branch=branch, head=head, created=True)


class RunIntegration(SimpleNamespace):
    """Outcome of trying to land a run branch on the mainline.

    ``integrated`` -- did the mainline move; ``reason`` -- why not, if it did not;
    ``target`` -- the branch we tried to land on; ``sha`` -- its tip afterwards;
    ``detail`` -- git's own message, for the operator-facing reply.
    """


def _git_result(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, text=True, encoding="utf-8", capture_output=True, check=False)


def _branch_exists(repo_root: Path, branch: str) -> bool:
    return _git_result(
        repo_root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"
    ).returncode == 0


def integrate_run_branch(
    *, repo_root: Path, branch: str, approved: bool, target: str | None = None
) -> RunIntegration:
    """Land an approved run branch on the mainline, fast-forward only.

    ``approved`` is the gate, and it is the point of the whole step: the mainline
    moves on a reviewer verdict, not because a turn happened to end.

    Fast-forward only, deliberately. The mainline is a live branch the resident
    agent also commits to, so a merge commit or a rebase performed on the
    operator's behalf is not this step's call to make. When the mainline has moved
    on, we say so and stop.

    Every refusal leaves the run branch untouched, so nothing is ever stranded
    beyond recovery -- the work can always be landed by hand.
    """
    repo_root = repo_root.resolve()
    if not approved:
        return RunIntegration(
            integrated=False, reason="review_not_approved", target=None, sha=None,
            detail="the reviewer has not approved this run",
        )
    if not _branch_exists(repo_root, branch):
        return RunIntegration(
            integrated=False, reason="run_branch_missing", target=None, sha=None,
            detail=f"{branch} does not exist",
        )

    head = _git_result(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    current = head.stdout.strip()
    if head.returncode != 0 or not current or current == "HEAD":
        return RunIntegration(
            integrated=False, reason="target_detached", target=None, sha=None,
            detail="the repository is not on a branch; cannot fast-forward it",
        )
    if target is not None and target != current:
        return RunIntegration(
            integrated=False, reason="target_not_checked_out", target=target, sha=None,
            detail=f"{target} is not the branch checked out at {repo_root}",
        )

    merge = _git_result(repo_root, "merge", "--ff-only", branch)
    if merge.returncode != 0:
        # Two shapes end up here: the mainline has diverged, and the merge would
        # have to overwrite a file the operator is currently editing. Neither is
        # ours to resolve, and git's own message says which one it was.
        detail = (merge.stderr or merge.stdout).strip()
        reason = "not_fast_forward" if "fast-forward" in detail.lower() else "merge_refused"
        return RunIntegration(
            integrated=False, reason=reason, target=current, sha=None, detail=detail,
        )

    return RunIntegration(
        integrated=True, reason="", target=current,
        sha=_git_result(repo_root, "rev-parse", "HEAD").stdout.strip(),
        detail=(merge.stdout or "").strip(),
    )


def release_run_worktree(
    *, repo_root: Path, workspace: Path, delete_branch: bool = False
) -> None:
    """Give back a run's checkout. Best-effort: cleanup must never break a run.

    ``delete_branch`` only after the work has landed -- removing the checkout is
    reversible, dropping the branch is not.
    """
    branch = _git_result(workspace, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git_result(repo_root, "worktree", "remove", "--force", str(workspace))
    if delete_branch and branch.startswith(RUN_BRANCH_PREFIX):
        _git_result(repo_root, "branch", "-D", branch)
    # Without this a wiped runs root (…/tmp is not tmpfs here, but it is swept)
    # leaves the worktree registered forever, and the name cannot be reused.
    _git_result(repo_root, "worktree", "prune")


def sweep_run_worktrees(
    *,
    repo_root: Path,
    runs_root: Path,
    max_age_seconds: float,
    now: float,
    durable_root: Path | None = None,
    target_branch: str | None = None,
) -> list[str]:
    """Drop run worktrees nobody came back for. Returns what was removed.

    Age alone is not evidence that a checkout is disposable, so anything holding
    uncommitted work is left where it is: an abandoned run with edits in it is
    exactly the thing an operator comes looking for later.
    """
    if not runs_root.exists():
        return []
    removed: list[str] = []
    target = target_branch or _git_result(repo_root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    artifact_root = Path(durable_root or "/home/hermes/.hermes/controlled-runs")
    for candidate in sorted(runs_root.iterdir()):
        if not candidate.is_dir() or not (candidate / ".git").exists():
            continue
        if now - candidate.stat().st_mtime <= max_age_seconds:
            continue
        try:
            if classify_dirty(candidate):
                continue
        except Exception:
            continue
        branch = _git_result(candidate, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch.startswith(RUN_BRANCH_PREFIX) and target and not _branch_reachable_from_target(
            repo_root=repo_root,
            branch=branch,
            target=target,
        ):
            run_id = branch[len(RUN_BRANCH_PREFIX):]
            if not is_verified_change_artifact(
                durable_root=artifact_root / run_id,
                repo_path=candidate,
                canonical_repo_path=repo_root,
            ):
                # A clean worktree can still be the only checkout containing a
                # committed run branch.  Keep it until a verified durable
                # artifact exists or the branch has reached the target.
                continue
        release_run_worktree(repo_root=repo_root, workspace=candidate)
        if not candidate.exists():
            removed.append(str(candidate))
    return removed


def _branch_reachable_from_target(*, repo_root: Path, branch: str, target: str) -> bool:
    return _git_result(repo_root, "merge-base", "--is-ancestor", branch, target).returncode == 0


class CommitGateAuthorization(SimpleNamespace):
    """What the operator's commit approval may actually do.

    ``allow_commit`` -- may the commit be made at all; ``approved_for_landing``
    -- may its branch move the mainline; ``is_run_worktree`` -- which of the two
    blast radii we are in; ``detail`` -- the outstanding findings, for the reply.
    """


def commit_gate_authorization(
    *, workspace: Path, session_id: str, debt_root: Path | None = None
) -> CommitGateAuthorization:
    """Weigh a commit request against the session's outstanding review findings.

    The two cases differ by blast radius, so they get different answers:

    * inside a run worktree the commit is contained -- it lands on
      ``hermes-run/<id>`` and moves nothing, so debt gates the *landing*, not the
      commit. Refusing here would only strand the pipeline's own work.
    * in the live repository the commit moves the mainline immediately. That is
      the 25 July shape, where a commit reached origin with a changes_requested
      still outstanding, so debt has to stop it outright.

    Fails open. A missing session id or an unreadable debt store is absence of
    evidence, not evidence of debt, and turning a cache problem into an
    un-diagnosable refusal to commit is worse than the thing being guarded.
    """
    workspace = Path(workspace)
    branch = _git_result(workspace, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    is_run_worktree = branch.startswith(RUN_BRANCH_PREFIX)

    allowed, detail = True, ""
    session = str(session_id or "").strip()
    if session:
        try:
            from hermes_cli.review_gate import (
                ReviewGateState,
                authorize_operation,
                default_debt_root,
            )

            root = debt_root if debt_root is not None else default_debt_root()
            decision = authorize_operation(
                ReviewGateState.load(session, root),
                operation_category="git_remote_mutation",
            )
            allowed, detail = decision.allowed, decision.detail
        except Exception:
            allowed, detail = True, ""

    return CommitGateAuthorization(
        allow_commit=is_run_worktree or allowed,
        approved_for_landing=allowed,
        is_run_worktree=is_run_worktree,
        detail=detail,
    )


def _repo_root_of(workspace: Path) -> Path | None:
    """The repository a worktree belongs to, or None if this is not one.

    Call sites (the Slack reaction and the gateway reply intercept) only ever
    know the workspace recorded in the commit-gate marker, so the mapping back to
    the repository has to happen here. ``--git-common-dir`` is the one that
    resolves across linked worktrees; ``--git-dir`` would give the per-worktree
    admin directory instead.
    """
    result = _git_result(workspace, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if result.returncode != 0:
        return None
    common = Path(result.stdout.strip())
    return common.parent if common.name == ".git" else None


def main_checkout_of(workspace: Path) -> Path | None:
    """The repository's MAIN checkout for any path inside it, or None.

    Public name for `_repo_root_of` -- the ops gate needs exactly this mapping
    and must not grow a second implementation of it. A linked worktree (a per-run
    `hermes-run/*` one included) resolves to the main worktree; the main worktree
    resolves to itself, so applying this twice is a no-op. Anything git cannot
    answer for (not a repository, missing directory, a repo whose common dir is
    not named `.git`) is None -- the caller decides what to do with "unknown"
    rather than getting a guess.
    """
    try:
        return _repo_root_of(Path(workspace))
    except OSError:
        # A recorded path that no longer exists (the run worktree was swept)
        # makes subprocess fail before git ever runs.
        return None


def land_run_branch_after_commit(
    *, workspace: Path, approved: bool
) -> RunIntegration | None:
    """Land the run this workspace belongs to, then give the checkout back.

    Returns None when the workspace is not a run worktree, so the in-place commit
    path is left exactly as it was.

    ``approved`` is passed in rather than derived. Today the commit gate fills it
    from the operator's own approval, which is no weaker than the behaviour this
    replaces (a commit straight onto the live branch, with no reviewer gate at
    all). Task 6 replaces it with the reviewer's verdict; until then the seam is
    explicit instead of guessed.

    The branch is only dropped once its commits are on the mainline. Every other
    outcome keeps both the branch and the checkout, so a refused landing is
    always recoverable by hand.
    """
    workspace = Path(workspace)
    branch = _git_result(workspace, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if not branch.startswith(RUN_BRANCH_PREFIX):
        return None
    repo_root = _repo_root_of(workspace)
    if repo_root is None:
        return RunIntegration(
            integrated=False, reason="repo_root_unresolved", target=None, sha=None,
            detail=f"cannot resolve the repository behind {workspace}",
        )

    result = integrate_run_branch(repo_root=repo_root, branch=branch, approved=approved)
    if result.integrated:
        release_run_worktree(repo_root=repo_root, workspace=workspace, delete_branch=True)
    return result


def describe_run_integration(result: RunIntegration | None) -> str:
    """One operator-facing line. Empty when there is nothing to say."""
    if result is None:
        return ""
    if result.integrated:
        return f"↪️ Влито в {result.target} ({result.sha})."
    hints = {
        "review_not_approved": "ревью не одобрено",
        "not_fast_forward": "основная ветка ушла вперёд",
        "merge_refused": "мерж отклонён",
        "run_branch_missing": "ветка прогона не найдена",
        "target_detached": "репозиторий не на ветке",
        "repo_root_unresolved": "не удалось определить репозиторий",
    }
    why = hints.get(result.reason, result.reason or "неизвестно")
    return f"⏸ Не влито ({why}). Работа сохранена в ветке прогона."


def _exclude_locally(workspace: Path, pattern: str) -> None:
    """Ignore a path in this worktree only, without touching tracked .gitignore.

    The venv symlink is injected by us, so the baseline check must not see it as
    untracked dirt. Relying on the target repo happening to gitignore "venv"
    would make a clean worktree depend on something we do not control -- and
    ``classify_dirty`` is precisely the gate we are trying to keep meaningful.

    ``rev-parse --git-path`` resolves info/exclude correctly for a linked
    worktree, where ``.git`` is a file rather than a directory.
    """
    exclude = Path(_git_stdout(workspace, "rev-parse", "--git-path", "info/exclude"))
    if not exclude.is_absolute():
        exclude = workspace / exclude
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if pattern in existing.split():
        return
    exclude.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    exclude.write_text(f"{existing}{prefix}{pattern}\n", encoding="utf-8")


def _ensure_workspace_layout(*, workspace: Path, repo_root: Path) -> None:
    expected_venv = (repo_root / "venv").resolve(strict=False)
    workspace_venv = workspace / "venv"
    if workspace_venv.is_symlink():
        if workspace_venv.resolve(strict=False) == expected_venv:
            return
        workspace_venv.unlink()
    elif workspace_venv.exists():
        raise ValueError("workspace_venv_conflict")
    workspace_venv.symlink_to(repo_root / "venv")
    # Only meaningful for a real git worktree; the fake smoke scaffold gitignores
    # venv itself and a failure here must not break workspace preparation.
    try:
        _exclude_locally(workspace, "venv")
    except (ValueError, OSError):
        pass


def _build_bridge_runtime_plans(*, loaded_specs: Any, pipeline_session_id: str | None, user_message: str, config: dict[str, Any] | None) -> dict[str, Any]:
    session = SimpleNamespace(
        pipeline_session_id=pipeline_session_id or "autonomous",
        trace_id=pipeline_session_id or "autonomous",
        pipeline_id=ENGINEERING_PIPELINE_ID,
        user_message=user_message,
    )
    return {
        subagent_id: build_runtime_factory_plan(
            session=session,
            planned_step=SimpleNamespace(subagent_id=subagent_id, step_kind=step_kind),
            subagent_spec=loaded_specs.subagent_specs.get(subagent_id),
            config=config,
        )
        for subagent_id, step_kind in ((ENGINEER_SUBAGENT_ID, "engineer"), (REVIEWER_SUBAGENT_ID, "reviewer"))
    }


def _default_runtime_context(workspace: Path) -> dict[str, Any]:
    return {
        "real_executor_ready": False,
        "blocked_reason": "real_subagent_executor_missing",
        "allow_real_provider_execution": False,
        "request_real_provider_execution": False,
        "allowed_real_providers": (),
        "allowed_real_models": (),
        "allowed_real_providers_by_role": {},
        "allowed_real_models_by_role": {},
        "allowed_real_providers_by_subagent": {},
        "allowed_real_models_by_subagent": {},
        "allow_mutations": False,
        "mutation_workspace": str(workspace),
        "allow_test_commands": False,
        "test_workspace": str(workspace),
        "allow_model_escalation": False,
    }


def _allow_real_provider_execution(config: dict[str, Any] | None) -> bool:
    return bool((((config or {}).get("pipelines") or {}).get("execution") or {}).get("allow_real_provider_execution", False))


def _git(cwd: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, encoding="utf-8", capture_output=True, check=False)
    if result.returncode != 0:
        raise ValueError("workspace_git_setup_failed")


def _git_stdout(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, encoding="utf-8", capture_output=True, check=False)
    if result.returncode != 0:
        raise ValueError("workspace_git_setup_failed")
    return result.stdout.strip()
