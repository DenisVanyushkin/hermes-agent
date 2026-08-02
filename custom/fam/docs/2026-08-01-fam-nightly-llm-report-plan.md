# Ночной отчёт fam через LLM — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить сырую ночную сводку `fam` структурированным дайджестом, который отдельный cron-джоб агента прогоняет через `gpt-5.6-luna` и доставляет человекопонятным отчётом.

**Architecture:** `fam` собирает дайджест (схлопывание ошибок в сигнатуры, статусы `new`/`known`/`resolved`, счётчики, пробы, таймеры, бэкапы) и пишет его в `~/.hermes/diagnostics/fam-digest-latest.json`. Cron-джоб агента читает файл через контекст-скрипт, рендерит отчёт LLM и доставляет в Telegram. `fam` подтверждает доставку чтением `~/.hermes/cron/jobs.json` и при провале шлёт сырой откат.

**Tech Stack:** Python 3.12, только stdlib (`json`, `re`, `sqlite3`, `subprocess`, `pathlib`), pytest. Хранилище состояния — существующая таблица `meta` в `assistant.db`.

**Дизайн:** `docs/2026-08-01-fam-nightly-llm-report-design.md` — читать целиком перед началом.

## Global Constraints

- **Только stdlib.** `fam` не имеет внешних зависимостей; ни одна задача не добавляет пакетов и не делает сетевых вызовов. LLM вызывается исключительно агентом, снаружи `fam`.
- **Никаких изменений схемы БД.** `db.init_db` не трогать, новых таблиц и колонок не создавать. Всё состояние — в таблице `meta` через `famdb.meta_get` / `famdb.meta_set`. Это устраняет необходимость в `fam init` перед выкаткой.
- **Whitelist полей, не blacklist.** Из `audit_log` в дайджест попадают только явно перечисленные поля. `gate.error` содержит `raw` и `final` — полный текст сообщения Амине; они не должны покинуть хост ни при каких условиях.
- **Изоляция секций.** Каждая секция дайджеста собирается в своём `try/except`; упавшая секция попадает в `section_errors`, остальной дайджест выходит целым. Тот же принцип уже применён в `health.all_probes` и `maint.run_maintenance`.
- **Критерий приёмки — дельта к эталону, а не «зелёный прогон».** На macOS/Python 3.13 стабильно падают ~20 тестов по окружению. Эталон снимается в задаче 0 и сравнивается после каждой задачи.
- **Стиль коммитов:** `feat(fam/diag): ...`, `test(fam/diag): ...`, `docs(fam): ...` — как в истории репозитория.
- Ветка: `feature/fam-nightly-llm-report`. Мерж в `local/customizations` = деплой, поэтому мержим только после задачи 9.

---

## Структура файлов

| Файл | Ответственность |
|---|---|
| `fam/diag.py` (**создать**) | Вся сборка дайджеста: нормализация сигнатур, whitelist, статусы находок, секции, запись файла, чтение ack из `jobs.json`. Не отправляет ничего. |
| `fam/maint.py` (**изменить**, `problem_summary` строки 16–105) | Оркестрация: окно, ack, сборка, запись, аварийный откат, водяной знак. |
| `fam/cli.py` (**изменить**, `_audit_tick_error` строки 1009–1021) | Добавить `exc_type` в payload. |
| `fam/tick.py` (**изменить**, строки 1169–1172) | То же для per-row обработчика `meds_row`. |
| `fam/gate.py` (**изменить**, `CONFIG_DEFAULTS` ~строка 108) | Три новых ключа конфига. |
| `scripts/fam_report_context.py` (**создать**) | Самодостаточный stdlib-скрипт: читает дайджест, ужимает, метит `MISSING`/`STALE`. Не импортирует `fam`. |
| `tests/conftest.py` (**изменить**) | Изоляция каталога диагностики от прода. |
| `tests/test_diag.py` (**создать**) | Сигнатуры, whitelist, статусы, секции, ack. |
| `tests/test_problem_summary.py` (**переписать**) | Оркестрация, водяной знак, откат. |
| `tests/test_report_context.py` (**создать**) | `MISSING` / `STALE` / нормальный путь. |

**Почему отдельный модуль `diag.py`, а не рост `maint.py`:** `maint.py` — 292 строки про retention/бэкапы/offsite; сборка дайджеста добавила бы к нему ~250 строк чужой ответственности. Разделение даёт `maint.py` роль оркестратора, а `diag.py` — чистых функций без побочных эффектов (кроме записи файла), которые тестируются без моков доставки.

**Почему `scripts/fam_report_context.py` не импортирует `fam`:** скрипт запускается планировщиком агента с `workdir=/home/denis/.hermes/hermes-agent`, где пакет `fam` не на `sys.path`. Эталон на VPS (`morning_report_context.py`) по той же причине самодостаточен.

---

## Task 0: Снять эталон тестов

**Files:** нет изменений.

- [ ] **Step 1: Создать venv и зафиксировать эталон**

```bash
cd ~/dev/fam-dev/custom/fam
python3 -m venv .venv && .venv/bin/pip install -q pytest
.venv/bin/python -m pytest -q 2>&1 | tail -30 > /tmp/fam-baseline.txt
cat /tmp/fam-baseline.txt
```

Ожидаемо: ~20 падений по окружению (нет submodule `starline`, нет кириллических шрифтов, `test_extcal_parse.py` ломает механизм отчётов pytest). Записать точное число `failed` — это эталон.

- [ ] **Step 2: Зафиксировать список падающих тестов**

```bash
.venv/bin/python -m pytest -q 2>&1 | grep -E "^(FAILED|ERROR)" | sort > /tmp/fam-baseline-names.txt
wc -l /tmp/fam-baseline-names.txt
```

После каждой следующей задачи повторять и сравнивать: `diff` должен быть пустым, кроме новых тестов задачи.

---

## Task 1: `exc_type` в payload `tick.error`

**Files:**
- Modify: `fam/cli.py:1009-1021` (`_audit_tick_error`)
- Modify: `fam/tick.py:1169-1172` (per-row `meds_row`)
- Test: `tests/test_diag.py` (создать файл этой задачей)

**Interfaces:**
- Produces: payload `tick.error` получает необязательное поле `exc_type` (строка или `null`). Все последующие задачи считают поле необязательным.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_diag.py`:

```python
import json

from fam import audit, cli


def test_audit_tick_error_records_exception_type(db):
    cli._audit_tick_error("reminders", KeyError("No item with that key"))
    row = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.error'").fetchone()
    payload = json.loads(row["payload"])
    assert payload["where"] == "reminders"
    assert payload["exc_type"] == "KeyError"


