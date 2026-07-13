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
- Plans: an errand or a "надо сделать" without a fixed time — "запиши
  план...", "надо купить...", "до пятницы", agreeing to do a plan "по
  пути" to an event, or later reporting a plan is done. See Plan Verbs
  below.
- Meds: "добавь лекарство...", "какие лекарства", "сколько осталось X",
  or reacting to a medication reminder the agent itself sent —
  "выпила"/"приняла", "пропускаю"/"перестань". See Medication Verbs
  below.
- Shopping: "добавь в покупки...", "что купить", "купила X". See
  Shopping Verbs below.
- Place category: "это аптека", "там продуктовый" — categorizing a place
  for the shopping "по пути" match. See Place Category below.

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
  - `places add|update|alias|resolve|list`
  - `cal add|update|cancel|done|show|day|range|grid`
  - `rem list|ack|cancel|rules|active`
  - `plan add|list|done|drop|attach`
  - `meds add|list|edit|rm`
  - `med taken|skip`
  - `shop add|list|done`
  - `road <event_id>`
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
2. **`--start` is when the event BEGINS (быть на месте), never the
   departure time.** The system computes departure itself (start −
   travel_min) — never pass a departure time as `--start`. If the user
   names a DEPARTURE time («выезжаем в 9», «выедем в час», or answers a
   question of yours about выезд): travel known → start = departure time
   + travel_min; travel unknown → first ask for the travel time or the
   arrival time. Ask for times yourself as "во сколько нужно быть на
   месте?" — NOT "во сколько выезжать" (departure is derived; the system
   computes it). If the user clearly thinks in departure terms, convert
   as above and name BOTH times in the confirmation ("на месте в 09:40,
   выезд в 09:00"). Exception to the no-arithmetic rule above: this
   minute addition (выезд + travel_min) is the ONE calculation you do
   yourself — "now" for it still comes the usual way (timestamp prefix,
   or one `date` call). If the road hasn't been computed yet, this
   conversion uses the user's stated travel (or you ask for the arrival
   time as above) — the system will correct the departure time itself via
   the road re-checks before выезд.
3. **Unknown person/place ⇒ stop and ask, then retry.** fam exits 2 with
   `unknown person: X` or `unknown place: X` on stderr when a name doesn't
   resolve. Stop, ask the user to confirm who/where it is ("это кто/где —
   записать?"). When they confirm, run `fam people add <X> --alias <X>` or
   `fam places add <X> --alias <X>` using their exact wording verbatim —
   never normalize or clean up the form, so the alias matches what they
   actually said. Then retry the original command.
4. **Destination phrasing ⇒ resolve the place before recording.**
   "съездить/поехать/сходить в/к X" means X is a place, not just words in
   the title — run `fam places resolve X` first. Unknown → same
   unknown person/place stop-and-ask rule as above ("это где — записать?");
   on confirmation `fam places add` with the user's exact wording, then
   pass `--place` to `cal add`/`cal update`. Don't ask about travel time
   (`--travel-min`) — record it only if the user brings it up themselves.
   When the event's place HAS coordinates, the system computes the real
   road with live traffic itself at creation and re-checks it before
   выезд — never ask for travel time then, and don't treat user-stated
   minutes as the truth. Minutes the user volunteers are still recorded
   (`--travel-min`) as a fallback, but once a computed value exists, tell
   the user THAT one: after `cal add`, if the returned JSON shows
   `travel_min_road` set, put the computed дорога and выезд in your
   confirmation instead of the user's guess (`fam road <event_id>`
   returns the current `travel_min_road` + `leave_at_local` any time).
   Exception: the user named a departure time — then the
   departure-vs-start rule above governs (уточни дорогу или
   время-на-месте).
