"""Fork: what this deployment's WhatsApp adapter adds on top of upstream.

``WhatsAppForkMixin`` is mixed into ``WhatsAppAdapter`` (plugins/platforms/
whatsapp/adapter.py), which calls in from ``__init__``, ``_attach_to_bridge``,
``disconnect`` and the bridge-log open.

* Reactions (hermes-home / Amina). The bridge queues emoji reactions on our
  own messages separately from ordinary messages (GET /reactions). Each one
  goes to the deterministic ack hook first (``extra.reaction_hook_cmd``, e.g.
  ``fam react-hook``: a ⏰ on a dose reminder postpones it an hour) and only a
  reaction the hook does not consume becomes an agent turn, quoting the
  message it reacts to (``extra.reaction_dialogue``). A hook that hangs is
  killed with its whole process tree.
* ``whatsapp.profile_photo_path``: applied to the account on connect.
* bridge.log rotation at 5 MB, one generation kept.

The bodies moved from the pre-v0.22.5 adapter. Upstream's own reaction
delivery (a reaction as an ordinary ``[Reaction: ...]`` message) never sees
reactions on our messages: the bridge routes those to /reactions first.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from plugins.platforms.whatsapp.reactions import is_dialogue_emoji, normalize_emoji

logger = logging.getLogger("plugins.platforms.whatsapp.adapter")

# The ack hook must never cost more than this per reaction (_poll_reactions
# applies events serially, so a hung hook stalls every reaction behind it).
# The reap that follows a kill is bounded separately and much shorter:
# asyncio.Process.wait() returns when the inherited pipe transports close,
# not merely when the pid dies, so a killed hook with a surviving descendant
# that still holds those pipes (e.g. a shell wrapper that forked instead of
# exec'ing) would otherwise hang this wait forever.
_REACTION_HOOK_TIMEOUT_S = 30
_REACTION_HOOK_REAP_TIMEOUT_S = 5


def _rotate_bridge_log_if_large(path, max_bytes=5 * 1024 * 1024):
    """Keep bridge.log bounded: past ``max_bytes`` rename it to bridge.log.1
    (replacing any prior .1) before it is reopened in append mode, so one
    generation of post-mortem history is kept without unbounded growth.
    Best-effort: an OS error here must not block the bridge from starting."""
    try:
        p = Path(path)
        if p.exists() and p.stat().st_size > max_bytes:
            p.replace(Path(str(p) + ".1"))
    except OSError:
        pass


class WhatsAppForkMixin:
    """Fork behaviour for ``WhatsAppAdapter``; relies on its ``_http_session``,
    ``_bridge_port``, ``_running``, ``name``, ``build_source``,
    ``handle_message``, ``_text_batch_key``, ``_flush_text_batch_now`` and
    ``_check_managed_bridge_exit``."""

    def _init_fork_whatsapp(self, extra: Dict[str, Any]) -> None:
        self._profile_photo_path: Optional[str] = extra.get("profile_photo_path")
        # Reaction acks: when configured, emoji reactions on Hermes's own
        # reminder messages are applied deterministically by an external hook
        # command -- no model turn. Unset (the default, and the VPS) leaves
        # the whole path dormant.
        # config.yaml: gateway.platforms.whatsapp.extra.reaction_hook_cmd
        self._reaction_hook_cmd = extra.get("reaction_hook_cmd") or None
        # config.yaml: gateway.platforms.whatsapp.extra.reaction_dialogue
        # When true, a reaction the ack hook did not consume becomes an
        # ordinary agent turn. Either flag alone arms the reaction poll loop.
        self._reaction_dialogue = bool(extra.get("reaction_dialogue", False))
        self._reaction_poll_task: Optional[asyncio.Task] = None

    def _start_fork_bridge_tasks(self) -> None:
        """Called once the adapter is attached to a bridge. Tolerates an adapter built
        without __init__ (upstream's own tests do that): attaching must never fail on
        the fork's state being absent."""
        if not hasattr(self, "_reaction_poll_task"):
            return
        if str(getattr(self, "_profile_photo_path", None) or "").strip():
            asyncio.ensure_future(self._apply_configured_profile_photo())
        self._start_reaction_polling()

    # ── profile photo ───────────────────────────────────────────────────

    async def _set_own_profile_photo(self, file_path: str):
        """Set the account's own WhatsApp profile photo via the bridge."""
        from gateway.platforms.base import SendResult

        if not self._http_session:
            return SendResult(success=False, error="Not connected")
        bridge_exit = await self._check_managed_bridge_exit()
        if bridge_exit:
            return SendResult(success=False, error=bridge_exit)
        normalized_path = str(file_path or "").strip()
        if not normalized_path:
            return SendResult(success=False, error="file_path is required")
        if not os.path.isabs(normalized_path):
            return SendResult(success=False, error="file_path must be an absolute path")
        if not os.path.exists(normalized_path):
            return SendResult(success=False, error=f"File not found: {normalized_path}")
        try:
            import aiohttp

            async with self._http_session.post(
                f"http://127.0.0.1:{self._bridge_port}/profile-photo",
                json={"filePath": normalized_path},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status == 200:
                    return SendResult(success=True, raw_response=await resp.json())
                return SendResult(success=False, error=await resp.text())
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def _apply_configured_profile_photo(self) -> None:
        """Apply whatsapp.profile_photo_path from config.yaml when configured."""
        configured = str(self._profile_photo_path or "").strip()
        if not configured:
            return
        result = await self._set_own_profile_photo(configured)
        if result.success:
            logger.info("[%s] Applied configured WhatsApp profile photo: %s", self.name, configured)
        else:
            logger.warning(
                "[%s] Failed to apply configured WhatsApp profile photo %s: %s",
                self.name, configured, result.error,
            )

    # ── reactions ───────────────────────────────────────────────────────

    def _start_reaction_polling(self) -> None:
        """Arm the reaction loop when configured."""
        if not (self._reaction_hook_cmd or self._reaction_dialogue):
            return
        if self._reaction_poll_task and not self._reaction_poll_task.done():
            return
        self._reaction_poll_task = asyncio.create_task(self._poll_reactions())

    async def _stop_reaction_polling(self) -> None:
        # getattr: disconnect() also runs on adapters built without __init__ (tests, partial
        # construction), and must never fail on the fork's state being absent.
        task, self._reaction_poll_task = getattr(self, "_reaction_poll_task", None), None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _poll_reactions(self) -> None:
        """Drain the bridge's reaction queue and apply each reaction.

        The bridge only queues reactions on messages Hermes itself sent, but
        sender gating here is much thinner than for an ordinary inbound
        message: this loop never calls ``_should_process_message``, so with
        ``reaction_dialogue`` enabled, under a pairing DM policy or in a group
        chat, a sender the normal message gates would reject can still
        produce an agent turn via a reaction -- a known, accepted gap.

        The hook reads one JSON event on stdin and prints a JSON verdict
        ``{"handled": bool, "react": str, ...}``. ``handled`` consumes the
        reaction (``react``, if present, is applied as visible feedback) and
        keeps it from the agent; anything else -- including any failure of the
        hook -- falls through to the agent-turn path. A failing hook must never
        take down the adapter.
        """
        import aiohttp

        while self._running:
            try:
                if not self._http_session:
                    break
                async with self._http_session.get(
                    f"http://127.0.0.1:{self._bridge_port}/reactions",
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    events = await resp.json() if resp.status == 200 else []
                for event in events:
                    await self._apply_reaction_event(event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[{self.name}] Reaction poll error: {e}")
                await asyncio.sleep(5)
            await asyncio.sleep(1)

    async def _apply_reaction_event(self, event: Dict[str, Any]) -> None:
        """Route one reaction: ack fast path first, agent turn second.

        The ack hook is deterministic, idempotent and costs no tokens, so a
        reaction that acks a reminder or a med dose must never also spend a
        model turn. Both filters run BEFORE the hook: un-reacting has no
        semantics on either path, and an off-whitelist emoji is not worth a
        subprocess. Every hook failure falls through to the dialogue path: a
        broken ack hook degrades to "the agent sees it", never to "the
        reaction vanished".
        """
        if bool(event.get("removal")):
            return
        if not is_dialogue_emoji(event.get("emoji") or ""):
            return
        if not self._reaction_hook_cmd:
            await self._dispatch_reaction_dialogue(event)
            return

        payload = json.dumps({
            "target_message_id": event.get("targetMessageId"),
            "emoji": event.get("emoji") or "",
            "removal": bool(event.get("removal")),
            "chat_jid": event.get("chatId") or "",
            "sender": event.get("senderId") or "",
        }, ensure_ascii=False)
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._reaction_hook_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as e:
            print(f"[{self.name}] Reaction hook failed to run: {e}")
            await self._dispatch_reaction_dialogue(event)
            return
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(payload.encode()), timeout=_REACTION_HOOK_TIMEOUT_S)
        except asyncio.TimeoutError:
            # Cancelling communicate() does not signal the subprocess. Kill the
            # whole tree, not just proc: a hook that forked instead of exec'ing
            # leaves a live grandchild after a plain proc.kill(), which leaks the
            # process this exists to stop AND makes an unbounded proc.wait()
            # hang forever -- so the kill is tree-aware and the reap bounded.
            #
            # Only while proc is still alive: a hook shaped like `sh -c "... &"`
            # exits at once while its grandchild keeps the pipes open, so this
            # branch fires 30s later with proc.returncode long set -- and
            # proc.pid may by then belong to an unrelated process. A plain
            # proc.kill() is guarded on returncode internally; the tree-kill
            # needs the same guard explicitly.
            if proc.returncode is None:
                import psutil

                from plugins.platforms.whatsapp import adapter as _wa

                try:
                    _wa._terminate_bridge_process(proc, force=True)
                except psutil.AccessDenied:
                    # The one failure the tree-kill does not swallow itself
                    # (NoSuchProcess is caught inside). Kill the direct child.
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                except Exception:
                    pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=_REACTION_HOOK_REAP_TIMEOUT_S)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
            print(f"[{self.name}] Reaction hook timed out after "
                  f"{_REACTION_HOOK_TIMEOUT_S}s; killed pid {proc.pid}")
            await self._dispatch_reaction_dialogue(event)
            return
        except Exception as e:
            print(f"[{self.name}] Reaction hook failed to run: {e}")
            await self._dispatch_reaction_dialogue(event)
            return
        if proc.returncode != 0:
            print(f"[{self.name}] Reaction hook exit {proc.returncode}: "
                  f"{(stderr or b'').decode(errors='replace')[:200]}")
            await self._dispatch_reaction_dialogue(event)
            return
        try:
            verdict = json.loads((stdout or b"").decode().strip() or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"[{self.name}] Reaction hook returned non-JSON output")
            await self._dispatch_reaction_dialogue(event)
            return

        if verdict.get("handled"):
            feedback = verdict.get("react")
            if feedback:
                await self._send_reaction(event.get("chatId") or "", event.get("targetMessageId") or "", feedback)
            return
        await self._dispatch_reaction_dialogue(event)

    async def _dispatch_reaction_dialogue(self, event: Dict[str, Any]) -> None:
        """Turn an unacked reaction into an ordinary agent turn.

        Shaped as a reply-with-quote, not a bespoke event type: the gateway
        already renders reply_to_* into the prompt for every platform, so a
        reaction arrives structurally identical to a quoted reply.
        """
        from gateway.platforms.base import MessageEvent, MessageType

        if not self._reaction_dialogue:
            return
        target_text = event.get("targetText")
        target_message_id = event.get("targetMessageId")
        if not target_text or not target_message_id:
            # Bridge restart emptied its message store, or no id to quote.
            # Reply-to rendering needs BOTH, and a contextless turn is worse
            # than a dropped reaction.
            print(f"[{self.name}] Reaction on unknown message "
                  f"{event.get('targetMessageId')}: no cached text, dropped")
            return

        emoji = normalize_emoji(event.get("emoji") or "")
        chat_id = event.get("chatId") or ""
        source = self.build_source(
            chat_id=chat_id,
            chat_type="group" if chat_id.endswith("@g.us") else "dm",
            user_id=event.get("senderId") or "",
        )
        message = MessageEvent(
            text=f"[Реакция {emoji}]",
            message_type=MessageType.TEXT,
            source=source,
            raw_message=event,
            # No metadata: upstream's session envelope admits a closed set of WhatsApp keys
            # (gateway/session_envelope.py) and refuses any other. The text and the
            # quoted reply already carry the reaction.
            reply_to_message_id=str(target_message_id),
            reply_to_text=str(target_text),
            reply_to_is_own_message=True,
        )
        # Text events sit in the debounce buffer while reactions dispatch
        # immediately: without this flush a reaction would overtake a message
        # typed a second earlier. Upstream's flush pops the batch before
        # dispatching, so a timer already mid-dispatch is left alone.
        await self._flush_text_batch_now(self._text_batch_key(message))
        await self.handle_message(message)

    async def _send_reaction(self, chat_id: str, message_id: str, emoji: str) -> bool:
        """Put an emoji reaction on one of our own messages (empty emoji
        removes it). Best-effort: the ack it confirms already happened."""
        import aiohttp

        if not (self._http_session and chat_id and message_id):
            return False
        try:
            async with self._http_session.post(
                f"http://127.0.0.1:{self._bridge_port}/send-reaction",
                json={"chatId": chat_id, "messageId": message_id, "emoji": emoji},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    print(f"[{self.name}] send-reaction failed: {resp.status}")
                    return False
                return True
        except Exception as e:
            print(f"[{self.name}] send-reaction error: {e}")
            return False
