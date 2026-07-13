# Hermes @ hermes-home — Phase 0 state (Amina instance)

This documents the live `~/.hermes` configuration on the `hermes-home` box, which is
**outside** this git repo. It exists so future operators don't have to reverse-engineer
`config.yaml` / `.env` / `auth.json` to understand why this instance looks different
from the VPS instance.

Recorded: 2026-07-10, end of Phase 0 acceptance (Task 9).

## Purpose

This is **Гермес** running as the personal assistant for Амина (Amina Mirzatbayeva),
reachable over WhatsApp. Денис (Denis) is the administrator and controls/observes the
instance via a private Telegram admin channel. Unlike the VPS instance, WhatsApp here
is Amina's primary, first-class channel — not an external-comms surface layered on
top of a dev assistant.

## Model / auth

- `model.default: gpt-5.6-luna`, `model.provider: openai-codex` (`~/.hermes/config.yaml`).
- OpenAI-codex credential is an OAuth credential pool entry in `~/.hermes/auth.json`
  (`credential_pool.openai-codex[0]`, label `openai-codex-oauth-1`) — tied to the
  ChatGPT Plus account — Denis's separate account (Apple-relay email), dedicated to this instance. Do not print `auth.json` raw;
  it contains live access/refresh tokens.
- OpenRouter API key is supplied via `.env` (`OPENROUTER_API_KEY`), used only for
  auxiliary (non-main-conversation) functions — see below.

## Channels

### WhatsApp — primary user channel
- `WHATSAPP_MODE=bot` + `WHATSAPP_ALLOWED_USERS=+77011102626,+77012110625` (`.env`) —
  allowlists only Amina (+77011102626) and Denis (+77012110625).
- `whatsapp.unauthorized_dm_behavior: ignore` (`config.yaml`).
- Bridge session lives at `~/.hermes/whatsapp/session` (Baileys creds/keys/pre-keys).
  `~/.hermes/platforms/whatsapp` is a symlink to `~/.hermes/whatsapp` (`../whatsapp`),
  which is how the gateway/bridge resolve the session path.

### Telegram — admin channel only
- `telegram.allowed_chats: ['79564752']` (`config.yaml`) + `TELEGRAM_ALLOWED_USERS=79564752`
  (`.env`) — Denis only. Not a channel Amina has access to.

### Home channels (cron/alert default delivery targets, `.env`)
- `TELEGRAM_HOME_CHANNEL=79564752` (Denis)
- `WHATSAPP_HOME_CHANNEL=107494508621998@lid` — **Denis's** main WhatsApp LID,
  not Amina's. Switched from Amina's LID because the home channel receives
  system/restart noise, which should not land in Amina's chat.

## Deliberate deviations from the VPS/default global config

- **`plugins.enabled: []`** — the `whatsapp-policy` plugin that runs on the VPS
  instance is deliberately **not** enabled here. That plugin's policy assumptions
  (WhatsApp = external/unsolicited correspondence surface) don't hold for this
  instance, where WhatsApp is Amina's main, trusted, allowlisted channel. Enabling it
  would misapply VPS-style gating to normal conversation with Amina.
- **`display.runtime_footer.enabled: false`** — Amina should not see internal
  runtime/debug info (active model, token/quota usage, cwd) in replies. This is
  intentionally off, unlike operator-facing instances where the footer is useful.

## Weather crons

Two recurring jobs deliver a short Almaty weather forecast to Amina over WhatsApp
(`whatsapp:+77011102626`):

| Job ID | Name | Schedule (Almaty-local) | Status |
|---|---|---|---|
| `8b751dbfd5d6` | Утренний короткий прогноз погоды Алматы — Амина | `0 7 * * *` | **paused** (2026-07-11, Phase 2b Task 8 — replaced by `fam-digest.timer`, see "Phase 2b — proactive timers" below) |
| `150d115fe905` | Вечерний короткий прогноз погоды Алматы — Амина | `0 20 * * *` | active |

Inspect with `$H cron list --all` (plain `list` hides paused jobs) where
`H="/home/denis/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main"`.

