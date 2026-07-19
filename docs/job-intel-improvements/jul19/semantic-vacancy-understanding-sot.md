# Semantic Vacancy Understanding — Source of Truth (v1.0.0)

**Статус:** канонический контракт semantic extraction (Step 4A). Runtime НЕ
реализован. Третий и последний архитектурный SoT системы:

```text
Career Preference SoT (Step 1)  — что хочет кандидат
Semantic Vacancy Understanding SoT (этот) — что на самом деле означает вакансия
Decision SoT (Step 3A)          — как сравнивать первое со вторым
```

**Машинный контракт:** `job_intel/vacancy_understanding/semantic/semantic-fact-contract.yaml`
(+ generated schema, structural validator `semantic/contract.py`). Human и
machine контракты эквивалентны; расхождение — блокирующий дефект.
**Зачем:** Step 3 replay доказал, что боттлнек — не decision policy (0
contract gaps, 0 critical FN/FP), а отсутствие semantic-фактов (62/63 =
insufficient_vacancy_evidence). Без отдельного SoT extraction-политика
расползётся по промптам и Python-эвристикам («`if "growth" in title`»), и
replay/калибровка/смена провайдера станут невозможными.

## 1. Каноническая семантическая модель

- **Единственное определение на факт**, ключ = канонический путь Step 2
  (`mandate.scope_breadth`, …): семантический контракт нормирует заполнение
  уже существующей канонической записи, второго словаря нет.
- Каждый факт задаёт: meaning, valid values (enum Step 2), required
  evidence, prohibited evidence, связь с deterministic-фактами Step 2, связь
  с evaluator Step 3, unknown policy, synthetic controls.
- Классификация: `deterministic_only` / `semantic_only` / `hybrid` /
  `enrichment_only` (semantic ЗАПРЕЩЁН) / `rule_produced` (только правилами
  конфликтов).

## 2. Инвентарь (36 фактов, полный список в YAML)

- **Mandate (21):** scope_breadth, revenue_proximity, growth/expansion,
  monetization/pricing/acquiring core, pnl, strategy, org design, team
  build, executive/board exposure, market entry, turnaround, zero-to-one,
  maintenance-only, feature-delivery-only, digital_business_ownership,
  transformation_phase, mandate_summary.
- **Product shape (4):** platform_as_business, platform_engineering,
  internal_tools_backoffice, risk_compliance_heavy.
- **Organization (2 семантических):** cross_functional_leadership,
  budget_ownership (reports-to/team-size — deterministic Step 2).
- **Company (7, только из текста вакансии):** scale, stage, customer_model,
  product_culture_signal, platform_ecosystem, is_crypto_exchange;
  **brand_recognition = enrichment_only** — тир бренда есть репутация по
  определению и из текста вакансии не выводим (прямое следствие §3).
- **Requirements (1):** overall_transferability (+entry_barriers: языковые —
  deterministic, доменная глубина — semantic).
- **Risk (3):** title_scope_mismatch (semantic), internal_contradiction и
  ambiguity (rule_produced — только правилами конфликтов).

Coverage-инвариант (тест): каждый semantic-заполняемый факт Step 2 модели
покрыт ровно одним определением.

## 3. Иерархия evidence

`explicit_text > structured_source_field > deterministic_derivation >
semantic_inference > human_gold` (gold — только тестовая истина). Точные
определения уровней — в YAML. **Запрещённые источники:** репутация
компании/бренда; интуиция рекрутёра/оператора; исторические допущения по
прошлым вакансиям компании; прошлые оценки/скоры/фидбек; world knowledge
провайдера, не заземлённый в тексте. **Только evidence вакансии порождает
факты вакансии.**

## 4. Observation model

Промежуточный слой обязателен:

```text
"Own the payments platform"  (текст)
  ↓ observation {excerpt verbatim, location, signal_type: business_ownership,
                 interpretation, maps_to: [mandate.platform_as_business]}
  ↓ canonical fact: platform_as_business = true (method=semantic_inference,
                 evidence -> observation)
```

