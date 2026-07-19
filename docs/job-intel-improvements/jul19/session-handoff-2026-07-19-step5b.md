# Handoff — Job Intel Semantic Provider Benchmark (сессия 2026-07-19, Step 5B)

Скопируй это в новый чат как стартовый контекст.

---

Продолжаем **Job Intel Career Preference System** (hermes-agent), сейчас в фазе **Phase II — Semantic Provider Benchmark** (Roadmap SoT §9). Предыдущая сессия 2026-07-19 закрыла Step 5A и выполнила Slice 5B-0/5B-1. Этот промпт — передача контекста для следующего слайса.

## Рабочий ритуал (обязателен)

- Канонический хост: `ssh hermes-agent`, репо `/home/hermes/.hermes/hermes-agent`, ветка `local/customizations`. Локальные копии — не Source of Truth.
- Коммиты на remote делаем (bounded, по слайсам), **push НЕ делаем**. Gateway не рестартуем, live config/БД не трогаем, Slack/Telegram не шлём, **платные provider-вызовы запрещены без отдельного owner approve** (spend gate report → явный `APPROVE_..._WITH_CAP_$N` в чате).
- После каждого слайса: полный regression (`venv/bin/python -m pytest tests/job_intel/ -k "semantic or shadow_evaluator or preference_model or vacancy_understanding" -q`), `git diff --check`, working tree clean, один docs/code коммит на слайс.
- Артефакты слайсов зеркалируются локально в `docs/job-intel-improvements/jul19/step-5a-artifacts/` (общая папка для Step 5A и Step 5B).
- Задания приходят текстом от владельца по одному слайсу за раз; каждый слайс кончается конкретным exit-gate вердиктом и **остановкой** — следующий слайс не начинается автоматически.

## HEAD и коммиты этой ветки работы

```
d328f185a2  Slice 5B-1 — provider-agnostic benchmark runner        ← HEAD
c3b79fde6f  Slice 5B-0 — benchmark contract and metric definitions
29fe6177c4  Step 5A closure report + Step 5B readiness gate
9b52bd659b  empirical codex probe (json_schema ignored, temp dropped, model echoed)
e492b0c05b  Step 5B transport research — KEEP_OPENROUTER_AS_CANONICAL
a21c94402a  Step 5A-5 — $defs schema fix + live smoke report (15/15, $0.0911)
8c981ea40a  Step 5A-4a — pre-smoke transport integrity (model identity/retry/fallback)
a29a5f8749  Step 5A — minimal LLM observation provider (offline, spend-gated)
77c030a8d8  Provider Contract SoT (Step 5A.0)
f3e8483b01  Roadmap v2 — Phase II Provider Benchmark
```

Working tree чистый на всех коммитах; `git diff --check` чистый.

## Архитектурная цепочка контрактов

```
Career Preference SoT → Vacancy Semantic Contract (Step 4A) → Provider Contract (Step 5A.0)
    → Runtime (Step 4B) → Decision SoT (Step 3A) → Benchmark Contract (Step 5B-0) → [этот слайс]
```

Все SoT неизменны с момента их принятия в этой цепочке.

## Что сделано в Phase II (эта и предыдущие сессии)