The morning cron's digits were retuned `0 2 * * *` → `0 7 * * *` during the
Task 15 global `timezone: Asia/Almaty` flip (see "`gateway.message_timestamps`
+ global timezone" below) — cron `schedule` fields are interpreted
Almaty-local now, not server-local UTC, so the same 07:00 firing instant
needed different digits. It was retuned alongside the evening job even though
it's kept paused; `next_run_at` only gets recomputed when a job is
resumed, so until then `jobs.json` still shows a stale past value for this
job rather than a fresh future one.

**Note:** during the pilot (see "Pilot mode" below), both jobs' `deliver`
field is temporarily repointed away from `+77011102626` — see that section
for current state and the revert steps.

## Pilot mode (2026-07-11)

**Гермес is running a PILOT with a test number, not Amina.** Amina
(+77011102626) has been temporarily and fully removed from the loop: the
gateway will not answer her (she is no longer in the WhatsApp allowlist), and
the weather crons no longer deliver to her. This overrides the Phase-0
baseline documented above (WhatsApp channel, weather crons) for the duration
of the pilot.

- **Pilot user:** +77782110625 (Denis's second number), standing in for
  Amina for pilot testing.
- **WhatsApp allowlist** (`.env`, `WHATSAPP_ALLOWED_USERS`): now
  `+77782110625,+77012110625` (pilot number + Denis main). Amina's number is
  **not** in the allowlist — unauthorized DMs from her are dropped per
  `whatsapp.unauthorized_dm_behavior: ignore`.
- **Weather crons** (`~/.hermes/cron/jobs.json`): both `8b751dbfd5d6`
  (morning) and `150d115fe905` (evening) now have
  `"deliver": "whatsapp:+77782110625"` instead of Amina's number. Edited via
  `$H cron edit <job_id> --deliver whatsapp:+<number>`.
- Pre-change `.env` backup: `~/.hermes/.env.bak-pilot-20260710203739`.

### Revert checklist for go-live (Amina back in the loop)

1. `.env`: swap `WHATSAPP_ALLOWED_USERS` pilot number back to Amina's:
   `WHATSAPP_ALLOWED_USERS=+77011102626,+77012110625`.
2. Crons: repoint both jobs back to Amina —
   `$H cron edit 8b751dbfd5d6 --deliver whatsapp:+77011102626` and
   `$H cron edit 150d115fe905 --deliver whatsapp:+77011102626`.
3. fam-config: switch `~/.hermes/private/amina/fam-config.json`'s `target`
   field from the pilot number back to Amina's WhatsApp:
   `"target": "whatsapp:+77011102626"` — otherwise fam's proactive sends
   (reminders/digest) keep going to the pilot number after everything else
   has gone live.
4. Restart the gateway: `$H gateway restart`.
5. Verify: `grep WHATSAPP_ALLOWED_USERS ~/.hermes/.env`,
   `grep '"deliver"' ~/.hermes/cron/jobs.json`, and
   `grep '"target"' ~/.hermes/private/amina/fam-config.json` all show
   `+77011102626`, and the pilot number (`+77782110625`) no longer appears in
   any of them.

## Backups inventory (outside git, under `~/.hermes/`)

Left in place intentionally as Phase-0 rollback points — do not delete without checking
which task/phase depends on them:

- `config.yaml.bak-amina-p0` — pre-Amina-Phase-0 baseline config snapshot.
- `config.yaml.bak-task6-20260710181428`
- `config.yaml.bak-task7-20260710184021`
- `config.yaml.bak-task7-plugin`
- `.env.bak-task7-20260710184021`
- `.env.bak-task7fix-20260710191913`
- `SOUL.md.bak-denis` — pre-Amina-persona SOUL.md backup.
- `private-career-removed.tar.gz` — Denis's private career-related context stripped
  out of this instance before it became Amina's assistant.

(Backups under `~/.hermes/hermes-agent/**/*.bak*` and `*.bak.<feature>.<timestamp>`
are unrelated pre-existing dev-branch backups from feature work on the fork itself,
not Phase-0-specific.)

## Roles

- Active role package: `core-shadow` at `~/.hermes/role_packages/core-shadow/`
  (`role_packages.routing.package_path`), with
  `active_roles: [scribe, researcher, engineer, security_auditor, career_strategist,
  artist, lawyer]` and `activation_mode: selected_roles`.
- **`hermes role list` (and `$H role list`) reports "No role packages installed."
  This is expected and NOT a misconfiguration.** That command reads a *different*
  registry — package directories under a hyphenated naming scheme
  (`hermes-<role>-core`) that aren't registered through whatever index `role list`
  queries. The role package directories do exist
  (`hermes-artist-core`, `hermes-career-strategist-core`, `hermes-engineer-core`,
  `hermes-lawyer-core`, `hermes-researcher-core`, `hermes-scribe-core`,
  `hermes-security-auditor-core`) and are wired via `role_packages.routing` above,
  which is the config that actually governs routing.

## Skills: activation caveats

**Gateway caching:** The gateway's skills system prompt uses an in-process LRU cache
keyed on directory structure, available tools, and platform — **not** on file
content. Adding or editing a `SKILL.md` file requires a gateway restart to take
effect: `systemctl --user restart hermes-gateway`.

**Session-baked skills list:** Existing chat sessions keep the skills list baked
into their system prompt at session creation time. After installing a new skill (or
after gateway restart), active sessions must be reset with `/reset` in chat, or they
will not see the newly available skill.

## Aux functions on OpenRouter

`auxiliary.title_generation`, `auxiliary.compression`, and `auxiliary.web_extract` are
routed to `provider: openrouter`, `model: tencent/hy3:free`. Confirmed exercised on the
live code path in `~/.hermes/logs/agent.log`, e.g.:

```
2026-07-10 18:21:42,912 INFO agent.auxiliary_client: Auxiliary title_generation: using openrouter (tencent/hy3:free)
2026-07-10 18:58:52,708 INFO agent.auxiliary_client: Auxiliary title_generation: using openrouter (tencent/hy3:free)
2026-07-10 19:08:55,449 INFO agent.auxiliary_client: Auxiliary title_generation: using openrouter (tencent/hy3:free)
```

Other aux functions (`approval`, `curator`, `mcp`, `session_search`, `triage_specifier`)
stay on `openai-codex` / `gpt-5.4-mini`, separate from the main conversation model
(`gpt-5.6-luna`).

## Phase 1 — voice (2026-07-11)

Voice messages (WhatsApp PTT) are transcribed to text before entering the normal
conversation pipeline. Chain: `whatsapp voice note` → `stt.providers.transcriber`
(config.yaml, `type: command`) → `custom/stt/transcribe_remote.sh {input_path}
{output_path}` → remote transcriber at `http://192.168.1.20:5001/transcribe`
(home-pc, docker swarm, OpenAI Whisper + Redis cache), with automatic fallback
inside the wrapper to a local `faster-whisper` (`small`, `int8`, CPU) running in
`hermes-agent`'s own venv when the remote call fails or returns empty.

### Happy path — confirmed on the live gateway

`~/.hermes/logs/agent.log`, 2026-07-10 20:22 UTC, real WhatsApp voice note from
Denis, transcriber reachable:

```
2026-07-10 20:22:22,915 INFO gateway.run: inbound message: platform=whatsapp user=Denis Vanyushkin ... msg='[ptt received]'
2026-07-10 20:22:23,035 INFO tools.transcription_tools: Transcribing aud_8ab4d1acc5e2.ogg via command STT provider 'transcriber'...
2026-07-10 20:22:31,585 INFO tools.transcription_tools: Transcribed aud_8ab4d1acc5e2.ogg via command STT provider 'transcriber' (47 chars)
```

followed by a normal `agent.turn_context` conversation turn and a reply ~32s later
(49 chars). Denis confirmed the reply matched what he actually said — voice in
produces a meaningful answer out.

### Fallback path — confirmed on the live gateway

Method: `TRANSCRIBER_URL` swapped to a dead endpoint (`http://127.0.0.1:9`) in the
STT command invocation, gateway restarted (`Gateway startup ...` at 20:24:25) to
pick it up. Two real voice notes sent afterward both transcribed successfully —
with the dead URL, only the local `faster-whisper` fallback could have produced
these transcripts:

```
2026-07-10 20:26:17,826 INFO gateway.run: inbound message: platform=whatsapp user=Denis Vanyushkin ... msg='[ptt received]'
2026-07-10 20:26:17,932 INFO tools.transcription_tools: Transcribing aud_9a3e13f8fdde.ogg via command STT provider 'transcriber'...
2026-07-10 20:26:25,024 INFO tools.transcription_tools: Transcribed aud_9a3e13f8fdde.ogg via command STT provider 'transcriber' (32 chars)

2026-07-10 20:26:49,159 INFO gateway.run: inbound message: platform=whatsapp user=Denis Vanyushkin ... msg='[ptt received]'
2026-07-10 20:26:49,234 INFO tools.transcription_tools: Transcribing aud_42eb5cfc79e5.ogg via command STT provider 'transcriber'...
2026-07-10 20:26:57,649 INFO tools.transcription_tools: Transcribed aud_42eb5cfc79e5.ogg via command STT provider 'transcriber' (8 chars)
```

Both replies delivered, human-confirmed by Denis. Config was then reverted to the
live transcriber URL and the gateway restarted again (20:28:14) back to normal
operation. Note the fallback transcriptions (~7.2s and ~8.5s) are about as fast as
the remote happy-path call (~8.5s) because `faster-whisper small` was already
warm on that host — cold-start (first model load) took ~16.6s during Task 11's
standalone testing.

Transcript *char counts* are visible directly in these `tools.transcription_tools`
log lines for every voice message on both paths; full transcript text in a
dedicated audit log is Phase 2 scope, not yet wired.

### Regression check (no gateway needed)

`custom/stt/test_transcribe_remote.sh` exercises the wrapper standalone: happy
path against the real transcriber, then fallback with
`TRANSCRIBER_URL=http://127.0.0.1:9` forcing a connection failure — both legs
assert the transcript contains "гермес" (not just non-empty). Re-run 2026-07-11:

```
$ bash custom/stt/test_transcribe_remote.sh
transcriber unavailable, falling back to local faster-whisper
ALL PASS
```

### Network hole (two layers)

The gateway host (`hermes-home`, VM 200, VLAN 20 "Agents") is otherwise isolated
from the rest of the LAN (see `hermes-vlan-isolation` notes); voice needed one
narrow, outbound-only exception to reach the transcriber on `home-pc`
(`192.168.1.20:5001`). Added in both enforcement layers, per the project's
"add holes in two places" rule:

1. **Firewalla** — allow rule added by Denis (Agents VLAN → `192.168.1.20:5001`),
   layered on top of the existing Agents→LAN block-all.
2. **Proxmox belt** (`/etc/pve/firewall/200.fw` on `pve1`) — explicit `ACCEPT`
   line ahead of the default-DROP block:

   ```
   OUT ACCEPT -dest 192.168.1.20 -p tcp -dport 5001 # transcriber STT (Amina assistant, 2026-07-11)
   ```

   Backup of the pre-change file: `pve1:/root/200.fw.bak-amina-p1`.

Verified from `hermes-home` (2026-07-11): transcriber reachable and healthy
(`curl http://192.168.1.20:5001/health` → `{"status":"healthy", ...}`); every
other previously-open LAN target stays blocked (`192.168.1.1:22` Firewalla SSH,
`192.168.1.4:8006` Proxmox UI, `192.168.1.21:80` homeserver, `192.168.102.22:8123`
Home Assistant, `192.168.1.20:80` transcriber host's other port) — all timeout;
internet egress unaffected (`https://api.telegram.org` → HTTP 302).

### Test entry points

- `custom/stt/test_transcribe_remote.sh` — standalone regression test, no gateway
  needed (see above). Requires `custom/stt/fixture_ru.ogg` (voice fixture
  containing the word "гермес", used as the correctness check).
- `TRANSCRIBER_URL` env var overrides the remote endpoint for
  `custom/stt/transcribe_remote.sh` directly — useful for pointing at a
  different transcriber or forcing the fallback branch manually (this is the
  same mechanism used for the live-gateway fallback proof above, applied at the
  command level instead of the script level).

### Known notes

- The transcriber's `/tts` endpoint is broken upstream (home-pc side, no TTS
  model registered on the `speaches.vanyushk.in` backend) — Phase 1 only uses
  `/transcribe`; text-to-speech is out of scope here.
- Remote STT latency is ~8s end-to-end for a short voice note; the local
  `faster-whisper` fallback is comparable once warm (~7-8s) but has a slow
  cold start (~16.6s first load) if the process hasn't transcribed anything
  yet.


## Phase 2a — fam sandbox bridge (2026-07-11)

Amina's personal-assistant data tool (`custom/fam`, a small CLI backed by SQLite —
people/places/calendar/audit log) is reachable both from the host and from the
agent's own terminal sandbox (Docker), against the **same** live database.

### Mounts (`terminal.docker_volumes`, `~/.hermes/config.yaml`)

Two mounts make this work, both required:

- `~/.hermes/hermes-agent:/workspace/live-hermes` — fam's **code** (already existed,
  used by other `custom/` tools too).
