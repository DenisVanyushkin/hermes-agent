---
schema_version: 1.0.0
gate: gate-a
document: p0-authorization
status: authorized
authorized_base: 496e542b3b002d28a65a814fd2d56b84a63c8cdc
plan: docs/plans/2026-08-26-p0-open-market-acquisition.md
plan_sha256: 4c02fb0282e58f545fc3590b9e4755c1660cf2ccfeb4418ba7ac46e4f7d60a85
supersedes_product_claim_of: docs/evidence/product-search-gate-a/owner-decision.md
owner_decision: authorized
owner_decision_date: 2026-08-26
gate_a_reopened: true
privileged_bootstrap_unit_authorized: true
prior_product_claim_superseded: true
---

# Разрешение на переоткрытие Gate A для среза P0

Владелец принял решение 2026-08-26; раздел 10 содержит его дословно.

## 1. Почему запрос вообще нужен

Действующее решение по Gate A содержит дословный запрет:

> It supersedes the protected-path freeze only for the reviewed
> `job_intel/browser_worker.py` repair in canonical commit
> `65d60daae16093a9a7e34a11a159e2f789dd14dd` and its browser desktop/network
> bootstrap scripts and regression tests. It does not authorize changes to
> `job_intel/sources.py`, `job_intel/ats_sources.py`,
> `job_intel/browser_sourcing.py`, production source configuration, Slack
> access, the production Job Intel database, legacy activation, or a transition
> beyond Gate A.

Срез P0 изменяет `job_intel/browser_sourcing.py`, `job_intel/sources.py` и
добавляет конфигурацию источника — то есть ровно то, что процитированный
абзац прямо не разрешает. План неисполним не из осторожности, а по букве.

## 2. Что предлагается делать и зачем

Прогон Gate A `gate-a-20260816T141344Z` дал корпус, в котором:

- 1692 строки из 1814 (93%) — десять ATS-арендаторов из рукописного реестра;
- LinkedIn на 112 запросов вернул **7** уникальных вакансий, каждую по 42–121
  разу, уровня Product Owner, и один и тот же набор во всех географических
  ячейках;
- DuckDuckGo дал 80 строк агрегаторов, включая `en.wikipedia.org`;
- при этом **все** ячейки отрапортованы как `qualified_results_found`, потому
  что `acquisition_probe.py:404` ставит это состояние по факту непустой выдачи.

Два теневых прогона 2026-08-26 (`run_id` 467 и 468) подтвердили, что LinkedIn
падает и сейчас, причём по причине из собственного юнита
(`NoNewPrivileges` против `sudo` в `browser_worker.py`), а причина отказа
теряется: `source_kpi_run` несёт `source_status = error`, а `error_class`,
`error_fingerprint` и `error_message_truncated` — `NULL`.

Срез делает Open Market проверяемым источником и приводит измерение в
соответствие с тем, что оно декларирует. Он **не** трогает решатель, портфель
и доставку.

## 3. Базовый коммит

`authorized_base` = `496e542b3b002d28a65a814fd2d56b84a63c8cdc` — фактический
HEAD `local/customizations` после слияния предусловий (теневой сбор и его
hardening). База выбрана именно такой, чтобы проверка объёма не падала на уже
выполненной и отдельно санкционированной работе.

## 4. Полный перечень путей

Список закрыт. Путь, не названный здесь, работами не затрагивается; его
появление требует нового разрешения. Проверяется скриптом по объединению путей
каждого коммита в диапазоне, а не только по итоговому diff.

| Путь | Раздел | Основание |
|---|---|---|
| `job_intel/browser_sourcing.py` | A1, A3, A4 | назван в запрете дословно |
| `job_intel/sources.py` | A2 | назван в запрете дословно |
| `job_intel/browser_worker.py` | A0, A1, A2, A3 | покрыт исключением только для коммита `65d60daae16` |
| `job_intel/product_search/acquisition_probe.py` | A0, A2, B1–B3 | в исключении не назван |
| `config/product_search/linkedin_geography.v1.yaml` (новый) | A2, B2 | «production source configuration» |
| `deploy/systemd/experiments/job-intel-product-search-probe-experiment.service` | A0 | зависимость от bootstrap вместо `sudo` |
| `scripts/job_intel_browser_supervisor.py` (новый) | A0 | супервизор `Type=notify` |
| `scripts/job_intel_profile_lock.sh` (новый) | A0 | блокировка профиля |
| `scripts/job_intel_profile_manifest.py` (новый) | A0 | манифест дерева профиля |
| `scripts/check_p0_scope.sh` (новый) | 0.1 | проверяльщик объёма |
| `tests/job_intel/test_browser_acquisition.py` | A0–A3 | регрессионные тесты |
| `tests/job_intel/test_profile_lock_and_manifest.py` (новый) | A0 | тесты блокировки и манифеста |
| `tests/job_intel/test_p0_scope_checker.py` (новый) | 0.1 | тест проверяльщика |
| `tests/product_search/test_runtime_capability_gate.py` (новый) | A0 | тесты способности среды |
| `tests/product_search/test_acquisition_probe.py` | A2, B1–B3 | регрессионные тесты |
| `tests/product_search/test_search_contract.py` | A2, B2 | регрессионные тесты |
| `tests/product_search/test_gate_a_cell_outcomes.py` (новый) | B1 | тест таблицы исходов |
| `tests/product_search/test_gate_a_geography.py` (новый) | B2 | тест контракта улики |
| `docs/plans/2026-08-26-p0-open-market-acquisition.md` | весь план | артефакт работ |
| `docs/evidence/product-search-gate-a/2026-08-26-p0-authorization.md` | 0.1 | этот документ |

