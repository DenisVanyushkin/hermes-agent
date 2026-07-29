"""Emoji-reaction acks for outbound reminders (WhatsApp).

Amina reacts 👍 on a reminder message -> the WhatsApp adapter's
config-gated hook pipes the reaction event into `fam react-hook`
(stdin JSON) -> handle() below maps it deterministically (no LLM) onto
the existing ack primitives:

  confirm (👍 ❤️ 💪 ✅):  reminder -> rem.ack_chain (whole chain)
                          med      -> meds.take (dose taken, stops the
                                      +45min persistent series)
  skip (👎 ❌):           reminder -> rem.cancel_chain
                          med      -> meds.skip (only this dose)

Correlation lives in the `sent_messages` table (schema v10): gate.deliver
records the bridge-returned WhatsApp message id for every reminder/med
send (tick.py passes sent_ref), so a later reaction can be resolved back
to the exact reminder row / med intake it targets.

Design contract (spec: reaction-acks, 2026-07-22):
  * A reaction on a message we did not record is NOT an error -- Amina
    reacting to ordinary chat is normal ("unknown_message", no ack, no
    feedback reaction).
  * Idempotent: a second 👍 (or 👍 after a verbal "выпила") returns
    "already_acked" -- still a success for the caller, the adapter keeps
    the ✅ feedback on the message.
  * Reaction *removal* has no semantics (un-acking a taken med would be
    more confusing than helpful) -> "ignored".
  * Every outcome is audited (react.handle) -- phase-6b visibility
    discipline: negative outcomes are data, not noise.
"""
import json
import sys
import unicodedata

from fam import audit, meds, rem
from fam import db as famdb

# Base emoji AFTER _normalize_emoji (variation selectors and skin-tone
# modifiers stripped), so 👍🏽 and ❤️ match their bare forms.
#
# INVARIANT: EMOJI_CONFIRM | EMOJI_SKIP must remain a subset of
# DIALOGUE_EMOJI in plugins/platforms/whatsapp/reactions.py. The
# whitelist filter there runs BEFORE this hook is ever invoked, so any
# emoji added here without also adding it to DIALOGUE_EMOJI would
# silently never reach react-hook -- no error, just a dropped ack.
# Enforced by tests/gateway/test_whatsapp_reactions_normalize.py.
EMOJI_CONFIRM = {"\U0001F44D", "❤", "\U0001F4AA", "✅"}  # 👍 ❤ 💪 ✅
EMOJI_SKIP = {"\U0001F44E", "❌"}                              # 👎 ❌

# Feedback reaction the adapter puts on the reminder message once an ack
# (or an idempotent re-ack) landed.
FEEDBACK_EMOJI = "✅"  # ✅

_SKIN_TONES = {chr(cp) for cp in range(0x1F3FB, 0x1F400)}  # U+1F3FB..U+1F3FF


def _normalize_emoji(emoji):
    """Strip variation selectors (U+FE0F/U+FE0E), skin-tone modifiers and
    ZWJ so the mapping tables above can stay tiny. NFC first so
    decomposed forms collapse."""
    if not emoji:
        return ""
    out = []
    for ch in unicodedata.normalize("NFC", emoji.strip()):
        if ch in ("️", "︎", "‍") or ch in _SKIN_TONES:
            continue
        out.append(ch)
    return "".join(out)


