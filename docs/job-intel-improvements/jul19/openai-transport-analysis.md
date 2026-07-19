# OpenAI Transport Analysis — Subscription vs API для Semantic Provider

**Дата:** 2026-07-19 · Read-only · Все утверждения с file:line-доказательствами.

## 1. Существует ли subscription-based OpenAI transport? — ДА

Провайдер `openai-codex`: OAuth-токены ChatGPT-подписки из пула `~/.hermes/auth.json` (ключи `providers`/`credential_pool`; account claims `https://api.openai.com/auth → chatgpt_account_id`, auxiliary_client.py:762). Base URL `https://chatgpt.com/backend-api/codex` (config.yaml:67 и далее). Это НЕ API billing: расход идёт из подписочных лимитов («reached your session usage limit», «weekly usage limit» — распознаются как credit-исчерпание, auxiliary_client.py:2962–2969).

Транспорта «ChatGPT Plus/Pro web» помимо codex-бэкенда в кодовой базе нет (единственные chatgpt.com-пути — codex; доказательство: `grep -rn "chatgpt.com" agent/ hermes_cli/` даёт только backend-api/codex ссылки).

Native OpenAI **API** transport (api.openai.com, API-key billing) существует как generic-ветка (`OPENAI_BASE_URL`/`OPENAI_API_KEY`, auxiliary_client.py:2374–2387), но ключ на сервере не сконфигурирован — использовать = завести новый платёжный контур.

## 2. Codex-интеграция engineering pipeline

- Main-agent transport: `agent/transports/codex.py` — raw Responses streaming, prompt_cache_key-роутинг; auxiliary-путь: `_CodexCompletionsAdapter` (auxiliary_client.py:905) конвертирует chat-messages → Responses input.
- Модели: gpt-5.6-lineup (luna/terra/sol) поверх codex-бэкенда; fallback-модель в config.yaml.
- Auth: внешне обновляемый OAuth-пул; известная нестабильность — server-side revocation 401 `token_invalidated` (гео-мисматч KZ-login vs DE-server; memory `codex-auth-geo-invalidation`, инцидент 2026-07-12/13 c HermesCodexAuthDead alert).

**Может ли codex-транспорт обслуживать Semantic Provider?** Технически вызвать можно (это OpenAI-совместимый клиент), но он нарушает жёсткие требования Provider Contract / Step 5A-4a — см. ниже.

## 3. Дисквалифицирующие факты codex-транспорта для Step 5B

1. **Нет structured output.** Ни `agent/transports/codex.py`, ни адаптер не транслируют `response_format`/`json_schema` (grep по обоим файлам — 0 совпадений). Кастомный chatgpt-бэкенд поле не принимает. Parser Step 5A требует строгий JSON Schema; вытаскивать JSON из свободного текста запрещено контрактом (§8.3 задания 5A).
2. **Нет детерминированного декодирования.** Прямой комментарий в адаптере: «the Codex endpoint … does NOT support max_output_tokens or **temperature** — omit to avoid 400 errors» (auxiliary_client.py:971–972).
3. **Model identity неверифицируема.** Адаптер собирает ответ как `SimpleNamespace(choices=…, model=model, usage=…)`, где `model` — **эхо запрошенного** (auxiliary_client.py:1227–1231), а не фактически обслуживший. Проверка 5A-4a (`allowed_response_model`) на этом транспорте проходит тривиально и потому бессмысленна; server-side подмену модели не поймать.
4. **Cost accounting невозможен.** Usage-токены есть (input/output_tokens, :1195–1204), но цена per-token отсутствует — биллинг подписочный, capped. Оси benchmark «cost» нечем заполнить, кроме «доля сожжённого недельного лимита».
5. **Конкуренция с production.** Пул auth.json — это рабочие креды основного агента. 3 626 replay-вызовов будут жечь session/weekly limits того же пула, что уже приводило к инцидентам при куда меньших нагрузках (weather-cron RCA 2026-07-12). Benchmark, способный положить production-гейтвей, неприемлем.
6. **Модельный ряд другой.** Codex-бэкенд отдаёт gpt-5.6-lineup; бенчмарк-кандидаты класса gpt-5-mini там не выбираются свободно.
7. **Стабильность.** История 401 geo-invalidation и pool-reset'ов = риск невоспроизводимого прерывания длинного replay.

## 4. Native OpenAI API (api.openai.com)

Функционально эквивалентен OpenRouter по всем осям Step 5B (structured output, temperature 0, точный `response.model`, usage, per-token cost), но:

- требует нового API-ключа и платёжного аккаунта (сейчас не существует — доказательство: в `~/.hermes/.env` есть только `OPENROUTER_API_KEY`, `grep -c OPENROUTER` = 2, `OPENAI_API_KEY` отсутствует);
- цена на openai/gpt-5-mini через OpenRouter идентична прямой (OpenRouter даёт passthrough-прайс $0.25/M / $2/M — подтверждено live 2026-07-19);
- преимущество отсутствует; недостаток — второй биллинг-контур и отказ от уже проверенного в smoke пути.

Единственный сценарий, где прямой OpenAI API станет нужен: если benchmark потребует фичу, которую OpenRouter не проксирует (например, канонический snapshot-пиннинг вида `gpt-5-mini-YYYY-MM-DD` в запросе). Политика identity 5A-4a это уже учитывает (снапшоты принимаются), пока необходимость не материализовалась.
