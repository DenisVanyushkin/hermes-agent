# Semantic Extractor Runtime — Implementation Report (Step 4B)

**Дата:** 2026-07-19. Runtime v0.1.0; контракт Step 4A v1.0.0. Shadow/offline.

## 1. Архитектура

`job_intel/vacancy_understanding/semantic/runtime/`: `models.py` (Observation,
SemanticExtraction, ProviderProtocol), `provider.py` (DeterministicPhraseProvider
+ gated LLMProvider), `pipeline.py` (стадии 1–10), `calibration.py`
(synthetic controls + per-fact калибровка), `replay_flagships.py` (read-only
replay-harness, единственный модуль с правом импортировать evaluator).

## 2. Pipeline (стадии — ровно по заданию)

1 input validation (malformed → ValueError) → 2 provider observations (только
observations) → 3 validation (verbatim excerpt, unique id, location, valid
signal/value против contract+Step 2 enums, maps_to resolve, enrichment_only
запрещён; каждый отказ классифицирован) → 4 normalization (merge duplicates,
provenance сохраняется) → 5 fact mapping (только observation→fact; словарь
mapping'а = инвентарь контракта + Step 2 enums, convention `leaf=value`; hidden
mappings отсутствуют) → 6 conflict engine (все 6 cf_* правил, golden-тест на
каждое) → 7 confidence (только evidence-basis: explicit→high, direct→medium,
weak→low; численные значения запрещены и тестируются) → 8 unknown policy +
clarifications со ссылкой на contract entry → 9 Step 2 validation (terminal)
→ 10 детерминированная сериализация (byte-identical повторные прогоны —
тест).

## 3. Provider abstraction

`SemanticProvider` protocol: `extract_semantic_observations() -> Observation[]`.
**Первый конформный провайдер — DeterministicPhraseProvider** (правила-фразы =
его «промпт», implementation detail): pure, replayable, без сети и моделей.
Основание выбора: (а) Step 4A признаёт deterministic rules конформной
реализацией; (б) DoD-9 требует byte-identical прогонов; (в) live-LLM вызовы
остаются за отдельным approval-гейтом — `LLMProvider` поднимает
NotImplementedError с указанием гейта (тест). Ничего провайдер-специфичного
не покидает `provider.py` (import-guard тест).

## 4. Валидация и запреты

Факты не порождаются из provider metadata / промпта / имени компании / world
knowledge / ATS-источника: единственный путь — валидированное observation.
`company.brand_recognition` (enrichment_only) отвергается на Stage 3.
Runtime не импортирует shadow_evaluator/preference_model (кроме
replay-harness), не содержит desirability-словаря, не пишет в БД и не шлёт
сообщений (guard-тесты).

## 5. Conflicts / Confidence / Unknown

Все правила Step 4A реализованы буквально; дополнительных эвристик нет:
equal-level противоречие → unknown + internal_contradiction risk (+
title_scope_mismatch при участии title-observation); уровень выше побеждает
с сохранением проигравшего; **deterministic wins** (never overwrite);
невозможные пары (обе platform-формы) — по уровню evidence, при равенстве обе
unknown. Confidence — только качественный; unknown никогда не заменяется
догадкой; clarifications ссылаются на unknown policy контракта.

## 6. Калибровка и controls

`run_synthetic_controls`: **126 pass / 0 fail / 39 exempt** — каждый exempt
объяснён (control без машинно-конструируемой пары фраз; mandate_summary и
title_scope_mismatch валидируются другими путями). `run_calibration`:
per-fact precision/recall vs gold по 21 фикстуре, без агрегата
(semantic-runtime-calibration-report.md).

## 7. Replay

`replay_flagships` прогоняет 10 флагманов (5 Wise recovered full texts + 5
полных DB-текстов) через deterministic extraction → semantic extraction →
НЕИЗМЕНЁННЫЙ Step 3 evaluator (semantic-runtime-replay-report.md).

## 8. Limitations (не дефекты архитектуры)

Phrase-провайдер имеет узкое покрытие реальных формулировок — большинство
semantic-фактов на живых текстах остаётся unknown (честный unclear вместо
галлюцинаций). Это количественно показано калибровкой и есть ровно тот разрыв, который
закрывает LLM-провайдер (следующий approval-гейт). Архитектурный вывод
задания выполнен: качество меняется ТОЛЬКО провайдером — ни Decision SoT, ни
evaluator, ни контракт не менялись.