def record_sent(conn, wa_message_id, kind, ref_id, event_id=None,
                chat_jid="", now_utc=None, ref_ids=None):
    """Persist one outbound message id -> reminder/med mapping.

    Called from gate.deliver (via its sent_ref parameter) right after a
    successful send. INSERT OR IGNORE: the bridge splitting one long text
    into chunks reports the LAST chunk's id (adapter contract), so ids
    are unique; the guard is for a retried deliver ever re-reporting the
    same id -- silently keeping the first row is correct either way.
    Does NOT commit -- runs inside the caller's transaction so the
    mapping and the reminder's own status='sent' UPDATE land atomically.

    ref_ids: the FULL list of targets when this is ONE message covering
    several doses (same-tick gate release, tick._meds_series). Such a
    message cannot be represented by sent_messages alone:
    wa_message_id is UNIQUE (so one row per message, not per dose) and
    ref_id is a scalar. sent_messages.ref_id keeps the first target for
    compatibility with every existing reader; the full list goes into
    sent_message_refs, which handle() consults.

    A single-target send writes NOTHING extra: with 0 or 1 entries in
    ref_ids the fan-out table stays empty, so the pre-Task-6 shape of a
    recorded message (and of handle()'s resolution) is bit-for-bit
    unchanged for every existing call site.
    """
    from fam.gate import _now  # local import: gate imports this module too
    conn.execute(
        "INSERT OR IGNORE INTO sent_messages("
        "  wa_message_id, chat_jid, kind, ref_id, event_id, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (wa_message_id, chat_jid or "", kind, ref_id, event_id,
         now_utc or _now()))
    if not ref_ids or len(ref_ids) <= 1:
        return
    row = conn.execute("SELECT id FROM sent_messages WHERE wa_message_id=?",
                       (wa_message_id,)).fetchone()
    if row is None:
        return
    for rid in ref_ids:
        conn.execute(
            "INSERT INTO sent_message_refs(sent_message_id, kind, ref_id) "
            "VALUES(?,?,?)", (row["id"], kind, rid))


def handle(conn, wa_message_id, emoji, removal=False, now_utc=None):
    """Resolve a reaction on WhatsApp message `wa_message_id` and apply
    the mapped ack. Returns a dict whose "result" is one of:

      confirmed | skipped     -- ack applied now
      already_acked           -- idempotent repeat (or the underlying row
                                 was already taken/acked/cancelled)
      ignored                 -- unmapped emoji, or a reaction removal
      unknown_message         -- id not in sent_messages (normal chat)

    Commits on every path that wrote anything. Never raises for the
    outcomes above; genuine DB errors propagate to the CLI's exit-2
    handler.
    """
    row = conn.execute(
        "SELECT * FROM sent_messages WHERE wa_message_id=?",
        (wa_message_id,)).fetchone()
    if row is None:
        # No audit row: reactions on ordinary chat messages are routine
        # and would flood audit_log (cf. minute-tick retention lesson).
        return {"result": "unknown_message", "wa_message_id": wa_message_id}

    base = {"wa_message_id": wa_message_id, "kind": row["kind"],
            "ref_id": row["ref_id"], "event_id": row["event_id"],
            "emoji": emoji}

    norm = _normalize_emoji(emoji)
    if removal or (norm not in EMOJI_CONFIRM and norm not in EMOJI_SKIP):
        out = {**base, "result": "ignored",
               "reason": "removal" if removal else "unmapped_emoji"}
        audit.log(conn, "react.handle", out)
        conn.commit()
        return out

    action = "confirm" if norm in EMOJI_CONFIRM else "skip"

    if row["ack_status"] != "none":
        out = {**base, "result": "already_acked",
               "ack_status": row["ack_status"]}
        audit.log(conn, "react.handle", out)
        conn.commit()
        return out

    detail = {}
    if row["kind"] == "reminder":
        if action == "confirm":
            detail["acked"] = rem.ack_chain(conn, row["event_id"])
            result, new_status = "confirmed", "confirmed"
        else:
            detail["cancelled"] = rem.cancel_chain(conn, row["event_id"])
            result, new_status = "skipped", "skipped"
    else:  # med
        # One message may cover several doses (same-tick gate release,
        # tick._meds_series): sent_message_refs carries the full target
        # list, and its absence means the pre-Task-6 single-dose shape --
        # exactly one target, row["ref_id"], handled below by the same
        # code path with the same idempotency semantics.
        targets = [r["ref_id"] for r in conn.execute(
            "SELECT ref_id FROM sent_message_refs "
            "WHERE sent_message_id=? AND kind='med' ORDER BY ref_id",
            (row["id"],))] or [row["ref_id"]]

        applied = 0
        for rid in targets:
            try:
                if action == "confirm":
                    take_out = meds.take(conn, rid, now_utc=now_utc)
                    detail.update({k: take_out[k]
                                   for k in ("remaining", "restock")
                                   if isinstance(take_out, dict)
                                   and k in take_out})
                else:
                    meds.skip(conn, rid)
                applied += 1
            except ValueError:
                # This dose already left 'pending' through another door (a
                # verbal "выпила", midnight missed-closeout, ...).
                # For a group that is normal -- its other members are
                # still waiting, so keep going and only fall back to
                # "already_acked" if NOTHING was applicable.
                continue

        if applied == 0:
            # Single-dose case: byte-for-byte the pre-Task-6 not_pending
            # path -- the reaction's intent is satisfied or moot, so it is
            # an idempotent success, and the mapping is marked so repeats
            # short-circuit at the ack_status check above.
            conn.execute(
                "UPDATE sent_messages SET ack_status='confirmed' "
                "WHERE kind=? AND ref_id=? AND ack_status='none'",
                (row["kind"], row["ref_id"]))
            out = {**base, "result": "already_acked",
                   "reason": "not_pending"}
            audit.log(conn, "react.handle", out)
            conn.commit()
            return out

        if len(targets) > 1:
            detail["applied"] = applied
        result = "confirmed" if action == "confirm" else "skipped"
        new_status = result

    # Mark every recorded message of this reminder chain / med intake
    # (multi-stage chains and +45min med resends each have their own
    # sent_messages row) so a reaction on ANY of them is one ack and
    # later reactions on siblings read as already_acked.
    if row["kind"] == "reminder":
        conn.execute(
            "UPDATE sent_messages SET ack_status=? "
            "WHERE kind='reminder' AND event_id=? AND ack_status='none'",
            (new_status, row["event_id"]))
    else:
        for rid in targets:
            conn.execute(
                "UPDATE sent_messages SET ack_status=? "
                "WHERE kind='med' AND ref_id=? AND ack_status='none'",
                (new_status, rid))

    out = {**base, "result": result, **detail}
    audit.log(conn, "react.handle", out)
    conn.commit()
    return out