5. **A 2GIS link carries the place's coordinates — extract them.** A
   2GIS URL ends in `.../geo/<id>/LON,LAT`, and the order is LON,LAT
   (сначала долгота, потом широта — NOT lat,lon). Example:
   `https://2gis.kz/almaty/geo/70000001030764296/76.781529,43.233821`
   → `--lon 76.781529 --lat 43.233821`. When creating a place from such
   a link, pass them along with the link:
   `fam places add "Name" --address <link> --lat <LAT> --lon <LON>`.
   When the place already exists WITHOUT coordinates and the user sends
   a 2GIS link for it: `fam places update <ref> --lat <LAT> --lon <LON>
   --address <link>`. Double-check the order every time — the first
   number in the URL is the longitude (долгота), the second is the
   latitude (широта); swapping them puts the place in the ocean and the
   computed road becomes garbage.
6. **Other stderr + exit 2 failures are real errors, not retry bait.**
   Examples: `alias already in use by ...`, `unknown field: ...`, `unknown
   event: ...`. Read the message and fix the actual problem (different
   alias, a valid field name, the right event id) or tell the user what's
   wrong — don't blindly resubmit the same command. Exception: `start is
   in the past` has its own protocol, the past-start protocol below.
7. **`start is in the past` ⇒ past event or stale "now", not a bug.**
   `cal add`/`cal update --start` exits 2 with `start is in the past
   (now: <ISO+05:00>). If the user means a past event, retry with
   --allow-past; otherwise re-derive the date (run date).` when the ISO
   you sent is more than a few minutes behind now. The user is clearly
   talking about something already past ("вчера были у бабушки", "запиши,
   что мы ездили в...") → retry the identical command with
   `--allow-past`. Otherwise your "now" was wrong → make one `date` call,
   re-derive the ISO from the fresh date/time, and retry without
   `--allow-past`. If the error fires on a start you CONVERTED from a
   departure time («выезжаем в X»), do NOT use `--allow-past` — a
   converted start in the past almost always means the departure date was
   mis-anchored; re-ask the user for the correct date/time instead.
8. **Never delete or overwrite anything without an explicit request.**
   Cancelling an event is `fam cal cancel <id>`, never a raw edit. Get the
   id from `cal show`/`cal day --json`/`cal range --json`, never guess it.
9. `cal day` / `cal range` / `cal grid` only ever list **active** events —
   cancelled events are hidden by design, not gone.
10. **A plan's `--deadline` follows the same no-arithmetic rule as rule 1.**
    "до пятницы", "к концу недели" → resolve to a plain `YYYY-MM-DD` from
    "now" as established under Time above (the message's timestamp prefix,
    or one `date` call) — never from a date mentioned earlier in the
    conversation. A plan with no relative date in the request gets no
    `--deadline`; don't invent one.

## Quick Reference

| Goal | Command |
| --- | --- |
| Record an event (`--start` = время начала, не выезда) | `fam cal add --title T --start ISO [--end ISO] [--place P] [--with NAME]... [--transport car\|walk\|public\|unknown] [--notes N]` |
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
| Update a place (coords from a 2GIS link, address) | `fam places update <ref> [--lat LAT] [--lon LON] [--address A] [--travel-min M]` |
| Пересчитать дорогу / когда выходить | `fam road <event_id>` |
| Recent activity | `fam log --last-hours 24` |
| Reminders for one event | `fam rem list --event ID --json` |
| Reminders due right now | `fam rem list --due --json` |
| Events with an in-progress reminder chain | `fam rem active --json` |
| Just starting to get ready (ack prepare stages only) | `fam rem ack EVENT_ID --scope prepare` |
| Already on the way (ack whole chain) | `fam rem ack EVENT_ID` |
| Stop nagging about it (cancel chain) | `fam rem cancel EVENT_ID` |
| What rules generate reminders | `fam rem rules --json` |
| Record a plan/errand | `fam plan add "TITLE" [--place P] [--person NAME] [--deadline YYYY-MM-DD]` |
| See open plans | `fam plan list` (add `--all` for done/dropped too) |
| Mark a plan done | `fam plan done <id>` |
| Drop a plan | `fam plan drop <id>` |
| Attach a plan to an event ("по пути") | `fam plan attach <id> --event <event_id>` |
| Add a med | `fam meds add "NAME" --times HH:MM,HH:MM --remaining N [--dose D] [--threshold N]` |
| See meds / stock | `fam meds list` |
| Edit a med | `fam meds edit <id> [--name] [--dose] [--times] [--remaining] [--threshold] [--enabled 0\|1]` |
| Mark a dose taken | `fam med taken <intake_id>` |
| Skip one dose | `fam med skip <intake_id>` |
| Add to shopping list | `fam shop add "NAME" [--qty Q] [--by WHO]` |
| See shopping list | `fam shop list` |
| Mark item bought | `fam shop done <id>` |
| Categorize a place (аптека/продуктовый) | `fam places update <ref> --category pharmacy\|grocery` |

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
just silenced something. In particular, after `rem ack EVENT_ID --scope
prepare` returns `acked: 0`, do not say that preparation reminders were
silenced; state that no preparation stages remained and preserve any
still-pending departure reminder. If the user then says they are leaving,
acknowledge the full chain with `rem ack EVENT_ID` and confirm that remaining
reminders were stopped.

## Plan Verbs

A plan is an errand or a to-do without a fixed time — unlike a calendar
event, it never gets a `--start`. Same unknown-place stop-and-ask
protocol as rule 3 applies if `--place` is given and doesn't resolve.

- **Recording a plan** — "запиши план...", "надо купить...", "не забыть
  ..." → `fam plan add "TITLE" [--place P] [--person NAME] [--deadline
  YYYY-MM-DD]`. A relative deadline ("до пятницы") is resolved per rule
  10 above. Confirm in one line, same style as calendar adds: "Записал в
  планы: купить куртку, до пятницы."
- **Reporting a plan done** (typically answering the evening follow-up
  the agent itself sent, but also mid-conversation — "куртку купила",
  "всё сделали", "готово") → find the matching plan, then mark it done:
  1. `fam plan list` (open plans only, no `--all` needed).
  2. Exactly one plan plausibly matches what the user said → `fam plan
     done <id>`, confirm briefly ("Отметил: куртка куплена.").
  3. Several plausible matches, or none → ask which plan they mean, or
     say there's no open plan like that — never guess an id.
- **Dropping a plan** ("уже не надо", "отменяется", "передумали") →
  `fam plan drop <id>`, found the same way as done above.
- **Accepting "по пути"** — the agent may mention in a reminder that an
  open plan is on the way to an event ("по пути можно заехать за..."); a
  plain agreement in reply ("да, заеду", "давай", "ок") → `fam plan
  attach <id> --event <event_id>`. The reminder text names the plan(s)
  by **title only, no ids** — and the reminder itself is NOT in your
  session context (same as Reminder Reactions above: a background tick
  sent it, you cannot recall it). Resolve both ids fresh, every time:
  1. Plan id — `fam plan list`, match by title the same way as
     Reporting a plan done above: exactly one match → use its id;
     several or none → ask which plan they mean.
  2. Event id — the same lookup as Reminder Reactions' "Finding the
     event_id for cancel" (`fam log --kind gate.sent ...`, then match
     against the day's events); if only one event plausibly fits, use
     it directly.
  `leave_at` is recomputed automatically by the existing road logic —
  don't touch `--start` or travel time yourself. Confirm briefly, no
  need to restate the recomputed time unless the user asks.

  Example: reminder said "По пути: забрать куртку из химчистки" for the
  дантист event; user replies "да, заеду" →
  `fam plan list` → one open plan titled "забрать куртку из химчистки" →
  id 7; `fam log --kind gate.sent --last-hours 6 --json` → that
  reminder's `raw.event_id` → 42 →
  `fam plan attach 7 --event 42` → "Записал, куртку заедете забрать по
  пути к стоматологу."

## Medication Verbs

A med has a schedule (`--times`, comma-separated `HH:MM`), a stock count
(`--remaining`), and a low-stock cutoff (`--threshold`, defaults to 0
server-side). The household's own persistent reminder series fires
"пора принять X" (and, out of stock, "пора купить X") out-of-band —
a background tick, same as calendar reminders above, not this
conversation.

- **Adding a med** — "добавь лекарство X, утром и вечером, осталось 20"
  → `fam meds add "X" --times 08:00,20:00 --remaining 20 [--dose D]
  [--threshold N]`. The named times are literal clock times, not offsets
  from "now" — rule 1's no-arithmetic rule doesn't apply here. Only pass
  `--dose`/`--threshold` if the user actually mentions a dose or a
  specific low-stock cutoff; don't invent one. Confirm in one line:
  "Записал лекарство: X, 08:00 и 20:00, осталось 20."
- **Checking meds** ("какие лекарства", "сколько X осталось") → `fam
  meds list`.
- **Editing a med** ("поменяй время X", "теперь осталось Y", "отключи
  X") → resolve `<id>` from `fam meds list` by name first — never guess
  it — then `fam meds edit <id> [--name] [--dose] [--times]
  [--remaining] [--threshold] [--enabled 0|1]`.
- **"выпила"/"приняла" (this dose taken)** and **"пропускаю"/"перестань"
  (skip this dose)** both need the pending intake_id first. The reminder
  that fired is NOT in your session context (same out-of-band constraint
  as Reminder Reactions above) — resolve it fresh, never guess, same
  match-then-act pattern as Shopping Verbs' "Marking bought" below:
  1. `fam med list --pending --json` — every dose currently awaiting an
     ack.
  2. Match by medication name (and time, if the user gave one) against
     that list.
  3. Exactly one match → use its `intake_id`. Several plausible matches,
     or none → ask which dose they mean, or say nothing's pending for X
     — never guess an id.
  - "выпила"/"приняла" → `fam med taken <intake_id>` (singular `med`,
    not `meds` — `meds` manages med definitions, `med` acks one dose).
    If the result has `"restock": true`, mention "пора купить X" in your
    reply — it's already on the shopping list (auto-added just now, or
    already open there); don't add it yourself.
  - "пропускаю"/"перестань" → `fam med skip <intake_id>` — closes only
    that one dose; `remaining` and the next scheduled dose are
    untouched. If the user actually means "stop reminding me about X
    altogether", that's `fam meds edit <id> --enabled 0`, not a skip.

## Shopping Verbs

- **Adding an item** — "добавь в покупки молоко" → `fam shop add
  "молоко"` (`--qty`/`--by` only if the user mentions a quantity or who
  it's for). Confirm in one line: "Добавил в покупки: молоко."
- **Checking the list** ("что купить", "список покупок") → `fam shop
  list` (open items only).
- **Marking bought** ("купила молоко", "молоко взяли") → find the
  matching item, then mark it done, same pattern as Plan Verbs' "done"
  above:
  1. `fam shop list`.
  2. Exactly one item plausibly matches what the user said → `fam shop
     done <id>`, confirm briefly ("Отметил: молоко куплено.").
  3. Several plausible matches, or none → ask which item they mean, or
     say there's nothing open like that — never guess an id.
- An item with `"source": "meds"` in `fam shop list`'s JSON was
  auto-added by a low-stock restock (see Medication Verbs above) — treat
  it the same as any manually-added item once the user says it's bought.

## Place Category

"это аптека" / "там продуктовый" categorizes a place for the restock
"по пути" shopping match — it doesn't touch address or coordinates:
`fam places update <place> --category pharmacy` or `--category
grocery`. Same unknown-place stop-and-ask protocol as rule 3 applies if
`<place>` doesn't resolve.

## Digest Replies

The morning digest always closes with the same question: "Если появятся
планы или изменения — расскажи или надиктуй, я запишу."

- Plans or changes in the reply → ordinary calendar intake, the same
  add/update rules as any other conversation, or a new plan per Plan
  Verbs above if it has no fixed time — nothing digest-specific about
  either.
- A plain "всё в силе" / "без изменений" / "как обычно" acknowledgment →
  no fam call needed; one short line back is enough.
- The digest itself may list plans with an approaching deadline
  alongside the day's events — reacting to those follows the same Plan
  Verbs rules as any other plan mention.

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
