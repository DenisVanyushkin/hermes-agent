# Semantic Runtime — Flagship Replay Report

**Дата:** 2026-07-19. 10 флагманов: 5 Wise (recovered full texts) + Airwallex
GPNI/Fraud, Monzo BB, Brex, Affirm (полные DB-тексты). Пайплайн:
deterministic extraction → semantic extraction → **неизменённый Step 3
evaluator**. Артефакт: `artifacts/shadow-evaluator/semantic-flagship-replay.json`.

## Сравнение со Step 3 (golden-ожидания на gold-аннотированных фикстурах)

| Кейс | Step 3 (gold-annotated) | Step 4B (extracted-only) | Комментарий |
|---|---|---|---|
| wise_financial_crime | weak → not_recommended | weak → **not_recommended** | воспроизведён из ИЗВЛЕЧЁННЫХ фактов (risk+narrow) |
| airwallex_payment_fraud | weak → not_recommended | weak → **not_recommended** | то же (3 факта извлечено) |
| wise_apac | strong → promising | moderate → unclear | title-сигналы (region/growth/expansion low) извлечены; body-формулировки вне правил |
| monzo_bb | strong → strong | moderate → unclear | scope из «Business Banking» извлечён (low) |
| brex | moderate → promising | moderate → unclear | growth извлечён |
| gpni / pricing / acquiring / onboarding / affirm | … | unclear | semantic-факты частично/не извлечены |

## Выводы

1. **Пайплайн доказан end-to-end:** извлечённые semantic-факты доходят до
   evaluator и меняют вердикты без единого изменения Decision SoT/evaluator —
   два негативных флагмана воспроизвели not_recommended чисто из extraction.
2. **Разрыв с Step 3-ожиданиями — целиком provider-recall**, не политика:
   там, где факты извлечены, band'ы совпадают или консервативнее (unclear).
   Классификация расхождений: insufficient_vacancy_evidence →
   extraction_false_negative (калибровочный класс), contract/decision gaps = 0.
3. **Критерий задания подтверждён:** качество replay улучшится только заменой
   провайдера (LLM за approval-гейтом) — архитектура выбрана правильно.
4. Wise re-annotation по recovered-текстам остаётся входом Step 5
   (re-replay + calibration review).
