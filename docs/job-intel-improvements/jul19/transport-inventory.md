# Transport Inventory — Hermes LLM Provider Infrastructure

**Дата:** 2026-07-19 · Read-only research (Step 5B transport evaluation) · HEAD `a21c94402a`

## Архитектурная схема

```text
                        ┌──────────────────────────────────────────────┐
                        │ agent/auxiliary_client.py                    │
                        │ resolve_provider_client(provider, model)     │  ← central router
                        │  - alias normalization (_PROVIDER_ALIASES,   │
                        │    строки 268–319)                           │
                        │  - auth resolution (env / auth.json pool /   │
                        │    config.yaml)                              │
                        │  - returns OpenAI-compatible client          │
                        └───────┬──────────────────────┬───────────────┘
              chat_completions  │                      │ codex_responses
                                ▼                      ▼
            ┌──────────────────────────┐   ┌─────────────────────────────────┐
            │ Plain OpenAI SDK client  │   │ CodexAuxiliaryClient (line 1241)│
            │ base_url per provider:   │   │  └ _CodexCompletionsAdapter     │
            │  openrouter.ai / nous /  │   │    (line 905): chat kwargs →    │
            │  zai / kimi / minimax /  │   │    Responses streaming API      │
            │  custom OPENAI_BASE_URL  │   │  base_url:                      │
            └──────────────────────────┘   │  chatgpt.com/backend-api/codex  │
                                           └─────────────────────────────────┘
   Callers:
   - main agent loop ──────────────► agent/transports/codex.py (raw Responses stream)
   - auxiliary tasks (compression, ► call_llm() (auxiliary_client) — has AUTO
     titles, web_extract, review)    PROVIDER FALLBACK on 402/credit errors
   - legal_review_gate, recruiter ─► resolve_provider_client(...) direct,
     executors, Step 5A provider     client.chat.completions.create() — NO fallback
```

## Найденные транспорты

| # | Transport | Код | Auth | Billing |
|---|---|---|---|---|
| 1 | **OpenRouter** | `resolve_provider_client("openrouter")`, plain SDK client, base openrouter.ai | `OPENROUTER_API_KEY` (`~/.hermes/.env`; pool: `_select_pool_entry("openrouter")`, auxiliary_client.py:2039) | API, per-token |
| 2 | **openai-codex** (ChatGPT subscription) | `CodexAuxiliaryClient` (auxiliary_client.py:1241) + `_CodexCompletionsAdapter` (:905); main-agent path `agent/transports/codex.py` | OAuth token pool `~/.hermes/auth.json` (`credential_pool`), chatgpt_account_id claims (auxiliary_client.py:762) | **ChatGPT subscription** (session/weekly caps, не per-token) |
| 3 | **custom / native OpenAI API** | `OPENAI_BASE_URL` + `OPENAI_API_KEY` env path (auxiliary_client.py:2374–2387; hermes_cli/auth.py:1740,1838) | API key | API, per-token; **ключ на сервере не сконфигурирован** (в .env только OPENROUTER_API_KEY) |
| 4 | Прочие (nous, zai, kimi-coding, minimax, gemini, xai, copilot, anthropic…) | `_PROVIDER_ALIASES` (auxiliary_client.py:268) + ветки resolve_provider_client | смешанные | вне scope Step 5B (не OpenAI-модели) |

## Provider selection / retry / fallback

- **Selection:** `resolve_provider_client()` (auxiliary_client.py:4584) — explicit provider обходит auto-chain; `"auto"` включает цепочку автодетекта.
- **Retry:** OpenAI SDK per-client (`max_retries`, у Step 5A обнулён `with_options(max_retries=0)`); codex-адаптер — свой timeout-watchdog (threading.Timer, :1080+).
- **Fallback:** существует ТОЛЬКО в `call_llm()` — авто-переключение провайдера на 402/credit-маркеры (docstring «Payment / credit exhaustion fallback»; маркеры включая "weekly usage limit" — auxiliary_client.py:2955–2970). Прямые вызовы `client.chat.completions.create()` (путь Step 5A) fallback НЕ имеют.

## Execution paths по провайдерам

- **OpenAI API:** нет активных путей (ключа нет); generic custom-ветка существует.
- **Codex/ChatGPT:** основной agent loop (все роли, gpt-5.6-lineup), auxiliary compression/review через CodexAuxiliaryClient; engineering pipeline — тот же codex transport.
- **OpenRouter:** nightly free-model refresh, auxiliary fallback-цепочки, recruiter/positioning executors (через resolve_provider_client), Step 5A `llm-observation`.
