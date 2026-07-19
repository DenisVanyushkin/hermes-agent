# Shadow Evaluator — Evidence & Ambiguity Inventory (SoT Slice A)

**Дата:** 2026-07-19. Вход для Decision SoT (Step 3A). Кода нет.

## 1. Reviewed source map

| Источник | Роль | Ключевое для решений |
|---|---|---|
| process SoT (`job-intel-career-preference-system-development-sot.md`) | приоритетный процессный контракт | §4 целевые блоки вердиктов; §7 acceptance criteria; §8 change control |
| Step 1 `career-preference-model.yaml` + `model.py` | политика предпочтений | 9 feasibility constraints, 8 interaction rules (priority 10–80), anti-prefs company/role × strong/soft, KZ lane, comp/timezone политики |
| Step 1 contract md §8 | оговорки | exploration без provenance; feasibility `strength` — описательная, НЕ вес; gate/allow/route_to_fallback декларативны — **runtime-семантика обязана появиться здесь** |
| Step 2 `vacancy_understanding/model.py` + feature dictionary | фактическая сторона | Fact{value,confidence,method,evidence}; unknown first-class; риски ≠ штрафы |
| Step 2 fixtures (21) | материал для golden decision cases | Wise-кейсы title-only (source_text_incomplete) |
| Research/audit 2026-07-19 | поведенческая калибровка | lifts; save_for_later = «нравится × сомнительная реализуемость»; L1–L9 |
| Legacy `evaluator.py`, `scoring.yaml`, `feedback_taxonomy.py`, `digest.py` | только constraints/history | пороги 90/75/60/40, numeric веса — НЕ авторитетны |

## 2. Policy ambiguity list (что не определено существующими SoT)

A1. **Словарь recommendation.** Process SoT §4: `apply|investigate|save|exploration|reject`; задание 3A: `exceptional|strong|promising|unclear|not_recommended`. Конфликт версий словарей (см. legacy conflicts C5). Требует owner-поправки process SoT §8.
A2. **Uncertain feasibility → потолок?** Step 1 даёт verdict, но не его влияние на recommendation.
A3. **Комбинаторика fit'ов** (mandate × company) нигде не задана.
A4. **Unknown-эскалация:** когда unknown понижает confidence, а когда меняет label.
A5. **Суммирование soft concerns** без числовых весов — порог/правило отсутствует.
A6. **Title-only вакансии:** Step 2 честно даёт unknown-семантику; влияние на recommendation не задано. Напряжение с acceptance-критерием «Wise APAC стабильно в верхнем band» (флагман в БД title-only).
A7. **Crypto employer:** Step 1 говорит «company concern / exploration, не role veto» — но не задаёт cap.
A8. **KZ fallback recommendation vocabulary** (отдельный словарь vs lane-маркер) не выбран.
A9. **Exploration eligibility** для big_tech/early_startup: Step 1 помещает их в `direct_questions_not_exploration` → до ответа владельца не exploration-оси.
A10. **Confidence-модель секций** (пропагация факт→секция→overall) не существует.
A11. **Work format unknown** при не-домашней стране: Step 1 constraints матчатся только на onsite/hybrid — unknown проскальзывает в feasible. Нужна unknown-политика поверх (не правка Step 1).
A12. **Порог заполненности evidence для exceptional** не определён.

## 3. Legacy conflict list

C1. Numeric score/thresholds (90/75/60/40) — не переносятся; сравнение только как evidence.
C2. `fintech_or_telecom` +15/+18, title-бонусы — deprecated; ожидаемые расхождения replay помечать `expected_architecture_change`.
C3. Двойной RU/BY geo-штраф — заменён одним constraint.
C4. Legacy `recommendation` (strong_fit/potential_fit/near_miss/needs_review/reject) — сравнительная шкала replay, не словарь Step 3.
C5. Прежние keyword-«matched_signals» не имеют evidence → в replay классифицировать insufficient_vacancy_evidence, а не чинить совместимостью.
C6. Feedback-коды (1–8) — многозначны (напр. «4 company_quality» для OKX содержал и доменный барьер) → replay: feedback = evidence, не ground truth.

## 4. Proposed decision dimensions (входы Decision SoT)

1. Четыре выхода: feasibility(+lane), mandate_fit, company_fit, overall_recommendation(+confidence).
2. Порядок: validate → lane → feasibility (+interactions) → [terminal if infeasible] → mandate (+interactions) → company (+interactions) → confidence/unknowns → matrix → explanation/clarifications.
3. Precedence-оси: verdict-merge; rule-priority (Step 1 int, меньше=раньше, later-no-reversal); evidence-иерархия; unknown-политика по семействам полей.
4. Результаты-примитивы: blocker / concern / support (+ suppressed в trace).
5. Caps-механизм вместо чисел: словарные потолки recommendation (uncertain, incomplete-text, crypto, company-unknown).
