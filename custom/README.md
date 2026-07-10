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
- `WHATSAPP_HOME_CHANNEL=244882006364348@lid` (Amina's WhatsApp LID)

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

Confirmed on the live path in `~/.hermes/logs/agent.log` (WhatsApp PTT → text,
2026-07-10):

```
2026-07-10 20:22:23,035 INFO tools.transcription_tools: Transcribing aud_8ab4d1acc5e2.ogg via command STT provider 'transcriber'...
2026-07-10 20:22:31,585 INFO tools.transcription_tools: Transcribed aud_8ab4d1acc5e2.ogg via command STT provider 'transcriber' (47 chars)
2026-07-10 20:26:17,932 INFO tools.transcription_tools: Transcribing aud_9a3e13f8fdde.ogg via command STT provider 'transcriber'...
2026-07-10 20:26:25,024 INFO tools.transcription_tools: Transcribed aud_9a3e13f8fdde.ogg via command STT provider 'transcriber' (32 chars)
```

followed by a normal `agent.turn_context` conversation turn and a reply — i.e. voice
in produces a meaningful answer out. Transcript *char counts* are visible in these
log lines (full transcript text in the audit log is Phase 2 scope, not yet wired).

These three logged transcriptions all completed in ~7-9s, consistent with the
remote transcriber succeeding (matches the "STT ~8s" note below) — none of them
exercise the local fallback. The fallback path itself is proven separately by
`custom/stt/test_transcribe_remote.sh`, which runs the wrapper twice: once against
the real transcriber (happy path) and once with `TRANSCRIBER_URL=http://127.0.0.1:9`
(forced-dead endpoint) to force the `faster-whisper` branch. Re-run 2026-07-11,
both legs pass:

```
$ bash custom/stt/test_transcribe_remote.sh
transcriber unavailable, falling back to local faster-whisper
ALL PASS
```

An earlier fallback run through the *live* gateway (dead URL swapped into
`stt.providers.transcriber` temporarily, real WhatsApp voice note, human-confirmed
by Denis) also produced a correct reply, but that run predates this log file and
is not present in current `agent.log` — treat the script above as the reproducible
regression check for the fallback branch going forward.

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
  needed. Exercises the wrapper directly: happy path against the real transcriber,
  then fallback with `TRANSCRIBER_URL=http://127.0.0.1:9` forcing a connection
  failure. Requires `custom/stt/fixture_ru.ogg` (voice fixture containing the word
  "гермес", used as the correctness check instead of a mere non-empty-output check).
- `TRANSCRIBER_URL` env var overrides the remote endpoint for
  `custom/stt/transcribe_remote.sh` directly — useful for pointing at a
  different transcriber or forcing the fallback branch manually.

### Known notes

- The transcriber's `/tts` endpoint is broken upstream (home-pc side) — Phase 1
  only uses `/transcribe`; text-to-speech is out of scope here.
- Remote STT latency is ~8s end-to-end for a short voice note (see log excerpt
  above); local `faster-whisper` fallback is slower (model load + CPU inference)
  but was not the timed path in the reproducible test above.
