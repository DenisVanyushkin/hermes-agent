# Shadow Preference Evaluator — Implementation Report (Step 3)

**Дата:** 2026-07-19. Evaluator v0.1.0, shadow/offline only.
**Контракты:** Decision SoT v1.1.0 (`decision-contract.yaml`), Preference
Model 1.x, Vacancy Understanding 1.x.

## 1. Архитектура

| Модуль | Ответственность |
|---|---|
| `models.py` | строгая выходная модель (4 секции, evidence-backed items, trace, unknown ledger, clarifications); `semantic_dump()/semantic_hash()` отделяют run-metadata (`evaluated_at`) от семантического равенства |
| `policy.py` | immutable RuntimePolicy: загрузка/валидация decision contract + Step 1 модели; version guards (unsupported major → error record, БЕЗ legacy fallback); единственный matrix resolver; единственный монотонный cap resolver; O1 action mapping |
| `signals.py` | чистая трансляция канонических фактов Step 2 в сигналы правил Step 1: сигнал существует только при известном факте (unknown ≠ false), несёт fact path + evidence refs; ledger unknown-полей |
| `engine.py` | 12-стадийный граф решения (нумерация = contract.evaluation_order) |
| `replay.py` | read-only исторический replay (snapshot → extract → evaluate → classify → артефакты) |
| `contract.py` | (Step 3A) структурная валидация contract — verdict-кода не содержит |

Изоляция: production ↛ shadow_evaluator; shadow_evaluator ↛
cli/digest/store/feedback/evaluator/crm/slack (guard-тесты в обе стороны);
записи в БД/сообщения запрещены и проверяются тестом паттернов.

## 2. Ключевые механики

- **Interactions**: `allow` = prevent-match (правило не срабатывает, trace
  `prevented`); `suppress` сохраняет item c `suppressed_by`;
  `limit_to_company_fit` — crypto живёт только в company-секции;
  `route_to_fallback` — lane + fallback-подавление `small_local_company`;
  `exclude_from` — platform_engineering не питает platform_as_the_business.
  Применение детерминированное, идемпотентное (тест), по priority.
- **Band-логика** — качественная, из критериев SoT: exceptional требует
  scope≥business_line@high + ≥2 strong-support@high + отсутствие concerns +
  known revenue; strong — scope≥bl@≥medium + ≥1 strong-support@high без
  material concerns, либо monetization exception; narrow/risk/infra без
  strong-оси → weak; счёта concerns нет (O6).
- **Unknown policy**: полевые записи contract → ledger + clarifications;
  «uncertain-grade» unknown (work_format вне KZ, country, digital-ownership
  при non-product) капит на promising, verdict не меняет.
- **Confidence**: min по критическим фактам секции (scope/revenue; work
  format/country/sponsorship-when-relevant; scale/brand); revenue-конфиденс
  наследуется от факта; overall = min секций; terminal infeasible → high.
- **Action** (O1): exceptional/strong→apply; promising→investigate, save при
  low-conf или uncertain; unclear→investigate (+ обязательный clarification);
  not_recommended→reject.

## 3. Golden выравнивание (dataset 1.1.0) — все изменения объяснены

Матрица и caps НЕ менялись. Приведены в соответствие с утверждёнными
критериями band'ов и честным evidence-состоянием снапшотов:

1. `gd_wise_apac_titleonly`: mandate exceptional→**strong** (title-only не
   может дать exceptional по критерию high-confidence scope); итог
   promising/save не изменился; full-text ожидание живёт в
   `gd_wise_apac_fulltext`.
2. `gd_airwallex_gpni`: feasibility feasible→**uncertain** — снапшот 1262
   (SG onsite) не содержит sponsorship-формулировок; по утверждённой политике
   это honest uncertain → promising/save + clarification. Добавлен
   policy-кейс `gd_airwallex_gpni_relocation_variant` (зеркало варианта 6123
   «Relocate to Singapore») → exceptional/apply.
3. `gd_brex_growth_vancouver`: mandate strong→**moderate** (growth-домен ниже
   business line); итог promising/save прежний.
4. Confidence-поля пересчитаны по политике (revenue medium → секция medium;
   wf-unknown → low), company-поля кейсов с необогащёнными фикстурами →
   unknown.
5. Fixture dataset 1.0.1: enrichment-grade gold (Wise/Airwallex/Monzo/Brex/
   Affirm brand+scale; Monzo country UK; Affirm 7740 risk-факты; GPNI work
   format onsite) — все с manual-gold provenance и rationale.

## 4. Exploration

Реализация ограничена контрактом: оси Step 1 only, big-tech/early-startup
нейтральны (O5). Маркер `exploration_axis` в overall зарезервирован; в
текущем golden-наборе exploration-кейс (crypto broad mandate) проверяет
caps/band-путь; автоматическая расстановка маркера — часть Step 5 selection
policy, не shadow-вердикта.

## 5. Известные ограничения (не дефекты контракта)

- Semantic extraction отсутствует (по плану Step 2): исторический replay
  видит только deterministic-факты → mandate почти всегда unknown → honest
  unclear. Это зафиксировано replay-отчётом как
  `insufficient_vacancy_evidence`, не как качество контракта.
- Recovered Wise full-texts (5/5) сохранены offline-артефактами; их gold
  re-annotation — вход следующего слайса (перед calibration, O3).