- `~/.hermes/private/amina:/root/.hermes/private/amina` — fam's **database**
  (`assistant.db`), added in Phase 2a Task 7.

Inside the sandbox: `python3 -c "import fam.db as d; print(d.resolve_db_path())"`
(or just `custom/fam/bin/fam --json init`) resolves to
`/root/.hermes/private/amina/assistant.db`, the exact bind-mounted path — same
file the host resolves to at `/home/denis/.hermes/private/amina/assistant.db`. One
database, two access points.

**Recreate sandbox containers after any `docker_volumes` change.**
`terminal.container_persistent: true` means running `hermes-*` containers keep
whatever mounts they were created with; editing `config.yaml` alone does **not**
retroactively add a new mount to an already-running container. After changing
`docker_volumes`, remove the *sandbox* containers so the agent creates fresh ones
on next use:

```
docker ps -a --filter 'name=^hermes-' --format '{{.Names}}'   # verify list first!
docker rm -f <name-1> <name-2> ...                            # then remove by exact name
systemctl --user restart hermes-gateway
```

**Use the anchored `^hermes-` filter, and eyeball the printed list before removing
anything.** An unanchored `--filter name=hermes-` matches *substrings* anywhere in
the container name — it would also catch `deploy-hermes-webui-1` (the production
web UI container, unrelated to the agent sandbox, must never be touched here).

### `bin/fam` — venv-preferring shim

`custom/fam/bin/fam` runs `python3 -m fam` with `PYTHONPATH` set to its own
package directory, so it works unmodified from either context. It prefers the
repo's own `venv/bin/python3` (has Pillow, needed for `fam cal grid`) over
whatever `python3` resolves to on `PATH`, mirroring
`custom/stt/transcribe_remote.sh`'s `VENV_PY` pattern — with one extra check
`transcribe_remote.sh` didn't need: since the sandbox bind-mounts the *entire*
repo (including `venv/`), `venv/bin/python3`'s absolute symlink target
(`-> /usr/bin/python3`) resolves *inside the container's own filesystem* to an
unrelated system Python of a different minor version (no matching
`venv/lib/pythonX.Y/site-packages` for that version), rather than failing
outright — so the shim also checks that the resolved interpreter's version has a
matching `site-packages` dir in the venv before trusting it, and falls back to
`python3` on `PATH` otherwise (the sandbox's own interpreter, where Pillow is
installed separately — see below).

### Pillow in the sandbox

**Superseded 2026-07-11 (Phase 2a Task 9):** `terminal.docker_image` now points at
a custom, durable image, `hermes-sandbox-amina:1` (built from
`~/.hermes/sandbox-image/Dockerfile`, `FROM nikolaik/python-nodejs:python3.11-nodejs20`
+ `RUN pip install Pillow==12.2.0`), so Pillow is baked in and survives sandbox
container recreation — the old "reinstall after every recreation" workflow below no
longer applies. See "Sandbox image" under "Phase 2a — fam core" for the rebuild
command.

Historical note (Phase 2a Task 7, no longer accurate): Pillow used to be **not**
part of the sandbox image and **not** picked up from the host venv inside the
container (see above), so `fam cal grid` needed it installed directly in the
running sandbox container (`pip install Pillow`), which landed in the container's
own (non-bind-mounted) `site-packages` and had to be reinstalled after every
container recreation.

### Regression coverage

`custom/fam/tests/test_no_pillow_import.py` guards that `fam.cli` / `fam.grid`
import cleanly with Pillow completely blocked (via a `sys.meta_path` finder that
raises `ImportError` for `PIL`/`PIL.*`) — Pillow is only ever imported lazily
inside `grid.render_month()`/`render_week()`, so every other `fam` subcommand
must keep working in environments without Pillow (e.g. a sandbox running the
stock image instead of `hermes-sandbox-amina:1`, or any host Python without
Pillow installed).

## Phase 2a — fam core (2026-07-11)

Accepted (Task 10). `fam` is Amina's private family-data core: calendar,
people/places glossaries, PNG grid rendering, and an audit log, driven
entirely through its own CLI and exposed to the agent via the `amina-fam`
skill (see "Phase 2a — fam sandbox bridge" above for the sandbox mount
details). Full history: `.superpowers/sdd/2a-task-{1..10}-*.md` reports and
`.superpowers/sdd/progress.md` (`2a-T1`..`2a-T9` entries) in this repo;
design spec `docs/superpowers/specs/2026-07-10-amina-assistant-design.md`
§10; plan `docs/superpowers/plans/2026-07-11-amina-phase2a-fam-core.md`.

### Components

- `custom/fam/fam/db.py` — schema/init, WAL + foreign keys on.
- `custom/fam/fam/audit.py` — `audit_log` write (`log()`) + query (`query()`,
  filters: `--since`/`--last-hours`, `--kind`, `--grep`, `--limit`).
- `custom/fam/fam/people.py`, `places.py` — glossaries: add/alias/resolve/list,
  case-insensitive matching with a cyrillic-safe alias-collision guard,
  `people` also supports groups (`--group`, `member` to add members; a group
  ref passed to `cal add --with`/`--add-person` expands to its members
  automatically).
- `custom/fam/fam/cal.py` — event CRUD (`add/update/cancel/done/show/day/range`),
  UTC storage with Asia/Almaty local-time conversion via `zoneinfo`; unknown
  person/place refs raise `UnknownRefError` (stderr `unknown person|place: X`,
  exit 2) **before** any write — the CLI-facing signal for the skill to stop
  and ask the user, then `people add`/`places add`, then retry.
- `custom/fam/fam/grid.py` — PNG rendering, `render_day`/`render_week`/`render_month`
  (Pillow, imported lazily so non-grid commands work without it — see
  "Regression coverage" above).
- `custom/fam/fam/cli.py` + `custom/fam/bin/fam` — the CLI surface and its
  venv-preferring shim (see above).
- `custom/skills/amina-fam/SKILL.md` — the skill that drives all of the above
  from a live conversation (glossary interrogation loop, verbatim alias
  wording, no shell chaining, honest grid-failure reporting).

### Database

Same SQLite file, two access points (see "Mounts" above):
- Host: `/home/denis/.hermes/private/amina/assistant.db`
- Sandbox: `/root/.hermes/private/amina/assistant.db`

