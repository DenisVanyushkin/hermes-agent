# Shadow Evaluator — Historical Replay Report (run `replay-20260719-step3`)

**Дата:** 2026-07-19. Read-only; артефакты:
`artifacts/shadow-evaluator/replay/replay-20260719-step3/` (run-metadata,
case-results.jsonl, summary.json, disagreements.jsonl, FN/FP review queues,
clarification-summary). Snapshot hash `c36413544a25c43b`. Версии: contract
1.1.0 / preference 1.0.0 / vacancy schema 1.0.0 / evaluator 0.1.0 /
extractor 0.1.1.

## 1. Кохорта

63 уникальные вакансии с активной реакцией реального пользователя
(`vacancy_feedback_state.active=1`); исключены test/smoke users,
resend-дубли (collapse по company+normalized title), data-quality шум.
Live DB читалась строго read-only (`mode=ro`).

## 2. Метрики (без единого агрегата)

| Метрика | Значение |
|---|---|
| Recommendation distribution | unclear 59, not_recommended 4 |
| Action distribution | investigate 59, reject 4 |
| Positive precision by band | unclear 0.254; not_recommended 0.0 (то есть ни одного позитива отклонено ошибочно среди классифицируемых) |
| Recall applied/exceptional/interesting | 0.0 — см. интерпретацию §3 |
| Negative precision | 1.0 |
| Infeasible precision | 1.0 (4/4 infeasible = 👎; все — `fc_usa_onsite_requires_explicit_sponsorship`) |
| Unclear rate | 0.937 |
| Lane distribution | core 63 (fallback-кейсов в реакционной кохорте нет) |
| Explanation coverage | 0.111 (items появляются только при известных фактах) |
| Top blockers | fc_usa_onsite_requires_explicit_sponsorship ×4 |
| Top clarifications | mandate.scope_breadth ×63, revenue ×63, company ×63, work_format ×44, country ×19, source_text ×18 |
| Critical FN / FP | 0 / 0 |

## 3. Интерпретация (главный вывод)

Кохорта оценивалась на **deterministic-only** канонических записях
(semantic extraction по плану Step 2 отложен). Поэтому mandate почти всегда
unknown → evaluator честно отвечает **unclear + clarifications** вместо
галлюцинированных вердиктов. Recall 0.0 — это свойство ВХОДНЫХ ДАННЫХ, не
контракта: ни один позитив не был отвергнут (0 critical FN); там, где фактов
хватало (USA onsite без sponsorship), шедоу дал terminal reject с precision
1.0. Классификация 62/63 = `insufficient_vacancy_evidence` — корректное
применение таксономии.

**Следствие:** содержательная калибровка качества (O3) возможна только после
semantic-extraction слайса; до него replay подтверждает (а) корректность
терминальной feasibility-политики на реальных данных, (б) отсутствие
«тихих» вердиктов при нехватке evidence, (в) детерминизм (semantic_hash
стабилен между прогонами).

## 4. Full-text recovery (флагманы, O3)

| Кейс | Статус | Артефакт |
|---|---|---|
| Wise APAC Growth & Expansion (5919) | **recovered_full_text** (4752 симв.) | artifacts/shadow-evaluator/recovered-wise/wise_5919.json |
| Wise Pricing (1728) | recovered_full_text (4661) | wise_1728.json |
| Wise Acquiring (4510) | recovered_full_text (3860) | wise_4510.json |
| Wise Financial Crime (1813) | recovered_full_text (4102) | wise_1813.json |
| Wise Onboarding (1773) | recovered_full_text (4788) | wise_1773.json |

Источник — публичный SmartRecruiters API по сохранённым URL (без логинов,
без обхода anti-bot, без mutating-запросов). Тексты хранятся ТОЛЬКО как
offline-артефакты (в git не коммитятся — copyright/retention); в отчёте
зафиксированы sha16. Ни в одном тексте нет relocation/visa-формулировок —
подтверждает honest-uncertain для SG-вариантов. Re-annotation фикстур по
полным текстам — вход следующего слайса перед calibration.

## 5. Replay-acceptance статус

По O3 replay acceptance НЕ объявляется завершённым: он требует semantic
extraction + re-annotated флагманов. Настоящий отчёт фиксирует
инфраструктурную готовность replay-контура и валидность терминальной
политики.
