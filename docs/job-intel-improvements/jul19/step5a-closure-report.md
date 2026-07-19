# Step 5A — Closure Report

**Дата:** 2026-07-19 · **Verdict:** **STEP_5A_COMPLETE_READY_FOR_CROSS_PROVIDER_BENCHMARK**
**SoT alignment:** SOT_ALIGNMENT_CONFIRMED (Roadmap SoT §9.2–9.6, Provider Contract 1.0.0, Checklist 1.0.0, Semantic Contract 1.0.0, Step 4B closure, все Step 5A отчёты — конфликтов нет; порядок дальнейших шагов определяется §9).

## Normative interpretation (принята)

Step 5A = реализация + доказательство жизнеспособности LLM-провайдера. Calibration, bounded/full replay — **вход Step 5B**, а не незакрытые слайсы 5A. Transport research — не стадия Roadmap; OpenRouter — implementation detail принятой реализации. Prompt `llm-obs-1.0.0` **заморожен как benchmark baseline**; любой tuning до baseline-бенчмарка запрещён.

## Identity

| Поле | Значение |
|---|---|
| provider_id | `llm-observation` |
| prompt_version | `llm-obs-1.0.0` (frozen baseline) |
| model_id | `openai/gpt-5-mini` (actual == requested во всех 15 smoke-вызовах) |
| transport | OpenRouter через существующий `resolve_provider_client`; max_retries=0; allow_fallbacks=false |
| implementation commits | `a29a5f8749` (offline impl) → `8c981ea40a` (transport integrity) → `a21c94402a` ($defs fix + smoke); research: `e492b0c05b`, `9b52bd659b` |
| Semantic Contract | 1.0.0 (не изменён) |
| Provider Contract | 1.0.0 (не изменён) |

## Evidence completed

| Ось | Evidence |
|---|---|
| Protocol conformance | структурная конформность `SemanticProvider`; только `Observation[]`; extra-поля запрещены схемой (тесты, 52 passed) |
| Parser/schema validation | strict json_schema из `Observation.model_json_schema()` + no-dangling-refs регресс; invalid output = явные reason-коды, без repair/fallback |
| Evidence/basis controls | 8 Stage 3 rejection-кодов покрыты тестами; basis-матрица (числа/logprobs не проходят); live: **0 verbatim-нарушений на 96 observations** |
| Model identity | `allowed_response_model` (exact slug / dated snapshot, 11-кейсовая матрица); live 15/15 identity ok; mismatch записывается и не парсится |
| No fallback | поведенческие тесты + live: allow_fallbacks=false в каждом запросе, retry_count=0 во всех записях |
| Record/replay | RecordingStore c response-hash верификацией; ошибки тоже записываются и реплеятся как failure |
| Offline tests | 52 provider-тестов; полный suite 202 passed; 0 изменённых существующих файлов в 5A-реализации |
| Live smoke | 15/15 ok, $0.0911/$1 cap, empty-control честно пуст; contrast pair различена |
| Cost/latency | 20 958 in / 42 914 out токенов; p50 20.3s / p95 80.9s / max 176.1s |
| Live↔replay stability | 15/15 match (Observation[] + semantic_dump + hash) при физически отключённом socket |
| Transport research | KEEP_OPENROUTER_AS_CANONICAL, codex-дисквалификация подтверждена эмпирически (probe) |

## Known measured limitations (зафиксированы, НЕ исправляются до baseline-бенчмарка)

1. **Signal-prefix confusion** — модель иногда пишет путь в `signal_type` (`mandate.X=...`, `company.` там, где его нет/не должно быть).
2. **11/96 rejected observations**, единственный код `unknown_fact_reference` (следствие п.1).
3. **Концентрация 10/11 rejects на Airwallex GPNI** (4/14 принято на кейсе).
4. **2 натянутые interpretation/evidence cases** (boilerplate-контекст как мандат роли; requirements-строка как evidence мандата).
5. **Latency tail** — p95 80.9s, max 176.1s (длинные тексты).
6. **Нет benchmark-level precision/recall выводов** — smoke это contract conformance, не сравнительное измерение; никаких утверждений «LLM лучше baseline» не сделано (предмет Step 5B).

## Verdict rationale

Все 30 пунктов DoD Step 5A выполнены в согласованном scope (calibration/replay нормативно перенесены в 5B); checklist A–G зелёный; ограничения — измеренные quality findings провайдера, именно их квантифицирует benchmark. Оснований для `NOT_COMPLETE` нет; «WITH_MEASURED_LIMITATIONS» не выбран, потому что ограничения задокументированы как benchmark inputs и не сужают готовность к 5B.
