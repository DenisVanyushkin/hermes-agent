# Step 5B — Slice 5B-4: Spend Gate Report (LLM Calibration Live Run)

**Дата:** 2026-07-20 · **Статус:** ОЖИДАЕТ РЕШЕНИЯ ВЛАДЕЛЬЦА — live-вызовы не выполнялись и технически заблокированы (`JOB_INTEL_LLM_LIVE_APPROVED` не установлен).

## Provider identity (заморожена, из Step 5A)

- **Provider:** `llm-observation`, prompt `llm-obs-1.0.0` (заморожен как benchmark baseline)
- **Model:** `openai/gpt-5-mini`, transport OpenRouter, `temperature=0`, `max_retries=0`, `allow_fallbacks=false`, record/replay обязателен
- Фактический прайс сверяется на openrouter.ai перед запуском и **публикуется в manifest run-а** (`price_input_usd_per_mtok` / `price_output_usd_per_mtok` / `pricing_source`) — механика Slice 5B-2. Прайс входит в `provider_config_hash`: resume под другим прайсом блокируется.

## Corpus (фактический, из Slice 5B-3 — не плановые цифры хэндовера)

| Датасет | Кейсов | dataset_hash |
|---|---|---|
| controls-1.0.0 | 158 | `185bac3f4e7899b8…` |
| golden-fixtures-21 | 21 | `d6484c3b7797671e…` |
| decision-golden-1.1.1 | 20 | `ee3864db6ef2d23b…` |
| **Итого calibration live run** | **199** | — |

Датасеты и хэши идентичны deterministic-baseline прогонам 5B-3 — сравнение providers идёт на байт-в-байт том же корпусе.

## Смета

Опора: смоук 5A — 15 вызовов = $0.0911 (~$0.0061/вызов на полноразмерных вакансиях). Controls (158) — короткие тексты, дешевле среднего; golden/decision (41) — полноразмерные.

| Параметр | Значение |
|---|---|
| Вызовы | 199 (одна попытка на кейс, retry=0) |
| Input | ~650k tok (промпт ~2.5k/вызов + текст) |
| Output | ≤1500/вызов, ожидаемо ~250k tok |
| Ожидаемая стоимость | **~$0.7–0.9** |
| Практический cap | **$3** |
| Stop conditions | 3 transport-ошибки подряд; model identity mismatch; превышение cap; parse-fail > 50% |
| Длительность | ~15–25 мин последовательно |
| Артефакты | recordings ~2.5 МБ в `artifacts/semantic-benchmark/` (вне git) |

Прайс дороже допущения ×2 → стоп и пересогласование.

## Что произойдёт после approve (scope 5B-4)

1. Registry получает spend-gated live-режим (конструируется только при установленном approve-флаге, по образцу 5A `build_live_llm_provider`).
2. Live run 199 кейсов через common runner → recordings + case rows + `provider_benchmark_summary.json` (cost из фактического usage, latency_mode=live).
3. Немедленный offline replay тех же recordings → проверка live-to-replay equality (любой `replay_mismatch` = benchmark blocker по контракту §8).
4. Отчёт + commit. Сравнение с deterministic baseline и вердикт — это 5B-5, не 5B-4.

## Explicit non-goals 5B-4

Bounded historical (200) и full historical (3626) — ОТДЕЛЬНЫЕ spend gates (5B-6/5B-7). Prompt не трогается. Никакого provider selection.

## Required owner decision

```text
APPROVE_5B4_CALIBRATION_WITH_CAP_$3    # или свой cap
REVISE_MODEL_OR_BUDGET
DO_NOT_RUN_LIVE_CALLS
```