`fam.db.resolve_db_path()` picks the right one automatically (`$FAM_DB` env
var overrides both, for tests/scratch DBs).

### Test command

```
cd ~/.hermes/hermes-agent
PYTHONPATH=custom/fam venv/bin/python -m pytest custom/fam/tests -v
```

54 tests, all green, clean output (no warnings) as of Task 10 acceptance
(2026-07-11).

### Sandbox image

`terminal.docker_image: hermes-sandbox-amina:1` (`~/.hermes/config.yaml`) —
a custom image with Pillow baked in, replacing the stock
`nikolaik/python-nodejs:python3.11-nodejs20` (which lacks Pillow and doesn't
durably pick it up from the host venv — see "Pillow in the sandbox" above).

Rebuild after changing `~/.hermes/sandbox-image/Dockerfile`:

```
docker build -t hermes-sandbox-amina:1 ~/.hermes/sandbox-image/
docker ps -a --filter 'name=^hermes-' --format '{{.Names}}'   # verify list first!
docker rm -f <name-1> <name-2> ...                             # recreate sandboxes
systemctl --user restart hermes-gateway
```

(Same anchored-filter caution as "Recreate sandbox containers after any
`docker_volumes` change" above — never touch `deploy-hermes-webui-1`.)

### Media exchange dir convention

Grid PNGs (and any other file the agent needs to hand back to the user) go
through `/home/denis/.hermes/cache/documents/`, bind-mounted at the **same
absolute path** inside the sandbox (`docker_volumes`:
`/home/denis/.hermes/cache/documents:/home/denis/.hermes/cache/documents`) —
not `/tmp`, which is sandbox-local and invisible to the gateway process that
serves `MEDIA:` attachments. Convention: `fam cal grid ... -o
/home/denis/.hermes/cache/documents/grid.png`, then reply with
`MEDIA:/home/denis/.hermes/cache/documents/grid.png`. Fixed in commit
`d31ca793c` after the original SKILL.md draft used `/tmp/grid.png`, which
rendered successfully but the picture never reached the user.

### Known limitations

- **No alias rename/remove CLI.** `people`/`places` support `add`/`alias`
  (append-only) but not renaming or removing an existing alias — admin fixes
  go through direct `sqlite3` + a matching `fam log`-visible audit row (as
  done once during Phase 2a itself). Candidate for Phase 2b/3
  (`progress.md` backlog note under `2a-T9`). **Because a direct `sqlite3`
  edit bypasses `fam`'s own `audit.log()` call, the operator must manually
  `INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES (...)` (UTC
  timestamp, a descriptive `kind`, `actor='admin'`) as part of the same fix
  — easy to forget, but skipping it silently breaks `fam log`'s claim to be
  the complete append-only record of every mutation, per the pattern used for
  the «Мег»→«Мега» alias rename in T9.**
- **Lat/lon are not parsed from 2GIS links yet.** `fam places add` accepts
  `--lat`/`--lon` as plain floats; there is no code that extracts coordinates
  from a pasted 2GIS URL — whoever adds a place either supplies them manually
  or leaves them unset.
- **Reminder engine, Тая-rules, morning digest, email, and the style gateway
  are Phase 2b**, not part of this core (spec §10, "Фаза 2 — ядро fam" lists
  them as later scope; this Phase 2a slice covers CRUD + glossaries + grid +
  audit only, per `.superpowers/sdd/2a-task-10-brief.md`).
- **Group unwrapping is unit-verified, not live-E2E-verified.** `cal add
  --with <group>` expanding to member `person_id`s is covered by
  `test_group_participant_expands`/`test_group_resolves_with_members`
  (`custom/fam/tests/test_cal.py`, `test_people.py`) but wasn't specifically
  exercised through a live pilot-chat dialogue in Task 9's E2E pass.
- Day-view grid's hour window is fixed (08:00–22:00); events outside it are
  clamped to the nearest edge row rather than shown at their true time
  (deliberate simplicity trade-off, `2a-task-9-gridfix-report.md`).

## Phase 2b — proactive timers (reminders + digest) (2026-07-11)

Two `systemd --user` timers drive fam's proactive side (reminder chains and the
morning digest) independently of `hermes cron` — they call `fam tick <name>`
directly, no LLM in the loop for the tick itself (only the style gate's rewrite
step, `hermes -z`, touches the model). Unit files live in git
(`custom/fam/systemd/`) and are installed as symlinks, per this project's
"units in git, install by symlink" convention (see fam-reminders/fam-digest
below).

| Timer | Schedule | Service | Notes |
|---|---|---|---|
| `fam-reminders.timer` | `OnCalendar=*:0/5` (every 5 min), `Persistent=false` | `fam tick reminders` (`Type=oneshot`) | Sends due reminder-chain stages via the style gate; always writes a `tick.reminders` audit row, even when 0 due. |
| `fam-digest.timer` | `OnCalendar=*-*-* 02:30:00 UTC` (07:30 Almaty), `Persistent=true` | `fam tick digest` (`Type=oneshot`) | Weather + today's events + a "what are your plans today?" prompt, one gated send/day (`force=True`, outside the daily budget); has its own dup-guard independent of systemd (`gate.sent kind=digest` already logged today → `{"skipped": "already_sent"}`). |

No unit uses `--now`/`WantedBy` tricks to fire immediately — the dup-guard for
the digest and the due-time filter for reminders both key off the real wall
clock, so an extra unplanned run is a no-op, not a duplicate send.

### Install

```
mkdir -p ~/.config/systemd/user
cd ~/.config/systemd/user
for f in fam-reminders.service fam-reminders.timer fam-digest.service fam-digest.timer; do
  ln -sf /home/denis/.hermes/hermes-agent/custom/fam/systemd/$f $f
done
systemctl --user daemon-reload
systemctl --user enable --now fam-reminders.timer fam-digest.timer
systemctl --user list-timers --all | grep fam   # confirm next-run times
```

### Disable / roll back

```
systemctl --user disable --now fam-reminders.timer fam-digest.timer
```

(The service units are `Type=oneshot` with no `[Install]` section — only the
`.timer` units are enabled; disabling the timers is sufficient, no separate
service-level action needed.) To revert to the old weather-cron delivery
instead of the digest:

```
H="/home/denis/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main"
$H cron resume 8b751dbfd5d6
```

### Manual smoke test

```
systemctl --user start fam-reminders.service
journalctl --user -u fam-reminders --since "2 min ago"
custom/fam/bin/fam log --last-hours 1 --kind tick --json   # tick.reminders audit row

custom/fam/bin/fam tick digest --json                      # DO NOT pass --now; real send
custom/fam/bin/fam log --last-hours 1 --json                # gate.sent row with raw/final text
```

### Live verification (2026-07-11, Task 8 acceptance)

- `fam-reminders.timer` / `fam-digest.timer` both `enabled`+`active`, confirmed
  via `systemctl --user list-timers --all`.
- `fam-reminders.service` manual run: clean journal, `due=0 sent=0 ... stale=0
  error_capped=0`, matching `tick.reminders` audit row (no reminder chains exist
  yet — expected, Task 8 predates any live event with an armed chain).
