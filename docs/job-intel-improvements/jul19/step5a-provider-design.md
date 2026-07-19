# Step 5A — Provider Design

**Модуль:** `job_intel/vacancy_understanding/semantic/runtime/llm_provider.py` (единственный новый runtime-файл)
**Provider Contract:** 1.0.0 · **Semantic Contract:** 1.0.0

## Архитектура

```text
extract_semantic_observations(title, text, structured)
    │  input_hash = sha256(provider_id + prompt_version + model_id + decoding + input)
    ├─ mode="replay":  RecordingStore.load(hash) ─ verify format/response_hash/model/prompt ─ parse
    └─ mode="record":  ONE chat.completions.create(temperature=0, json_schema strict)
                        ─ RecordingStore.save(full record) ─ parse
                                 ↓
                        parse_llm_response() → list[Observation]
                                 ↓
                   existing runtime Stage 3+ (unchanged)
```

## Компоненты

| Компонент | Решение |
|---|---|
| Adapter | `LLMObservationProvider` — реализует `SemanticProvider` Protocol; никакие другие модули не изменены |
| Model config | `model_id` пиннится конструктором; `DEFAULT_MODEL_ID = "openai/gpt-5-mini"` (предложение spend gate); `DECODING_PARAMETERS = {"temperature": 0}` |
| Prompt | `build_prompt(contract)` — версия `llm-obs-1.0.0`; словарь сигналов **генерируется** из `contract.facts` (semantic_only+hybrid) + `pipeline._values_for()` → промпт не может разойтись со Stage 3; содержит basis-определения (класс evidence, «NOT your own confidence»), verbatim-требование, разрешение пустого списка, запрет unknown/рекомендаций/world knowledge/CoT |
| Response schema | `response_schema()` — производная от `Observation.model_json_schema()` (одна копия истины), `strict: true`, `additionalProperties: false` |
| Parser | `parse_llm_response()` — только json.loads + `Observation.model_validate`; любой невалидный элемент (включая `*=unknown`) = `LLMProviderError`, весь вызов fail. Никакого repair/fuzzy/извлечения JSON регэкспом |
| Record/replay | `RecordingStore` — 1 JSON на вызов, ключ = input_hash; поля §10 задания (raw_response_text, response_hash, usage, latency_ms, retry_count, error, decoding, request_ts); загрузка проверяет format version + response hash; секреты не пишутся |
| Live gate | `build_live_llm_provider()` — требует env `JOB_INTEL_LLM_LIVE_APPROVED=1`, иначе `live_calls_not_approved`; replay-режим фабрику не использует и transport держать не может (`replay_must_be_offline`) |
| Retry/fallback | Никаких fallback (model/provider/phrase/ensemble/self-consistency). SDK-ретраи капированы `with_options(max_retries=1)` (существующая стандартная политика транспорта, transient-only) |
| Metadata | `last_call_metadata` (mode, usage, latency, retry_count); точный model из ответа пишется в recording (`response_model`) |

## Ключевые trade-offs

1. **Ошибочный вызов тоже записывается** (recording с `error`) — replay честно воспроизводит failure (`recorded_call_failed`), а не прячет его.
2. **Excerpt >400 падает на парсинге** (модель Observation сама ограничивает) — Stage 3 `excerpt_too_long` остаётся defense-in-depth.
3. **Смена model_id/prompt_version меняет input_hash** — replay чужих записей физически невозможен (`recording_missing`), плюс явные проверки mismatch внутри записи.
4. Gated-заглушка `LLMProvider` из Step 4B не тронута — её semantics («без approve — fail loudly») сохраняет существующий тест; live-гейт нового провайдера реализует то же требование через spend-gate env flag.
