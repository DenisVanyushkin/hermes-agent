# Step 5A — Slice 5A-5: Controlled Live Smoke Report

**Дата:** 2026-07-19 · **Approval:** `APPROVE_STEP5A_SMOKE_WITH_CAP_$1` (только smoke)
**Verdict:** **STEP_5A_SMOKE_COMPLETE**

## Identity

- provider_id `llm-observation`, prompt_version `llm-obs-1.0.0` (НЕ менялся во время smoke)
- Requested model: `openai/gpt-5-mini`; actual `response.model` во всех 15 записях: `openai/gpt-5-mini` — identity validation пройдена во всех вызовах, mismatch = 0.
- temperature 0, max_retries 0 (retry_count=0 во всех записях), `allow_fallbacks: false` в каждом запросе.
- `JOB_INTEL_LLM_LIVE_APPROVED=1` выставлялся только в env smoke-процесса; persistent config не менялся, gateway не рестартовался.

## Вызовы и стоимость

| Метрика | Значение |
|---|---|
| Attempted / succeeded / failed | **18 / 15 / 3**¹ |
| Input / output tokens (15 успешных) | 20 958 / 42 914 |
| Фактическая стоимость | **$0.0911** (cap $1; прайс $0.25/M in, $2/M out подтверждён на OpenRouter) |
| Latency p50 / p95 / max | 20.3 s / 80.9 s / 176.1 s |
| Transport / parse / schema failure rate (после фикса) | 0% / 0% / 0% |

¹ Первые 3 попытки первого прогона упали с HTTP 400 **до инференса, $0.00 стоимости**: `response_schema()` оставлял pydantic `$defs` вложенными в `items`, давая dangling `$ref` — API отклонял схему. Это transport/serialization-баг, а не prompt/parser policy: исправлен хойстом `$defs` на корень документа (+ регресс-тест `test_response_schema_has_no_dangling_refs`), после чего корпус прогнан заново. Промпт, parser policy, evidence-правила, runtime — не менялись.

## Observations

| Метрика | Значение |
|---|---|
| Emitted / accepted / rejected | 96 / **85** / 11 |
| Rejection codes | `unknown_fact_reference` × 11 — единственный код |
| `excerpt_not_verbatim` rate | **0%** (0/96 — все цитаты verbatim) |
| Zero-observation cases | 1 — ровно ожидаемый `syn-empty` (no-evidence control) ✅ |
| Semantic facts emitted (сумма) | 84 |

**Природа всех 11 реджектов одна:** модель в части сигналов добавила префикс пути в `signal_type` (`mandate.platform_engineering=true`, `company.platform_as_business=true`, `platform_ecosystem=true` без `company.`) — naming-конвенция промпта нарушена, Stage 3 корректно отсёк (`mandate.mandate.*` / несуществующий fact id). 10 из 11 — на одном кейсе `airwallex-gpni`. Verbatim и enum-значения при этом везде валидны.

## Разбивка по 15 кейсам

| Кейс | Kind | Obs acc/emit | Facts | Live↔Replay |
|---|---|---|---|---|
| syn-pos-growth-pnl | synthetic_positive | 6/6 | 5 | ✅ |
| syn-pos-market-entry | synthetic_positive | 5/5 | 5 | ✅ |
| syn-pos-pricing | synthetic_positive | 5/5 | 5 | ✅ |
| syn-pos-platform-business | synthetic_positive | 2/3 | 2 | ✅ |
| syn-pos-org-build | synthetic_positive | 4/4 | 4 | ✅ |
| syn-pos-company-scale | synthetic_positive | 5/5 | 5 | ✅ |
| syn-neg-maintenance | synthetic_negative | 2/2 | 2 | ✅ |
| syn-neg-internal-tools | synthetic_negative | 3/3 | 3 | ✅ |
| syn-neg-fraud | synthetic_negative | 5/5 | 5 | ✅ |
| syn-amb-delivery | synthetic_ambiguous | 3/3 | 3 | ✅ |
| syn-empty | empty_control | 0/0 | 0 | ✅ |
| wise-apac-growth | flagship_positive | 14/14 | 14 | ✅ |
| wise-financial-crime | flagship_negative (contrast) | 13/13 | 13 | ✅ |
| wise-acquiring | flagship_conditional | 14/14 | 14 | ✅ |
| airwallex-gpni | flagship_positive | 4/14 | 4 | ✅ |

Contrast pair (wise-apac vs wise-financial-crime) различён: FinCrime получил `risk_compliance_heavy=true` (explicit, из title) — ключевой негативный сигнал присутствует.

## Ручная проверка unsupported evidence (выборочная)

- Подавляющее большинство accepted observations поддержаны цитатой корректно; basis используется по классам (title-only сигналы честно `weak`, near-paraphrase — `explicit`).
- **Findings (2, не блокирующие smoke, материал для calibration):**
  1. `wise-financial-crime`: `growth_mandate=true` / `expansion_mandate=true` из boilerplate «enable our APAC Product team to launch new products…» — это контекст команды, а не мандат самой FinCrime-роли; интерпретация натянута.
  2. `airwallex-gpni`: `risk_compliance_heavy=true` из строки требований («Familiarity with risk management…») — requirements-строка использована как evidence мандата.

## Live ↔ replay

- Полный корпус повторён offline; **сеть технически отключена** (`socket.socket` заменён на функцию, кидающую AssertionError — ни одного срабатывания).
- Parsed `Observation[]`: live == replay на всех 15 кейсах.
- `semantic_dump()` / semantic hash: replay-хэш стабилен при повторном прогоне на всех кейсах; **15/15 match**.
- Все 15 recordings созданы и проходят response-hash verification (плюс 3 записи неуспешных 400-попыток первого прогона сохранены с error и реплеятся как `recorded_call_failed`).

## Артефакты

- Recordings: `artifacts/semantic-llm/smoke-20260719/recordings/` (18 файлов)
- Результаты/дампы: `artifacts/semantic-llm/smoke-20260719/results/` (smoke-summary.json + 15 semantic-дампов)
- Runner: `artifacts/step5a_smoke.py` (вне git, как все run-артефакты)

## Рекомендация

**Допускать `llm-obs-1.0.0` к calibration в неизменённом виде** (после отдельного approve): контрактная конформность подтверждена live (0 verbatim-нарушений, unknown через отсутствие observation, identity/no-fallback/no-retry доказаны), а два измеренных ограничения — signal-prefix confusion (11.5% emitted) и редкие натянутые интерпретации — именно то, что calibration должна квантифицировать. Правка промпта, если понадобится, = `llm-obs-1.1.0` после calibration, не раньше.