Пути `scripts/job_intel_startup_guard.sh` и `scripts/job_intel_site_integrity.py`
относятся к уже выполненному и отдельно санкционированному предусловию; новой
санкции не требуют и остаются в списке только для проверки объёма.

## 5. Отдельное решение: привилегированный юнит

Холодный старт браузера требует привилегий, а сборочный процесс их иметь не
должен. Предлагается вынести привилегированную часть в отдельный юнит
`deploy/systemd/experiments/job-intel-browser-bootstrap.service`, оставив
сборочный юнит непривилегированным (`NoNewPrivileges=yes`, без `sudo`).

**Это отдельный вопрос**, и на него можно ответить отдельно: без него
`runtime_capability_blocked` останется терминальным исходом A0, и весь срез
остановится на нём, а не пойдёт дальше вслепую.

## 6. Окно работ и порядок

- Работы ведутся в отдельном worktree, не в живом чекауте.
- Изменения кода под работающим прогоном запрещены: стоп таймера теневого
  сбора → подтверждение неактивности → слияние → пин → preflight → старт.
- Живые прогоны с реальным провайдером и реальными тратами в срез **не входят**.
- Теневой сбор на время работ продолжается; при слиянии останавливается на
  время процедуры.

## 7. Откат

- Каждый раздел откатывается своим `git revert` без затрагивания соседних.
- Откат раздела A возвращает прежний путь сбора.
- Корпуса и артефакты прежних прогонов неизменяемы и не удаляются.
- Привилегированный юнит, если он будет разрешён, снимается
  `systemctl disable --now` и удалением файла; сборочный юнит от этого не
  зависит функционально до раздела A0.

## 8. Что этим запросом НЕ запрашивается

- доставка чего-либо владельцу и снятие kill-switch;
- размаскирование боевых legacy-таймеров job-intel;
- расширение рукописного реестра компаний;
- Decision v2, портфельная выборка, persistence, Slack-публикация;
- живые прогоны с реальными тратами;
- изменение `job_intel/ats_sources.py` (в запрете назван, в срезе не нужен).

## 9. Статус прежнего решения

Решение `Одобряю Gate A: proceed` от 2026-08-16 остаётся действительным как
операционное. Его **продуктовая** часть — утверждение о наблюдении рынков —
измерением не обеспечена: состояние ячейки ставилось по факту непустой выдачи
(`acquisition_probe.py:404`), из-за чего Туркменистан, Катар, Япония и Бахрейн
отрапортованы наблюдёнными на одних и тех же семи вакансиях. Признание этой
части superseded — решение владельца, не исполнителя, и запрашивается здесь
отдельным пунктом.

## 10. Решение владельца

Принято 2026-08-26. Дословные ответы владельца на три поставленных вопроса:

| Вопрос | Ответ владельца |
|---|---|
| Переоткрытие Gate A в объёме таблицы раздела 4 | **Разрешаю** |
| Привилегированный bootstrap-юнит (раздел 5) | **Разрешаю** |
| Продуктовая часть решения от 2026-08-16 | **Признаю superseded** |

Следствия, зафиксированные явно:

- работы разделов A и B ведутся от `authorized_base`
  `496e542b3b002d28a65a814fd2d56b84a63c8cdc`, объём ограничен таблицей раздела 4;
- привилегированная часть существует только как отдельный юнит
  `job-intel-browser-bootstrap.service`; сборочный юнит остаётся
  непривилегированным, и это проверяется тестом, а не намерением;
- утверждение прогона `gate-a-20260816T141344Z` о наблюдении рынков более не
  является действующим основанием; операционная часть решения от 2026-08-16
  (авторизация Task 8, holds, retention) остаётся в силе;
- разрешение **не** распространяется на доставку, размаскирование legacy,
  расширение реестра компаний, Decision v2, портфельную выборку и живые прогоны
  с реальными тратами — см. раздел 8.