1. **Roadmap v2 (`f3e8483b01`)** — Phase I объявлена архитектурно завершённой; roadmap: Step 5A (LLM Provider) → **Step 5B (Provider Benchmark)** → Step 5C (Provider Selection) → Phase III Shadow → Production.
2. **Provider Contract 1.0.0 (`77c030a8d8`)** — нормативный контракт реализации провайдера + checklist.
3. **Step 5A — LLM Observation Provider, ЗАКРЫТ.** `job_intel/vacancy_understanding/semantic/runtime/llm_provider.py`: `LLMObservationProvider` (`provider_id=llm-observation`, **`prompt_version=llm-obs-1.0.0` — заморожен как benchmark baseline, не трогать**), model `openai/gpt-5-mini` через OpenRouter, record/replay, model-identity verification (exact slug / dated snapshot only), `max_retries=0`, `allow_fallbacks=false`. Live smoke 15/15, $0.0911 (cap был $1), 0 verbatim-нарушений, live↔replay 15/15 match с физически отключённой сетью. Вердикт: `STEP_5A_COMPLETE_READY_FOR_CROSS_PROVIDER_BENCHMARK`.
4. **Transport research** — codex-транспорт (ChatGPT-подписка) **эмпирически** дисквалифицирован для benchmark (live probe, `9b52bd659b`): `response_format json_schema` молча игнорируется, `temperature` молча отбрасывается, `response.model` = эхо запроса (не факт), `gpt-5-mini` вообще недоступен на codex-бэкенде. Вердикт: `KEEP_OPENROUTER_AS_CANONICAL`.
5. **Step 5B-0 (`c3b79fde6f`)** — `docs/job-intel-improvements/jul19/step5b-benchmark-contract.md`: нормативные формулы precision/recall (matching policy exact/compatible/partial/mismatch), evidence coverage (4 различающие метрики), reproducibility (5 проверок, live-repeat ≠ replay), cost/latency states (`known_zero/known_value/unknown/not_applicable`), error taxonomy (12 кодов). 3 открытых вопроса владельцу были решены им же в задании на 5B-1 (compatible-match derivation approved, ambiguous labels = authoritative gold, unsupported-evidence review policy зафиксирована).
6. **Step 5B-1 (`d328f185a2`)** — provider-agnostic benchmark runner: новый пакет `job_intel/vacancy_understanding/semantic/benchmark/` (models/hashing/provider_registry/compatible_match/runner). Provider-branching изолирован ТОЛЬКО в `provider_registry.py` (доказано source-scan тестом). Manifest (24 поля) + CaseResult (22 поля), atomic write, resume заблокирован по 7 осям identity. `compatible_match.py` — read-only деривация equivalence-классов из 36-cell Decision SoT матрицы. `replay_full.py`/`replay_flagships.py` получили опциональный `provider=` (дефолт не изменён). 25 новых тестов, полный suite 227 passed. **Cost formula, aggregation (percentiles), precision/recall — намеренно НЕ реализованы в этом слайсе** (см. §Known gaps).

## Полный план Step 5B (из задания владельца, ещё не пройденные слайсы)

```
5B-0 Benchmark Contract               ✅ DONE (c3b79fde6f)
5B-1 Common Runner Infrastructure     ✅ DONE (d328f185a2)
5B-2 Cost and Latency Instrumentation ← СЛЕДУЮЩИЙ
5B-3 Offline Deterministic Baseline (175 controls + 21 gold + 25 decision + 3626 eligible, offline, $0)
5B-4 LLM Calibration Live Run         ← требует spend gate approve (175 controls)
5B-5 Calibration Comparison Report    → LLM_HISTORICAL_REPLAY_RECOMMENDED / NOT_JUSTIFIED / INCONCLUSIVE
5B-6 Bounded Historical LLM Benchmark ← требует spend gate approve (200 стратифицированных vacancies)
5B-7 Full Historical LLM Benchmark    ← требует ОТДЕЛЬНЫЙ spend gate approve (3626 eligible, ~$5.5 оценка)
5B-8 Final Cross-provider Benchmark Report → Step 5C evidence package (НЕ выбор победителя)
```

Правило: каждый слайс = отдельный коммит, останавливаться после exit-gate, не начинать следующий без нового задания владельца.

## Known gaps, оставленные для Slice 5B-2 (из step5b-common-runner-report.md)

1. **Cost formula не реализована** — `cost_usd` для LLM-кейсов = `None`/`not_applicable` намеренно; token→price маппинг = 5B-2.
2. **Aggregation отсутствует** — нет percentiles, precision/recall, `provider_benchmark_summary.json`.
3. **Compatible-match артефакт существует, но не используется** — вычислен и захеширован в manifest, но matched/mismatched verdict по нему не считается (это 5B-5).
4. **Live latency vs replay latency не сведены** — live-latency живёт в recording (`recording_path`), но не surfaced как отдельная серия.

## Важные технические детали для следующей сессии