def run_hook(stdin=None, stdout=None, connect=None):
    """`fam react-hook` entry: read ONE reaction-event JSON object from
    stdin, apply handle(), and print the adapter's verdict:

        {"handled": true,  "react": "✅", "result": "confirmed"}
            -- consumed as an ack; put/keep the feedback reaction and do
               NOT route this reaction to the agent
        {"handled": false, "result": "unknown_message"}
            -- not an ack; the adapter may route it to the dialogue path

    Event shape (produced by plugins/platforms/whatsapp/adapter.py):
      {"target_message_id": str, "emoji": str, "removal": bool,
       "chat_jid": str, "sender": str}

    Sender gating here is thinner than it looks: bridge-level
    WHATSAPP_ALLOWED_USERS is skipped entirely under
    WHATSAPP_DM_POLICY=pairing, and the adapter's reaction path never
    calls its own DM/group gates (_should_process_message and friends)
    before invoking us. With reaction_dialogue enabled, a sender those
    gates would otherwise reject can still reach us via a reaction --
    a known, accepted gap. This process trusts its caller regardless,
    same as every other fam entry point.

    Exit codes: 0 handled (including unknown_message/ignored);
    2 malformed event. Internal failures propagate to cli.main's
    exit-2 handler; the adapter treats nonzero as "no feedback" and its
    notify path takes over.
    """
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    try:
        event = json.load(stdin)
        wa_message_id = str(event["target_message_id"])
        emoji = str(event.get("emoji") or "")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"react-hook: malformed event: {e}", file=sys.stderr)
        return 2
    conn = (connect or famdb.connect)()
    out = handle(conn, wa_message_id, emoji,
                 removal=bool(event.get("removal")))
    # `handled` is the adapter's routing signal, not a success flag: true
    # means this reaction was consumed as an ack and must NOT become an
    # agent turn. `ignored`/`unknown_message` are ordinary chat reactions
    # and belong to the dialogue path (spec: reactions-dialogue,
    # 2026-07-29).
    handled = out["result"] in ("confirmed", "skipped", "already_acked")
    feedback = {"react": FEEDBACK_EMOJI} if handled else {}
    print(json.dumps({"handled": handled, **feedback, "result": out["result"]},
                     ensure_ascii=False), file=stdout)
    return 0
