"""Fork: what finalize_turn adds on top of upstream -- the review gate and the
audience policy for engineering footers.

Called from upstream's ``agent/turn_finalizer.py`` at three points:

* ``snapshot_dirty_baseline`` -- from ``build_turn_context`` before the model
  runs, so the review gate can tell this turn's edits from a work-in-progress
  diff that was already in the repo.
* ``review_turn`` -- inside the persist step, after the transcript tail is
  closed and before micro-compaction and ``_persist_session``, so a blocked
  turn is persisted with its block message.
* ``footer_policy`` / ``append_execution_locus`` -- around upstream's
  file-mutation footer and turn-completion explainer.

The bodies moved from the pre-v0.22.5 ``finalize_turn``. Dropped on the way:
``agent._turn_runtime_request``, which nothing ever set (every read saw
``None``), and the opt-in role debug header, which is off in production.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agent.conversation_loop")

#: Operation categories that make an engineer turn a review-gate candidate.
_REVIEWED_MUTATIONS = frozenset(
    {"repo_mutation", "git_remote_mutation", "normal_operational_mutation", "security_critical_mutation"}
)


def snapshot_dirty_baseline(agent: Any) -> None:
    """Record what the worktree already had dirty BEFORE the agent runs.

    The review gate subtracts this so an unrelated work-in-progress diff left in
    the repo is not attributed to this session (2026-07-19 rebase-cron block).
    """
    try:
        from hermes_cli.review_gate import snapshot_material_dirty_paths

        agent._turn_dirty_baseline = snapshot_material_dirty_paths()
    except Exception:
        agent._turn_dirty_baseline = []


def review_turn(
    agent: Any,
    *,
    final_response: str,
    messages: list,
    user_message: Any,
    original_user_message: Any,
) -> tuple[str, bool]:
    """Run the review gate on a finished turn. Returns ``(final_response, blocked)``.

    A blocked turn gets the gate's block message in place of the model's answer
    and an assistant message carrying the ``_review_gate`` packet. Any failure
    leaves the turn as it was: the gate must never break a turn.
    """
    try:
        from hermes_cli.profile_execution import build_role_execution_plan
        from hermes_cli.review_gate import (
            ReviewGateState,
            build_review_gate_evaluation_log_fields,
            debt_key,
            decision_to_dict,
            default_debt_root,
            evaluate_review_gate,
            render_review_gate_block_message,
        )

        review_task = (
            original_user_message
            if isinstance(original_user_message, str) and original_user_message.strip()
            else str(user_message or "")
        )
        review_plan = build_role_execution_plan(review_task)
        # Review debt belongs to the session, not the turn: a rework turn that
        # edits nothing new still owes the findings a re-review. Best-effort --
        # a missing or unreadable store just means the gate behaves as it did
        # before debt existed.
        _debt_root = None
        _review_state = None
        try:
            _debt_root = default_debt_root()
            _review_state = ReviewGateState.load(
                debt_key(
                    gateway_session_key=getattr(agent, "_gateway_session_key", None),
                    session_id=getattr(agent, "session_id", None),
                ),
                _debt_root,
            )
        except Exception:
            _review_state = None

        review_gate = evaluate_review_gate(
            review_plan,
            messages,
            runtime_request=None,
            baseline_dirty_paths=getattr(agent, "_turn_dirty_baseline", None),
            state=_review_state,
        )
        if _review_state is not None and _debt_root is not None:
            try:
                _review_state.save(_debt_root)
            except Exception:
                pass
        log_fields = build_review_gate_evaluation_log_fields(review_gate)
        logger.info(
            "review gate evaluation: session=%s review_gate.mode=%s "
            "review_gate.reviewer_tier=%s automatic_review_invoked=%s "
            "automatic_review_verdict=%s reviewer_provider=%s reviewer_model=%s "
            "selected_role=%s changed_paths_count=%d blocking=%s status=%s",
            getattr(agent, "session_id", None) or "-",
            log_fields["review_gate.mode"],
            log_fields["review_gate.reviewer_tier"],
            log_fields["automatic_review_invoked"],
            log_fields["automatic_review_verdict"],
            log_fields["reviewer_provider"],
            log_fields["reviewer_model"],
            review_plan.selected_role,
            log_fields["changed_paths_count"],
            log_fields["blocking"],
            log_fields["status"],
        )
        if not review_gate.blocking:
            return final_response, False

        blocked_response = render_review_gate_block_message(review_gate)
        messages.append(
            {
                "role": "assistant",
                "content": blocked_response,
                "_review_gate": {
                    "required": True,
                    "status": review_gate.status,
                    "mode": review_gate.mode,
                    "review_required": True,
                    "selected_role": review_plan.selected_role,
                    "reviewer_tier": review_gate.reviewer_tier,
                    "reviewer_provider": review_gate.reviewer_provider,
                    "reviewer_model": review_gate.reviewer_model,
                    "changed_paths": list(review_gate.changed_paths),
                    "packet": decision_to_dict(review_gate),
                    "packet_hash": review_gate.packet_hash,
                    "automatic_review_invoked": review_gate.automatic_review_invoked,
                    "automatic_review_verdict": review_gate.automatic_review_verdict,
                    "reviewer_summary": review_gate.reviewer_summary,
                    "reviewer_findings": list(review_gate.reviewer_findings),
                    "required_changes": list(review_gate.required_changes),
                    "tests_required": list(review_gate.tests_required),
                    "approval_sensitive": review_gate.approval_sensitive,
                    "user_override": review_gate.user_override,
                    "review_error": review_gate.review_error,
                },
            }
        )
        logger.info(
            "review gate blocked completion: session=%s changed_paths=%d reviewer_tier=%s reviewer_model=%s/%s",
            getattr(agent, "session_id", None) or "-",
            review_gate.changed_path_count,
            review_gate.reviewer_tier,
            review_gate.reviewer_provider,
            review_gate.reviewer_model,
        )
        return blocked_response, True
    except Exception as review_gate_err:
        logger.warning("review gate evaluation failed: %s", review_gate_err)
        return final_response, False


def footer_policy(agent: Any) -> tuple[bool, str | None]:
    """Engineering footers stay off end-user channels.

    Returns ``(suppress_footers, explainer_notice)``. Computed once per
    finalization: three independent reads of the config are three chances to
    disagree. A failing predicate means "show everything", so an exception here
    cannot hide anything.

    ``explainer_notice`` is a separate decision from suppression. On a cron turn
    the explainer must NOT be replaced: ``cron/scheduler.py`` recognises an
    abnormally empty turn by comparing ``final_response`` for EQUALITY with the
    same formatter's text and blanks the answer on a match, so the job delivers
    nothing. Substitute another string and the equality fails -- the end user
    gets an apology instead of silence and the run is marked successful. So a
    cron turn drops the footers and keeps the explainer as it was.
    """
    try:
        from hermes_cli.run_evidence import (
            SUPPRESSED_TURN_NOTICE,
            cron_end_user_turn,
            engineering_footers_suppressed,
        )

        suppress = engineering_footers_suppressed(fallback_platform=getattr(agent, "platform", None))
        notice = SUPPRESSED_TURN_NOTICE if suppress and not cron_end_user_turn() else None
        return suppress, notice
    except Exception as _sup_err:
        logger.debug("engineering footer suppression check failed: %s", _sup_err)
        return False, None


def append_execution_locus(final_response: str, messages: list) -> str:
    """Say where this turn's checks ran.

    An observed fact, not the agent's claim: commands come from the turn's
    tool_calls, and ``terminal``/``execute_code`` run inside the container. On
    2026-07-28 the agent presented an ``EXIT=0`` measured in a sandbox with no
    browser environment as proof that something worked, and nobody asked where
    it had been measured. The block blocks nothing; it only keeps the place from
    going unsaid.
    """
    try:
        from hermes_cli.run_evidence import (
            observed_sandbox_commands,
            observed_write_refusals,
            render_execution_locus_block,
        )

        locus = render_execution_locus_block(
            observed_sandbox_commands(messages),
            write_refusals=observed_write_refusals(messages),
        )
        if locus:
            return final_response.rstrip() + "\n\n" + locus
    except Exception as _locus_err:
        logger.debug("execution locus block failed: %s", _locus_err)
    return final_response
