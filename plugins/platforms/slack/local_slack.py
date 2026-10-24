"""Fork: what this deployment's Slack adapter adds on top of upstream.

``SlackForkMixin`` is mixed into ``SlackAdapter`` (plugins/platforms/slack/
adapter.py), which calls in at a few marked sites:

* reactions -- one pipeline per reaction event, ahead of upstream's own
  reaction handling: job-intel vacancy feedback, idea capture (👍 → Google Doc),
  vacancy triggers (🔍/👍 → recruiter task in the card's thread), baseline
  doctor (🧹, then 📥/🙈/📦), and the commit gate (✅/❌);
* the job-intel negative-feedback thread replies, consumed before the agent;
* ``msg_too_long`` on an edit: one strictly shorter plain-text retry with a
  visible truncation marker;
* the workspace domain → team id map that permalink resolution
  (gateway/local_pipelines.py) reads.

The bodies moved from the pre-v0.22.5 adapter. One change on the way: a ✅
the commit gate refuses (unresolved review) now posts its refusal -- the old
code returned the text from the handler, so the operator got silence.
"""

from __future__ import annotations

import importlib.util as _ilu
import json
import logging
import os
from pathlib import Path as _Path
from typing import Any, Dict

from hermes_cli import commit_gate_service
from hermes_cli.baseline_doctor_service import apply_action as _apply_action
from hermes_cli.baseline_doctor_service import (
    classify_action,
    is_block_message,
    is_operator,
    is_trigger_reaction,
    pop_pending,
    record_pending,
)

logger = logging.getLogger("plugins.platforms.slack.adapter")

_AGENT_REPO = _Path(__file__).resolve().parents[3]


def _load_run_baseline_doctor():
    spec = _ilu.spec_from_file_location("baseline_doctor", _AGENT_REPO / "scripts" / "baseline_doctor.py")
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.run_doctor


def run_baseline_doctor():
    return _load_run_baseline_doctor()(_AGENT_REPO)


def _render_doctor_report(result: dict) -> str:
    lines = ["🧹 *Baseline doctor*"]
    if result["fixed"]:
        lines.append(f"✅ Fixed: chowned {len(result['fixed'])} root-owned file(s)")
    if result["clean"]:
        lines.append("✅ Baseline clean — retry the request.")
        return "\n".join(lines)
    lines.append("⚠️ Remaining (blocks run):")
    for r in result["remaining"]:
        lines.append(f"  • {r['path']} [{r['category']}] — {r.get('hint','')}")
    lines.append("React:  📥 commit all · 🙈 gitignore all · 📦 stash all")
    return "\n".join(lines)


def workspace_domain_from_auth(auth_response: Any) -> str:
    """The ``<domain>`` of ``https://<domain>.slack.com/`` from an auth.test reply, or ""."""
    workspace_url = str((auth_response or {}).get("url") or "").strip()
    if not workspace_url:
        return ""
    from urllib.parse import urlparse

    hostname = (urlparse(workspace_url).hostname or "").casefold()
    suffix = ".slack.com"
    if hostname.endswith(suffix):
        return hostname[: -len(suffix)]
    return ""


def slack_provider_error_code(error: BaseException) -> str:
    """Extract a bounded Slack Web API error code from an exception."""
    response = getattr(error, "response", None)
    response_get = getattr(response, "get", None)
    if callable(response_get):
        try:
            code = str(response_get("error") or "").strip()
        except Exception:
            code = ""
        if code:
            return code
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        code = str(data.get("error") or "").strip()
        if code:
            return code
    # Keep this fallback limited to provider codes that have explicit
    # handling; never copy the full exception into a safe diagnostic.
    message = str(error).lower()
    for code in ("msg_too_long", "invalid_blocks", "too_many_blocks"):
        if code in message:
            return code
    return ""