- `fam tick digest --json` (real run, no `--now`): `{"status": "sent",
  "date_local": "2026-07-11", "weather_present": true, "n_events": 0}` — live
  Open-Meteo fetch, real `hermes -z` rewrite, real WhatsApp send to the pilot
  number. Audit `gate.sent` row's `final` text:
  > Сегодня тепло: до 32.7°, без осадков, ветер около 8.1 м/с. Завтра ещё
  > жарче — до 33.9°, тоже сухо; дел на сегодня нет.

  **Known gap (resolved — see "Phase 2b — acceptance" below):** the rewrite
  dropped the raw payload's `question` field ("Какие планы на сегодня?
  Расскажи или надиктуй — запишу.") — the delivered message covers weather
  only. Per the spec amendment making the digest double as the daily-plan
  intake prompt, this defeats that half of its purpose. The gate's
  `GATE_STYLE_INSTRUCTION` (`custom/fam/fam/gate.py`) doesn't currently
  tell the rewrite model to preserve the question — worth a prompt tweak or an
  explicit "always keep the question line" rule in a follow-up task; not fixed
  here per this task's "don't rewrite code without evidence of a send failure"
  scope (the send itself succeeded). Fixed by a prompt tweak the same day; live
  digests since (e.g. 2026-07-12 07:31 Almaty) close with the invitation —
  confirmed in the acceptance section below.
- Second `fam tick digest --json` run (same day): `{"skipped": "already_sent",
  "date_local": "2026-07-11"}` — dup-guard confirmed live, independent of the
  systemd schedule.
- Morning weather cron `8b751dbfd5d6` paused (`$H cron pause 8b751dbfd5d6`,
  confirmed via `$H cron list --all` showing `[paused]`); evening
  `150d115fe905` untouched, still `[active]`.

## Phase 2b — acceptance: reminders, gate, digest, email (2026-07-12)

Accepted (Task 12; `.superpowers/sdd/task-12-report.md`). This phase grew
beyond the original plan — Tasks 13–16 were added mid-phase, incident-driven
(see "Date-anchor incident" below) — and this section documents the state as
it shipped, on top of "Phase 2b — proactive timers" above. Full history:
`.superpowers/sdd/progress.md` (`2b-T8`..`2b-T16` entries),
`.superpowers/sdd/task-{13,14,15,16}-report.md`; design spec
`docs/superpowers/specs/2026-07-10-amina-assistant-design.md` §10; plan
`docs/superpowers/plans/2026-07-11-amina-phase2b-reminders.md`.

### What an operator needs to know

**The two timers** (detail above, under "Phase 2b — proactive timers"):
`fam-reminders.timer` fires every 5 min (`OnCalendar=*:0/5`), `fam-digest.timer`
fires once a day at `02:30:00 UTC` = `07:30` Almaty. Both `enabled`+`active` as
of this acceptance (`systemctl --user list-timers --all | grep fam`).

**`fam-config.json` knobs**
(`~/.hermes/private/amina/fam-config.json`, not in git — private data dir):

```json
{
  "target": "whatsapp:+77782110625",
  "quiet_start": "21:30",
  "quiet_end": "07:30",
  "daily_budget": 8,
  "gate_model": "gpt-5.4-mini",
  "gate_provider": "openai-codex",
  "max_len_reminder": 300,
  "max_len_digest": 900
}
```

