# Step 5B — Input Inventory (Cross-provider Benchmark)

**Дата:** 2026-07-19 · Documentation-only. Ничего из перечисленного не пересоздавать.

## Providers (2, сравнимые через общий Protocol)

| Provider | id / версия | Статус |
|---|---|---|
| `DeterministicPhraseProvider` | `deterministic-phrase` / rules-1.0.0 | baseline; controls 175/0 uncovered; full replay пройден (Step 4B) |
| `LLMObservationProvider` | `llm-observation` / `llm-obs-1.0.0` (frozen) / `openai/gpt-5-mini` | закрыт Step 5A; smoke-конформность доказана |

## Corpora

| Корпус | Размер | Источник / labels | Exclusions |
|---|---|---|---|
| Synthetic controls | 175 (158 generic + 17 specialized) | `semantic-fact-contract.yaml` controls; runner `runtime/calibration.py::run_synthetic_controls` | 2 экземпляра CONTROL_EXEMPTIONS (mandate_summary, title_scope_mismatch — причины в коде) |
| Step 2 gold fixtures | 21 (dataset 1.0.1) | ручная разметка; сырьё `/tmp/step2_fixture_source.json` (восстановимо read-only из live DB) | Wise-gold re-annotation по recovered-текстам — открытый owner-гейт (не blocker) |
| Golden decision cases | 25 (dataset 1.1.1) | Step 3A Decision SoT; исполняются движком | — |
| Historical eligible | **3 626** из 10 013 (FULL_MIN 600 / PARTIAL_MIN 200, классификация в `replay_full.py`) | live DB read-only `mode=ro` | test users/resend по правилам Step 4B replay |
| Flagship set | 5 recovered Wise (полные тексты) + Airwallex GPNI/Fraud из fixture-дампа | `artifacts/shadow-evaluator/recovered-wise/*.json` | вне git |
| Smoke corpus | 15 кейсов + 18 recordings | `artifacts/semantic-llm/smoke-20260719/` | уже оплачено; реюзать записи, не перевызывать |

## Runners / инфраструктура

| Компонент | Статус для 5B |
|---|---|
| `runtime/calibration.py` | **уже параметризован** провайдером (`provider or DeterministicPhraseProvider()`, строки 63/136) |
| `runtime/replay_full.py`, `replay_flagships.py` | захардкожен DeterministicPhraseProvider (строки 84/59) — нужна параметризация в 5B-плане (см. gaps) |
| Общий runtime path / diagnostics / semantic_hash | единые для обоих провайдеров (доказано boundary-тестом: одинаковые observations → идентичный fragment) |
| Record/replay | `RecordingStore` — LLM реплеится offline; deterministic провайдеру не нужен |
| Provider metadata | provider_id/prompt_version/model/usage/latency/retry в recordings и diagnostics |

## Метрики: есть / отсутствуют

- **Есть:** per-fact calibration metrics (без агрегата), rejection-коды, evidence coverage (calibration runner), semantic hashes, usage/cost/latency (в smoke-harness ad hoc), before/after decision transitions (replay_full).
- **Отсутствуют (собрать в 5B):** cost/latency оси в самих раннерах (сейчас только в smoke-скрипте), сравнительная precision/recall по gold между провайдерами, reproducibility-отчёт LLM на calibration/replay корпусах.

## Cost-bearing operations (только они требуют approve)

| Операция | Оценка | Статус |
|---|---|---|
| LLM calibration corpus (~196 вызовов) | ~$0.25 (cap $1.5) | не одобрено |
| LLM bounded replay (200) | ~$0.35 (cap $2) | не одобрено |
| LLM full replay (3 626) | ~$5.5 (cap $15) | отдельный approve |

Deterministic-провайдер по всем корпусам — $0.
