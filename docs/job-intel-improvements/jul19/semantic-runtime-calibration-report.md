# Semantic Runtime — Calibration Report (deterministic-phrase provider)

**Дата:** 2026-07-19. Gold: Step 2 fixtures 1.0.1 (21 кейс, replay_input =
bounded excerpts реальных текстов). Provider: deterministic-phrase/rules-1.0.0.
Артефакт: `artifacts/shadow-evaluator/semantic-calibration.json`. Без агрегата.

## Per-fact (только факты с gold или emitted; полный JSON в артефакте)

| Fact | Precision vs gold | Recall vs gold | gold_known | emitted | unknown rate |
|---|---|---|---|---|---|
| mandate.scope_breadth | 1.0 | 0.118 | 17 | 2 | 0.905 |
| mandate.growth_mandate | 1.0 | 0.75 | 4 | 3 | 0.857 |
| mandate.risk_compliance_heavy | 1.0 | 0.667 | 3 | 2 | 0.905 |
| mandate.pricing_core / acquiring_core / expansion_mandate | 1.0 | 1.0 | 1 | 1 | 0.952 |
| company.scale | 1.0 | 0.154 | 13 | 2 | 0.905 |
| company.is_crypto_exchange | 1.0 | 0.2 | 10 | 2 | 0.905 |
| mandate.internal_tools_backoffice | 0.0* | 0.0 | 1 | 1 | 0.952 |
| company.product_culture_signal | 0.0* | — | 0 | 1 | 0.952 |
| monetization/platform-формы/revenue/digital/summary и др. | — | 0.0 | 1–3 | 0 | 1.0 |

Clarification rate: 8.86/кейс (в основном blocking scope/digital-ownership).

## Классы расхождений (руками разобраны)

- `extraction_false_positive` ×2 (*): (1) internal_tools сработал на фразе
  «back-office tooling» в OKX KYB-фикстуре, где gold оставил unknown —
  формально FP против gold, содержательно спорно → кандидат
  `gold_ambiguous`; (2) product_culture=true на «true ownership» в Airwallex
  GPNI, gold unknown — аналогично `gold_ambiguous` (текст реально содержит
  сигнал).
- `extraction_false_negative` — доминирующий класс: правила-фразы не покрывают
  реальные формулировки (scope 15/17, monetization 3/3 и т.д.).
- `provider_divergence` / `contract_gap` — 0.

## Вывод

Провайдер консервативен: **если факт emitted — он верен (precision 1.0 по
всем однозначным gold)**, но recall низок по конструкции. Ceiling
phrase-подхода зафиксирован; contract_gap = 0 — контракт исполним. Числовой
порог evidence-coverage для go/no-go LLM-провайдера — owner-решение Q2
(Step 4A review report).
