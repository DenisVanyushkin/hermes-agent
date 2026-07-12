---
name: amina-fam
description: "fam CLI: calendar/people/places + reminders ('уже выходим/едем/собираемся/готовимся/на месте/знаю', 'не напоминай про это', 'какие напоминания' are reactions to a reminder/digest the agent itself already sent — use fam here too). Also for recording/checking events, 'who is X', 'where is Y', day/week/month views, or any household schedule/contacts/locations request."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [fam, calendar, family, people, places, cli, amina]
---

# Amina Fam Skill

`fam` is Amina's private family database — calendar, people, and places —
backed by one shared SQLite file the agent and the host both read/write.
This skill drives it entirely through its CLI; there is no other supported
way to read or change family data.

## When to Use

- Calendar: "запиши", "когда у нас...", "перенеси", "отмени", "покажи
  неделю/месяц/день". Synonyms for the grid picture: "сетка", "расписание",
  "календарь покажи/пришли" — all of these mean render the day/week/month
  grid.
- People: "кто такой X", adding/looking up a family member, friend, or group.
- Places: "где находится Y", adding/looking up an address.
- Any request to view or change the household schedule or the people/places glossary.
- Reacting to a reminder or the morning digest the agent itself sent
  earlier — "уже выходим", "не напоминай про это", "какие напоминания",
  or a reply to the digest's closing question. See Reminder Reactions and
  Digest Replies below.

## Tool

- Binary: `/workspace/live-hermes/custom/fam/bin/fam` — a bash shim
  (executable), **not** a Python file. Run it directly with the `terminal`
  tool; never prefix it with `python3`.
- Always pass `--json`. It works before or after the subcommand
  (`fam --json cal show 1` and `fam cal show 1 --json` are identical) — parse
  this output, don't scrape the human-readable line.
- Subcommands:
  - `init`
  - `log [--since|--last-hours] [--kind] [--grep] [--limit]`
  - `people add|alias|member|resolve|list`
  - `places add|alias|resolve|list`
  - `cal add|update|cancel|done|show|day|range|grid`
  - `rem list|ack|cancel|rules|active`