- `quiet_start`/`quiet_end` (`21:30`–`07:30` local): no proactive send inside
  this window; the digest at `07:30` lands right at the boundary. Reminder
  chains due during quiet hours are gated (`gate.skip`, `reason: "quiet"`) and
  left pending — a later tick retries once the window ends ("остальное ждёт
  утра", spec §6.4). A reminder repeatedly parked this way for too long
  (`reminder_max_age_min`, default 120 min, or past the event's own start by
  that same margin) is cancelled instead of retried forever
  (`rem.cancel_stale_age`) — that's the "протухшее умирает" half of the rule.
- `daily_budget` (`8`/day): proactive-message ceiling, counted as `gate.sent`
  rows for "today" in Asia/Almaty (`budget_spent_today`, `custom/fam/fam/gate.py`).
  The digest is excluded from the count on both sides — it doesn't consume
  budget and isn't capped by it (`force=True`, per the timer table above).
  Reminder stages are **not** currently exempt from the cap in code (spec §6.4
  describes cap-exempt reminder chains and non-chain overflow "accumulating
  into the digest" as the target design; as implemented in this phase there's
  only the reminders+digest tick pair, and a budget-capped reminder stage is
  simply left pending — `gate.skip`, `reason: "budget"` — for a later
  `fam-reminders` tick to retry, same mechanism as a quiet-hours skip). Worth
  a follow-up task if the budget is ever tuned low enough for this gap to
  matter in practice; at `8`/day it hasn't been observed live.
- `target` is the pilot's WhatsApp number (`whatsapp:+77782110625`), **not
  Amina** — pilot mode is still active as of this acceptance (see "Pilot
  mode" above). The "Revert checklist for go-live" above originally only
  swapped the `.env` allowlist and the two weather-cron `--deliver` targets
  back to Amina's number and missed this file; fixed post-review (phase-2b
  final review) by adding an explicit `fam-config.json` `target` step to that
  checklist, so the go-live revert no longer leaves fam's reminders/digest
  pointed at the pilot number after everything else has switched over.
- `gate_model`/`gate_provider` (`gpt-5.4-mini`/`openai-codex`) is the small,
  cheap model used only for the style-gate rewrite step — separate from
  whatever model drives the main conversational agent.
- `max_len_reminder`/`max_len_digest` (`300`/`900` chars) are the
  deterministic-ceiling values from spec §6.2 step 2 (rewrite too long → one
  "shorter" retry → send as-is + audit flag, never silently truncated).

**`--allow-past` flag** (`fam cal add`/`fam cal update`, Task 13,
`ce75d5271`): both subcommands reject a `--start` more than 10 minutes in the
past (`_PAST_START_GRACE`) with exit 2 and this exact stderr:

```
start is in the past (now: <ISO+05:00>). If the user means a past event,
retry with --allow-past; otherwise re-derive the date (run date).
```

`--allow-past` bypasses the check for genuinely retroactive entries (e.g. "we
went to the doctor yesterday, log it"). Nothing is written to the DB or the
audit log on rejection — the check runs before `cal.add()`/`cal.update()` is
called. `cal update` only runs the check when `--start` is actually passed;
editing an unrelated field on an already-past event needs no flag. `--end` is
never validated against "now" by this guardrail.

**`gateway.message_timestamps` + global timezone** (Task 15): as of this
acceptance, `~/.hermes/config.yaml` has

```yaml
gateway:
  message_timestamps:
    enabled: true
timezone: 'Asia/Almaty'
```

Every inbound user message the gateway replays to the model now carries a
`[Dow YYYY-MM-DD HH:MM:SS +05]`-style prefix (Almaty, not UTC or server-local
— server-local is `Etc/UTC`, confirmed via `timedatectl`; without the
`timezone` key set, the prefix would have rendered 5 hours off). This is a
**global instance setting** — it affects every session's replayed history, not
just Amina's fam usage.

Side effect: `timezone` flipping means every `hermes cron` schedule
(`OnCalendar`/`crontab`-style `schedule` field) that was hand-tuned to hit a
specific Almaty wall-clock time under the old empty-timezone (server-local
UTC) default had to be retuned so the same instant still fires. The evening
weather cron (`150d115fe905`) moved from `0 15 * * *` to `0 20 * * *` — both
mean "15:00 UTC", but the cron field itself is now interpreted Almaty-local,
so the digits changed to keep the same firing instant. Anyone adding a new
`hermes cron` job from here on should write the schedule in Almaty-local
terms directly, not UTC.

**Skill v3 time-source rules**
(`custom/skills/amina-fam/SKILL.md`, Task 14, `087e462f5`): "now" is defined
strictly as (a) the `[Dow YYYY-MM-DD HH:MM:SS TZ]` prefix on the **latest**
user message (see above), converted to Almaty if needed, or (b) if no prefix
is present, exactly one `date` call via the `terminal` tool before any
date/time arithmetic. Deriving "today" from an earlier turn in the
conversation is explicitly forbidden — this is the direct fix for the
2026-07-12 date-anchor incident (below). The skill also gained: a
stop-and-ask rule for destination places ("съездить/поехать в X" ⇒ resolve or
add `X` via `fam places` before recording, don't just leave it as prose in the
title) and a documented reaction to the `--allow-past` guardrail's exit-2 (ask
"is this a past event?" → retry with the flag, or re-derive today's date and
retry without it).

**Gate reminder-semantics instruction**
(`custom/fam/fam/gate.py`, Task 16, `83920cffc`+`87fd399bb`+`673e754c8`): the
style-gate rewrite prompt for `kind="reminder"` now gets an extra instruction
block (`GATE_REMINDER_TIME_SEMANTICS_INSTRUCTION`, mirroring how
`kind="digest"` gets `GATE_DIGEST_NO_QUESTION_INSTRUCTION`) that pins down
three things the small rewrite model kept getting wrong on live traffic:
`start_local` is the event's start time, not the time the reminder's `label`
action should happen (they're anchored differently — see spec §5); the actor
performing the `label` action is determined by the label's own wording, not
defaulted to the chat owner or to a `participants` entry that doesn't belong
there; and no facts/people/actions may be invented beyond what's in the raw
payload. `GATE_STYLE_INSTRUCTION`'s addressing rule also lost its old
copy-pasteable literal example (few-shot bleed risk — the model was echoing
it onto unrelated data) in favor of a grammatical description. This ships
live on the next `fam-reminders` tick with no restart needed (it's a plain
Python prompt-string change picked up by the systemd timer's next
invocation, not part of the gateway process or the skill file).

**`/reset` after a skill change**: any edit to
`custom/skills/amina-fam/SKILL.md` (or its deployed copy at
`~/.hermes/skills/amina-fam/SKILL.md` — both must stay byte-identical, `scp`
one from the other rather than hand-editing both) only takes effect for
**new** hermes sessions. An already-open session keeps serving the system
prompt it was built with, skills baked in at session-creation time — the
in-process skills-prompt cache doesn't self-invalidate on a new `SKILL.md`.
After any skill edit that reaches a live session (pilot WhatsApp, admin
Telegram), send `/reset` (or `/new`) in that chat once the gateway has
picked up the file (a `systemctl --user restart hermes-gateway` is *not*
required for the skill text itself — only `/reset` is — but Task 15 bundled
a restart anyway because it was also flipping `message_timestamps`). No
scripted/CLI path exists for this; it's a live chat command, so it has to be
run by whoever has access to that chat (Denis, for the admin/pilot chats in
this instance).

### Date-anchor incident (2026-07-12) and the fix chain

Root cause: `gateway.message_timestamps` defaulted off + the skill said "take
now from context already available" + a day-old session → the only date
signal available to the model was yesterday's turns. Result: "запиши на
сегодня в час" was logged against yesterday's date (event 10, later corrected
by hand to `2026-07-12T13:00+05:00`). Fix chain, in order: **T13** (`fam`
guardrail rejecting past `--start` unless `--allow-past`, catches this class
of error at write time even if the model still misderives "now") → **T14**
(skill v3, forces the model to always establish "now" from the message
timestamp or a `date` call) → **T15** (turns the timestamp prefix on, sets
`timezone: Asia/Almaty` so the prefix is correct, restarts, live-verified: a
real WhatsApp message with no explicit date landed on the correct day with no
`date` call needed) → **T16** (gate reminder-semantics fix, a related but
independent bug found during T14/T15's live reminder-chain observation: the
style-gate rewrite was misattributing/mistiming the reminder text itself,
not the event date).

### Live verification (2026-07-12)

- **Digest**: `07:31:23` Almaty, single delivery (one `tick.digest` + one
  `gate.sent kind=digest` audit row), min…max weather range, closes with the
  daily-plan question — confirms the Task-8-era "known gap" above is fixed:
  > Сегодня без планов: погода +21…34, без осадков, ветер 9.4 м/с. Завтра тоже
  > без осадков: +22…31, ветер 8.8 м/с.
  >
  > Если появятся планы или изменения — расскажи или надиктуй, я запишу.
- **Full reminder chain, event 10** (Тая-participant event, `start_local
  13:00`): three stages fired and were delivered correctly-addressed —
  `12:01:18` (start-60, generic label, audit id `314`, predates all Task 16
  commits — no `sent_now_local` in the raw payload yet) → *"В 13:00 вам с
  Таей нужно съездить в поселок. Тае пора собираться."*; `12:16:19`
  (dedicated Тая leave_at stage, audit id `318`, also pre-Task-16) →
  *"В 13:00 Тае пора собираться в поселок."*; `13:01:19` (start/leave stage,
  audit id `328`, sent after `83920cffc`+`87fd399bb` had landed — `08:03:56`
  `673e754c8` landed ~2.5 min later, so this reflects the round-1/round-2
  fix, not yet the final three-way attribution rule) →
  *"Пора выходить с Таей в посёлок."* — owner-addressed, Тая correctly kept
  in third person, no action misattributed to the owner. Ids `318`/`328` are
  the same before/after pair `.superpowers/sdd/progress.md`'s `2b-T16` entry
  cites as "production before/after" — note its own caveat still applies:
  `328` validates the round-1 code (`sent_now_local` + time-semantics
  instruction, live and correct here), not the round-2/final wording
  (owner-reassignment ban, three-way attribution rule) — those were validated
  by Task 16's `hermes -z` probe transcripts, not by this one production send.
- **Email + `.ics`**: live send verified twice during Task 10, and again
  during Task 11's E2E chain-generation pass (`email+ics OK`) — Denis
  receives a calendar invite on create/update of any event with him as a
  participant, per spec §7.1.
- **Quiet hours / gate / budget**: unit-covered (part of the 278-test suite)
  and live-confirmed during Task 11 (`gate.skip`, `reason: "quiet"`, on a
  chain stage that fell inside the `21:30`–`07:30` window).
- **Ack quenches chain ("уже едем/выходим/собираемся")**: reminders are
  delivered out-of-band (tick → `hermes send`), so a reaction in the next
  chat turn has no `event_id` in context — `fam rem active --json` (added in
  Task 11's ack-fix) looks up in-progress chains by title/time so the agent
  can resolve which event the reaction refers to without guessing, then
  `fam rem ack EVENT_ID` marks that event's remaining pending stages `acked`
  (`rem.ack_chain`; the separate "не напоминай про это" path uses
  `fam rem cancel` → `cancelled` instead). Live-confirmed end-to-end via `-z`
  smoke test in Task 11 (`.superpowers/sdd/2b-task-11-ackfix-report.md`):
  "уже выходим" → `rem active` → `rem ack` → one-line confirmation, DB
  state verified (`fam rem active --json` → `[]` after).
- **Full test suite**: `278 passed`, zero warnings —
  `PYTHONPATH=custom/fam venv/bin/python -m pytest custom/fam/tests -q`.

### Known gaps carried forward (not blocking this acceptance)

- A residual, rare (`1/9` live probes) name-declension slip in gate-rewritten
  reminder text (`Тане` instead of the correct dative `Тае`) — flagged in
  Task 16's report as small-model stochastic noise, not an
  instruction-following failure; watch the live audit log before deciding
  whether it needs a follow-up task.
- Reminder-chain timing drift (13:00-anchored stage arriving at `13:01:19`,
  not `13:00:00`) is a known, separately-scoped issue — Denis decided to fix
  it in a follow-up mini-phase (2c: finer-grained timer + `AccuracySec=1s` +
  an expanded stage schedule) rather than block this acceptance on it
  (`.superpowers/sdd/progress.md`, "PHASE 2c DECISIONS").
- `people`/`places` alias rename/remove CLI is still missing (carried over
  from Phase 2a's "Known limitations" above, unchanged in 2b).

## Phase 2c — escalation chains (2026-07-12)

Accepted (Task 11; `.superpowers/sdd/task-2c-11-report.md`). Builds on top of
"Phase 2b — acceptance" above; full history: `.superpowers/sdd/progress.md`
(`2c-T1`..`2c-T10` entries + "PHASE 2c DECISIONS"/"PHASE 2c DECISION UPDATE"),
task reports `.superpowers/sdd/task-2c-{1..9}-report.md`; plan
`docs/superpowers/plans/2026-07-12-amina-phase2c-escalation.md`.

### Stage formula and rule precedence

For a reminder with lead `D` minutes to departure `T`, stages are
`{D, D-5, D-15} ∪ ({30, 15} if < D) ∪ {0}`, one countdown value winning on
overlap (no duplicate stage at the same minute-offset). `D=60` (default rule,
Тая not a participant) → `60/55/45/30/15/0`. Тая-participant events get
`D=60` too via a **dedicated `slug:taya` rule**, but the precedence rule is:
if any slug-scoped rule (`slug:taya`, `slug:amina`, …) applies to an event
and its stages list is non-empty, it **suppresses** the `default` rule for
that event — only the more specific rule fires, not both
(`fam/rem.py::applicable_rules`, `2c-T2`). An empty-stages slug rule does
**not** claim precedence (default still applies alongside it). Non-Тая events
without a matching slug rule fall back to `D=30` on `default`
(`30/25/15/0`).

**Reseeding after manual rule edits**: `rem.py` gained
`migrate_rules_2c(conn, now_utc=None)` — a one-shot, `meta`-guarded
(`meta.rules_version='2c'`) reseed of the `default`/`slug:taya` rule stages
plus a `regenerate()` pass over every active future event. It's called
automatically from `cmd_init` on every `fam init`/CLI bootstrap, so it's a
no-op once the guard key is set. If you hand-edit `fam_rules` stages directly
in the DB (or restore an old `stages` JSON), delete the `meta.rules_version`
key and re-run any `fam` command that reaches `cmd_init` (or call
`migrate_rules_2c(conn)` directly) — it reseeds the built-in rules from code
and regenerates pending instances for every future event. The pre-migration
stages are preserved in the audit log (`rem.migrate_2c`, payload's `old` key)
before being overwritten, so a hand rollback is always recoverable from
audit. Live migration (2c-T3) touched rules `4/6/0` stages with `0` events
regenerated (the one live event at the time was already past the
future-event filter).

### Ack scopes

`fam rem ack EVENT_ID [--scope prepare|all]` (default `all`, byte-compatible
with existing skill/README invocations). `--scope prepare` acks only
`kind='prepare'` stages, leaving `kind='leave'` stages pending; `--scope all`
(default) acks every pending stage regardless of kind
(`fam/rem.py::ack_chain`, `2c-T4`). Maps to the two conversational triggers:
«собираемся» (we're getting ready) → `prepare` only; «выходим/едем» (we're
leaving) → `all`. Live-confirmed 2026-07-12: «начали собираться» at 22:06
Almaty (17:06 UTC) → `rem.ack scope=prepare`, `count=0` (both prepare stages
had already fired — a valid no-op, not an error); «уже выходим» at 22:16
Almaty (17:16 UTC) → `rem.ack scope=all`, `count=1`.

### Chain budget semantics

A reminder chain spends **one** `daily_budget` unit total, not one per
stage: the first stage of a chain to actually send debits the budget; every
follow-up stage for the same `event_id` is free (dedup keyed off
`event_id`, so an event with `event_id IS NULL` never dedups against
anything — verified with no ripple to existing tests) (`fam/gate.py`/`tick`
path, `2c-T5`).

### Night-fire semantics

Reminder chains **override quiet hours** — Denis's decision
(`PHASE 2c DECISION UPDATE`, progress.md) supersedes the quiet-hours-aware
front-load originally planned for Phase 2b's final review: night plans must
fire on schedule and are never silenced. `tick.reminders` skips the quiet
check entirely when `kind='reminder'`; quiet hours remain in force for any
future non-reminder proactive kind. Live-confirmed 2026-07-12: `gate.sent`
at `22:00:19` and `22:05:21` Almaty (`17:00:19`/`17:05:21` UTC) — both
inside the `21:30`–`07:30` quiet window — for event 15's "Лемана ПРО" chain.
The staleness guard (`rem.cancel_stale_age`, still `quiet`/`budget`-skip
driven) now only matters for genuinely missed ticks, not a quiet-hours
morning backlog.

### Minutely timer

`fam-reminders.timer` moved from `OnCalendar=*:0/5` (every 5 min) to
`OnCalendar=*:0/1` (every 1 min) with `AccuracySec=1s`, fixing the ~1 min
delivery drift observed in Phase 2b acceptance (e.g. a 13:00 reminder
arriving at 13:01:19). `systemctl --user list-timers | grep fam` shows the
minutely cadence live. Live-confirmed exact on-the-minute firing: a
`tick.reminders` audit row at `13:12:00.000` UTC. Rollback: restore
`OnCalendar=*:0/5` in the unit file and `daemon-reload`.

### Text variation (`prior_texts`)

Each `gate.sent` payload for a reminder chain carries `prior_texts`: the
list of already-sent stage texts for that `event_id`'s chain so far. The
style-gate rewrite prompt uses it to avoid repeating the same wording stage
to stage (labels carry intent; no verbatim repetition across a chain)
(`2c-T7`). Live-confirmed 2026-07-12: the `17:05:21` UTC stage's raw payload
carries `prior_texts: ["Пора собираться в Лемана ПРО."]`; the `17:15:23`
stage carries both prior texts, and each rewritten `final` text differs from
the ones before it.

### Departure-vs-start conversion

A live Phase-2c E2E run (2c-T10, evening 12.07) surfaced a bug: when the
user answers "во сколько выезжать?" with a departure time, that time is the
**leave/departure** instant, not the event's `start` — passing it straight
through as `--start` shifted the event's actual start earlier than intended.
Live example: event 15 ("Поездка в Лемана ПРО"), user said «выезжаем в
22:30», travel time 40 min → correct `start_local` is `23:10` (22:30 +
40 min travel), recorded that way with the departure intent preserved in the
event notes (`"Выезд в 22:30"`). Skill v4.1 documents this
departure-vs-start conversion explicitly so the agent computes `start` from
a stated departure time + travel, rather than passing the departure time
straight through as `--start`.

### Decisions checklist — evidence

| Decision | Evidence | Verdict |
|---|---|---|
| Stage formula `D`/`D-5`/`D-15`/`{30,15}`/`0`, countdown wins on overlap | `2c-T1` report + `test_rem.py` `build_stages` tests (byte-match to plan, incl. `lead=45` hand-trace) | PASS |
| Тая ⇒ `D=60` else `D=30`, rule precedence (slug suppresses default; empty-stages slug does not) | `2c-T2` report, `test_applicable_rules_slug_rule_suppresses_default` etc.; live migration reseeded rules `4/6/0` stages (`2c-T3`) | PASS |
| Chain = 1 budget unit | `2c-T5` report, dedup-by-`event_id` tests, `None`-eid no-dedup case | PASS |
| Scoped ack («собираемся»→prepare, «выходим»→all) | `2c-T4` tests + live: `rem.ack scope=prepare` 22:06 Almaty count=0 (valid no-op), `scope=all` 22:16 Almaty count=1 | PASS |
| Night-fire (reminders override quiet hours) | `2c-T6` report + live `gate.sent` 22:00:19 and 22:05:21 Almaty inside quiet window | PASS |
| Minutely timer + `AccuracySec=1s` | `2c-T8`, `systemctl --user list-timers` minutely cadence, live tick `13:12:00.000` UTC exact | PASS |
| Text variation via `prior_texts` | `2c-T7` report + live raw payload at 17:05/17:15 UTC carrying `prior_texts` | PASS |
| Departure-vs-start conversion | Live 2c-T10 bug + fix: event 15 `start` 23:10 from «выезжаем в 22:30» + travel 40; notes `"Выезд в 22:30"`; skill v4.1 | PASS |
| Destination clarification (resolve → ask → `places add`) | Live 2c-T10 evening run, 13:27 UTC, "Лемана ПРО" flow | PASS |
| 22:30 silence (last chain stage sent, chain then goes quiet — no further stage past the final one) | Newest `gate.sent` as of this acceptance is `17:15:23` UTC (22:15 Almaty, "выходить через 15 минут" stage); check was performed before the 22:30 stage's own due time elapsed enough margin to call it — **see note below** | SEE NOTE |

Note on the last row: at the time this acceptance was written (17:25–17:26
UTC / 22:25–22:26 Almaty), the newest `gate.sent` row was still the
`17:15:23` UTC one and the brief's own confirmation threshold (past `17:31`
UTC with no newer `gate.sent`) had not yet been reached — the check is
honestly **inconclusive at write time**, not confirmed. Re-run
`fam log --last-hours 1 --kind gate.sent` after `17:31` UTC on 2026-07-12 to
settle it; if `17:15:23` is still the newest row, silence is confirmed.

### Full suite

`308 passed`, zero warnings —
`PYTHONPATH=custom/fam venv/bin/python -m pytest custom/fam/tests -q`.

## Phase 3a — real road (2026-07-12/13)

Accepted (Task 8; `.superpowers/sdd/task-3a-8-report.md`). Builds on top of
"Phase 2c — escalation chains" above; full history: `.superpowers/sdd/progress.md`
(`PHASE 3a` entries, `3a-T1`..`3a-T9`), plan
`docs/superpowers/plans/2026-07-12-amina-phase3a-real-road.md`. Spec update:
`docs/superpowers/specs/2026-07-10-amina-assistant-design.md` §3/§5/§9
(traffic-aware road replaces "no traffic needed" 2GIS assumption; provider is
TomTom, not 2GIS — the 2GIS API key is opaque and unusable for this).

### Road ladder and sources

`travel_min_road` is computed by `fam/road.py::compute_travel_min` with a
fallback ladder, each rung auditing its own `road.*` kind and never raising:
TomTom Routing API (`traffic=true`, real departure time) → straight-line
distance × `road_coef` (default 1.4) at `road_speed_kmh` (default 30) →
manual `travel_min` on the event → the place's own `travel_min` → `0`.
`leave_at` priority (since 3a-T2) is road-first: a non-NULL
`travel_min_road` beats a manually-said `travel_min`, which beats the
place default. Every rung failure is `road.error`; a successful compute is
`road.computed {minutes, source}`; the TomTom key is read only from env,
never logged or put in audit/exception text (`fam/road.py` docstring).
`fam/cal.py::recompute_road` is the single call site cal add/update's hook
and the tick's threshold recompute and `fam road <id>` all share.

### TOMTOM_API_KEY and env sourcing

The key lives in `~/.hermes/.env` (`TOMTOM_API_KEY=...`), never in the repo.
Both fam systemd user units (`fam-tick.service`/`fam-tick.timer` and any
other fam unit that shells out to `fam`) got `EnvironmentFile=-%h/.hermes/.env`
(commit `5a632179e`) so ticks pick up the key automatically; the leading `-`
makes a missing file non-fatal (falls back to no-key/straight-line). Manual
CLI invocations (`fam road <id>`, ad-hoc `fam` commands run interactively)
do **not** inherit systemd's `EnvironmentFile` and must source the file by
hand first:

```sh
set -a; . ~/.hermes/.env; set +a
```

### Config keys

`fam/road.py::CONFIG_DEFAULTS`: `road_provider` (`"tomtom"`),
`road_home_lat` / `road_home_lon` (home coordinates the road ladder departs
from — Denis's live values: `43.197391` / `76.872737`, parked in
`fam-config.json`), `road_coef` (straight-line fallback multiplier, default
`1.4`), `road_speed_kmh` (fallback speed, default `30`), `road_daily_cap`
(TomTom call budget per day, default `100`, guarded independently of
`cal.py` so every call site is covered), `road_timeout_sec` (default `10`),
`road_recompute_min` (threshold minutes before departure that trigger a
recompute, default `[120, 60]`).

### Thresholds T−120/T−60 and anchor re-check

The tick recomputes `travel_min_road` for events crossing `T−120` or `T−60`
minutes before their (road-adjusted) leave time. A recompute that changes
the minutes value audits `road.recompute {old, new, source}` and
regenerates the reminder chain so it reflects the corrected `leave_at`; an
unchanged value just bumps `road_checked_at` with no separate audit row (the
`road.error`/`road.computed` row from the underlying compute call is the
evidence trail). **Anchor re-check**: when a recompute shifts the departure
anchor by more than 10 minutes (`|Δ|>10`), `road_checked_at` is set back to
`NULL` — this forces exactly one bounded follow-up recompute on the next
tick using the corrected anchor, rather than leaving a stale estimate live
until the next scheduled threshold. This is a self-healing freshness
invariant, not a missed-window bug (adjudicated in 3a-T4 review).

### `fam road <id>`

`fam road EVENT_ID [--json]` runs the same `cal.recompute_road` path as the
`cal add`/`cal update` hook (identical audit kinds), then regenerates that
event's reminder chain — useful for debugging or forcing a recompute outside
the tick's threshold windows. A recompute that can't produce minutes is
informational, not an error (exit 0, `source: "none"`), with one of four
distinguishable reasons: `no_place_coords` (event's place has no lat/lon),
`no_home_config` (`road_home_lat`/`road_home_lon` not set), a
`fallback_source:<src>` reason (recompute didn't run but `leave_at` still
falls back to a non-road source), or a bare `error` (a real failure — see
`fam log --kind road`).

### `places update` ripple

`fam places update REF --lat ... --lon ... --travel-min ...` changing
`lat`/`lon`/`travel_min` on a place ripples to every **future active** event
held at that place: their reminder chains are regenerated (the place-travel
rung of `leave_at` may shift) and their `road_checked_at` is NULLed so the
next tick recomputes with fresh coordinates. Past and non-active (cancelled)
events are untouched. The audit payload carries `events_touched` with the
ripple count; everything runs inside the caller's single transaction
(`fam/places.py::update` docstring, 3a-T5). Live-verified 2026-07-12: Лемана
coordinates backfilled via `places update`, alias resolve confirmed, ripple
regenerated the event's chain.

### Skill-sync curator drift auto-commit (3a-T9)

Hermes's upstream `skill_manage` background curator can patch *deployed*
skill files (`~/.hermes/skills/<name>/SKILL.md`) directly, bypassing git —
first observed live 2026-07-12 ~17:16 UTC. Denis's decision: keep the
curator on, close the drift automatically instead. `custom/fam/bin/skill-sync`
compares each deployed `SKILL.md` against its git counterpart
(`custom/skills/<name>/SKILL.md`); when they differ and the deployed copy is
newer, it copies deployed→git and commits
(`chore(amina): curator patch auto-commit (<name>)`) + pushes (a push
failure is non-fatal — the commit stays local). When git is newer or equal,
it does nothing (our own edits sync forward normally). Installed as a
systemd user timer `skill-sync.timer`, `custom/fam/systemd/`, symlink
pattern, cadence every 30 minutes. Live-verified: a deploy-side patch was
folded into git and then reverted in a full cycle, final sha256 matched.

### Open item

TomTom returns `401` on the live key currently in `~/.hermes/.env` —
activation/dashboard issue on Denis's TomTom account, not a code bug. Until
resolved, the road ladder correctly and silently falls through to the
straight-line fallback (`source: "straight"`) on every compute; nothing is
broken, estimates are just coarser than with live traffic. Re-verify with
`fam road <id> --json` once the key is confirmed active.

### Full suite

`356 passed`, zero warnings —
`PYTHONPATH=custom/fam venv/bin/python -m pytest custom/fam/tests -q`.
