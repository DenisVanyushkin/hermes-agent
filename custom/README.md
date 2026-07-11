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

| Job ID | Name | Schedule (UTC) | Local (Almaty) | Status |
|---|---|---|---|---|
| `8b751dbfd5d6` | Утренний короткий прогноз погоды Алматы — Амина | `0 2 * * *` | 07:00 | active |
| `150d115fe905` | Вечерний короткий прогноз погоды Алматы — Амина | `0 15 * * *` | 20:00 | active |

Inspect with `$H cron list` where
`H="/home/denis/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main"`.

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
3. Restart the gateway: `$H gateway restart`.
4. Verify: `grep WHATSAPP_ALLOWED_USERS ~/.hermes/.env` and
   `grep '"deliver"' ~/.hermes/cron/jobs.json` both show `+77011102626`, and
   the pilot number (`+77782110625`) no longer appears in either.

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
  (`progress.md` backlog note under `2a-T9`).
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
