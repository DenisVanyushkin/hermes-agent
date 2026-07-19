# Step 5A — Slice 5A-1: Reconnaissance Report

**Дата:** 2026-07-19 · **Host:** hermes-agent · **Repo:** /home/hermes/.hermes/hermes-agent · **Branch:** local/customizations · **Starting HEAD:** 77c030a8d8

## Canonical SoT paths (прочитаны)

| SoT | Path | Версия |
|---|---|---|
| Process/Roadmap SoT | `docs/job-intel-improvements/jul19/job-intel-career-preference-system-development-sot.md` | Roadmap v2, §9 (2026-07-19) |
| Provider Contract | `docs/job-intel-improvements/jul19/semantic-provider-contract.md` | 1.0.0 |
| Provider Checklist | `docs/job-intel-improvements/jul19/semantic-provider-checklist.md` | 1.0.0 |
| Semantic Contract (4A) | `job_intel/vacancy_understanding/semantic/semantic-fact-contract.yaml` | 1.0.0 |
| Decision SoT (3A) | `job_intel/shadow_evaluator/decision-contract.yaml` | 1.1.0 |
| Step 4B closure | `docs/job-intel-improvements/jul19/semantic-step4b-closure-report.md` | STEP_4B_COMPLETE_WITH_PROVIDER_RECALL_LIMITATION |

## Existing extension points

- **`SemanticProvider` Protocol** (`runtime/models.py`): `provider_id`, `prompt_version`, `extract_semantic_observations(*, title, text, structured) -> list[Observation]`. Не runtime_checkable — конформность структурная.
- **`Observation`** — 7 полей, `extra="forbid"`, excerpt ≤ 400 (лимит зашит и в модель, и в Stage 3).
- **Gated stub `LLMProvider`** (`runtime/provider.py`) — placeholder «fail loudly»; оставлен нетронутым (его охраняет `test_llm_provider_is_gated`). Новый провайдер — отдельный модуль.
- **Stage 3 валидация** — 8 rejection-кодов; runtime уже отбрасывает всё неконформное. Runtime changes НЕ требуются.
- **Calibration/replay runners** (`runtime/calibration.py`, `replay_full.py`, `replay_flagships.py`) — принимают любой provider-объект; для Step 5A-6 достаточно параметризовать провайдером (отдельный будущий слайс запуска, не изменение runners).

## Existing LLM transport (переиспользован)

`agent/auxiliary_client.resolve_provider_client(provider, model)` — центральный роутер, возвращает OpenAI-совместимый клиент (`.chat.completions.create()`). Паттерн structured output (`response_format={"type":"json_schema", ...}`, `temperature=0`) уже используется в `hermes_cli/legal_review_gate.py` и recruiter-executors. `OPENROUTER_API_KEY` присутствует в `~/.hermes/.env`.

Проверки против Provider Contract §8/§9:
- fallback: при явных `provider="openrouter"` + `model=<pinned>` роутер модель не подменяет; провайдер дополнительно сверяет resolved_model == requested (иначе `model_version_mismatch`). OpenRouter-роутинг между upstream-хостами одной и той же модели остаётся (сама модель фиксирована); при необходимости для smoke можно добавить `provider.allow_fallbacks=false` в extra_body — решение зафиксировать на spend gate.
- raw response: доступен (`choices[0].message.content` + метаданные ответа);
- usage: `response.usage` (prompt/completion/total tokens);
- retries: OpenAI SDK по умолчанию делает 2 тихих ретрая → капируется `client.with_options(max_retries=1)` (единственный допустимый technical retry, transport-уровень).

Второй параллельный transport НЕ создан.

## Предложение

- **provider_id:** `llm-observation`
- **prompt_version:** `llm-obs-1.0.0`
- **model (предложение, платится только после spend gate):** `openai/gpt-5-mini` через OpenRouter — строгий structured output, temperature 0, дешёвый для calibration/replay, точный идентификатор фиксируется из ответа.
- **Минимальный file diff:** 1 новый модуль `job_intel/vacancy_understanding/semantic/runtime/llm_provider.py`, 1 новый тестовый файл `tests/job_intel/test_semantic_llm_provider.py`, артефакты. Ноль изменений существующих файлов.

## Риски

1. Verbatim-дисциплина LLM — главный риск качества; митигируется Stage 3 (`excerpt_not_verbatim`) и метрикой rejected-rate в smoke. Чинить цитаты постфактум запрещено и не делается.
2. OpenRouter может вернуть `model` с суффиксом ревизии — фиксируется в recording (`response_model`) как точный идентификатор.
3. Стохастичность: даже при temperature 0 повторный live-вызов не гарантирует байт-идентичность → воспроизводимость обеспечивается record/replay (контракт §6), не повторным сэмплированием.
4. Protected stash `codex-preserve-db-persistence-...` из задания в `git stash list` отсутствует (список пуст) — трогать нечего; зафиксировано как расхождение задания с фактическим состоянием.

## Вывод

Архитектурный blocker отсутствует; runtime changes не требуются. Продолжаем Slice 5A-2.
