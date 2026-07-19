# Step 5A — Slice 5A-3: Offline Conformance Report

**Дата:** 2026-07-19 · **Live-вызовы:** 0 (все тесты на fake transport / записанных фикстурах)

## Тесты

| Прогон | Команда | Результат |
|---|---|---|
| Новые provider-тесты | `venv/bin/python -m pytest tests/job_intel/test_semantic_llm_provider.py -q` | **34 passed** |
| Полный существующий suite | `venv/bin/python -m pytest tests/job_intel/ -k "semantic or shadow_evaluator or preference_model or vacancy_understanding" -q` | **184 passed** (150 прежних + 34 новых, 0 failed) |

Покрытие обязательной матрицы §13 задания:

- **Protocol/model:** структурная конформность Protocol; `list[Observation]`; extra-поля отклоняются (`extra="forbid"`); стабильные provider_id/prompt_version. ✔
- **Valid response:** валидный structured response проходит Stage 3 с 0 rejected; пустой список валиден; порядок и ids сохраняются. ✔
- **Evidence safety:** non-verbatim, wrong location, unknown fact, invalid enum, unresolved maps_to, duplicate id — отклоняются существующим Stage 3 с точными кодами; overlong excerpt = schema_invalid на парсинге; `fact=unknown` = явный provider failure. ✔
- **Basis safety:** только explicit/direct/weak; числовые значения и «high»/«certain» отклоняются; model self-confidence не имеет канала в выход. ✔
- **Failure behaviour:** malformed JSON / schema-invalid / transport error / timeout = явные `LLMProviderError` с reason-кодами; нет silent empty-list; нет fallback на phrase provider или другую модель (поведенчески доказано + import-guard). ✔
- **Determinism/replay:** повторный replay байт-стабилен (`semantic_dump()` идентичен); replay-провайдер физически не может держать transport (0 network); model/prompt mismatch детектируется; порча записи (`response_hash`) = `recording_corrupt`; recording содержит все обязательные метаданные и не содержит секретов. ✔
- **Boundary regression:** одинаковые observations от phrase- и LLM-провайдера дают идентичные fragment/conflicts/clarifications — в конвейере нет provider-specific ветвлений; запрещённые импорты отсутствуют. ✔

## Conformance checklist (A–G)

- **A. Интерфейс и границы** — да по всем пунктам (Protocol, изоляция в одном модуле, ноль сайд-эффектов кроме RecordingStore-артефактов, только переданный вход, только Observation[]).
- **B. Observations** — да (уникальные стабильные ids; verbatim ≤400; signal_type из машинного словаря; enrichment_only не эмитится промптом и режется Stage 3; maps_to валиден; interpretation 1–2 предложения без CoT; unknown = отсутствие observation; rejected-rate на валидных фикстурах = 0).
- **C. Confidence** — да (basis = класс evidence; числа/logprobs не проходят схему).
- **D. Prompt/версии** — да (промпт внутри модуля; не переопределяет контракт; prompt_version зафиксирован; model version пиннится и записывается).
- **E. Детерминизм и replay** — да в offline-scope (record/replay, temperature 0, hash-стабильность повторного replay). Live-часть (стабильность на реальных ответах) — после spend approve.
- **F. Benchmark-метаданные** — да (usage/latency/retry_count/model/prompt в recording и last_call_metadata; для fake transport значения фиктивные, живые появятся в smoke).
- **G. Границы изменений** — да: `git diff` не содержит ни одного изменённого существующего файла — только новые (`llm_provider.py`, тесты, артефакты). Runtime/evaluator/SoT нетронуты.

**H** — частично по определению Step 5A: contract compliant = yes (offline), replay reproducible/calibration = после approve, benchmark = Step 5B, recommendation = Step 5C.

## Вывод

Offline-конформность доказана. Готов к Spend Gate (Slice 5A-4).
