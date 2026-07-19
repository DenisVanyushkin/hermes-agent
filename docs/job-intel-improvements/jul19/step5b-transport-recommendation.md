# Step 5B Transport Recommendation

**Дата:** 2026-07-19 · **Verdict: `KEEP_OPENROUTER_AS_CANONICAL`**

## Capabilities matrix

| Ось | OpenRouter (Step 5A as-is) | openai-codex (ChatGPT subscription) | Native OpenAI API |
|---|---|---|---|
| Authentication | OPENROUTER_API_KEY (есть) | OAuth pool auth.json (есть, production) | OPENAI_API_KEY (**нет**) |
| Billing source | API per-token | подписка, session/weekly caps | API per-token (новый контур) |
| Model pinning | ✅ explicit slug + resolved_model check | ⚠️ запрошенный slug отправляется, но… | ✅ |
| Exact model identity | ✅ `response.model` фактический (проверено smoke 15/15) | ❌ адаптер эхо-ит запрошенную модель (aux_client.py:1227–1231) | ✅ |
| Structured output / JSON schema | ✅ `response_format json_schema strict` (проверено live) | ❌ не транслируется, бэкенд не поддерживает | ✅ |
| Usage metadata | ✅ prompt/completion tokens | ✅ input/output tokens (:1195) | ✅ |
| Latency metadata | ✅ (замер harness) | ✅ (замер harness) | ✅ |
| Raw response | ✅ | ⚠️ синтетический SimpleNamespace из stream-событий | ✅ |
| Deterministic settings | ✅ temperature=0 | ❌ temperature отвергается бэкендом (:971) | ✅ |
| Retry control | ✅ max_retries=0 | ⚠️ свой watchdog; SDK-ретраи в Responses-стриме | ✅ |
| Fallback control | ✅ allow_fallbacks=false (проверено) | ⚠️ вне call_llm() fallback нет, но пул ротируется | ✅ |
| Replay compatibility | ✅ (15/15 smoke) | ⚠️ технически да (raw text записываем сами) | ✅ |
| Offline reproducibility | ✅ | ⚠️ да для записей, но live-ре-ран нестабилен (401-инциденты) | ✅ |
| Throughput / limits | платный, без недельных капов | session/weekly caps, деля лимит с production | rate limits нового аккаунта |
| Cost accounting | ✅ точный ($0.0911 за smoke) | ❌ нет per-token цены | ✅ |
| **Годен для Step 5B** | **Полностью** | **Не поддерживает** (identity, schema, determinism, cost) | Полностью, но без преимуществ |

## Step 5B requirements: поддержка

| Требование | OpenRouter | codex | OpenAI API |
|---|---|---|---|
| Replay (record/replay, hash-стабильность) | полностью (доказано smoke) | частично (запись возможна, но identity в записи недостоверна) | полностью |
| Calibration | полностью | частично (без temperature 0 сравнение зашумлено) | полностью |
| Полный replay 3 626 | полностью (~$5.5, без капов) | не поддерживает (сожжёт weekly-лимит production-пула; риск остановки на середине) | полностью |
| Cost accounting | полностью | не поддерживает | полностью |
| Reproducibility | полностью | частично | полностью |

## Cost comparison

- **OpenRouter:** passthrough-прайс OpenAI-моделей ($0.25/M in / $2/M out для gpt-5-mini, live-подтверждено); smoke стоил $0.0911; полный replay ≈ $5.5. Комиссия OpenRouter при пополнении ≈5% — на этих объёмах копейки.
- **Codex:** «бесплатно» деньгами, но платится недельным лимитом production-агента — это самый дорогой ресурс в системе (инциденты 07-12/07-16 показали, чем кончается конкуренция за квоту).
- **OpenAI API:** та же цена, что через OpenRouter, плюс операционная стоимость нового биллинг-аккаунта и ключа.

## Риски выбранного пути (OpenRouter)

1. Зависимость от прокси-провайдера (доступность, изменение passthrough-политики) — митигируется record/replay: benchmark перевоспроизводим из записей.
2. Идентичность модели опирается на честность `response.model` OpenRouter — митигируется политикой 5A-4a (snapshot-паттерн) и, при желании, sanity-сверкой с прямым OpenAI API на выборке (будущий опциональный шаг).
3. `allow_fallbacks:false` — контракт OpenRouter; тест в CI это проверяет только на нашей стороне запроса.

## Engineering impact (при гипотетической смене транспорта)

Благодаря provider abstraction смена транспорта = только `build_live_llm_provider()` в `llm_provider.py` (1 функция, 1 файл) + spend-gate документы. Provider Contract, Runtime, replay, benchmark-методика, Step 5A tests — без изменений (transport инжектится). То есть решение обратимо и НЕ является архитектурным lock-in: если позже появится прямой OpenAI-ключ, миграция — один слайс.

## Recommendation

**`KEEP_OPENROUTER_AS_CANONICAL`** для Step 5B. Codex-транспорт дисквалифицирован по четырём жёстким осям (identity, structured output, determinism, cost accounting) плюс риск production-квоты; прямой OpenAI API не даёт преимуществ и требует нового биллинга. Follow-up implementation plan не требуется.
