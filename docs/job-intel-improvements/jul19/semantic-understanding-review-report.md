# Semantic Understanding SoT — Review & Readiness Report (Step 4A)

**Дата:** 2026-07-19. Артефакты: human SoT, semantic-fact-contract.yaml
(+schema, структурный валидатор), 19 validation-тестов. Runtime НЕ создан.

## 1. Ключевые решения и их обоснование

1. **Canonical ids = пути Step 2** — второго словаря нет; coverage-инвариант
   тестируется против фактических полей `Mandate` модели: невозможно
   «забыть» семантическое поле или определить его дважды.
2. **brand_recognition = enrichment_only** — прямое следствие запрета
   «company reputation» как источника: тир бренда не выводим из текста
   вакансии в принципе. Semantic-extraction этого факта запрещена схемой.
3. **Observation-слой обязателен и verbatim** — запрет «писать факты из
   текста напрямую» стал schema-проверяемым; evidence coverage измерим
   механически (excerpt ⊂ text).
4. **Deterministic wins** при конфликте с semantic (наследие Step 2 plan) —
   защищает воспроизводимость и делает LLM-провайдера несущественным для
   уже-детерминированных фактов.
5. **Company-факты — только из текста вакансии**: scale/stage/crypto могут
   заполняться semantic'ом лишь при явных self-statements; famous-brand
   shortcuts запрещены (контроль: экстрактор не имеет права «знать OKX»).
6. Confidence — по качеству evidence; самоуверенность провайдера — не
   источник (schema-инвариант).

## 2. Ambiguities found (закрыты в SoT)

- Разграничение growth_mandate (роль) vs «high-growth company» (не evidence).
- «Infrastructure» в титуле: не решает ни одну platform-форму — обе unknown
  до появления audience-evidence (согласовано со Step 2 контрастами).
- maintenance_only: требует maintenance-фрейминга ВСЕХ core-обязанностей —
  одна optimization-строка не делает роль maintenance.
- mandate_summary: explanation-only, никогда не decision input.

## 3. Missing semantic facts (кандидаты, сознательно НЕ включены)

- `people_scope` численно (FTE) — deterministic «team of N» есть; semantic
  оценка размера орг — низкая ценность/высокий риск галлюцинаций; отложено.
- `industry_vertical` — намеренно отсутствует: индустрия нейтральна
  (process SoT), нужна только exploration-учёту, который берёт её из
  company-записи, не из semantic extraction.
- `comp_range` — compensation вне политики (Step 1), не извлекаем.

## 4. Unresolved owner questions

- **Q1.** Достаточно ли «self-statement в тексте» для `is_crypto_exchange`,
  или до появления company-enrichment этот факт лучше держать enrichment_only
  тоже? Текущее решение: hybrid (текст может утверждать), т.к. OKX-подобные
  постинги самоописываются; риск — рекламные самоописания.
  *Рекомендация: оставить hybrid, наблюдать в калибровке.*
- **Q2.** Порог для `evidence_coverage`-акцепта Step 4B (какая доля
  non-verbatim excerpts блокирует включение провайдера) — числовой порог
  сознательно не задан здесь; это calibration-решение владельца при Step 4B
  go/no-go.

## 5. Implementation readiness (Step 4B)

Контракт достаточен для нескольких взаимозаменяемых реализаций: канонический
выход задан (Step 2 фрагменты + observations), evidence/unknown/conflict/
provenance-политики детерминированы, синтетические контроли (5 × 30
semantic/hybrid фактов) образуют provider-независимый смок-набор, калибровка
определена без агрегата. Evaluator менять не требуется. Step 4B может
начинаться после owner-approve этого SoT; первые шаги: re-annotation Wise по
recovered-текстам (gold), затем extractor против golden + controls.

## 6. Риски преждевременной реализации

1. Промпт-эвристики вне контракта → незаметный возврат keyword-скоринга.
2. Провайдер-знание брендов просочится в company-факты → нарушение §3
   (ловится контролем «extractor must not know OKX» и evidence_not_verbatim).
3. Численные пороги confidence в коде — запрещены; только словарь этого SoT.