def test_audit_tick_error_accepts_plain_string(db):
    # cli.py:1245 passes a joined string, not an exception -- exc_type is
    # None there rather than "str", which would be meaningless noise.
    cli._audit_tick_error("offsite", "backup failed; disk full")
    payload = json.loads(db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.error'").fetchone()["payload"])
    assert payload["exc_type"] is None
    assert "disk full" in payload["error"]
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `.venv/bin/python -m pytest tests/test_diag.py -v`
Expected: FAIL — `KeyError: 'exc_type'`

- [ ] **Step 3: Реализация в `cli.py`**

Заменить тело `_audit_tick_error` (строки 1009–1021):

```python
def _audit_tick_error(where, exc):
    """Persist a tick.error marker so the nightly problem_summary sweep
    (6b) can see a failure that would otherwise only hit journald.
    Best-effort: a failure to record must not mask the original error.

    `exc_type` (design 2026-08-01, §8): the exception class name, or None
    when the caller passes a pre-joined string (cli.py's offsite path).
    str(exc) alone is ambiguous -- "No item with that key" is a KeyError
    from sqlite3.Row and almost always means the prod schema lags the
    code, which the text does not say. The nightly reporter keys its
    diagnosis off this field, so it is worth the five lines."""
    try:
        conn = famdb.connect()
        try:
            audit.log(conn, "tick.error",
                      {"where": where,
                       "exc_type": type(exc).__name__ if isinstance(exc, BaseException) else None,
                       "error": str(exc)[:200]}, actor="tick")
            conn.commit()
        finally:
            conn.close()
    except Exception:                                # noqa: BLE001
        pass
```

- [ ] **Step 4: Реализация в `tick.py`**

Заменить вызов `audit.log` в блоке `except` (строки 1169–1172):

```python
            audit.log(conn, "tick.error",
                      {"where": "meds_row", "intake_id": intake_id,
                       "exc_type": type(e).__name__,
                       "error": str(e)[:200]})
```

- [ ] **Step 5: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/test_diag.py tests/test_tick_meds.py -v`
Expected: PASS. Если тест `meds_row` ассертит payload целиком — обновить его на новый набор полей.

- [ ] **Step 6: Сверить дельту и закоммитить**

```bash
.venv/bin/python -m pytest -q 2>&1 | grep -E "^(FAILED|ERROR)" | sort | diff /tmp/fam-baseline-names.txt -
git add fam/cli.py fam/tick.py tests/test_diag.py
git commit -m "feat(fam/tick): record exception class in tick.error payload"
```

---

## Task 2: Сигнатуры ошибок и whitelist полей

**Files:**
- Create: `fam/diag.py`
- Test: `tests/test_diag.py`

**Interfaces:**
- Produces:
  - `normalize_signature(text: str) -> str`
  - `ERROR_SPEC: dict[str, dict]` — по одному ключу на вид события, поля `sig` / `ctx` / `text`
  - `collect_errors(conn, since: str) -> list[dict]` — находки с ключами `signature`, `kind`, `count`, `context`, `examples` плюс поля из `sig`

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_diag.py`:

```python
from datetime import datetime, timezone

from fam import diag


def test_normalize_signature_collapses_numbers_and_hex():
    assert diag.normalize_signature("row 4211 of abcdef1234567890") == \
        "row <n> of <hex>"


def test_identical_errors_collapse_into_one_finding(db):
    for _ in range(113):
        audit.log(db, "tick.error",
                  {"where": "meds_row", "intake_id": 10, "exc_type": "KeyError",
                   "error": "No item with that key"}, actor="tick")
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert len(findings) == 1
    assert findings[0]["count"] == 113
    assert findings[0]["kind"] == "tick.error"
    assert findings[0]["p_where"] == "meds_row"
    assert findings[0]["p_exc_type"] == "KeyError"
    assert findings[0]["context"]["intake_id"] == [10]


def test_same_defect_on_several_doses_stays_one_finding(db):
    for intake in (10, 11, 12):
        audit.log(db, "tick.error",
                  {"where": "meds_row", "intake_id": intake, "exc_type": "KeyError",
                   "error": "No item with that key"}, actor="tick")
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert len(findings) == 1, "intake_id must not split the signature"
    assert findings[0]["context"]["intake_id"] == [10, 11, 12]


def test_road_error_without_error_text_is_not_lost(db):
    # road.py writes {"event_id": ...} with no "error" key at all.
    audit.log(db, "road.error", {"event_id": 7})
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert len(findings) == 1
    assert findings[0]["kind"] == "road.error"
    assert findings[0]["context"]["event_id"] == [7]


def test_gate_error_never_exposes_message_text(db):
    audit.log(db, "gate.error",
              {"kind": "reminder", "attempt": 2,
               "raw": {"text": "прими лекарство"}, "final": "Прими лекарство"})
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    blob = json.dumps(findings, ensure_ascii=False)
    assert "лекарство" not in blob
    assert "final" not in blob and "raw" not in blob
    # gate.error's payload has its own "kind" (the MESSAGE kind). It must
    # not shadow the audit event kind in the finding -- hence the p_ prefix.
    assert findings[0]["kind"] == "gate.error"
    assert findings[0]["p_kind"] == "reminder"
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/python -m pytest tests/test_diag.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fam.diag'`

- [ ] **Step 3: Создать `fam/diag.py`**

```python
"""Nightly diagnostics digest for fam (design 2026-08-01).

Collects a structured 24h picture -- error signatures, probes, activity
counters, timers, backups -- into ~/.hermes/diagnostics/fam-digest-latest.json
for the agent's `fam-nightly-report` cron job to render via LLM. Collects
only; never delivers. Delivery and the watermark contract live in maint.py.
"""
import json
import re

MAX_EXAMPLES = 3
MAX_CONTEXT_VALUES = 10
SIGNATURE_MAX = 300

_HEX_RE = re.compile(r"[0-9a-fA-F]{8,}")
_NUM_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")

# Per-kind ALLOW-list (design §7). A deny-list would leak the day someone
# adds a field: gate.error already carries `raw` and `final` -- the full
# message text to Amina -- and this digest is handed to an external LLM.
#   sig  -- fields that are part of the signature (a different value is a
#           different defect)
#   ctx  -- fields collected as context (a different value is the SAME
#           defect hitting another row; intake_id must not split findings)
#   text -- whether payload["error"] may be used for signature/examples
ERROR_SPEC = {
    "tick.error": {"sig": ("where", "exc_type"), "ctx": ("intake_id",), "text": True},
    "gate.error": {"sig": ("kind",), "ctx": ("attempt",), "text": False},
    "mail.error": {"sig": (), "ctx": ("event_id",), "text": True},
    "road.error": {"sig": (), "ctx": ("event_id", "via"), "text": False},
}
ERROR_KINDS = tuple(ERROR_SPEC)


def normalize_signature(text):
    text = _HEX_RE.sub("<hex>", text or "")
    text = _NUM_RE.sub("<n>", text)
    return _WS_RE.sub(" ", text).strip()[:SIGNATURE_MAX]


def collect_errors(conn, since):
    """Fold every *.error row since `since` into deduplicated findings.

    Returns findings sorted by descending count. A row whose payload is
    not a JSON object still produces a finding keyed on its kind alone --
    silently dropping a malformed error row would hide exactly the kind
    of breakage this digest exists to surface."""
    placeholders = ",".join("?" * len(ERROR_KINDS))
    rows = conn.execute(
        f"SELECT kind, payload FROM audit_log WHERE ts_utc >= ? "
        f"AND kind IN ({placeholders}) ORDER BY id",
        (since, *ERROR_KINDS)).fetchall()
    buckets = {}
    for row in rows:
        spec = ERROR_SPEC[row["kind"]]
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        parts = [row["kind"]]
        for field in spec["sig"]:
            if payload.get(field) is not None:
                parts.append(f"{field}={payload[field]}")
        text = normalize_signature(str(payload.get("error") or "")) if spec["text"] else ""
        if text:
            parts.append(text)
        signature = "|".join(parts)
        bucket = buckets.get(signature)
        if bucket is None:
            bucket = {"signature": signature, "kind": row["kind"], "count": 0,
                      "context": {}, "examples": []}
            for field in spec["sig"]:
                if payload.get(field) is not None:
                    # p_ prefix: payload field names share a namespace with
                    # the finding's own keys, and gate.error's payload has
                    # a "kind" of its own (reminder/med/digest) that would
                    # otherwise overwrite the audit event kind above.
                    bucket[f"p_{field}"] = payload[field]
            buckets[signature] = bucket
        bucket["count"] += 1
        for field in spec["ctx"]:
            value = payload.get(field)
            if value is None:
                continue
            values = bucket["context"].setdefault(field, [])
            if value not in values and len(values) < MAX_CONTEXT_VALUES:
                values.append(value)
        if spec["text"]:
            example = str(payload.get("error") or "")[:200]
            if example and example not in bucket["examples"] \
                    and len(bucket["examples"]) < MAX_EXAMPLES:
                bucket["examples"].append(example)
    return sorted(buckets.values(), key=lambda b: -b["count"])
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/test_diag.py -v`
Expected: PASS (все 7 тестов)

- [ ] **Step 5: Сверить дельту и закоммитить**

```bash
.venv/bin/python -m pytest -q 2>&1 | grep -E "^(FAILED|ERROR)" | sort | diff /tmp/fam-baseline-names.txt -
git add fam/diag.py tests/test_diag.py
git commit -m "feat(fam/diag): fold *.error rows into deduplicated signatures"
```

---

## Task 3: Статусы находок new / known / resolved

**Files:**
- Modify: `fam/diag.py`
- Test: `tests/test_diag.py`

**Interfaces:**
- Consumes: `collect_errors` из задачи 2
- Produces:
  - `STATE_KEY = "maint_known_issues"`
  - `load_state(conn) -> dict`, `save_state(conn, state) -> None`
  - `diff_known_issues(state: dict, findings: list, now: datetime) -> tuple[list, list, dict]` — `(annotated, resolved, new_state)`

- [ ] **Step 1: Написать падающие тесты**

```python
def test_first_sighting_is_new_then_known(db):
    now1 = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    findings = [{"signature": "tick.error|where=meds_row", "count": 3}]
    annotated, resolved, state = diag.diff_known_issues({}, findings, now1)
    assert annotated[0]["status"] == "new"
    assert annotated[0]["age_days"] == 0
    assert resolved == []

    now2 = datetime(2026, 8, 4, 22, 30, tzinfo=timezone.utc)
    annotated, resolved, state = diag.diff_known_issues(state, findings, now2)
    assert annotated[0]["status"] == "known"
    assert annotated[0]["age_days"] == 3


def test_disappeared_signature_becomes_resolved(db):
    now1 = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    _, _, state = diag.diff_known_issues(
        {}, [{"signature": "tick.error|where=digest", "count": 1}], now1)
    now2 = datetime(2026, 8, 2, 22, 30, tzinfo=timezone.utc)
    annotated, resolved, state = diag.diff_known_issues(state, [], now2)
    assert annotated == []
    assert [r["signature"] for r in resolved] == ["tick.error|where=digest"]
    assert state == {}, "a resolved signature must not linger in state forever"


def test_state_round_trips_through_meta(db):
    diag.save_state(db, {"sig": {"first_seen": "2026-08-01T00:00:00+00:00",
                                 "last_seen": "2026-08-01T00:00:00+00:00", "count": 2}})
    db.commit()
    assert diag.load_state(db)["sig"]["count"] == 2


def test_corrupt_state_degrades_to_empty(db):
    from fam import db as famdb
    famdb.meta_set(db, diag.STATE_KEY, "{not json")
    db.commit()
    assert diag.load_state(db) == {}
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/python -m pytest tests/test_diag.py -k "new_then_known or resolved or meta or corrupt" -v`
Expected: FAIL — `AttributeError: module 'fam.diag' has no attribute 'diff_known_issues'`

- [ ] **Step 3: Дописать в `fam/diag.py`**

Добавить импорты `from datetime import datetime` и `from . import db as famdb` в шапку, затем:

```python
STATE_KEY = "maint_known_issues"


def load_state(conn):
    """Known-issue state from the meta table. Corrupt state degrades to
    empty rather than raising: a bad value must cost one night of age
    tracking, not the whole nightly sweep."""
    raw = famdb.meta_get(conn, STATE_KEY)
    if not raw:
        return {}
    try:
        state = json.loads(raw)
    except ValueError:
        return {}
    return state if isinstance(state, dict) else {}


def save_state(conn, state):
    famdb.meta_set(conn, STATE_KEY, json.dumps(state, ensure_ascii=False))


def diff_known_issues(state, findings, now):
    """Annotate findings with new/known/age_days against prior state.

    Returns (annotated, resolved, new_state). Signatures absent from this
    window drop out of new_state entirely -- otherwise state would grow
    without bound and every long-gone defect would keep re-reporting as
    'resolved' every night."""
    now_iso = now.isoformat(timespec="seconds")
    annotated = []
    new_state = {}
    for finding in findings:
        signature = finding["signature"]
        prior = state.get(signature) or {}
        first_seen = prior.get("first_seen")
        if first_seen:
            status = "known"
            try:
                age_days = (now - datetime.fromisoformat(first_seen)).days
            except (TypeError, ValueError):
                first_seen, age_days = now_iso, 0
        else:
            first_seen, age_days, status = now_iso, 0, "new"
        annotated.append({**finding, "status": status,
                          "first_seen": first_seen, "age_days": age_days})
        new_state[signature] = {"first_seen": first_seen, "last_seen": now_iso,
                                "count": finding["count"]}
    resolved = [{"signature": sig, "first_seen": meta.get("first_seen"),
                 "last_seen": meta.get("last_seen")}
                for sig, meta in state.items() if sig not in new_state]
    return annotated, resolved, new_state
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/test_diag.py -v`
Expected: PASS

- [ ] **Step 5: Сверить дельту и закоммитить**

```bash
.venv/bin/python -m pytest -q 2>&1 | grep -E "^(FAILED|ERROR)" | sort | diff /tmp/fam-baseline-names.txt -
git add fam/diag.py tests/test_diag.py
git commit -m "feat(fam/diag): track finding age via new/known/resolved state in meta"
```

---

## Task 4: Секции activity, timers, backups, calendar

**Files:**
- Modify: `fam/diag.py`
- Test: `tests/test_diag.py`

**Interfaces:**
- Produces:
  - `collect_activity(conn, cfg, since, now) -> dict`
  - `collect_calendar(conn, since) -> dict`
  - `collect_timers(runner=None) -> dict` — `runner` совместим по сигнатуре с `subprocess.run`
  - `collect_backups(cfg, now, verify) -> dict` — `verify` инжектируется (`maint.verify_backup`), чтобы `diag` не импортировал `maint` и не создавал цикл

- [ ] **Step 1: Написать падающие тесты**

```python
def test_activity_counts_by_message_kind(db):
    audit.log(db, "gate.sent", {"kind": "reminder", "raw": {"text": "секрет"}})
    audit.log(db, "gate.sent", {"kind": "med", "raw": {"text": "секрет"}})
    audit.log(db, "gate.sent", {"kind": "med", "raw": {"text": "секрет"}})
    audit.log(db, "tick.med", {"intake_id": 1})
    audit.log(db, "meds.take", {"intake_id": 1})
    db.commit()
    now = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    activity = diag.collect_activity(db, {"daily_budget": 8},
                                     "1970-01-01T00:00:00+00:00", now)
    assert activity["sent_by_kind"] == {"reminder": 1, "med": 2}
    assert activity["meds_generated"] == 1
    assert activity["meds_taken"] == 1
    assert activity["budget_limit"] == 8
    assert "секрет" not in json.dumps(activity, ensure_ascii=False)


def test_calendar_reports_collisions_only(db):
    audit.log(db, "cal.ext.sync", {"collisions": 2, "title": "Врач в 15:00"})
    audit.log(db, "cal.ext.sync", {"collisions": 1})
    db.commit()
    calendar = diag.collect_calendar(db, "1970-01-01T00:00:00+00:00")
    assert calendar == {"collisions": 3}


def test_timers_split_failed_and_ok():
    calls = []

    class _Result:
        def __init__(self, stdout):
            self.stdout, self.returncode = stdout, 0

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "--failed" in cmd:
            return _Result("fam-car.service loaded failed failed Amina fam: car\n")
        return _Result("fam-reminders.timer loaded active waiting Amina fam\n"
                       "fam-car.timer loaded active waiting Amina fam\n")

    timers = diag.collect_timers(runner=fake_run)
    assert timers["failed"] == [{"unit": "fam-car.service", "detail": "failed"}]
    assert timers["ok"] == ["fam-reminders.timer", "fam-car.timer"]


def test_backups_report_verify_result(tmp_path):
    backup = tmp_path / "assistant-20260801.db"
    backup.write_bytes(b"x")
    now = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    result = diag.collect_backups(
        {"backup_dir": str(tmp_path), "offsite_enabled": False}, now,
        verify=lambda path: (True, {"integrity": "ok", "schema_version": "12"}))
    assert result["last_path"] == "assistant-20260801.db"
    assert result["verify"] == "ok"
    assert result["schema_version"] == "12"
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/python -m pytest tests/test_diag.py -k "activity or calendar or timers or backups" -v`
Expected: FAIL — атрибуты не существуют

- [ ] **Step 3: Дописать в `fam/diag.py`**

Добавить в шапку `import subprocess`, `from pathlib import Path`, `from . import gate`, затем:

```python
SYSTEMCTL_TIMEOUT = 30


def collect_activity(conn, cfg, since, now):
    """Counters only -- never message text. `sent_by_kind` uses the inner
    payload["kind"] (reminder/med/digest), which is a fixed vocabulary,
    not user content."""
    def _count(kind):
        return conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE kind=? AND ts_utc >= ?",
            (kind, since)).fetchone()[0]

    sent_by_kind = {}
    for row in conn.execute(
            "SELECT payload FROM audit_log WHERE kind='gate.sent' AND ts_utc >= ?",
            (since,)):
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        kind = payload.get("kind") or "?"
        sent_by_kind[kind] = sent_by_kind.get(kind, 0) + 1
    return {
        "sent_by_kind": sent_by_kind,
        "messages_sent": sum(sent_by_kind.values()),
        "meds_generated": _count("tick.med"),
        "meds_taken": _count("meds.take"),
        "reminder_chains_built": _count("rem.regenerate"),
        # gate.budget_spent_today's now_utc contract is a string (its
        # _parse_utc calls datetime.fromisoformat directly) -- every other
        # caller in the codebase passes one; `now` here is a datetime.
        "budget_spent": gate.budget_spent_today(
            conn, now_utc=now.isoformat(timespec="seconds")),
        "budget_limit": cfg.get("daily_budget", 8),
    }


def collect_calendar(conn, since):
    """Counter only. The design's audit rule for iPhone-controlled data
    is UID-and-counts-only: no event titles, calendar names or URLs ever
    leave the host through this digest."""
    total = 0
    for row in conn.execute(
            "SELECT payload FROM audit_log WHERE kind='cal.ext.sync' AND ts_utc >= ?",
            (since,)):
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        collisions = payload.get("collisions") if isinstance(payload, dict) else None
        if isinstance(collisions, int):
            total += collisions
    return {"collisions": total}


def _systemctl(runner, *args):
    return runner(["systemctl", "--user", "--no-legend", "--no-pager", *args],
                  capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT)


def collect_timers(runner=None):
    """Failed fam units + the roster of loaded fam timers.

    Uses `list-units` rather than `list-timers`: list-timers output is
    six positional columns whose widths shift with locale and schedule,
    while list-units puts the unit name first on every line."""
    runner = runner or subprocess.run
    failed = []
    result = _systemctl(runner, "list-units", "--failed", "--all", "fam-*.service")
    for line in (result.stdout or "").splitlines():
        parts = line.replace("●", " ").split()
        if parts:
            failed.append({"unit": parts[0], "detail": parts[3] if len(parts) > 3 else "failed"})
    ok = []
    result = _systemctl(runner, "list-units", "--type=timer", "--all", "fam-*.timer")
    for line in (result.stdout or "").splitlines():
        parts = line.replace("●", " ").split()
        if parts:
            ok.append(parts[0])
    return {"failed": failed, "ok": ok}


def collect_backups(cfg, now, verify):
    """Newest dated backup + its integrity verdict. `verify` is injected
    (maint.verify_backup) so diag never imports maint -- maint imports
    diag, and the reverse edge would be a cycle."""
    backup_dir = Path(cfg["backup_dir"])
    files = sorted(backup_dir.glob("*-????????.db")) if backup_dir.is_dir() else []
    if not files:
        return {"last_path": None, "verify": "missing", "schema_version": None,
                "offsite_age_days": None}
    newest = files[-1]
    ok, detail = verify(newest)
    result = {"last_path": newest.name,
              "verify": "ok" if ok else str(detail.get("integrity")),
              "schema_version": detail.get("schema_version"),
              "offsite_age_days": None}
    if cfg.get("offsite_enabled"):
        offsite = Path(cfg["offsite_dir"])
        dumps = sorted(offsite.glob("*-????????.db.age")) if offsite.is_dir() else []
        if dumps:
            stamp = dumps[-1].name.split("-")[-1][:8]
            try:
                written = datetime.strptime(stamp, "%Y%m%d").replace(tzinfo=now.tzinfo)
                result["offsite_age_days"] = (now - written).days
            except ValueError:
                result["offsite_age_days"] = None
    return result
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/test_diag.py -v`
Expected: PASS

- [ ] **Step 5: Сверить дельту и закоммитить**

```bash
.venv/bin/python -m pytest -q 2>&1 | grep -E "^(FAILED|ERROR)" | sort | diff /tmp/fam-baseline-names.txt -
git add fam/diag.py tests/test_diag.py
git commit -m "feat(fam/diag): activity, calendar, timer and backup sections"
```

---

## Task 5: Сборка дайджеста и запись файла

**Files:**
- Modify: `fam/diag.py`
- Modify: `fam/gate.py` (`CONFIG_DEFAULTS`, после блока Phase 6a ~строка 116)
- Modify: `tests/conftest.py`
- Test: `tests/test_diag.py`

**Interfaces:**
- Consumes: всё из задач 2–4
- Produces:
  - `DEFAULT_DIAGNOSTICS_DIR: str`
  - `build_digest(conn, cfg, since, now, delivery, state, verify, runner=None) -> tuple[dict, dict]` — `(digest, new_state)`
  - `write_digest(digest, dest_dir, now) -> Path`
- Новые ключи конфига: `diagnostics_dir`, `report_jobs_path`, `report_job_name`

- [ ] **Step 1: Изолировать каталог диагностики в `conftest.py`**

Дописать в фикстуру `_isolate_prod_stores`:

```python
    from fam import car, diag, gate
    ...
    # Same class of leak as the starline token above: problem_summary({})
    # would otherwise write into the LIVE ~/.hermes/diagnostics.
    monkeypatch.setattr(diag, "DEFAULT_DIAGNOSTICS_DIR", str(tmp_path / "diagnostics"))
```

- [ ] **Step 2: Написать падающие тесты**

```python
def test_build_digest_isolates_a_failing_section(db, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("systemctl gone")
    monkeypatch.setattr(diag, "collect_timers", boom)
    now = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    digest, _ = diag.build_digest(
        db, {"daily_budget": 8, "backup_dir": "/nonexistent"},
        "1970-01-01T00:00:00+00:00", now,
        delivery={"previous_report_ok": True}, state={},
        verify=lambda p: (True, {}))
    assert "timers" not in digest["sections"]
    assert "RuntimeError" in digest["section_errors"]["timers"]
    assert "activity" in digest["sections"], "other sections must survive"


def test_build_digest_keeps_state_when_error_section_fails(db, monkeypatch):
    monkeypatch.setattr(diag, "collect_errors",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("db gone")))
    now = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    prior = {"sig": {"first_seen": "2026-07-01T00:00:00+00:00",
                     "last_seen": "2026-07-31T00:00:00+00:00", "count": 1}}
    digest, new_state = diag.build_digest(
        db, {"daily_budget": 8, "backup_dir": "/nonexistent"},
        "1970-01-01T00:00:00+00:00", now,
        delivery={"previous_report_ok": True}, state=prior,
        verify=lambda p: (True, {}))
    assert new_state == prior, "a failed collection must not wipe age tracking"


def test_write_digest_is_atomic_and_rotates(tmp_path):
    now = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    for day in range(1, 17):
        old = tmp_path / f"fam-digest-202607{day:02d}.json"
        old.write_text("{}", encoding="utf-8")
    path = diag.write_digest({"generated_at": now.isoformat()}, tmp_path, now)
    assert path.name == "fam-digest-latest.json"
    assert json.loads(path.read_text(encoding="utf-8"))["generated_at"]
    assert (tmp_path / "fam-digest-20260801.json").exists()
    dated = sorted(tmp_path.glob("fam-digest-????????.json"))
    assert len(dated) == diag.ROTATE_DAYS
    assert not list(tmp_path.glob("*.tmp"))
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `.venv/bin/python -m pytest tests/test_diag.py -k "build_digest or write_digest" -v`
Expected: FAIL — атрибуты не существуют

- [ ] **Step 4: Добавить ключи в `gate.CONFIG_DEFAULTS`**

После строки `"offsite_keep": 8,`:

```python
    # Nightly LLM report (design 2026-08-01). fam writes the digest here;
    # the agent's cron job reads it, renders it and delivers it. fam reads
    # that job's row back from jobs.json to learn whether the report was
    # actually delivered -- see maint.problem_summary.
    "diagnostics_dir": "/home/denis/.hermes/diagnostics",
    "report_jobs_path": "/home/denis/.hermes/cron/jobs.json",
    "report_job_name": "fam-nightly-report",
```

- [ ] **Step 5: Дописать в `fam/diag.py`**

Добавить `import os` в шапку, затем:

```python
DEFAULT_DIAGNOSTICS_DIR = "/home/denis/.hermes/diagnostics"
ROTATE_DAYS = 14
DIGEST_LATEST = "fam-digest-latest.json"


def build_digest(conn, cfg, since, now, delivery, state, verify, runner=None):
    """Assemble the digest. Returns (digest, new_state).

    Every section is isolated: one collector raising lands in
    section_errors and the rest of the digest still ships. If the error
    section itself fails, new_state is returned unchanged -- losing age
    tracking on a bad night would silently reset every 'known' finding
    back to 'new'."""
    sections, section_errors = {}, {}
    new_state = dict(state)

    def _section(name, fn):
        try:
            sections[name] = fn()
        except Exception as exc:                     # noqa: BLE001 -- isolate
            section_errors[name] = f"{type(exc).__name__}: {exc}"

    def _errors():
        nonlocal new_state
        annotated, resolved, new_state = diff_known_issues(
            state, collect_errors(conn, since), now)
        return {"findings": annotated, "resolved": resolved}

    _section("errors", _errors)
    _section("probes", lambda: health.all_probes(conn, cfg, now=now))
    _section("calendar", lambda: collect_calendar(conn, since))
    _section("activity", lambda: collect_activity(conn, cfg, since, now))
    _section("timers", lambda: collect_timers(runner=runner))
    _section("backups", lambda: collect_backups(cfg, now, verify))
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "window": {"since": since},
        "fam_schema_version": famdb.meta_get(conn, "schema_version"),
        "delivery": delivery,
        "sections": sections,
        "section_errors": section_errors,
    }, new_state


