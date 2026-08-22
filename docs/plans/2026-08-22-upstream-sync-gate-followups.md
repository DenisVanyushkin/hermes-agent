# Upstream-sync: handover после ремедиации 2026-08-22

План исполнения: [2026-08-22-upstream-sync-gate-remediation-plan.md](2026-08-22-upstream-sync-gate-remediation-plan.md).
Рабочая ветка реализации: `codex/upstream-sync-gate-remediation` в отдельном VPS
worktree `/tmp/upstream-sync-gate-remediation-20260822`.

## Фактический статус

- **A — канал квитанций:** реализован поштучный fingerprint-bound ack. Парсер
  принимает только `ack INV-...` целиком; состояние fail-closed, привязано к
  merge scope, origin/thread и текущему fingerprint; переходы и общий
  `finalize-request.json` защищены `flock`. Hard findings (`unparseable`,
  `unreadable_parent`) не квитируются.
- **B — совпавшая с локальной стороной резолюция:** исправлена. Кандидаты —
  `both_sides`, а не только diff результата относительно local parent; stage-0
  index является единственным источником результата.
- **C — сравнение только имён:** симметричное policy-aware правило теперь
  сравнивает точные top-level AST source segments. `merge-both` сообщает
  выброшенный body-вклад; `keep-local` и `take-upstream` подавляют только
  ожидаемую противоположную сторону. Повторные определения не схлопываются
  молча в блокирующем режиме: потерянная occurrence получает `name#N`.

## Контракты, которые теперь нельзя нарушать

1. Гейт проверяет именно дерево, которое будет закоммичено: любая unstaged
   tracked-разница или non-ignored untracked-файл останавливает прогон и
   говорит выполнить `git add -- <paths>`.
2. Результат, наличие, удаление, mode и OID читаются из stage-0 index/tree
   entries; pathname не превращается в `revision:path`.
3. `HERMES_SYNC_SKIP_INVARIANTS` больше не является штатным обходом. Единственный
   обход — явный manual-only `--break-glass`, который записывается в
   `apply-prepare.json` и не передаётся systemd.
4. Политика резолюции снимается в `resolution_policy_by_path` в merge-record и
   после этого не перечитывается из изменившегося `pending.json`. Конфликтный
   путь без политики и путь с несколькими разными политиками — hard refusal.
5. Fork-test boundary — `upstream/main`. К fork-only тестам добавляются тестовые
   файлы, изменённые самим merge относительно first parent; при превышении
   `HERMES_FORK_TEST_MAX_FILES` набор не режется молча.

## Доказательная база

- Replay fixture и методика: `tests/fixtures/upstream_sync/replay_9f3feebcd3.json`,
  `scripts/upstream_sync_replay.py`,
  `docs/reports/2026-08-22-upstream-sync-replay-methodology.md`.
- Anchor `9f3feebcd3 / tools/approval.py`: exact OIDs и ориентация родителей
  записаны; raw gate остаётся чистым, а body-contribution regression проверяется
  policy-aware режимом.
- На 15 merge-коммитах selector вычислялся за 0.581 s; максимум — 529 файлов,
  hard limit — 800.

## Осталось перед live rollout

Изменения находятся только в изолированном worktree. Runtime-копии
`/home/hermes/.hermes/scripts/` и gateway на VPS намеренно не перезапускались:
для этого нужен отдельный operator-approved rollout с проверкой фактического
`ExecStart`, canary обычного чата, triage/decision/ack и post-restart smoke.


## Review fixes (2026-08-22)

The review of commit 159c71d0cf was applied in the isolated remediation
worktree. Valid findings F1-F14 are covered: policy-aware replay fixtures and
exact expected-loss journaling; distinct discarded-contribution findings;
one finding per missing fact; file-level stage-0 deletion reporting; fail-closed
origin binding; gateway end-to-end armed/unarmed routing tests and concurrent
ack protection; durable neutral policy-loss reporting; copyable standalone
ack INV-... lines; explicit break-glass and blocked-state operator messages;
a separate report-only invariant mode; decorator-aware definition segments; and
the documented gate-order rationale.

Report-only mode is selected on prepare (--invariant-mode report or an
invariant_mode value in pending.json), then snapshotted in apply-prepare.json.
Commit/handoff never re-read the environment or accept a late override; the
live scheduler remains block-by-default.
The old global invariant skip remains rejected. The standalone ack line is
intentional: it keeps the receipt command copyable and is the parser's
whole-message contract.

No live deployment, restart, or push is part of this remediation. T12 remains
an operational follow-up: exercise the published wrapper/ExecStart path
end-to-end after these source and test changes are published.