- Time: the household lives in Asia/Almaty (+05:00). "Now" comes from the
  timestamp prefix on the **latest** user message, `[Dow YYYY-MM-DD
  HH:MM:SS TZ]` (e.g. `[Tue 2026-04-28 13:40:53 CEST]`) — read it from
  there and convert to Asia/Almaty as usual. If that message has no such
  prefix, make exactly one `date` call via the `terminal` tool before any
  date/time arithmetic, and use that. Never derive "today" from an
  earlier turn in the conversation — it drifts (the incident this rule
  exists for: "сегодня в час" got logged with yesterday's date). Talk to
  the user in Asia/Almaty local time, short form ("ср 15-го, 10:00").
  Every value you pass to fam (`--start`, `--end`, a `day` date) is
  ISO-8601 with an explicit offset, e.g. `2026-07-15T10:00:00+05:00`.

## Rules

### Terminal invocation discipline
Run each fam call as ONE plain command line, exactly:
`/workspace/live-hermes/custom/fam/bin/fam <subcommand> ... --json`
Never chain commands with `&&` or `;`, never wrap in `bash -c` /
`${SHELL} -lc`, never pipe. Chained or shell-wrapped commands trip the
dangerous-command approval gate and stall the conversation waiting for
admin approval. If you need a second result (e.g. show after cancel),
make a second, separate terminal call.


1. **Never do date/time arithmetic in your head or in prose.** Turn the
   user's wording ("завтра в 10 утра", "в среду вечером") into one concrete
   ISO-8601 string with offset, computed from "now" as established under
   Time above (the message's timestamp prefix, or one `date` call if it
   has none) — never from a date mentioned earlier in the conversation.
   Pass that string straight to fam. Don't narrate the calculation — just
   produce the ISO value.
2. **Unknown person/place ⇒ stop and ask, then retry.** fam exits 2 with
   `unknown person: X` or `unknown place: X` on stderr when a name doesn't
   resolve. Stop, ask the user to confirm who/where it is ("это кто/где —
   записать?"). When they confirm, run `fam people add <X> --alias <X>` or
   `fam places add <X> --alias <X>` using their exact wording verbatim —
   never normalize or clean up the form, so the alias matches what they
   actually said. Then retry the original command.
3. **Destination phrasing ⇒ resolve the place before recording.**
   "съездить/поехать/сходить в/к X" means X is a place, not just words in
   the title — run `fam places resolve X` first. Unknown → same
   unknown person/place stop-and-ask rule as above ("это где — записать?");
   on confirmation `fam places add` with the user's exact wording, then
   pass `--place` to `cal add`/`cal update`. Don't ask about travel time
   (`--travel-min`) — record it only if the user brings it up themselves.
4. **Other stderr + exit 2 failures are real errors, not retry bait.**
   Examples: `alias already in use by ...`, `unknown field: ...`, `unknown
   event: ...`. Read the message and fix the actual problem (different
   alias, a valid field name, the right event id) or tell the user what's
   wrong — don't blindly resubmit the same command. Exception: `start is
   in the past` has its own protocol, the past-start protocol below.
5. **`start is in the past` ⇒ past event or stale "now", not a bug.**
   `cal add`/`cal update --start` exits 2 with `start is in the past
   (now: <ISO+05:00>). If the user means a past event, retry with
   --allow-past; otherwise re-derive the date (run date).` when the ISO
   you sent is more than a few minutes behind now. The user is clearly
   talking about something already past ("вчера были у бабушки", "запиши,
   что мы ездили в...") → retry the identical command with
   `--allow-past`. Otherwise your "now" was wrong → make one `date` call,
   re-derive the ISO from the fresh date/time, and retry without
   `--allow-past`.
6. **Never delete or overwrite anything without an explicit request.**
   Cancelling an event is `fam cal cancel <id>`, never a raw edit. Get the
   id from `cal show`/`cal day --json`/`cal range --json`, never guess it.
7. `cal day` / `cal range` / `cal grid` only ever list **active** events —
   cancelled events are hidden by design, not gone.

## Quick Reference

| Goal | Command |
| --- | --- |
| Record an event | `fam cal add --title T --start ISO [--end ISO] [--place P] [--with NAME]... [--transport car\|walk\|public\|unknown] [--notes N]` |
| Change an event | `fam cal update <id> [--start ISO] [--place P] [--add-person N] [--rm-person N] ...` |
| Cancel an event | `fam cal cancel <id>` |
| Mark an event done | `fam cal done <id>` |
| One event | `fam cal show <id>` |
| One day | `fam cal day YYYY-MM-DD` |
| A date range | `fam cal range <from_iso> <to_iso>` |
| Day/week/month picture | `fam cal grid --day YYYY-MM-DD -o /home/denis/.hermes/cache/documents/grid.png` (or `--week YYYY-MM-DD` / `--month YYYY-MM`) |
| Who is X | `fam people resolve "X"` |
| Add a person/group | `fam people add "Name" [--group] [--alias A]` |
| Add an alias | `fam people alias <ref> <alias>` |
| Add to a group | `fam people member <group_ref> <person_ref>` |
| Where is Y | `fam places resolve "Y"` |
| Add a place | `fam places add "Name" [--address A] [--lat LAT] [--lon LON] [--alias A]` |
| Recent activity | `fam log --last-hours 24` |
| Reminders for one event | `fam rem list --event ID --json` |
| Reminders due right now | `fam rem list --due --json` |
| Events with an in-progress reminder chain | `fam rem active --json` |
| Just starting to get ready (ack prepare stages only) | `fam rem ack EVENT_ID --scope prepare` |
| Already on the way (ack whole chain) | `fam rem ack EVENT_ID` |
| Stop nagging about it (cancel chain) | `fam rem cancel EVENT_ID` |
| What rules generate reminders | `fam rem rules --json` |

## Calendar Grid

`fam cal grid --day YYYY-MM-DD -o /home/denis/.hermes/cache/documents/grid.png --json` (or `--week
YYYY-MM-DD`, or `--month YYYY-MM`) renders a PNG picture of the day, week,
or month — exactly one of `--day`/`--week`/`--month` is required.
Synonyms that all mean "render the grid": "сетка", "расписание", "календарь
покажи/пришли", as well as "покажи день/неделю/месяц".

If the render succeeds, send the picture back to the user by including
`MEDIA:/home/denis/.hermes/cache/documents/grid.png` in your reply.

**Honest failure — never claim a picture that doesn't exist.** If `cal
grid` exits non-zero, or the output file doesn't exist afterward (check
with `ls`), do **not** send a `MEDIA:` tag. Instead tell the user, briefly
and in one line, that the картинка не получилась и почему (e.g. "не смог
собрать картинку расписания — сбой в терминале"). Anything more detailed
for your own diagnosis (stderr output, stack traces, the exact failing
command) stays in your terminal session's stderr — never forward it to the
user.

## Reminder Reactions

Reminders and the digest are sent proactively, out-of-band — a background
tick fires them through a separate `hermes send`, not this conversation.
When the household reacts in a later turn ("уже выходим"), the reminder
that triggered it genuinely is NOT in your session context — you cannot
recall which event it was about, you have to look it up. Ack applies to
a stage-group — the prepare stages or the whole remaining chain — chosen
by the user's wording per the mapping below, never a single individual
stage and never guessed (unclear wording → one clarifying question).
Cancel always applies to the whole remaining chain:

- **Ack is scoped to what's actually done** — "собираемся" ≠ "выходим".
  Acking silences everything in the reply's scope; don't over-silence a
  chain that still has "пора выходить" stages left to fire:
  - **"собираемся / начали / начали собираться / готовимся" (getting
    ready)** → `fam rem ack EVENT_ID --scope prepare`. Only the getting-
    ready stages go quiet; departure-stage reminders still fire later.
  - **"выходим / уже выходим / едем / вышли / в пути / на месте" (on the
    way)** → `fam rem ack EVENT_ID` (no `--scope`, silences the whole
    remaining chain).
  - **Ambiguous ("уже", "знаю", "понял" with no event named)** — this is
    almost always a reaction to a reminder that just fired out-of-band,
    but the wording alone doesn't say prepare-stage vs departure-stage.
    Do NOT just say "понял" without looking it up first, and do NOT guess
    the scope — ask ONE short clarifying question ("уже выходите или
    пока собираетесь?").
  1. `fam rem active --json` — events with an in-progress reminder chain
     (still-pending reminders).
  2. Exactly one result → ack it with the scope matching the reply
     (`--scope prepare` or full), then tell the user briefly what you
     silenced (title + time).
  3. Several results → ask which one, by title/time — never guess.
  4. No results → nothing is pending; a plain conversational
     acknowledgment is enough, no fam call needed.

  Examples:
  - "начали собираться" → one active event → `fam rem ack 42 --scope
    prepare` → "Понял, сборы отметил — как выйдете, скажите."
  - "уже вышли" → one active event → `fam rem ack 42` → "Понял, больше не
    напоминаю про врача в 15:00."
  - "угу" (no context) → uncertain scope → "Уже выходите или пока
    собираетесь?"
- **"не напоминай про это" / "погаси напоминания про X" (stop nagging)**
  → `fam rem cancel EVENT_ID`. Cancel is ALWAYS whole-chain — it has no
  `--scope` option; never pass one.
- **"какие напоминания" (what's pending)** → `fam rem list --due --json`
  for what's about to fire, or `fam rem list --json` for everything.

**Finding the event_id for cancel** (ack resolves it via `fam rem active`
above instead). If only one event plausibly fits what the user just said,
use its id directly. Otherwise resolve it before calling `cancel`:
1. `fam log --kind gate.sent --last-hours 6 --json` — each row's
   `payload` carries the `raw` that produced that message: `raw.event_id`
   for a reminder, `raw.events[].event_id` per item for a digest. This is
   almost always enough to match "it" to a specific event.
2. Still unclear? Match the title the user mentioned against `fam cal day
   <today> --json` or `fam rem list --due --json`.
3. More than one candidate, or none? Stop and ask which event they mean
   — never guess an id.

`ack`/`cancel` report how many stages they touched (`{"acked": N}` /
`{"cancelled": N}`); `N == 0` means there was nothing pending (already
sent, or already acted on) — say that plainly rather than implying you
just silenced something.

## Digest Replies

The morning digest always closes with the same question: "Если появятся
планы или изменения — расскажи или надиктуй, я запишу."

- Plans or changes in the reply → ordinary calendar intake, the same
  add/update rules as any other conversation — nothing digest-specific
  about it.
- A plain "всё в силе" / "без изменений" / "как обычно" acknowledgment →
  no fam call needed; one short line back is enough.

## Reply Style

- Keep the confirmation of an operation to **one line**: "Записал: врач, ср
  15-го, 10:00."
- Never dump raw JSON at the user — translate it into Asia/Almaty local time
  and short, natural phrasing.

## Pitfalls

- `--json` position (before/after the subcommand) doesn't matter; either
  works — don't second-guess it.
- `bin/fam` is a shim script — call it directly, no `python3` in front.
- A group passed via `--with <group>` expands to its members automatically;
  you don't need to resolve members yourself.
- `cal show`/`cal day` on an id/date with no match still exits cleanly for
  `day`/`range` (empty list) but `cal show <unknown id>` exits 2 —
  treat it like any other unknown-ref error.

## Verification

- `fam --json init` returns `{"ok": true, "db": ...}` — confirms the shared
  database is reachable before doing anything else if you're unsure.