class SlackForkMixin:
    """Fork behaviour for ``SlackAdapter``; relies on its ``_get_client``,
    ``_add_reaction``, ``_handle_slack_message``, ``_team_bot_user_ids``,
    ``_bot_user_id``, ``_channel_team`` and ``MAX_MESSAGE_LENGTH``."""

    # ── workspace domain → team id ──────────────────────────────────────

    def _remember_workspace_domain(self, auth_response: Any, team_id: str) -> None:
        domain = workspace_domain_from_auth(auth_response)
        if domain and team_id:
            self._workspace_team_ids[domain] = team_id

    def resolve_workspace_team_id(self, workspace_domain: str) -> str:
        """Resolve a permalink workspace slug to an authenticated team id.
        Authenticated auth.test data owns this mapping; permalink text never
        chooses a client directly."""
        domain = str(workspace_domain or "").strip().casefold()
        return self._workspace_team_ids.get(domain, "")

    # ── msg_too_long on edit ────────────────────────────────────────────

    _slack_provider_error_code = staticmethod(slack_provider_error_code)

    def _shorten_slack_text_for_retry(self, text: str) -> str:
        """Return one strictly shorter plain-text retry with a visible marker."""
        marker = "\n\n_[Reply truncated: Slack shortened this update after msg_too_long.]_"
        if len(text) <= 1:
            return ""
        # Keep the retry below both the provider limit and the rejected text's
        # length. The marker is deliberately part of the visible fallback so a
        # successful recovery cannot look like a complete answer.
        prefix_limit = min(
            len(text) - 1,
            max(1, self.MAX_MESSAGE_LENGTH - len(marker)),
            max(1, len(text) - len(marker) - 1),
        )
        candidate = text[:prefix_limit].rstrip() + marker
        if len(candidate) >= len(text):
            # Extremely short content cannot fit the full marker and remain
            # shorter; preserve the strict retry invariant in that edge case.
            return text[: max(1, len(text) - 1)]
        return candidate

    async def _retry_too_long_edit(self, client_fn, chat_id: str, message_id: str, formatted: str,
                                   update_kwargs: dict, *, finalize: bool, metadata) -> Any:
        """After ``msg_too_long``: one shortened plain-text edit. Returns the SendResult to
        report when the shortened edit is still too long, else None (success)."""
        from gateway.platforms.base import SendResult

        retry_text = self._shorten_slack_text_for_retry(formatted)
        retry_kwargs: Dict[str, Any] = {"channel": chat_id, "ts": message_id, "text": retry_text}
        if "blocks" in update_kwargs:
            # Explicitly clear stale blocks on the existing message while
            # retrying the shortened plain-text payload.
            retry_kwargs["blocks"] = []
        diagnostic = {
            "provider": "slack",
            "error_code": "msg_too_long",
            "original_text_length": len(formatted),
            "retry_text_length": len(retry_text),
            "original_block_count": len(update_kwargs.get("blocks") or [])
            if isinstance(update_kwargs.get("blocks"), list) else 0,
            "retry_block_count": 0,
            "fallback_attempted": True,
        }
        logger.info(
            "[Slack] msg_too_long; retrying shortened edit text_length=%d->%d blocks=%d->0",
            diagnostic["original_text_length"], diagnostic["retry_text_length"],
            diagnostic["original_block_count"],
        )
        try:
            await client_fn().chat_update(**retry_kwargs)
        except Exception as retry_error:
            if slack_provider_error_code(retry_error) != "msg_too_long":
                raise
            if finalize:
                await self._clear_thread_status_quietly(chat_id, metadata)
            logger.warning(
                "[Slack] shortened edit still rejected as msg_too_long text_length=%d->%d",
                diagnostic["original_text_length"], diagnostic["retry_text_length"],
            )
            return SendResult(
                success=False, error="slack_api_error:msg_too_long", raw_response=diagnostic,
                retryable=False, error_kind="too_long",
            )
        return None

    # ── job-intel negative-feedback thread replies ──────────────────────

    def _consume_feedback_thread_reply(self, event: dict) -> bool:
        """True when a thread reply to an open job-intel feedback prompt was consumed.

        Such replies are parsed by the feedback loop and must not reach the
        agent. Commands (/, !) always fall through, and any error falls back
        to normal handling.
        """
        thread_ts = str(event.get("thread_ts") or "").strip()
        raw_text = str(event.get("text") or "")
        if not (
            thread_ts
            and thread_ts != str(event.get("ts") or "")
            and raw_text.strip()
            and not raw_text.lstrip().startswith(("/", "!"))
        ):
            return False
        try:
            from job_intel.cli import run_feedback_thread_reply

            feedback_result = json.loads(run_feedback_thread_reply({
                "channel": event.get("channel"),
                "thread_ts": thread_ts,
                "user": event.get("user"),
                "text": raw_text,
            }))
            status = str(feedback_result.get("status") or "")
            if status in {"classified", "clarification_requested", "stored_unclassified", "ignored_empty"}:
                logger.info(
                    "job_intel_feedback_thread_reply handled status=%s channel=%s thread_ts=%s",
                    status, event.get("channel"), thread_ts,
                )
                return True
        except Exception:
            logger.exception("job_intel_feedback_thread_reply failed; falling back to normal handling")
        return False

    # ── reactions ───────────────────────────────────────────────────────

    async def _handle_reaction_event(self, event: dict) -> None:
        """The fork's reaction pipeline. Each step is independent and log-only on failure."""
        event_type = str((event or {}).get("type") or "").strip()
        item = (event or {}).get("item") or {}
        channel = str(item.get("channel") or (event or {}).get("item_channel") or "").strip()
        message_ts = str(item.get("ts") or (event or {}).get("item_ts") or "").strip()
        reaction = str((event or {}).get("reaction") or "").strip()
        user = str((event or {}).get("user") or (event or {}).get("user_id") or "unknown").strip() or "unknown"
        logger.info(
            "slack_reaction_event_received type=%s channel=%s ts=%s reaction=%s user=%s",
            event_type, channel, message_ts, reaction, user,
        )
        if not channel or not message_ts:
            logger.warning(
                "slack_reaction_event_missing_message_reference type=%s reaction=%s user=%s",
                event_type, reaction, user,
            )
            return

        # Idea capture: a 👍 on a bot-authored idea post in the configured
        # channel(s) is appended to the Google Doc "Идеи и улучшения". Only
        # active when the idea-capture env vars are set.
        try:
            await self._handle_slack_idea_reaction_event(event)
        except Exception as exc:
            logger.exception(
                "slack_idea_reaction_capture_failed type=%s channel=%s ts=%s reaction=%s user=%s: %s",
                event_type, channel, message_ts, reaction, user, exc,
            )

        try:
            from job_intel.cli import run_feedback_event

            result = run_feedback_event(dict(event or {}))
            logger.info("job_intel_feedback_event_result %s", result)
        except Exception as exc:
            logger.exception(
                "job_intel_feedback_event_failed type=%s channel=%s ts=%s reaction=%s user=%s: %s",
                event_type, channel, message_ts, reaction, user, exc,
            )

        # 🔍 → vacancy+company evaluation (Russian), 👍 → application document
        # package (English), as a synthetic inbound message in the card's thread.
        try:
            await self._maybe_dispatch_vacancy_reaction_task(event)
        except Exception as exc:
            logger.exception(
                "vacancy_reaction_trigger_failed channel=%s ts=%s reaction=%s: %s",
                channel, message_ts, reaction, exc,
            )
            try:
                await self._add_reaction(channel, message_ts, "warning")
            except Exception:  # pragma: no cover - best effort
                pass

        # Baseline doctor: 🧹 on a preflight-block message runs the doctor;
        # 📥/🙈/📦 on the doctor's report applies the operator's chosen action.
        try:
            await self._maybe_run_baseline_doctor(event)
        except Exception as exc:
            logger.exception("baseline_doctor_reaction_failed ts=%s reaction=%s: %s", message_ts, reaction, exc)
        try:
            await self._maybe_apply_baseline_action(event)
        except Exception as exc:
            logger.exception("baseline_doctor_action_failed reaction=%s: %s", reaction, exc)

        try:
            await self._maybe_apply_commit_approval(event)
        except Exception:
            logger.exception("slack_commit_approval_failed reaction=%s user=%s", reaction, user)

    async def _maybe_run_baseline_doctor(self, event: dict) -> None:
        if str((event or {}).get("type") or "") != "reaction_added":
            return
        reaction = str((event or {}).get("reaction") or "").strip().lower()
        if not is_trigger_reaction(reaction):
            return
        user = str((event or {}).get("user") or "").strip()
        if not is_operator(user):
            logger.info("baseline_doctor_reaction_ignored non_operator user=%s", user)
            return
        item = (event or {}).get("item") or {}
        channel = str(item.get("channel") or "").strip()
        message_ts = str(item.get("ts") or "").strip()
        if not channel or not message_ts:
            return
        client = self._get_client(channel)
        # conversations_replies (not _history) so the exact message resolves even
        # when the block was a thread reply -- history returns only top-level
        # timeline messages and would hand back the thread parent.
        history = await client.conversations_replies(channel=channel, ts=message_ts, inclusive=True, limit=1)
        messages = history.get("messages", []) or []
        target = next(
            (m for m in messages if str(m.get("ts")) == message_ts),
            messages[0] if messages else None,
        )
        if target is None or not is_block_message(str(target.get("text") or "")):
            logger.info("baseline_doctor_reaction_ignored not_block_message ts=%s", message_ts)
            return
        result = run_baseline_doctor()
        report = _render_doctor_report(result)
        posted = await client.chat_postMessage(channel=channel, text=report, thread_ts=message_ts)
        report_ts = str(posted.get("ts") or "")
        if not result["clean"] and result["remaining"] and report_ts:
            record_pending(report_ts, result["remaining"])

    async def _maybe_apply_baseline_action(self, event: dict) -> None:
        if str((event or {}).get("type") or "") != "reaction_added":
            return
        action = classify_action(str((event or {}).get("reaction") or ""))
        if not action:
            return
        user = str((event or {}).get("user") or "").strip()
        if not is_operator(user):
            return
        item = (event or {}).get("item") or {}
        channel = str(item.get("channel") or "").strip()
        report_ts = str(item.get("ts") or "").strip()
        if not channel or not report_ts:
            return
        remaining = pop_pending(report_ts)
        if remaining is None:
            return  # reaction not on a doctor report we posted
        result = _apply_action(_AGENT_REPO, action, remaining)
        recheck = run_baseline_doctor()
        status = (
            "✅ Baseline clean — retry the request."
            if recheck["clean"]
            else "⚠️ Still dirty — check remaining files."
        )
        detail = result.get("detail", "")
        text = (
            f"🧹 Applied *{action}* to {len(result['paths'])} file(s). "
            f"{'ok' if result['ok'] else 'FAILED: ' + detail}\n{status}"
        )
        await self._get_client(channel).chat_postMessage(channel=channel, text=text, thread_ts=report_ts)

    async def _maybe_apply_commit_approval(self, event: dict) -> None:
        """Operator ✅ on the commit-gate message commits the pending deliverable;
        ❌ discards it. No-op unless: reaction is ✅/❌, reactor is the operator,
        a commit is pending, and the reacted message is the commit-gate message."""
        if str((event or {}).get("type") or "") != "reaction_added":
            return
        reaction = str((event or {}).get("reaction") or "").strip().lower()
        commit_emojis = {"white_check_mark", "heavy_check_mark", "ballot_box_with_check"}
        discard_emojis = {"x", "negative_squared_cross_mark", "wastebasket"}
        if reaction not in commit_emojis and reaction not in discard_emojis:
            return
        user = str((event or {}).get("user") or "").strip()
        if not commit_gate_service.is_operator(user):
            return
        pending = commit_gate_service.get_pending()
        if not pending:
            return
        item = (event or {}).get("item") or {}
        channel = str(item.get("channel") or "").strip()
        ts = str(item.get("ts") or "").strip()
        if not channel or not ts:
            return

        # Best-effort confirmation that the reaction is on the commit-gate message.
        # Reject ONLY if we positively read a message that is NOT a gate message.
        # If the fetch is empty/errors (Slack history quirk or missing scope),
        # proceed anyway -- operator + a pending commit marker is already a strong gate.
        confirmed_non_gate = False
        try:
            hist = await self._get_client(channel).conversations_history(
                channel=channel, latest=ts, oldest=ts, inclusive=True, limit=1,
            )
            msgs = (hist or {}).get("messages") or []
            if msgs:
                text = str(msgs[0].get("text") or "")
                if text and not commit_gate_service.is_gate_message(text):
                    confirmed_non_gate = True
        except Exception:
            logger.warning(
                "commit-approval: could not fetch reacted message; proceeding on operator+pending",
                exc_info=True,
            )
        if confirmed_non_gate:
            logger.info("commit-approval: reaction not on a commit-gate message; ignoring")
            return

        workspace = str(pending.get("workspace_path") or "").strip()
        repo = _Path(workspace) if workspace else _AGENT_REPO
        changed_files = list(pending.get("changed_files") or [])

        if reaction in discard_emojis:
            commit_gate_service.apply_discard(repo=repo, changed_files=changed_files)
            commit_gate_service.clear_pending()
            result_text = "🗑 Изменения отклонены и убраны в stash. Ничего не закоммичено."
        else:
            # The operator's approval is necessary, not sufficient: an unresolved
            # changes_requested still blocks a commit that would move the mainline.
            # A commit inside a run worktree is contained, so it proceeds and the
            # debt gates the landing instead.
            try:
                from hermes_cli.pipeline_autonomous_execution import commit_gate_authorization

                _auth = commit_gate_authorization(workspace=repo, session_id=str(pending.get("session_id") or ""))
            except Exception:
                _auth = None
            if _auth is not None and not _auth.allow_commit:
                result_text = "⛔ Не коммичу: ревью не закрыто.\n" + (_auth.detail or "")
            else:
                result = commit_gate_service.apply_commit(
                    repo=repo, changed_files=changed_files,
                    commit_message=str(pending.get("commit_message") or "chore: controlled-pipeline change"),
                    push=False,
                )
                if result.get("committed"):
                    commit_gate_service.clear_pending()
                    try:
                        from hermes_cli.pipeline_autonomous_execution import (
                            describe_run_integration,
                            land_run_branch_after_commit,
                        )

                        _landed = describe_run_integration(land_run_branch_after_commit(
                            workspace=repo,
                            approved=bool(_auth.approved_for_landing) if _auth is not None else True,
                        ))
                    except Exception:
                        _landed = ""
                    result_text = (
                        f"✅ Закоммичено: {result.get('sha') or '?'} ({len(changed_files)} файлов). "
                        "Пуш не делал (скажи «запушь», если нужно)."
                    )
                    if _landed:
                        result_text = f"{result_text}\n{_landed}"
                else:
                    result_text = f"⚠️ Не удалось закоммитить: {result.get('detail') or 'unknown'}"

        try:
            await self._get_client(channel).chat_postMessage(channel=channel, text=result_text, thread_ts=ts)
        except Exception:
            logger.warning("commit-approval: failed to post result", exc_info=True)

    async def _handle_slack_idea_reaction_event(self, event: dict) -> None:
        """Capture a configured 👍 reaction on a bot-authored idea post."""
        from job_intel.idea_reaction_capture import process_event

        from plugins.platforms.slack.adapter import _extract_text_from_slack_blocks

        raw_channels = os.getenv("SLACK_IDEA_REACTION_CHANNELS", "").strip()
        raw_doc_id = os.getenv("SLACK_IDEA_DOC_ID", "").strip()
        raw_reactions = os.getenv("SLACK_IDEA_REACTION_EMOJIS", "").strip()
        if not (raw_channels or raw_doc_id or raw_reactions):
            return
        if str((event or {}).get("type") or "").strip() != "reaction_added":
            return
        reaction = str((event or {}).get("reaction") or "").strip().lower()
        trigger_reactions = {
            part.strip().lower()
            for part in (raw_reactions.split(",") if raw_reactions else ["+1", "thumbsup", "thumbs_up"])
            if part.strip()
        }
        if reaction not in trigger_reactions:
            return
        item = (event or {}).get("item") or {}
        channel = str(item.get("channel") or (event or {}).get("item_channel") or "").strip()
        message_ts = str(item.get("ts") or (event or {}).get("item_ts") or "").strip()
        if not channel or not message_ts:
            return
        allowed_channels = {
            part.strip()
            for part in (raw_channels.split(",") if raw_channels else ["C0B55FPG5B7"])
            if part.strip()
        }
        if channel not in allowed_channels:
            return

        # Fetch the original message to confirm Hermes posted it and to capture
        # the actual idea text, not only the reaction event.
        client = self._get_client(channel)
        message = None
        try:
            result = await client.conversations_history(channel=channel, latest=message_ts, inclusive=True, limit=1)
            messages = result.get("messages", []) or []
            if messages:
                message = dict(messages[0])
        except Exception:
            logger.debug("[Slack] conversations.history failed while capturing idea reaction", exc_info=True)
            return
        if not message:
            return

        bot_uid = self._team_bot_user_ids.get((event or {}).get("team") or "", self._bot_user_id)
        if not (
            message.get("bot_id")
            or message.get("subtype") == "bot_message"
            or (bot_uid and str(message.get("user") or "").strip() == bot_uid)
        ):
            return
        if not message.get("text") and message.get("blocks"):
            try:
                blocks_text = _extract_text_from_slack_blocks(message.get("blocks") or [])
                if blocks_text:
                    message["text"] = blocks_text
            except Exception:
                logger.debug("[Slack] block text extraction failed for idea capture", exc_info=True)

        # Acknowledge on the post itself. A logger-only outcome is how this
        # stayed broken from 2026-06-14 to 2026-08-20: the operator kept
        # liking ideas and nothing ever said they were not being captured.
        team_id = str((event or {}).get("team") or "")
        try:
            result = process_event(event, message=message, bot_user_id=bot_uid, dry_run=False)
        except Exception:
            await self._add_reaction(channel, message_ts, "x", team_id)
            raise
        if str(result.get("status") or "") == "ok":
            await self._add_reaction(channel, message_ts, "memo", team_id)
        logger.info("slack_idea_reaction_capture_result %s", result)

    async def _maybe_dispatch_vacancy_reaction_task(self, event: dict) -> None:
        """🔍/👍 on a tracked vacancy card → synthetic recruiter task in-thread."""
        from job_intel.reaction_triggers import build_trigger_prompt, classify_trigger, should_process

        if str((event or {}).get("type") or "") != "reaction_added":
            return
        reaction = str((event or {}).get("reaction") or "").strip()
        kind = classify_trigger(reaction)
        if not kind:
            return
        item = (event or {}).get("item") or {}
        channel = str(item.get("channel") or "").strip()
        message_ts = str(item.get("ts") or "").strip()
        user_id = str((event or {}).get("user") or "").strip()
        if not channel or not message_ts or not user_id:
            return

        from job_intel.cli import _logical_aliases_for_channel_id, _store

        store = _store()
        message = store.find_vacancy_message(slack_channel=channel, slack_message_ts=message_ts)
        if not message:
            for alias in _logical_aliases_for_channel_id(channel):
                message = store.find_vacancy_message(slack_channel=alias, slack_message_ts=message_ts)
                if message:
                    break
        if not message:
            logger.info(
                "vacancy_reaction_trigger_not_tracked channel=%s ts=%s reaction=%s",
                channel, message_ts, reaction,
            )
            return
        if not should_process(channel=channel, message_ts=message_ts, reaction=reaction):
            logger.info(
                "vacancy_reaction_trigger_duplicate channel=%s ts=%s reaction=%s",
                channel, message_ts, reaction,
            )
            return

        team_id = self._channel_team.get(channel, "")
        bot_uid = self._team_bot_user_ids.get(team_id, self._bot_user_id)
        prompt = build_trigger_prompt(kind, message)
        if bot_uid:
            # Mention prefix so channel mention-gating processes the synthetic
            # message; the mention is stripped there.
            prompt = f"<@{bot_uid}> {prompt}"
        synthetic = {
            "type": "message",
            "text": prompt,
            "user": user_id,
            "channel": channel,
            "channel_type": "channel",
            # Reaction event_ts is unique per event → safe for the message
            # deduplicator; also dedups Slack redeliveries at message level.
            "ts": str((event or {}).get("event_ts") or message_ts),
            "thread_ts": message_ts,
            "team": team_id,
        }
        logger.info(
            "vacancy_reaction_trigger_dispatch kind=%s channel=%s ts=%s vacancy_id=%s",
            kind, channel, message_ts, message.get("vacancy_id"),
        )
        await self._handle_slack_message(synthetic)
