---
name: amina-fam
description: Use fam (the family calendar/people/places CLI) whenever the conversation touches the household schedule, contacts, or locations — recording or checking events, "who is X", "where is Y", showing a day/week/month.
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
  - `rem list|ack|cancel|rules`
- Time: the household lives in Asia/Almaty (+05:00). Take "now" from the
  date/time already available in your context — never ask the user what
  today's date is. Talk to the user in Asia/Almaty local time, short form
  ("ср 15-го, 10:00"). Every value you pass to fam (`--start`, `--end`, a
  `day` date) is ISO-8601 with an explicit offset, e.g.
  `2026-07-15T10:00:00+05:00`.

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
   ISO-8601 string with offset, computed from the current date/time in your
   context, and pass that string straight to fam. Don't narrate the
   calculation — just produce the ISO value.
2. **Unknown person/place ⇒ stop and ask, then retry.** fam exits 2 with
   `unknown person: X` or `unknown place: X` on stderr when a name doesn't
   resolve. Stop, ask the user to confirm who/where it is ("это кто/где —
   записать?"). When they confirm, run `fam people add <X> --alias <X>` or
   `fam places add <X> --alias <X>` using their exact wording verbatim —
   never normalize or clean up the form, so the alias matches what they
   actually said. Then retry the original command.
3. **Other stderr + exit 2 failures are real errors, not retry bait.**
   Examples: `alias already in use by ...`, `unknown field: ...`, `unknown
   event: ...`. Read the message and fix the actual problem (different
   alias, a valid field name, the right event id) or tell the user what's
   wrong — don't blindly resubmit the same command.
4. **Never delete or overwrite anything without an explicit request.**
   Cancelling an event is `fam cal cancel <id>`, never a raw edit. Get the
   id from `cal show`/`cal day --json`/`cal range --json`, never guess it.
5. `cal day` / `cal range` / `cal grid` only ever list **active** events —
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
| Already on the way (ack chain) | `fam rem ack EVENT_ID` |
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

Reminders and the digest are sent proactively — the household reacts to
them in whatever conversation turn comes next. Ack/cancel always apply to
an event's whole remaining reminder chain, never a single stage:

- **"уже едем/выходим/собираемся/знаю" (already on it)** → `fam rem ack
  EVENT_ID`. Acks every still-pending reminder for that event; sent/acked/
  cancelled ones are untouched.
- **"не напоминай про это" / "погаси напоминания про X" (stop nagging)**
  → `fam rem cancel EVENT_ID`. Same scope as ack, cancelled instead.
- **"какие напоминания" (what's pending)** → `fam rem list --due --json`
  for what's about to fire, or `fam rem list --json` for everything.

**Finding the event_id.** If only one event plausibly fits what the user
just said, use its id directly. Otherwise resolve it before calling
`ack`/`cancel`:
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
