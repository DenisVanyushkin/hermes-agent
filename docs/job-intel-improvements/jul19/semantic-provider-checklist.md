# Semantic Provider Conformance Checklist

**Версия:** 1.0.0 (соответствует semantic-provider-contract.md v1.0.0)
**Назначение:** проверочный лист для ревью любой provider-реализации перед допуском к benchmark (Step 5B) и Shadow (Phase III). Каждый пункт — да/нет; любое «нет» = не конформен.

## A. Интерфейс и границы

- [ ] Реализует `SemanticProvider` Protocol: атрибуты `provider_id`, `prompt_version`, метод `extract_semantic_observations(*, title, text, structured) -> list[Observation]`.
- [ ] Весь код провайдера живёт внутри `runtime/provider.py` (или собственного модуля провайдера); ничего provider-специфичного не утекло в pipeline/models/evaluator.
- [ ] Никаких сайд-эффектов: не пишет файлы состояния, не мутирует вход, не трогает БД, не шлёт сообщения.
- [ ] Не читает ничего, кроме переданных `title`/`text`/`structured`: ни истории вакансий, ни прошлых оценок, ни preference model, ни внешних обогащений.
- [ ] Эмитит ТОЛЬКО `Observation[]`: ни фактов, ни fragment, ни verdicts, ни recommendations, ни clarifications.

## B. Observations

- [ ] Все `observation_id` уникальны в вызове и стабильны при повторном вызове на том же входе.
- [ ] `excerpt` — verbatim-подстрока заявленного `location` (title/description), ≤ 400 символов, без парафраза/склейки.
- [ ] `signal_type` строго `"<fact_leaf>=<value>"`; каждый leaf резолвится в существующий fact id контракта; value ∈ Step 2 enum факта.
- [ ] Не эмитит сигналы на `enrichment_only`-факты.
- [ ] `maps_to` непуст, все ids существуют в контракте.
- [ ] `interpretation` — 1–2 evidence-based предложения; без chain-of-thought; excerpt реально поддерживает interpretation (ручная выборочная проверка).
- [ ] Нет наблюдений «fact=unknown»; отсутствие evidence = отсутствие observation.
- [ ] Доля rejected observations на контрольном корпусе объяснена (цель ~0 для конформного провайдера).

## C. Confidence

- [ ] `basis` назначается по типу evidence (explicit / direct / weak), не по self-confidence модели.
- [ ] Нигде нет вероятностей, числовых скоров, logprobs — ни в полях, ни закодированных в interpretation/id.

## D. Prompt / версии

- [ ] Промпт (или таблица правил) целиком внутри модуля провайдера.
- [ ] Промпт не переопределяет значения фактов, инвентарь фактов, evaluator policy.
- [ ] Любое изменение промпта/правил сопровождается bump `prompt_version`; версия уникальна для содержимого промпта.
- [ ] Для LLM: model version зафиксирован и опубликован.

## E. Детерминизм и replay

- [ ] Deterministic: одинаковый вход → байт-идентичный выход; нет wall-clock/random/сети.
- [ ] Stochastic (LLM): temperature=0/детерминированное декодирование; record/replay raw-ответов; benchmark и calibration воспроизводимы без живых вызовов.
- [ ] Повторный replay на тех же версиях даёт стабильные semantic_hash (0 дрейфа).

## F. Benchmark-метаданные

- [ ] Публикует: provider_id, prompt_version, model version (LLM), счётчики токенов/стоимость на вызов (LLM; deterministic — 0-cost декларация), latency-замеры.
- [ ] `FactProvenance`/`ExtractionDiagnosticsOut` заполняются честными значениями.

## G. Границы изменений (extension rules)

- [ ] Не потребовал НИКАКИХ изменений runtime pipeline, моделей, валидации.
- [ ] Не потребовал изменений evaluator / Decision SoT.
- [ ] Не потребовал изменений Semantic SoT (факты, evidence-иерархия, confidence/conflict policy).
- [ ] Не ввёл provider-специфичную policy нигде ниже по конвейеру.

## H. Допуск (acceptance gate, Roadmap SoT §9.5)

- [ ] Contract compliant (разделы A–G — все «да»).
- [ ] Replay reproducible (полный historical replay без failures/contract gaps).
- [ ] Calibration complete по существующему framework.
- [ ] Benchmark completed по всем осям (replay, calibration, evidence coverage, precision, recall, cost, latency, reproducibility) — без агрегатного скора.
- [ ] Recommendation approved владельцем (Step 5C) — только после этого Shadow.