Extractor никогда не пишет канонический факт напрямую из текста: минимум одно
observation на факт, excerpt строго verbatim (≤400 симв.), нарушение —
schema-ошибка. Это делает запрет проверяемым, а evidence coverage —
измеримым (verbatim-присутствие excerpt'а в тексте).

## 5. Provenance

Каждый semantic-факт несёт: `origin, provider (opaque id), prompt_version,
evidence_refs (observation ids), reasoning_summary, confidence`.
Reasoning summary — 1–2 предложения строго из evidence («в тексте X и Y ⇒
факт V»), с цитированием observation ids. **Chain-of-thought запрещён.**

## 6. Confidence (качественный, evidence-based)

`high` — explicit_text observation прямо утверждает факт, противоречий нет;
`medium` — inference с прямой responsibility-опорой в теле; `low` —
title-only/boilerplate-смежные сигналы; `unknown` — нет квалифицирующего
observation → значение факта обязано быть unknown. **Самоуверенность
провайдера (logprobs, "confidence: 0.9") источником не является.**

## 7. Конфликты (детерминированное разрешение, 6 правил)

Противоречивые observations одного уровня → факт unknown +
internal_contradiction + clarification; разные уровни → высший побеждает,
проигравший сохраняется как overridden; **semantic ↔ deterministic →
deterministic побеждает**, semantic остаётся риском (никогда не
перезапись); невозможные комбинации (обе platform-формы) → значение с высшим
уровнем evidence остаётся, второе unknown; при равных уровнях — оба unknown
+ риск (зеркало валидатора Step 2); недостаточное evidence → unknown;
дубли-observations → один факт со всеми ссылками.

## 8. Unknown policy

Unknown — first-class; для каждого факта в YAML заданы: когда unknown
ОБЯЗАТЕЛЕН, когда inference ЗАПРЕЩЁН (например scope из титула, scale из
известности бренда, barrier из индустрии), clarification priority (словарь
Step 3A). Unknown никогда не заменяется догадкой.

## 9. Multi-provider contract

Контракт не зависит от GPT/Claude/любой модели (тест: имена вендоров в
контракте запрещены). Канонический выход любого провайдера — частичная
Step 2 запись (semantic-поля) + observation list, валидируемая схемой Step 2.
Промпты — implementation detail; extractor_version = (semantic_contract_version,
provider_id, prompt_version). Согласие провайдеров по ЗНАЧЕНИЯМ измеряется
калибровкой (cross_provider_agreement), а не предполагается.

## 10. Calibration contract

Gold: фикстуры Step 2 + re-annotated Wise (recovered full texts) + synthetic
controls этого контракта. Метрики (без единого агрегата): per-fact
precision/recall vs gold; evidence coverage (verbatim-проверка excerpts);
unknown rate; clarification rate; cross-provider agreement; confidence
calibration. Классы расхождений: extraction_false_positive/negative,
evidence_not_verbatim, gold_ambiguous, provider_divergence, contract_gap.

## 11. Human review

Gold-аннотация по правилам Step 2 (candidate-independent, evidence-cited);
sampled review; корректировки меняют ТОЛЬКО gold-данные; изменение поведения
экстрактора — только через prompt/impl-изменение с bump extractor_version;
изменение политики — только через explicit SoT amendment (owner).
No silent learning.

## 12. Replay integration

Replay различает происхождение фактов: deterministic / semantic / missing.
Расхождения из-за отсутствующего semantic evidence остаются в классе
`insufficient_vacancy_evidence` decision-таксономии; качество самих
semantic-фактов оценивается калибровочными классами (§10) и никогда не
смешивается с decision-политикой.

## 13. Synthetic controls

Для каждого semantic/hybrid факта в YAML заданы 5 контролей: positive /
negative / ambiguous / unknown / conflicting (schema-обязательны). Они
войдут в calibration-набор Step 4B как provider-независимые тесты.

## 14. Versioning & change policy

Semver (MAJOR = удаление/изменение семантики факта; MINOR = новые
факты/контроли; PATCH = формулировки); совместимость: Step 2 schema 1.x,
decision contract 1.x; изменения — только с owner approval (process SoT §8).
Evaluator никогда не содержит скрытой extraction-политики; extraction
никогда не содержит desirability-политики.