def write_digest(digest, dest_dir, now):
    """Publish the digest atomically and rotate dated copies.

    os.replace, not a plain write: the agent's cron job may read the file
    at any moment, and a half-written digest would surface to Denis as a
    bogus DIGEST MISSING."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_dir.chmod(0o700)                 # same PII posture as the DB backups
    body = json.dumps(digest, ensure_ascii=False, indent=1)
    latest = dest_dir / DIGEST_LATEST
    tmp = dest_dir / (DIGEST_LATEST + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, latest)
    dated = dest_dir / f"fam-digest-{now.strftime('%Y%m%d')}.json"
    dated.write_text(body, encoding="utf-8")
    dated.chmod(0o600)
    for old in sorted(dest_dir.glob("fam-digest-????????.json"))[:-ROTATE_DAYS]:
        old.unlink()
    return latest
```

Добавить в шапку модуля `from . import health`.

- [ ] **Step 6: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/test_diag.py -v`
Expected: PASS

- [ ] **Step 7: Сверить дельту и закоммитить**

```bash
.venv/bin/python -m pytest -q 2>&1 | grep -E "^(FAILED|ERROR)" | sort | diff /tmp/fam-baseline-names.txt -
git add fam/diag.py fam/gate.py tests/conftest.py tests/test_diag.py
git commit -m "feat(fam/diag): assemble and atomically publish the nightly digest"
```

---

## Task 6: Подтверждение доставки из `jobs.json`

**Files:**
- Modify: `fam/diag.py`
- Test: `tests/test_diag.py`

**Interfaces:**
- Produces: `report_delivery_status(jobs_path, job_name, previous_digest_at) -> tuple[bool, str]`

- [ ] **Step 1: Написать падающие тесты**

```python
def _jobs_file(tmp_path, **overrides):
    job = {"name": "fam-nightly-report", "last_status": "ok",
           "last_run_at": "2026-08-02T03:00:12+00:00", "last_delivery_error": None}
    job.update(overrides)
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps([job]), encoding="utf-8")
    return path


def test_delivery_ok_when_job_ran_after_digest(tmp_path):
    ok, detail = diag.report_delivery_status(
        _jobs_file(tmp_path), "fam-nightly-report", "2026-08-01T22:30:01+00:00")
    assert ok is True
    assert "2026-08-02" in detail


def test_delivery_not_ok_on_delivery_error(tmp_path):
    ok, detail = diag.report_delivery_status(
        _jobs_file(tmp_path, last_delivery_error="telegram 502"),
        "fam-nightly-report", "2026-08-01T22:30:01+00:00")
    assert ok is False
    assert "telegram 502" in detail


def test_delivery_not_ok_when_run_predates_digest(tmp_path):
    ok, detail = diag.report_delivery_status(
        _jobs_file(tmp_path, last_run_at="2026-07-30T03:00:00+00:00"),
        "fam-nightly-report", "2026-08-01T22:30:01+00:00")
    assert ok is False
    assert "predates" in detail


def test_delivery_not_ok_when_job_missing_or_file_unreadable(tmp_path):
    ok, _ = diag.report_delivery_status(
        _jobs_file(tmp_path), "no-such-job", "2026-08-01T22:30:01+00:00")
    assert ok is False
    ok, detail = diag.report_delivery_status(
        tmp_path / "absent.json", "fam-nightly-report", "2026-08-01T22:30:01+00:00")
    assert ok is False
    assert "unreadable" in detail


def test_first_run_is_not_confirmed(tmp_path):
    # No previous digest means the job did not exist yet: treating this as
    # confirmed would advance the watermark having sent nothing at all.
    ok, detail = diag.report_delivery_status(
        _jobs_file(tmp_path), "fam-nightly-report", None)
    assert ok is False
    assert "first run" in detail
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/python -m pytest tests/test_diag.py -k delivery -v`
Expected: FAIL — `AttributeError: report_delivery_status`

- [ ] **Step 3: Дописать в `fam/diag.py`**

```python
def _aware(value):
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def report_delivery_status(jobs_path, job_name, previous_digest_at):
    """Did the agent's reporter job deliver the previous digest?

    Returns (ok, detail). Every uncertain case answers False: the cost of
    a false negative is one redundant raw message, the cost of a false
    positive is a silently burnt watermark and a lost day of problems."""
    if not previous_digest_at:
        return False, "first run: no previous digest"
    try:
        data = json.loads(Path(jobs_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"jobs.json unreadable: {type(exc).__name__}"
    jobs = data.get("jobs", data) if isinstance(data, dict) else data
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    if not isinstance(jobs, list):
        return False, "jobs.json: unexpected shape"
    job = next((j for j in jobs
                if isinstance(j, dict) and j.get("name") == job_name), None)
    if job is None:
        return False, f"job {job_name!r} not found"
    if job.get("last_status") != "ok":
        return False, f"last_status={job.get('last_status')!r}"
    if job.get("last_delivery_error"):
        return False, f"delivery error: {str(job['last_delivery_error'])[:120]}"
    try:
        ran, previous = _aware(job.get("last_run_at")), _aware(previous_digest_at)
    except (TypeError, ValueError):
        return False, f"unparseable last_run_at={job.get('last_run_at')!r}"
    if ran <= previous:
        return False, f"last_run_at={job.get('last_run_at')} predates digest"
    return True, f"delivered at {job.get('last_run_at')}"
```

Добавить `timezone` в существующий импорт `from datetime import datetime`.

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/test_diag.py -v`
Expected: PASS

- [ ] **Step 5: Сверить дельту и закоммитить**

```bash
.venv/bin/python -m pytest -q 2>&1 | grep -E "^(FAILED|ERROR)" | sort | diff /tmp/fam-baseline-names.txt -
git add fam/diag.py tests/test_diag.py
git commit -m "feat(fam/diag): read report delivery confirmation from cron jobs.json"
```

---

## Task 7: Оркестрация в `problem_summary`

**Files:**
- Modify: `fam/maint.py:16-105`
- Test: `tests/test_problem_summary.py` (переписать)

**Interfaces:**
- Consumes: `diag.build_digest`, `diag.write_digest`, `diag.load_state`, `diag.save_state`, `diag.report_delivery_status`
- Produces: `problem_summary(cfg, now=None, notify=None, run_errors=None) -> dict` с ключами `digest_path`, `delivery_ok`, `delivery_detail`, `fallback_sent`, `problems`, `skipped_clean`

- [ ] **Step 1: Написать падающие тесты**

Переписать `tests/test_problem_summary.py` целиком:

```python
import json
from datetime import datetime, timezone

from fam import audit, db as famdb, diag, maint


def _ok_probe(name):
    return {"name": name, "status": "ok", "detail": "", "last_ok_ts": None}


def _all_probes_ok(monkeypatch):
    monkeypatch.setattr(maint.health, "all_probes",
                        lambda conn, cfg, now=None:
                        [_ok_probe("bridge"), _ok_probe("starline")])


def _cfg(tmp_path, **overrides):
    cfg = {"daily_budget": 8,
           "backup_dir": str(tmp_path / "backups"),
           "offsite_enabled": False,
           "diagnostics_dir": str(tmp_path / "diagnostics"),
           "report_jobs_path": str(tmp_path / "jobs.json"),
           "report_job_name": "fam-nightly-report"}
    cfg.update(overrides)
    return cfg


def _confirmed_job(tmp_path, last_run_at):
    (tmp_path / "jobs.json").write_text(json.dumps(
        [{"name": "fam-nightly-report", "last_status": "ok",
          "last_run_at": last_run_at, "last_delivery_error": None}]),
        encoding="utf-8")


NOW = datetime(2026, 8, 2, 22, 30, tzinfo=timezone.utc)


def test_writes_digest_instead_of_sending(db, tmp_path, monkeypatch):
    _all_probes_ok(monkeypatch)
    monkeypatch.setattr(maint.diag, "collect_timers",
                        lambda runner=None: {"failed": [], "ok": ["fam-reminders.timer"]})
    famdb.meta_set(db, "maint_digest_last_written", "2026-08-01T22:30:00+00:00")
    db.commit()
    _confirmed_job(tmp_path, "2026-08-02T03:00:00+00:00")
    audit.log(db, "tick.error", {"where": "meds_row", "intake_id": 10,
                                 "exc_type": "KeyError",
                                 "error": "No item with that key"}, actor="tick")
    db.commit()

    sent = []
    out = maint.problem_summary(_cfg(tmp_path), now=NOW,
                                notify=lambda t: sent.append(t) or True)

    assert sent == [], "a confirmed report means fam stays silent"
    assert out["delivery_ok"] is True
    digest = json.loads((tmp_path / "diagnostics" / "fam-digest-latest.json")
                        .read_text(encoding="utf-8"))
    assert digest["sections"]["errors"]["findings"][0]["count"] == 1
    # To the CONFIRMED digest's timestamp, not NOW: tonight's digest has
    # not been delivered yet and its window must stay open.
    assert famdb.meta_get(db, "maint_summary_last_run") == "2026-08-01T22:30:00+00:00"


def test_raw_fallback_when_report_not_confirmed(db, tmp_path, monkeypatch):
    _all_probes_ok(monkeypatch)
    monkeypatch.setattr(maint.diag, "collect_timers",
                        lambda runner=None: {"failed": [], "ok": []})
    famdb.meta_set(db, "maint_digest_last_written", "2026-08-01T22:30:00+00:00")
    famdb.meta_set(db, "maint_summary_last_run", "2026-08-01T22:30:00+00:00")
    db.commit()
    _confirmed_job(tmp_path, "2026-07-01T03:00:00+00:00")   # stale run
    audit.log(db, "tick.error", {"where": "digest", "exc_type": "ValueError",
                                 "error": "boom"}, actor="tick")
    db.commit()

    sent = []
    out = maint.problem_summary(_cfg(tmp_path), now=NOW,
                                notify=lambda t: sent.append(t) or True)

    assert out["delivery_ok"] is False
    assert out["fallback_sent"] is True
    assert len(sent) == 1 and "digest" in sent[0] and "×1" in sent[0]
    assert famdb.meta_get(db, "maint_summary_last_run") == NOW.isoformat(timespec="seconds")


def test_watermark_held_when_fallback_also_fails(db, tmp_path, monkeypatch):
    _all_probes_ok(monkeypatch)
    monkeypatch.setattr(maint.diag, "collect_timers",
                        lambda runner=None: {"failed": [], "ok": []})
    famdb.meta_set(db, "maint_digest_last_written", "2026-08-01T22:30:00+00:00")
    famdb.meta_set(db, "maint_summary_last_run", "2026-08-01T22:30:00+00:00")
    db.commit()
    _confirmed_job(tmp_path, "2026-07-01T03:00:00+00:00")
    audit.log(db, "tick.error", {"where": "digest", "error": "boom"}, actor="tick")
    db.commit()

    out = maint.problem_summary(_cfg(tmp_path), now=NOW, notify=lambda t: False)

    assert out["fallback_sent"] is False
    assert famdb.meta_get(db, "maint_summary_last_run") == "2026-08-01T22:30:00+00:00", \
        "an undelivered problem window must survive into the next sweep"


def test_clean_window_is_silent_and_holds_the_watermark(db, tmp_path, monkeypatch):
    # Daily cadence is the LLM report's job. The fallback stays an
    # emergency channel: silence on a clean night keeps rollout behaviour
    # identical to today's. And with nothing confirmed delivered, the
    # watermark must not move -- see the next test for why.
    _all_probes_ok(monkeypatch)
    monkeypatch.setattr(maint.diag, "collect_timers",
                        lambda runner=None: {"failed": [], "ok": []})
    sent = []
    out = maint.problem_summary(_cfg(tmp_path), now=NOW,
                                notify=lambda t: sent.append(t) or True)
    assert sent == []
    assert out["skipped_clean"] is True
    assert famdb.meta_get(db, "maint_summary_last_run") is None


def test_clean_night_does_not_strand_an_undelivered_digest(db, tmp_path, monkeypatch):
    # The failure this contract exists to prevent. Night A publishes a
    # digest holding a real error. Night B finds that digest undelivered
    # but has a quiet window of its own. If B advanced the watermark,
    # night A's error would fall behind every future window and be lost
    # with no fallback ever attempted for it.
    _all_probes_ok(monkeypatch)
    monkeypatch.setattr(maint.diag, "collect_timers",
                        lambda runner=None: {"failed": [], "ok": []})
    famdb.meta_set(db, "maint_summary_last_run", "2026-07-31T22:30:00+00:00")
    famdb.meta_set(db, "maint_digest_last_written", "2026-08-01T22:30:00+00:00")
    db.commit()
    _confirmed_job(tmp_path, "2026-07-01T03:00:00+00:00")   # never delivered digest A

    sent = []
    out = maint.problem_summary(_cfg(tmp_path), now=NOW,
                                notify=lambda t: sent.append(t) or True)

    assert out["delivery_ok"] is False
    assert sent == [], "a clean window has nothing of its own to report"
    assert famdb.meta_get(db, "maint_summary_last_run") == "2026-07-31T22:30:00+00:00"


def test_run_errors_are_folded_into_the_digest(db, tmp_path, monkeypatch):
    _all_probes_ok(monkeypatch)
    monkeypatch.setattr(maint.diag, "collect_timers",
                        lambda runner=None: {"failed": [], "ok": []})
    out = maint.problem_summary(_cfg(tmp_path), now=NOW,
                                notify=lambda t: True,
                                run_errors=["backup /x: disk full"])
    digest = json.loads((tmp_path / "diagnostics" / "fam-digest-latest.json")
                        .read_text(encoding="utf-8"))
    assert digest["sections"]["maintenance_errors"] == ["backup /x: disk full"]
    assert out["skipped_clean"] is False
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/python -m pytest tests/test_problem_summary.py -v`
Expected: FAIL — старая реализация шлёт текст и не пишет файл

- [ ] **Step 3: Переписать `problem_summary` в `fam/maint.py`**

Заменить строки 16–105 (от `def _summary_watermark` до конца `problem_summary`):

```python
def _summary_watermark(conn, now):
    val = famdb.meta_get(conn, "maint_summary_last_run")
    if val:
        return val
    return (now - timedelta(hours=24)).isoformat(timespec="seconds")


def _problem_lines(digest):
    """Human-readable problem lines for the emergency fallback only --
    the LLM report renders from the digest itself. Kept deliberately
    close to the pre-2026-08 format so a fallback message still reads
    like the summary Denis is used to, plus counts and age."""
    sections = digest["sections"]
    lines = []
    for finding in sections.get("errors", {}).get("findings", []):
        where = finding.get("p_where") or finding.get("kind")
        example = (finding.get("examples") or [""])[0]
        lines.append(f"{where}: {example} (×{finding['count']}, "
                     f"{finding['status']}, {finding['age_days']} дн.)")
    collisions = sections.get("calendar", {}).get("collisions", 0)
    if collisions:
        lines.append(f"календарь: {collisions} совпадающих записей, разобрать вручную")
    for probe in sections.get("probes", []):
        if probe["status"] != "ok":
            lines.append(f"{probe['name']}: {probe['detail'] or probe['status']}")
    for unit in sections.get("timers", {}).get("failed", []):
        lines.append(f"unit {unit['unit']}: {unit['detail']}")
    for err in sections.get("maintenance_errors", []):
        lines.append(f"maintenance: {err}")
    for name, err in digest.get("section_errors", {}).items():
        lines.append(f"сборщик {name}: {err}")
    return lines


def problem_summary(cfg, now=None, notify=None, run_errors=None):
    """Nightly sweep: collect the digest, publish it for the agent's
    `fam-nightly-report` job, and guard the delivery contract.

    Delivery moved out of fam (design 2026-08-01): the LLM report is
    rendered and delivered by the agent. What stays here is the invariant
    this function has always enforced -- the watermark closes a window
    only once its problems reached Denis. The watermark marks how far
    delivery is CONFIRMED, not when we last looked: on a confirmed report
    it moves to the previous digest's own timestamp, on a delivered raw
    fallback to now, and otherwise it does not move at all -- including
    on a clean night, because a clean window says nothing about an
    earlier digest that never arrived.

    The fallback stays an emergency channel and is silent on a clean
    night: daily cadence is the LLM report's contract, not fam's, and
    making fam chatty while the job is being rolled out would be a
    regression against today's behaviour."""
    now = now or _now_utc()
    notify = notify or gate.notify_denis
    conn = famdb.connect()
    try:
        since = _summary_watermark(conn, now)
        previous_digest_at = famdb.meta_get(conn, "maint_digest_last_written")
        delivered, detail = diag.report_delivery_status(
            cfg.get("report_jobs_path", gate.CONFIG_DEFAULTS["report_jobs_path"]),
            cfg.get("report_job_name", gate.CONFIG_DEFAULTS["report_job_name"]),
            previous_digest_at)
        digest, new_state = diag.build_digest(
            conn, cfg, since, now,
            delivery={"previous_report_ok": delivered,
                      "previous_digest_at": previous_digest_at,
                      "detail": detail},
            state=diag.load_state(conn), verify=verify_backup)
        digest["sections"]["maintenance_errors"] = list(run_errors or [])
        diag.save_state(conn, new_state)
        path = diag.write_digest(
            digest,
            cfg.get("diagnostics_dir", diag.DEFAULT_DIAGNOSTICS_DIR), now)
        famdb.meta_set(conn, "maint_digest_last_written", digest["generated_at"])
        conn.commit()

        problems = _problem_lines(digest)
        fallback_sent = False
        if delivered:
            # To the CONFIRMED digest's timestamp, not to now. The digest
            # written moments ago is still unconfirmed: closing the window
            # on it would stake this night's problems on a delivery nobody
            # has verified. Consequence -- consecutive digests overlap by
            # one night, which is the safety margin: an undelivered
            # digest's contents reappear in the next one, deduplicated by
            # signature and marked `known`.
            advance_to = previous_digest_at
        elif problems:
            fallback_sent = bool(notify(
                "Гермес — сводка за сутки (LLM-отчёт не дошёл):\n"
                + "\n".join(f"• {p}" for p in problems)))
            advance_to = now.isoformat(timespec="seconds") if fallback_sent else None
        else:
            # Clean window, report unconfirmed: nothing was delivered, so
            # nothing may be closed. Advancing here is what would strand an
            # earlier undelivered digest behind the watermark forever.
            advance_to = None
        if advance_to:
            famdb.meta_set(conn, "maint_summary_last_run", advance_to)
            conn.commit()
        return {"digest_path": str(path), "delivery_ok": delivered,
                "delivery_detail": detail, "fallback_sent": fallback_sent,
                "problems": problems, "skipped_clean": not problems}
    finally:
        conn.close()
