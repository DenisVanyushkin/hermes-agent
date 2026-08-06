# Handover — Hermes job-intel: Phase III + §7.2 (2026-08-06)

Скопируй в новый чат как стартовый контекст.

## Где мы

`Phase I ✅ → Phase II ✅ (Step 5C: выбран deterministic) → **Phase III** → Production`

Хост: `ssh hermes-agent` (это пользователь hermes; `ssh hermes` = root и ломает `~`).
Репо `/home/hermes/.hermes/hermes-agent`, ветка `local/customizations`.
Тесты: `sudo -u hermes venv/bin/python -m pytest tests/job_intel/ -k 'semantic or shadow or vacancy_understanding or mandate' -q`
Правки: локально → `rsync` → `chown hermes:hermes` → тест/коммит под `sudo -u hermes`.

## Что работает в проде прямо сейчас

- **Stage 0 (observe-only shadow)** — LIVE. `job-intel-semantic-shadow.timer` 09:30 UTC после daily (08:00). Прогоняет каждую вакансию через deterministic provider → semantic runtime → shadow evaluator, пишет в `semantic_shadow_evaluation`. Флаг `SEMANTIC_SHADOW_ENABLED` (default ON).
- **Stage 1 (soft feasibility advisory)** — LIVE, проверено в Slack. Отдельное сообщение в `#executive_search_report` (C0B4MM6D52A) с feasibility-оговорками по показанным ролям. Флаг `SEMANTIC_SHADOW_ADVISORY_ENABLED=1` в `/etc/job-intel/job-intel.env`, второй `ExecStart` того же сервиса.
- Откат обоих — один флаг; пользовательские решения не меняются.

## Решения владельца (не пересматривать без него)

- **Step 5C:** deterministic-phrase — канонический провайдер. LLM отклонён на mandate-оси (проверен дважды: базовый промпт и итерация 1.1.0, обе неудачны). 5B-6/5B-7 не запускать.
- **Ворота A2/B1/C2:** §7.2 отложен → 2-нед drift-report → Stage 1 узко (advisory, не фильтр). Все три отработаны.
- **§7.2 метод:** майнинг корпуса. **Критерий приёмки:** флагманы (GPNI, Wise APAC) в верхнем band воспроизводимо **И** `why_attractive` ≥60% на показанных ролях.

## Stage 2 — заблокирован

Готов только T1 (захват explanations, колонка `explanation_json`). Замер: `why_attractive` есть лишь у **18%** показанных ролей → рендер намеренно не строил. План: `docs/job-intel-improvements/jul19/phase3-stage2-plan.md`. Разблокируется закрытием §7.2.

## §7.2 — текущий фронт работ

**Диагноз (замером):** правила `DeterministicPhraseProvider` написаны под синтетические контрольные фразы контракта → 158/158 контролей проходят, а на живой вакансии ~7000 знаков медиана **1 наблюдение**. Валидация была замкнута сама на себя. Текст не виноват (медиана 6987, обрезаны 2/40).

**Сделано и в проде (ветка local/customizations, всё зелёное):**
- T1 сплит DEV/HOLDOUT 70/30 по хэшу ключа (`mandate_coverage.assign_split`) — не по порядку, иначе растущий корпус переназначает строки.
- T2 метрика покрытия на реальном корпусе (`coverage_report`) — недостающий не-циклический гейт.
- **BASELINE: DEV 6.90% / HOLDOUT 6.60%**; из 13 мандатных фактов стреляли 2.
- T3 майнер (`mandate_mining.py`) — DEV-only, жёстко (holdout → `HoldoutAccessError`).
- T4 негативные фикстуры: 22 ❌-строки из ручного ревью владельца → `tests/fixtures/vacancy_understanding/mandate_negative_fixtures.json`.
- T5 раунд 1: 11 правил под реальные конструкции.

**Важно:** целевая популяция в DEV = **52 из 2956** (прод показывает ~50 из ~3650). Частотное ранжирование невозможно (везде n=1) — отбор кандидатов идёт ручным чтением малого корпуса примеров.

**Результат раунда 1 ОТОЗВАН.** Покрытие выросло 6.9%→40.7% (holdout 41.75%, подгонки нет), но самопроверка срабатываний показала массовые ложные: `team_build_mandate` ловит боилерплейт «scale and solve them as a team» у рекрутёра/дата-инженера/AI-инженера; `strategy_ownership` — «drive a Talent Acquisition strategy» у рекрутёра. Это recall ценой precision — тот самый провал, за который дисквалифицировали LLM.

## ПЕРВАЯ ЗАДАЧА НОВОЙ СЕССИИ

Ветка **`s72/duty-scoped-rules`** (не влита) содержит починку обоих структурных дефектов:
1. провайдер матчит мандатные паттерны **только внутри duty-предложений** (`_duty_spans`/`_within`/`_DUTY_SCOPED` в `provider.py`);
2. `coverage_report(target_only=True)` меряет правильную популяцию;
3. в майнере — исключение для «candidate opening» (иначе «Own **user** acquisition» читалось как подлежащее *user*).

7 новых тестов зелёные. **Осталось 3 падающих теста синтетических контролей** (`test_all_synthetic_controls_pass`, `test_uncovered_controls_are_zero`, `test_calibration_controls_still_all_pass`) — контроли строились под старое «матчить где угодно». Их надо разобрать поштучно: какие контрольные фразы перестали проходить duty-фильтр и что правильнее — поправить фильтр или признать контроль нерепрезентативным.

Порядок: починить 3 контроля → влить ветку → **перемерить покрытие** (ожидать ПАДЕНИЯ с 40.7%, зато впервые доверяемого, и мерить с `target_only=True`) → выборочная проверка точности → раунд 2 правил для фактов, которые всё ещё на нуле (board_exposure, org_design_mandate, monetization_core, acquiring_core, expansion_mandate).

## Ключевые уроки, которые легко потерять

- **Барьеры импорта — 4 guard-теста.** Правило: никогда не ослаблять guard ради прохода — переносить код в разрешённую зону. `shadow_deploy.py` живёт в `job_intel/vacancy_understanding/` именно поэтому (в `semantic/runtime/` нельзя — там запрет на импорт shadow_evaluator).
- **Доставка в Slack идёт через hermes gateway**, не webhook (`JOB_INTEL_SLACK_WEBHOOK_URL` пуст). `tools.send_message_tool`, target `slack:<channel>`.
- **Никогда не рапортовать успех доставки без проверки** — был баг, печатавший «posted» при провале.
- **Артефакты бенчмарка — вне репозитория** (`/var/lib/job-intel/benchmark-artifacts`), после инцидента, когда upstream-sync снёс untracked `artifacts/` с платными записями.
- Метрики: **6.9%** (весь корпус) и **18%** (`why_attractive` на показанных) — разные вещи, не путать.

## Документы

`docs/job-intel-improvements/jul19/`: `s72-mandate-extraction-plan.md` (§8 план), `s72-baseline.md`, `s72-mining-round1-findings.md`, `s72-mining-round2-findings.md`, `s72-round1-results.md` (с CORRECTION), `phase3-stage2-plan.md`, `step5c-decision-and-phase3-entry.md`.
