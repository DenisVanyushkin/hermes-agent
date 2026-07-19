# Shadow Evaluator — Disagreement Analysis (run `replay-20260719-step3`)

**Таксономия:** только 10 утверждённых классов Decision SoT §15.

## 1. Распределение

| Класс | n | Комментарий |
|---|---|---|
| insufficient_vacancy_evidence | 62 | deterministic-only вход: mandate/company unknown → honest unclear; включает 18 кейсов с source_text_incomplete |
| legacy_false_positive | 1 | N26 «Head of Product - Lending»: legacy strong_fit (band 90–100) → 👎 пользователя; shadow честно даёт unclear (mandate moderate по head-of титулу, semantic-факты отсутствуют) — legacy-скоринг завысил, shadow не повторил ошибку |
| shadow_possible_false_negative | 0 | ни один позитив не отвергнут |
| shadow_possible_false_positive | 0 | ни один негатив не получил strong/exceptional |
| прочие классы | 0 | preference/vacancy/contract gaps не выявлены на этом входе |

## 2. Manual review queues

`critical-false-negatives.md` / `critical-false-positives.md` — пусты (0/0);
`disagreements.jsonl` — 63 записи для ручного просмотра (по формату задания:
vacancy, legacy, shadow, feedback, facts, rules, caps, unknowns, explanation,
suggested class). Автоматических изменений контракта нет; обучения нет.

## 3. Выводы для следующих слайсов

1. **Semantic extraction — единственный разблокирующий шаг** для
   содержательного disagreement-анализа: 98% кейсов упираются в отсутствие
   mandate-фактов, а не в политику.
2. Терминальная feasibility-политика подтверждена реальными данными: USA
   onsite без sponsorship — 4/4 совпадения с 👎 (инфизибельность объясняет
   исторические отклонения точнее legacy-скоринга, который слал их с
   score 68–95).
3. Clarification-топ (scope×63, revenue×63, company×63) — готовое ТЗ на
   приоритет semantic-полей Step 2 provider-слайса.
4. Ни одного `decision_contract_gap` — контракт исполним без изобретения
   политики (критерий успеха Step 3A подтверждён на живых данных).
