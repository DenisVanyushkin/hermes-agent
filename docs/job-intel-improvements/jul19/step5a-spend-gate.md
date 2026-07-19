# Step 5A — Slice 5A-4: Spend Gate Report

**Дата:** 2026-07-19 · **Статус:** ОЖИДАЕТ РЕШЕНИЯ ВЛАДЕЛЬЦА — live-вызовы не выполнялись.

## Proposed provider

- **Provider/transport:** OpenRouter через существующий `agent/auxiliary_client.resolve_provider_client("openrouter", model=…)` (OpenAI-совместимый; ключ уже в `~/.hermes/.env`).
- **Exact model identifier:** `openai/gpt-5-mini` (точная ревизия из ответа фиксируется в каждой записи как `response_model`).
- **Reason for selection:** строгий structured output (json_schema strict), temperature 0, низкая цена для calibration/полного replay, доступ через уже настроенный stack, usage metadata в каждом ответе, отсутствие model fallback при явном пине (провайдер дополнительно сверяет resolved_model).
- **Determinism settings:** `temperature=0`; воспроизводимость через record/replay (все raw-ответы записываются, offline replay без сети).
- Опционально для чистоты: `extra_body={"provider": {"allow_fallbacks": false}}` (запрет OpenRouter-роутинга между upstream-хостами) — включу, если не возражаешь.

## Ценовое допущение

Оценки ниже — по типичному прайсу mini-класса (~$0.25/M input, ~$2/M output). Перед запуском фактический прайс сверяется на openrouter.ai; если дороже допущения более чем ×2 — стоп и пересогласование.

## Proposed first smoke (Slice 5A-5)

| Параметр | Значение |
|---|---|
| Корпус | 15 вызовов: 6 synthetic positive controls, 4 synthetic negative/ambiguous, 1 empty/no-evidence, 3 flagship (Wise APAC, Airwallex GPNI, Wise Financial Crime), 1 contrast pair (входит в flagships) |
| Input tokens | ~70k (промпт ~2.5k + вакансия 0.5–4k на вызов) |
| Max output tokens | 1500/вызов, суммарно ≤ 25k |
| Ожидаемая стоимость | **~$0.05** |
| Hard max cost | **$1** |
| Timeout | 60s/вызов |
| Stop conditions | 3 transport-ошибки подряд; любой признак смены модели; превышение $1; parse-fail > 50% |

## Proposed calibration/replay budget (Slice 5A-6)

| Прогон | Вызовы | Input | Ожидаемо | Worst-case |
|---|---|---|---|---|
| Calibration corpus (175 controls + 21 gold fixtures) | ~196 | ~600k tok | ~$0.25 | $1.5 |
| Bounded replay (предлагаю 200 записей выборкой из eligible) | 200 | ~900k tok | ~$0.35 | $2 |
| **Полный replay 3 626 eligible** (НЕ запускается без отдельного approve) | 3 626 | ~16M tok | ~$5.5 | $15 |

- Длительность: smoke ~2 мин; calibration ~15 мин; bounded replay ~20 мин; полный replay ~2.5–4 ч (последовательно).
- Объём артефактов: ~12 КБ/запись → smoke+calibration+bounded ~5 МБ; полный ~45 МБ (в `artifacts/semantic-llm/`, вне git).

## Required owner decision

Одно из:

```text
APPROVE_STEP5A_SMOKE_WITH_CAP_<amount>     # напр. APPROVE_STEP5A_SMOKE_WITH_CAP_$5 (покроет smoke+calibration+bounded)
REVISE_MODEL_OR_BUDGET
DO_NOT_RUN_LIVE_CALLS
```

Полный replay 3 626 записей требует отдельного явного approve в любом случае.

До решения: `JOB_INTEL_LLM_LIVE_APPROVED` не установлен, live-вызовы технически невозможны (фабрика падает с `live_calls_not_approved`).