- **Provider construction boundary — жёсткое правило.** Единственный файл, которому разрешено знать `provider_id in {"deterministic-phrase","llm-observation"}` — `job_intel/vacancy_understanding/semantic/benchmark/provider_registry.py`. Он возвращает `identity: dict` с уже вычисленными policy-полями (`retry_policy`, `fallback_policy`, `cost_known_zero`, `reports_usage_metadata`) — `runner.py` читает их, никогда не сравнивает `provider_id` сам. Это ловится тестом `test_no_provider_branch_in_runner_or_runtime` — если добавляешь новую provider-зависимую логику в runner/pipeline, сначала выведи её в registry.
- **NumericState-дисциплина обязательна** для любого нового числового поля метрик (`known_zero | known_value | unknown | not_applicable`) — не путать "не измерено" с "равно нулю" (см. `models.py`).
- **LatencyMode** (`deterministic | replay | live`) — replay-latency и live-latency никогда не смешиваются в одну percentile-серию (contract §7); в 5B-2 при вводе live-режима не смешивай их.
- **Manifest resume** блокируется по 7 полям (`dataset_hash, provider_id, provider_version, provider_config_hash, prompt_version, metric_contract_hash, decision_matrix_hash`) — если меняешь benchmark contract или decision matrix между слайсами, старые manifest-ы автоматически инвалидируются (это фича, не баг).
- **`prompt_version=llm-obs-1.0.0` заморожен.** Найденные ограничения (signal-prefix confusion, 11/96 rejected на smoke, 2 натянутые интерпретации — см. `step5a-closure-report.md`) — это benchmark findings, не поводы чинить промпт. Правка промпта = новая версия `llm-obs-1.1.0` и новый benchmark identity, только после 5B-5/5C решения владельца.
- **OpenRouter — canonical transport**, решение подкреплено live-пробой codex (см. `openai-transport-analysis.md` §5). Не пытайся переключать транспорт без нового research-задания.
- **Spend gates.** На каждый платный слайс (5B-4, 5B-6, 5B-7) нужен отдельный `APPROVE_..._WITH_CAP_$N` от владельца в чате — готовь spend gate report как в Step 5A (`step5a-spend-gate.md` — шаблон).

## Ключевые пути

| Что | Путь |
|---|---|
| Roadmap SoT | `docs/job-intel-improvements/jul19/job-intel-career-preference-system-development-sot.md` (§9) |
| Provider Contract / Checklist | `docs/job-intel-improvements/jul19/semantic-provider-contract.md` / `-checklist.md` |
| Benchmark Contract | `docs/job-intel-improvements/jul19/step5b-benchmark-contract.md` |
| Step 5A closure | `docs/job-intel-improvements/jul19/step5a-closure-report.md` |
| Step 5B input inventory / gaps | `docs/job-intel-improvements/jul19/step5b-input-inventory.md`, `step5b-readiness-gaps.md` |
| Common runner report | `docs/job-intel-improvements/jul19/step5b-common-runner-report.md` |
| LLM provider код | `job_intel/vacancy_understanding/semantic/runtime/llm_provider.py` |
| Benchmark package | `job_intel/vacancy_understanding/semantic/benchmark/{models,hashing,provider_registry,compatible_match,runner}.py` |
| Benchmark тесты | `tests/job_intel/test_semantic_benchmark_runner.py` |
| Deterministic provider | `job_intel/vacancy_understanding/semantic/runtime/provider.py` |
| Decision SoT / матрица | `job_intel/shadow_evaluator/decision-contract.yaml` (v1.1.0, 36-cell matrix) |
| Semantic Contract | `job_intel/vacancy_understanding/semantic/semantic-fact-contract.yaml` (v1.0.0) |
| Smoke-артефакты (recordings) | `artifacts/semantic-llm/smoke-20260719/` (на remote, вне git) |

## Memory-файлы агента (сверяйся)

- `llm-observation-provider-step5a.md` — полная хронология Step 5A + research + 5B-0/5B-1 (актуализирован).
- `phase2-roadmap-sot-update.md` — Roadmap v2.
- `semantic-runtime-step4b.md` — предыдущая фаза.

## Следующее задание

Владелец пришлёт текст задания на **Slice 5B-2 — Cost and Latency Instrumentation** (или другой слайс по своему решению). Ознакомься с этим handoff, прочитай упомянутые SoT/отчёты, дождись задания и приступай.
