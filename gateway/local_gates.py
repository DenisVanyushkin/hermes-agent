"""Fork: operator text gates answered from the gateway's pending-reply slot.

The engineering pipeline, upstream-sync and baseline-doctor leave a gate armed
on disk (an ops plan, a commit, a sync decision, a cleanup action). The
operator answers it with a bare word in the chat. These functions recognize
that answer and act on it; anything else returns ``None`` and the message
continues as an ordinary turn.

Upstream calls ``pending_gate_reply`` from
``GatewayInboundMixin._hm_pending_reply_intercepts`` (gateway/run_inbound.py),
next to its own clarify and slash-confirm prompts. That slot runs after the
sender was authorized and before busy-session handling, so an answer that
arrives while a turn is running is not steered into that turn and does not
interrupt it, and events injected by plugins (``allow_gateway_control`` is
False) never reach it.

The function bodies moved here unchanged from ``GatewayRunner`` in the
pre-v0.22.5 ``gateway/run.py``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("gateway.run")

def operator_reply_text(message: str, raw_message: str | None) -> str:
    """Extract one canonical author body before any gate parser runs."""
    if isinstance(raw_message, str) and raw_message.strip():
        return raw_message.strip()
    if not isinstance(message, str):
        return ""
    # Some internal callers have only the assembled display message. Remove the
    # exact reply envelope once, so no parser can interpret quoted gate text.
    if message.startswith("[Replying to:"):
        end = message.find("]\n")
        if end < 0:
            end = message.find("]\r\n")
        if end >= 0:
            return message[end + 2:].strip()
    return message


def ops_approval_ack(message, source):
    """Выполнить или отменить одобренный план операций по ответу оператора.

    Возвращает строку-ack, когда ``message`` -- распознанный ответ гейту И план
    ожидает решения; иначе None, чтобы обычный путь продолжился.
    """
    try:
        import os as _os
        from pathlib import Path as _Path
        from hermes_cli import ops_gate_service
        from hermes_cli.ops_catalog import ResolvedOperation
        from hermes_cli.ops_executor import execute_operation

        action = ops_gate_service.parse_ops_reply(message)
        # «подтверждаю <op_id>» -- тоже ответ гейту, но parse_ops_reply о нём
        # знать не может: id операции виден только из pending-плана. Здесь --
        # дешёвая проверка префикса, чтобы не читать маркер на каждое
        # сообщение; само решение всегда через parse_destroy_confirmation.
        looks_like_confirmation = str(message or "").strip().casefold().startswith(
            ("подтверждаю", "confirm")
        )
        if not action and not looks_like_confirmation:
            return None
        pending = ops_gate_service.get_pending()
        if not pending:
            return None

        platform = str(getattr(source, "platform", "") or "")
        user_id = str(getattr(source, "user_id", "") or "")
        # Operator gate for Slack when an operator UID is configured; other
        # platforms fall through to the pending-marker gate (sole-operator setup).
        if platform == "slack" and (_os.getenv("HERMES_OPERATOR_SLACK_UID") or "").strip():
            if not ops_gate_service.is_operator(user_id):
                return None

        plan = list(pending.get("plan") or [])

        if action == "cancel":
            ops_gate_service.clear_pending()
            # Слова отмены -- единственное пересечение двух гейтов:
            # отмена/отмени/отменить/cancel принимаются и parse_commit_reply
            # (как discard). Двусмысленная отмена читается как «пусть ничего
            # не произойдёт», поэтому снимаем оба маркера: молча оставить
            # коммитный гейт висеть -- это решение за оператора, которого он
            # не принимал. Коммитный интерцепт вызываем явно, а не через
            # возврат None, чтобы ack был один и упоминал обе отмены.
            _commit_ack = commit_approval_ack(message, source)
            if _commit_ack:
                return "🚫 План операций отменён. Ничего не выполнено.\n" + _commit_ack
            return "🚫 План отменён. Ничего не выполнено."

        destroy_ids = [
            str(item.get("op_id"))
            for item in plan
            if str(item.get("risk") or "") == "destroy"
        ]
        confirmed = {
            op_id
            for op_id in destroy_ids
            if ops_gate_service.parse_destroy_confirmation(message, op_id)
        }
        if not action and not confirmed:
            # Префикс совпал, но это подтверждение не этого плана.
            return None
        if len(destroy_ids) > 1:
            # Одно сообщение подтверждает ровно один id: parse_destroy_confirmation
            # требует, чтобы сообщение было целиком «подтверждаю <op_id>». Значит
            # такой план неотвечаем в принципе -- взведённый маркер, на который
            # нельзя ответить, хуже отсутствующего (он ещё час ловит ответы и
            # отвечает отказом). Снимаем и просим разбить план.
            ops_gate_service.clear_pending()
            listed = ", ".join(destroy_ids)
            return (
                f"⛔ Не выполняю: в плане больше одной необратимой операции ({listed}). "
                "За раз подтверждается одна — перезапроси план по частям. Маркер снят."
            )

        unconfirmed = [op_id for op_id in destroy_ids if op_id not in confirmed]
        if unconfirmed:
            # Деструктив не выполняется по «выполни»: нужен явный эхо-повтор id.
            # Маркер НЕ снимаем -- оператор должен иметь возможность ответить.
            needed = ", ".join(f"«подтверждаю {op_id}»" for op_id in unconfirmed)
            return f"⛔ Не выполняю: план содержит необратимые операции. Ответь {needed}."

        # Path("") -> "." -- в проде это живой чекаут hermes-agent. Пустой
        # repo_path означает «неизвестно», а не «текущая директория». Резолвер
        # общий с сообщением гейта (resolve_operation_cwd), поэтому оператор
        # видел ровно ту директорию, в которой всё и выполнится; отказ вместо
        # догадки, если даже фолбэк не указывает на существующую директорию.
        from hermes_cli.ops_gate_message import resolve_operation_cwd

        repo_raw = str(pending.get("repo_path") or "").strip()
        repo = _Path(resolve_operation_cwd(repo_raw))
        if not repo.is_dir():
            ops_gate_service.clear_pending()
            return (
                f"⛔ Не выполняю: рабочая директория плана не определена ({repo_raw or 'пусто'}). "
                "Маркер снят — перезапроси операцию."
            )

        lines = []
        stopped_at = None
        try:
            for index, item in enumerate(plan):
                operation = ResolvedOperation(
                    op_id=str(item.get("op_id")),
                    risk=str(item.get("risk")),
                    argv=tuple(item.get("argv") or ()),
                    description=str(item.get("description") or ""),
                    irreversible=item.get("irreversible"),
                )
                try:
                    result = execute_operation(operation, cwd=repo)
                except Exception as exc:  # noqa: BLE001
                    # Широко, а не только OpsExecutionError: отсутствующий бинарник
                    # даёт FileNotFoundError, нехватка прав -- PermissionError. Утечка
                    # такого исключения во внешний except вернула бы None ПОСЛЕ того,
                    # как часть плана уже выполнилась, и оставила бы маркер взведённым:
                    # следующее «выполни» прогнало бы план с начала (второй
                    # service_restart, второй force-push).
                    lines.append(f"❌ {operation.op_id}: {type(exc).__name__}: {exc}")
                    stopped_at = index
                    break  # первая ошибка останавливает план: остальное могло на неё опираться
                marker = "✅" if result["status"] == 0 else "⚠️"
                lines.append(f"{marker} {operation.op_id} (exit {result['status']})")
                if result["output"]:
                    lines.append(f"```\n{result['output']}\n```")
                if result["status"] != 0:
                    stopped_at = index
                    break
        finally:
            # Исполнение началось -- маркер больше не должен быть отвечаемым ни при
            # каком исходе: ответ на него повторил бы уже выполненные операции.
            ops_gate_service.clear_pending()
        if stopped_at is not None:
            skipped = [str(item.get("op_id")) for item in plan[stopped_at + 1:]]
            lines.append(
                "⏹ План остановлен на первой ошибке, маркер снят."
                + (
                    f" Не выполнялись: {', '.join(skipped)} — перезапроси их отдельно."
                    if skipped
                    else ""
                )
            )
        return "\n".join(lines) or "Нечего выполнять."
    except Exception:
        # Возврат None здесь корректен только ДО начала исполнения: всё, что
        # может упасть после первой запущенной операции, ловится внутри цикла,
        # а маркер снимается в finally, поэтому «тихий» None уже не может
        # привести к повторному прогону выполненной части плана.
        logger.warning("ops approval intercept failed", exc_info=True)
        return None


def upstream_sync_triage_ack(message, source):
    """Answer a gate-triage proposal: ``apply fix`` or ``keep test``.

    When an upstream merge turns a fork test red, the host diagnoses it and
    proposes a patch to the test — it never applies one, because the same
    red test is equally likely to mean the merge dropped local behaviour and
    the test is the only sensor that noticed.

    The reply is matched WHOLE-MESSAGE (see parse_upstream_sync_triage_reply):
    "he wrote: apply fix" approves nothing. That strictness is also why this
    intercept runs BEFORE the decision-reply one — both gates can be armed at
    the same time and both answer to plain text, and a strict parser cannot
    swallow a message meant for the looser gate, while the reverse can happen.

    Returns an ack string when the message is an exact answer and a proposal
    is armed; otherwise ``None`` so the normal path proceeds.
    """
    try:
        from hermes_cli.upstream_sync_reply import (
            parse_upstream_sync_triage_reply,
            has_pending_upstream_triage,
            record_triage_decision,
            default_upstream_sync_state_dir,
        )

        answer = parse_upstream_sync_triage_reply(message)
        if not answer:
            return None
        state_dir = default_upstream_sync_state_dir()
        if not has_pending_upstream_triage(state_dir):
            return None

        source_dict = {
            "platform": str(getattr(source, "platform", "") or "") or None,
            "chat_id": str(getattr(source, "chat_id", "") or "") or None,
            "thread_id": str(getattr(source, "thread_id", "") or "") or None,
            "user_id": str(getattr(source, "user_id", "") or "") or None,
        }
        outcome = record_triage_decision(state_dir, answer, source_dict)
    except Exception:
        logger.warning("upstream-sync triage intercept failed", exc_info=True)
        return None

    if answer == "keep_test":
        return ("\u2705 Understood — the failing test stays as it is. Nothing was applied; the "
                "merge clone is preserved in the upstream-sync state dir for you to fix the "
                "merge itself.")
    if outcome.get("requested"):
        return ("\u2705 Applying the proposed test patch: the host amends the merge with it and "
                "re-runs the fork-test gate. One attempt \u2014 if the gate is still red the merge "
                "is left unlanded and the clone preserved. The result lands in this thread.")
    return "\u26a0\ufe0f Not applied: " + (outcome.get("reason") or "nothing to apply") + "."


def upstream_sync_invariant_ack(message, source):
    """Record one fingerprint-bound structural-gate receipt, if armed."""
    try:
        from hermes_cli.upstream_sync_reply import (
            parse_upstream_sync_ack_reply,
            has_pending_upstream_invariant_ack,
            record_invariant_ack,
            default_upstream_sync_state_dir,
        )
        finding_id = parse_upstream_sync_ack_reply(message)
        if not finding_id:
            return None
        state_dir = default_upstream_sync_state_dir()
        if not has_pending_upstream_invariant_ack(state_dir):
            return None
        source_dict = {
            "platform": str(getattr(source, "platform", "") or "") or None,
            "chat_id": str(getattr(source, "chat_id", "") or "") or None,
            "thread_id": str(getattr(source, "thread_id", "") or "") or None,
            "user_id": str(getattr(source, "user_id", "") or "") or None,
        }
        outcome = record_invariant_ack(state_dir, finding_id, source_dict)
    except Exception:
        logger.warning("upstream-sync invariant ack intercept failed", exc_info=True)
        return None
    if outcome.get("requested"):
        return f"✅ Receipt recorded for {finding_id}; the host will re-check this exact merge finding."
    if outcome.get("duplicate"):
        return f"✅ Receipt for {finding_id} was already recorded."
    return "⚠️ Not recorded: " + (outcome.get("reason") or "the finding is stale") + "."


def upstream_sync_decision_ack(message, source):
    """Record an operator decision reply and hand the apply to the host.

    Returns an ack string when ``message`` is a recognized decision reply and
    a decision is pending; otherwise ``None`` so the normal path proceeds.
    Nothing is spawned here any more: the answers go into pending.json and,
    once every feature has one, a finalize request with action
    ``apply-decisions`` — the host finalizer builds and lands the merge and
    reports back in the same Slack thread. (Until 2026-08-15 this queued a
    sandboxed one-shot agent for the apply; the agent held the state and
    died with the gateway restart, which is how results and memory got lost.)
    """
    try:
        from hermes_cli.upstream_sync_reply import (
            parse_upstream_sync_decision_reply,
            has_pending_upstream_decision,
            record_operator_decisions,
            default_upstream_sync_state_dir,
        )

        decisions = parse_upstream_sync_decision_reply(message)
        if not decisions:
            return None
        state_dir = default_upstream_sync_state_dir()
        if not has_pending_upstream_decision(state_dir):
            return None

        source_dict = {
            "platform": str(getattr(source, "platform", "") or "") or None,
            "chat_id": str(getattr(source, "chat_id", "") or "") or None,
            "thread_id": str(getattr(source, "thread_id", "") or "") or None,
            "user_id": str(getattr(source, "user_id", "") or "") or None,
        }
        outcome = record_operator_decisions(state_dir, decisions, source_dict)
    except Exception:
        logger.warning("upstream-sync decision intercept failed", exc_info=True)
        return None

    decision_line = ", ".join(f"{fid}: {opt}" for fid, opt in sorted(decisions.items()))
    lines = ["✅ Recorded your upstream-sync decisions (" + decision_line + ")."]
    if outcome.get("unknown"):
        lines.append("Ignored unknown feature number(s): " + ", ".join(str(n) for n in outcome["unknown"]) + ".")
    if outcome.get("still_awaiting"):
        lines.append("Still waiting for: " + ", ".join(outcome["still_awaiting"])
                     + ". The apply starts once every feature has an answer.")
    elif outcome.get("requested"):
        lines.append("The host is applying now: merge in a clone, model-resolved hunks, fork tests, "
                     "backup ref, fast-forward, publish, smoketest. The result lands in this thread.")
    else:
        lines.append("An apply is already in flight; your decision is recorded and will be picked up.")
    return "\n".join(lines)


def baseline_doctor_ack(message, source):
    """Run baseline-doctor (or apply a parked action) on an operator text command.

    Reactions are handled only by the Slack adapter, and \U0001F9F9 is a premium emoji
    on WhatsApp, so a text command is the only trigger available on every
    platform. Returns None for anything that is not an exact command, so
    ordinary messages reach the agent untouched.
    """
    try:
        from pathlib import Path as _Path

        from hermes_cli import baseline_doctor_service as _svc

        action = _svc.parse_doctor_command(message)
        if not action:
            return None

        repo = _Path(__file__).resolve().parent.parent
        if action == "run":
            result = _svc.run_doctor(repo)
            report = _svc.render_report(result, reactions=False)
            if not result["clean"] and result["remaining"]:
                _svc.record_pending(_svc.TEXT_PENDING_KEY, result["remaining"])
            return report

        remaining = _svc.pop_pending(_svc.TEXT_PENDING_KEY)
        if not remaining:
            return None
        applied = _svc.apply_action(repo, action, remaining)
        recheck = _svc.run_doctor(repo)
        status = "\u2705" if applied.get("ok") else "\u26a0\ufe0f"
        return (
            f"{status} {action}: {len(applied.get('paths') or [])} file(s). "
            f"{applied.get('detail') or ''}\n\n"
            + _svc.render_report(recheck, reactions=False)
        )
    except Exception:
        logger.warning("baseline-doctor command intercept failed", exc_info=True)
        return None


def commit_approval_ack(message, source):
    """Commit or discard the pending commit-gate deliverable on an operator reply.

    Returns an ack string when ``message`` is a recognized commit/discard reply
    AND a commit is pending; otherwise ``None`` so the normal path proceeds.
    Commits inline (no new pipeline run). Push only on an explicit push reply.
    """
    try:
        import os as _os
        from pathlib import Path as _Path
        from hermes_cli import commit_gate_service

        action = commit_gate_service.parse_commit_reply(message)
        if not action:
            return None
        pending = commit_gate_service.get_pending()
        if not pending:
            return None

        platform = str(getattr(source, "platform", "") or "")
        user_id = str(getattr(source, "user_id", "") or "")
        # Operator gate for Slack when an operator UID is configured; other
        # platforms fall through to the pending-marker gate (sole-operator setup).
        if platform == "slack" and (_os.getenv("HERMES_OPERATOR_SLACK_UID") or "").strip():
            if not commit_gate_service.is_operator(user_id):
                return None

        workspace = str(pending.get("workspace_path") or "").strip()
        repo = _Path(workspace) if workspace else _Path(__file__).resolve().parent.parent
        changed_files = list(pending.get("changed_files") or [])

        if action == "discard":
            commit_gate_service.apply_discard(repo=repo, changed_files=changed_files)
            commit_gate_service.clear_pending()
            return "🗑 Изменения отклонены и убраны в stash. Ничего не закоммичено."


        # The operator's approval is necessary, not sufficient: an unresolved
        # changes_requested still blocks a commit that would move the mainline.
        # A commit inside a run worktree is contained, so it proceeds and the
        # debt gates the landing instead.
        try:
            from hermes_cli.pipeline_autonomous_execution import commit_gate_authorization

            _auth = commit_gate_authorization(
                workspace=repo, session_id=str(pending.get("session_id") or "")
            )
        except Exception:
            _auth = None
        if _auth is not None and not _auth.allow_commit:
            return (
                "\u26d4 \u041d\u0435 \u043a\u043e\u043c\u043c\u0438\u0447\u0443: \u0440\u0435\u0432\u044c\u044e \u043d\u0435 \u0437\u0430\u043a\u0440\u044b\u0442\u043e.\n"
                + (_auth.detail or "")
            )
        result = commit_gate_service.apply_commit(
            repo=repo,
            changed_files=changed_files,
            commit_message=str(pending.get("commit_message") or "chore: controlled-pipeline change"),
            push=(action == "commit_push"),
        )
        if not result.get("committed"):
            return f"⚠️ Не удалось закоммитить: {result.get('detail') or 'unknown error'}"
        commit_gate_service.clear_pending()
        # The run committed inside its own worktree; land it on the mainline.
        # No-op (None) for the in-place commit path.
        try:
            from hermes_cli.pipeline_autonomous_execution import (
                describe_run_integration,
                land_run_branch_after_commit,
            )

            _landed = describe_run_integration(
                land_run_branch_after_commit(
                    workspace=repo,
                    approved=bool(_auth.approved_for_landing) if _auth is not None else True,
                )
            )
        except Exception:
            _landed = ""
        sha = result.get("sha") or "?"
        n = len(changed_files)
        _suffix = f"\n{_landed}" if _landed else ""
        if action == "commit_push":
            if result.get("pushed"):
                return f"✅ Закоммичено и запушено: {sha} ({n} файлов).{_suffix}"
            return f"✅ Закоммичено: {sha} ({n} файлов). ⚠️ Пуш не удался: {result.get('push_detail') or 'unknown'}{_suffix}"
        return f"✅ Закоммичено: {sha} ({n} файлов). Пуш не делал (скажи «запушь», если нужно).{_suffix}"
    except Exception:
        logger.warning("commit-approval intercept failed", exc_info=True)
        return None


# The triage gate answers to a bare word and is matched whole-message;
# the decision gate's parser is looser. Strict first: it cannot take an
# answer meant for the other one, but the loose one could. The cancellation
# words overlap, so the strict parser gets the first refusal: право первого отказа
# принадлежит тому, кто ошибается реже; this preserves the
# пересечение semantics that prevented one gate from stealing another reply.
# Ops goes first of all: its cancel also clears the commit gate (see
# ops_approval_ack).
_GATES = (
    ops_approval_ack,
    upstream_sync_triage_ack,
    upstream_sync_invariant_ack,
    upstream_sync_decision_ack,
    baseline_doctor_ack,
    commit_approval_ack,
)


def answer(text: str, source: Any) -> Optional[str]:
    """The first gate that recognizes ``text`` answers it; ``None`` otherwise."""
    for gate in _GATES:
        ack = gate(text, source)
        if ack is not None:
            logger.info("operator gate %s handled a reply: platform=%s",
                        gate.__name__, getattr(source, "platform", ""))
            return ack
    return None


async def pending_gate_reply(runner: Any, event: Any, source: Any) -> Optional[str]:
    """Answer an armed gate from the pending-reply slot, off the event loop.

    An ops plan or a commit can run for minutes (git, service restarts), so the
    gates never run on the loop.
    """
    text = operator_reply_text(getattr(event, "text", None) or "", None)
    if not text.strip():
        return None
    return await runner._run_in_executor_with_context(answer, text, source)
