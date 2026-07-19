# Semantic Control Coverage Report (Step 4B closure, Part 1)

**Дата:** 2026-07-19. Машинный артефакт:
`artifacts/shadow-evaluator/semantic-control-coverage.json` (запись на каждый
из 175 контролей: fact, kind, статус, причина не-generic исполнимости,
требуемая конструкция, путь+имя покрывающего теста).

## Итог аудита 39 прежних exemptions

| Категория | Было | Стало | Решение |
|---|---|---|---|
| unknown-контроли без цитат («no mention → unknown») | 32 exempt | **generic_pass** | Аудит признал прежний exempt дефектом runner'а, а не структурной невозможностью: сценарий исполняется нейтральным текстом без сигналов; runner расширен, все 32 исполняются и проходят |
| conflicting-контроли с абстрактными парами («acquiring title + devex duties») | 7 exempt | **equivalently_covered** | Однотекстовый generic-runner структурно не выражает парные наблюдения, описанные не-фразовыми словами; покрыты параметризованным specialized-тестом `test_semantic_control_coverage.py::test_conflicting_pair_controls`, конструирующим ровно те пары наблюдений (title-weak true × body-direct false) и проверяющим не-позитивную резолюцию + трассировку конфликта |
| risks.title_scope_mismatch ×5 | fact-exempt | **equivalently_covered** | требует title+body пары: `::test_title_scope_mismatch_controls` (positive → риск; negative → нет риска; title-only → нет риска) |
| mandate.mandate_summary ×5 | fact-exempt | **equivalently_covered** | free-text синтез; контракт делает emission опциональным — binding-инварианты (никогда не сфабрикован без evidence, никогда не содержит desirability-слов) покрыты `::test_mandate_summary_invariants`; текущий runtime консервативен (summary не синтезируется) |

## Acceptance

```
total_controls        175
generic_pass          158
equivalently_covered   17
uncovered               0   ✓
```

Gate-тест `test_uncovered_controls_are_zero` проверяет: uncovered==0, каждый
equivalently_covered ссылается на существующий файл и существующее имя теста.

## Соблюдение правил аудита

- Provider recall не использовался как основание exemption: все 32
  «неисполнимых» unknown-контроля переведены в исполняемые (и прошли), а не
  списаны на провайдера.
- Step 4A semantics не менялись; phrase-правила не добавлялись (провайдер не
  тронут в этом слайсе).