```

Добавить `from . import diag` в шапку `maint.py` рядом с существующими импортами.

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/test_problem_summary.py tests/test_maint.py -v`
Expected: PASS. `test_maint.py` ассертит форму результата `run_maintenance` — если он проверяет `result["summary"]["sent"]`, обновить на `delivery_ok` / `fallback_sent`.

- [ ] **Step 5: Сверить дельту и закоммитить**

```bash
.venv/bin/python -m pytest -q 2>&1 | grep -E "^(FAILED|ERROR)" | sort | diff /tmp/fam-baseline-names.txt -
git add fam/maint.py tests/test_problem_summary.py tests/test_maint.py
git commit -m "feat(fam/maint): publish nightly digest, keep delivery contract via ack"
```

---

## Task 8: Контекст-скрипт для джоба

**Files:**
- Create: `scripts/fam_report_context.py`
- Test: `tests/test_report_context.py`

**Interfaces:**
- Produces: исполняемый скрипт, печатающий в stdout тело дайджеста либо маркер `DIGEST MISSING` / `DIGEST STALE`. Функции `render(digest_path, now) -> str` и `compact_digest(digest) -> dict` для тестов.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_report_context.py`:

```python
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fam_report_context.py"


def _module():
    # Loaded by path, not imported as a package: the script must stay
    # self-contained because the agent runs it with workdir=hermes-agent,
    # where `fam` is not on sys.path.
    spec = importlib.util.spec_from_file_location("fam_report_context", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_file_is_reported(tmp_path):
    out = _module().render(tmp_path / "absent.json", datetime(2026, 8, 2, 3, 0))
    assert out.startswith("DIGEST MISSING")


def test_stale_digest_is_flagged(tmp_path):
    path = tmp_path / "d.json"
    generated = datetime(2026, 8, 1, 3, 0)
    path.write_text(json.dumps({"generated_at": generated.isoformat(),
                                "sections": {}}), encoding="utf-8")
    out = _module().render(path, generated + timedelta(hours=20))
    assert out.startswith("DIGEST STALE")
    assert "age=20h" in out


def test_fresh_digest_passes_through(tmp_path):
    path = tmp_path / "d.json"
    generated = datetime(2026, 8, 1, 22, 30)
    path.write_text(json.dumps(
        {"generated_at": generated.isoformat(),
         "sections": {"errors": {"findings": [], "resolved": []}}}), encoding="utf-8")
    out = _module().render(path, generated + timedelta(hours=4))
    assert not out.startswith("DIGEST")
    assert json.loads(out)["sections"]["errors"] == {"findings": [], "resolved": []}


def test_findings_are_truncated_with_a_visible_marker(tmp_path):
    module = _module()
    findings = [{"signature": f"s{i}", "count": 1, "examples": ["x" * 500]}
                for i in range(module.MAX_FINDINGS + 5)]
    compact = module.compact_digest(
        {"generated_at": "2026-08-01T22:30:00", "sections":
            {"errors": {"findings": findings, "resolved": []}}})
    section = compact["sections"]["errors"]
    assert len(section["findings"]) == module.MAX_FINDINGS
    assert section["findings_truncated"] == 5, "silent truncation would read as full coverage"
    assert len(section["findings"][0]["examples"][0]) == module.EXAMPLE_CHARS
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/python -m pytest tests/test_report_context.py -v`
Expected: FAIL — файла скрипта нет

- [ ] **Step 3: Создать `scripts/fam_report_context.py`**

Note (final review, 2026-08-02): this step originally showed a
`json.dumps(...)[:MAX_CHARS]` blind tail slice, no `resolved_truncated`
marker, and a naive `datetime.now()` in `main()`. All three were found
Critical/Important in review and fixed across two fix rounds (commits
`f86353eaf..65e7a9c17`) -- the blind slice cuts mid-token and hands the
LLM invalid JSON that still looks like a complete digest, which is
exactly the silent-coverage failure this whole feature exists to avoid,
and `datetime.now()` (naive local time, not UTC) mis-measures digest age
against `generated_at`. The block below is the shipped code, not the
original draft -- use it as reference, not the history above.

```python
#!/usr/bin/env python3
"""Print the latest fam diagnostics digest for the nightly reporter prompt.

Deliberately self-contained (stdlib only, no `fam` import): the agent runs
this with workdir=/home/denis/.hermes/hermes-agent, where the fam package
is not importable. Mirrors morning_report_context.py on hermes-agent.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

STALE_HOURS = 12
MAX_CHARS = 24000
MAX_FINDINGS = 30
MAX_RESOLVED = 10
EXAMPLE_CHARS = 200

DEFAULT_PATH = "/home/denis/.hermes/diagnostics/fam-digest-latest.json"


def compact_digest(digest):
    sections = digest.get("sections")
    if not isinstance(sections, dict):
        return digest
    compact = dict(sections)
    errors = sections.get("errors")
    if isinstance(errors, dict):
        findings = errors.get("findings") or []
        trimmed = []
        for finding in findings[:MAX_FINDINGS]:
            finding = dict(finding)
            examples = finding.get("examples")
            if isinstance(examples, list):
                finding["examples"] = [str(e)[:EXAMPLE_CHARS] for e in examples[:1]]
            trimmed.append(finding)
        resolved = errors.get("resolved") or []
        resolved_trimmed = resolved[:MAX_RESOLVED]
        section = {"findings": trimmed, "resolved": resolved_trimmed}
        findings_dropped = len(findings) - len(trimmed)
        if findings_dropped > 0:
            # Never truncate silently: a capped list that does not say so
            # reads to the reporter as full coverage.
            section["findings_truncated"] = findings_dropped
        resolved_dropped = len(resolved) - len(resolved_trimmed)
        if resolved_dropped > 0:
            # Same mechanism, mirrored: a resolved list quietly cut from
            # 25 to 10 would read as "everything open got fixed" unless we
            # say how much was cut.
            section["resolved_truncated"] = resolved_dropped
        compact["errors"] = section
    return {"generated_at": digest.get("generated_at"),
            "window": digest.get("window"),
            "fam_schema_version": digest.get("fam_schema_version"),
            "delivery": digest.get("delivery"),
            "section_errors": digest.get("section_errors", {}),
            "sections": compact}


def _fit_to_budget(compact, budget):
    """Serialise `compact` within `budget` chars, shedding whole findings first.

    A blind tail slice would cut mid-token: the reader gets invalid JSON
    that still looks like a complete digest, which is the silent-coverage
    failure this module exists to avoid. Shedding whole findings keeps the
    structure valid and the loss counted; the hard cut is a last resort and
    announces itself.
    """
    budget = max(budget, 0)
    body = json.dumps(compact, ensure_ascii=False, indent=1)
    # compact_digest() guards `sections` by type, not just by key presence
    # (it returns the digest unchanged when `sections` is present but not a
    # dict). A `.get(key, default)` chain here only covers a MISSING key —
    # `sections` can still be None/[]/a string/a number, and `.get` on any
    # of those raises AttributeError. Check the type at each step instead.
    sections = compact.get("sections")
    errors = sections.get("errors") if isinstance(sections, dict) else None
    if not isinstance(errors, dict):
        errors = None
    while len(body) > budget and isinstance(errors, dict) and errors.get("findings"):
        errors["findings"].pop()
        errors["findings_truncated"] = errors.get("findings_truncated", 0) + 1
        body = json.dumps(compact, ensure_ascii=False, indent=1)
    if len(body) > budget:
        marker = "\n... DIGEST TRUNCATED: output exceeded the prompt budget"
        cut = max(budget - len(marker), 0)
        body = body[:cut] + marker
    return body


def _to_naive_utc(dt):
    """Normalize an aware datetime to naive UTC; leave naive values as-is.

    Naive datetimes are treated as already being UTC (the convention this
    module uses throughout for both `generated_at` and `now`). Dropping
    tzinfo without converting first would keep the aware value's local
    wall-clock hour but silently discard its offset, mis-measuring age by
    that offset (an aware digest carrying a +05:00 offset would otherwise
    look several hours fresher than it really is).
    """
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def render(digest_path, now):
    digest_path = Path(digest_path)
    if not digest_path.exists():
        return f"DIGEST MISSING: collector did not produce {digest_path}"
    try:
        digest = json.loads(digest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return f"DIGEST MISSING: unreadable ({exc})"
    compact = compact_digest(digest)
    generated_raw = str(digest.get("generated_at", ""))
    try:
        generated = datetime.fromisoformat(generated_raw)
    except ValueError:
        prefix = f"DIGEST STALE (generated_at unparseable: {generated_raw!r})\n"
        return prefix + _fit_to_budget(compact, MAX_CHARS - len(prefix))
    # fam writes generated_at with a UTC offset (aware); main() passes an
    # aware datetime.now(timezone.utc). A caller (or a test) may still pass
    # either side as naive, so normalize both independently: naive values
    # are treated as already-UTC, aware values are converted to UTC first.
    # This keeps the subtraction below valid — and correct — for every
    # combination of aware/naive on either side.
    generated = _to_naive_utc(generated)
    now = _to_naive_utc(now)
    age_hours = (now - generated).total_seconds() / 3600
    if age_hours > STALE_HOURS:
        prefix = (f"DIGEST STALE (generated_at={generated_raw}, "
                  f"age={age_hours:.0f}h)\n")
        return prefix + _fit_to_budget(compact, MAX_CHARS - len(prefix))
    return _fit_to_budget(compact, MAX_CHARS)


def main():
    path = os.environ.get("FAM_DIGEST_PATH", "").strip() or DEFAULT_PATH
    print(render(path, datetime.now(timezone.utc)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/test_report_context.py -v`
Expected: PASS

- [ ] **Step 5: Сверить дельту и закоммитить**

```bash
chmod +x scripts/fam_report_context.py
.venv/bin/python -m pytest -q 2>&1 | grep -E "^(FAILED|ERROR)" | sort | diff /tmp/fam-baseline-names.txt -
git add scripts/fam_report_context.py tests/test_report_context.py
git commit -m "feat(fam/scripts): context provider for the nightly report job"
```

---

## Task 9: Выкатка и создание джоба

**Files:** изменений в коде нет; операции на `hermes-home`.

**Предусловие (риск Р1 дизайна):** прод-чекаут был `ahead 1, behind 2` относительно `origin/local/customizations` — незапушенный коммит `fix(fam/extcal): pre-prod hardening`. Развести до мержа.

- [ ] **Step 1: Развести расхождение прод-чекаута**

```bash
ssh hermes-home 'cd ~/.hermes/hermes-agent && git status -sb && git log --oneline origin/local/customizations..HEAD'
```

Решить с Денисом: пушить локальный коммит или отбрасывать. Не мержить фичу поверх нерешённого расхождения.

- [ ] **Step 2: Прогнать полный набор тестов и сверить дельту**

```bash
cd ~/dev/fam-dev/custom/fam
.venv/bin/python -m pytest -q 2>&1 | grep -E "^(FAILED|ERROR)" | sort > /tmp/fam-final.txt
diff /tmp/fam-baseline-names.txt /tmp/fam-final.txt
```

Ожидаемо: пустой diff. Ненулевой — разбирать, не выкатывать.

- [ ] **Step 3: Убедиться, что миграция не нужна**

```bash
git diff local/customizations..HEAD -- fam/db.py | head
```

Ожидаемо: пусто. Если `db.py` изменён — план нарушен, требуется `fam init` до мержа (см. память о деплое).

- [ ] **Step 4: Смержить (= задеплоить)**

```bash
git push origin feature/fam-nightly-llm-report
ssh hermes-home 'cd ~/.hermes/hermes-agent && git fetch origin && git merge --no-ff origin/feature/fam-nightly-llm-report -m "merge: nightly LLM report for fam"'
```

- [ ] **Step 5: Проверить первый дайджест вручную, не дожидаясь таймера**

```bash
ssh hermes-home 'cd ~/.hermes/hermes-agent/custom/fam && ./bin/fam tick maintenance'
ssh hermes-home 'python3 -m json.tool ~/.hermes/diagnostics/fam-digest-latest.json | head -60'
```

Проверить глазами: находка `meds_row` схлопнута с `count`, есть `status`/`age_days`, в файле **нет** названий лекарств, событий и текстов сообщений. Ожидаемо `delivery.previous_report_ok = false` — джоба ещё нет, поэтому придёт сырой откат. Это штатно.

- [ ] **Step 6: Развернуть контекст-скрипт**

```bash
ssh hermes-home 'ln -sf ~/.hermes/hermes-agent/custom/fam/scripts/fam_report_context.py ~/.hermes/scripts/fam_report_context.py && python3 ~/.hermes/scripts/fam_report_context.py | head -30'
```

- [ ] **Step 7: Создать cron-джоб `fam-nightly-report`**

Джоб добавляется через интерфейс планировщика агента (как остальные записи в `~/.hermes/cron/jobs.json`), поля:

```
name:     fam-nightly-report
schedule: {"kind": "cron", "expr": "0 3 * * *"}
script:   fam_report_context.py
model:    gpt-5.6-luna      provider: openai-codex
no_agent: false             deliver:  telegram:79564752
workdir:  /home/denis/.hermes/hermes-agent
prompt:   см. дизайн §9 (скопировать целиком, включая справочник поломок)
```

- [ ] **Step 8: Прогнать джоб вручную и оценить текст отчёта**

Запустить джоб принудительно, прочитать пришедшее сообщение. Критерий приёмки: про `meds_row` сказано, что схема прода отстала и нужен `fam init`, указан масштаб (`×N`, возраст) и что доза не доставляется. Если формулировка расплывчата — править справочник в промпте, а не код.

- [ ] **Step 9: Проверить контур на следующую ночь**

```bash
ssh hermes-home 'python3 - <<EOF
import json,sqlite3,os
c=sqlite3.connect(os.path.expanduser("~/.hermes/private/amina/assistant.db"))
for k in ("maint_summary_last_run","maint_digest_last_written"):
    print(k, c.execute("select value from meta where key=?",(k,)).fetchone())
EOF'
```

Критерий: `previous_report_ok = true` в свежем дайджесте, водяной знак сдвинулся, сырая сводка больше не приходит.

- [ ] **Step 10: Через неделю — проверить `resolved` и размер состояния**

```bash
ssh hermes-home 'python3 -c "
import sqlite3,os,json
c=sqlite3.connect(os.path.expanduser(\"~/.hermes/private/amina/assistant.db\"))
v=c.execute(\"select value from meta where key=?\",(\"maint_known_issues\",)).fetchone()[0]
print(len(json.loads(v)), \"signatures,\", len(v), \"bytes\")"'
```

Критерий: число сигнатур не растёт монотонно (исчезнувшие выбывают через `resolved`).

---

## Self-review

**Покрытие спеки:**

| Раздел дизайна | Задача |
|---|---|
| §3 архитектура, без миграции | 5 (ключи конфига), 7 (оркестрация), 9 (проверка `db.py` не тронут) |
| §4 схема дайджеста, `section_errors` | 4, 5 |
| §5 сигнатуры, статусы | 2, 3 |
| §6 водяной знак, откат, три ветки | 6, 7 |
| §7 приватность, whitelist по видам | 2 (тест `gate_error_never_exposes_message_text`), 4 (тест на `секрет` в activity) |
| §8 `exc_type` | 1 |
| §9 cron-джоб и контекст-скрипт | 8, 9 |
| §10 тесты 1–8 | распределены по задачам 1–8 |
| §11 риски и порядок выкатки | 9 |

**Согласованность имён:** `collect_errors` / `diff_known_issues` / `load_state` / `save_state` / `build_digest` / `write_digest` / `report_delivery_status` / `collect_activity` / `collect_calendar` / `collect_timers` / `collect_backups` — используются в задачах 5 и 7 ровно в том виде, в каком объявлены в 2–4 и 6. `DEFAULT_DIAGNOSTICS_DIR` объявлен в задаче 5 и патчится в `conftest.py` там же. `verify` инжектируется как `maint.verify_backup` (задача 7), сигнатура `(path) -> (bool, dict)` совпадает с существующей.

**Известные хрупкости, принятые осознанно:**
1. `collect_timers` парсит вывод `systemctl list-units` позиционно — минимально хрупкий вариант из доступных, покрыт тестом с фейковым `runner`.
2. Формат `jobs.json` — внутренний формат агента; `report_delivery_status` отвечает `False` на любую неожиданную форму, то есть деградирует в лишнее сырое сообщение, а не в тишину.
