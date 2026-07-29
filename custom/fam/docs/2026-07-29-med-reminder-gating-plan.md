# Med Reminder Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Не присылать напоминания о лекарствах, пока Амина спит или гарантированно не дома, не теряя при этом ни одной дозы.

**Architecture:** Новый модуль `fam/presence.py` даёт два предиката — `awake_since()` и `is_away()`. `tick._meds_series` перед отправкой спрашивает их и при удержании ставит `series_next_utc = now + med_gate_recheck_min` (10 мин) **без отправки и без инкремента попыток**, вместо обычных 45 минут. Дозы, освобождённые одним тиком, уходят одним сообщением; корреляция ack для него живёт в новой таблице `sent_message_refs`.

**Tech Stack:** Python 3.12, sqlite3 (stdlib), pytest. Без новых зависимостей.

**Spec:** `custom/fam/docs/2026-07-29-med-reminder-gating-design.md`

**Worktree:** `~/.hermes/worktrees/med-gating` на хосте `hermes` (`ssh -J proxmox denis@192.168.20.10`), ветка `local/med-gating`. Все команды выполняются на хосте, из `~/.hermes/worktrees/med-gating/custom/fam`.

## Global Constraints

- **Тесты:** `~/.hermes/hermes-agent/venv/bin/python -m pytest tests -q` из `custom/fam`. Полный прогон ~6 минут; при разработке гонять точечно по `-k`.
- **Известный красный baseline:** 3 падения в `tests/test_whereami_recompute.py` (чужая WIP-работа в основном чекауте). Любое ДРУГОЕ падение — регрессия. Перед мержем worktree перебазируется на зелёный HEAD.
- **Часовой пояс:** всё локальное время — `Asia/Almaty` (UTC+5, без DST). В `tick.py` уже есть `ALMATY`, `_parse_utc(s)`, `_today_almaty(now_utc)`.
- **Формат времени в БД:** ISO UTC, `isoformat(timespec="seconds")`, суффикс `+00:00`. Никогда не `Z` — `tick.py` сравнивает `series_next_utc` как голую строку, и `'Z' (0x5A) > '+' (0x2B)` ломает сравнение. Прецедент задокументирован в `meds.defer`.
- **Изоляция строк в тике:** per-row `try/except` в `_meds_series` со `conn.rollback()` — существующая гарантия «упавшая строка остаётся ровно как была». Не ломать.
- **Аудит только на переходах:** в `audit_log` уже 22 829 строк от `tick.reminders`. Ни одна новая запись не должна писаться на каждой перепроверке.
- **Никакого LLM в пути реакций:** `react.py` — детерминированное отображение эмодзи на ack-примитивы.
- **Инвариант эмодзи:** `EMOJI_CONFIRM | EMOJI_SKIP | EMOJI_SNOOZE` обязан быть подмножеством `DIALOGUE_EMOJI` в `plugins/platforms/whatsapp/reactions.py`. Фильтр там срабатывает ДО вызова `react-hook`, поэтому эмодзи, добавленный только в `fam/react.py`, молча никогда не доедет. Проверяется `tests/gateway/test_whatsapp_reactions_normalize.py`.
- **Чтение `state.db` — только read-only и защищённое.** Отсутствие файла, таблицы, строки или любая `sqlite3.Error` означают «нет сигнала» и никогда не роняют тик.
- **При недостатке данных оба гейта пропускают напоминание.** Ложное «спит»/«вне дома» = молчание там, где надо было напомнить.
- **Механика повтора не меняется.** 45-минутный цикл, отсутствие потолка попыток и прохождение сквозь тихие часы — сознательное решение Дениса от 2026-07-16, зафиксированное в докстринге `_meds_series`. Этот план меняет только условия уместности.

## File Structure

| Файл | Ответственность |
|---|---|
| `fam/presence.py` (создать) | Два предиката: `awake_since()`, `is_away()`. Ничего не пишет в БД. |
| `fam/db.py` (изменить) | Колонка `med_intakes.gate_reason`, таблица `sent_message_refs`. |
| `fam/gate.py` (изменить) | Новые ключи `CONFIG_DEFAULTS`; `GATE_MED_VARIATION_INSTRUCTION` в `_build_prompt`; проброс `ref_ids` в `record_sent`. |
| `fam/tick.py` (изменить) | Гейты и склейка при отпускании в `_meds_series`. |
| `fam/react.py` (изменить) | `EMOJI_SNOOZE`, групповой ack через `sent_message_refs`. |
| `fam/meds.py` (изменить) | `list_pending` отдаёт `gate_reason`. |
| `fam-config.example.json` (изменить) | Зеркало новых ключей. |
| `tests/test_meds_gate.py` (создать) | Схема, оба гейта, бэкстопы, склейка. |
| `tests/test_meds_snooze.py` (создать) | ⏰-реакция. |
| `tests/test_gate_med_variation.py` (создать) | Разные формулировки. |

---

### Task 1: Схема и конфиг

Фундамент для всех остальных задач: колонка состояния гейта, таблица групповых ссылок, шесть новых ключей конфига.

**Files:**
- Modify: `fam/db.py` (внутри `init_db`, около `_ensure_column` блока на строках 251-314; DDL `sent_messages` на 190-200)
- Modify: `fam/gate.py:55` (`CONFIG_DEFAULTS`)
- Modify: `fam-config.example.json`
- Test: `tests/test_meds_gate.py` (создать)

**Interfaces:**
- Produces: колонка `med_intakes.gate_reason TEXT NULL`; таблица `sent_message_refs(sent_message_id INTEGER, kind TEXT, ref_id INTEGER)`; ключи конфига `med_wake_gate_enabled`, `med_wake_gate_until`, `med_away_gate_enabled`, `med_away_gate_until`, `med_gate_recheck_min`, `med_snooze_min`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_meds_gate.py`:

```python
"""Sleep- и away-гейты напоминаний о лекарствах.

Спека: docs/2026-07-29-med-reminder-gating-design.md

Следует конвенции test_tick_meds_series.py: gate.deliver
монkeypatch-ится FakeDeliver'ом, реальный hermes-субпроцесс не
трогается.
"""
import json

import pytest

from fam import gate


def test_gate_reason_column_exists(db):
    cols = {r[1] for r in db.execute("PRAGMA table_info(med_intakes)")}
    assert "gate_reason" in cols


def test_sent_message_refs_table_exists(db):
    cols = {r[1] for r in db.execute("PRAGMA table_info(sent_message_refs)")}
    assert cols == {"id", "sent_message_id", "kind", "ref_id"}


def test_new_config_defaults_present():
    d = gate.CONFIG_DEFAULTS
    assert d["med_wake_gate_enabled"] is True
    assert d["med_wake_gate_until"] == "12:00"
    assert d["med_away_gate_enabled"] is True
    assert d["med_away_gate_until"] == "21:00"
    assert d["med_gate_recheck_min"] == 10
    assert d["med_snooze_min"] == 60


def test_example_config_mirrors_new_keys():
    import pathlib
    here = pathlib.Path(__file__).resolve().parent.parent
    cfg = json.loads((here / "fam-config.example.json").read_text())
    for key in ("med_wake_gate_enabled", "med_wake_gate_until",
                "med_away_gate_enabled", "med_away_gate_until",
                "med_gate_recheck_min", "med_snooze_min",
                "whereami_home_radius_km", "whereami_car_fresh_min"):
        assert key in cfg, key
```

- [ ] **Step 2: Прогнать тест, убедиться что падает**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_meds_gate.py -q`
Expected: 4 failed — нет колонки, нет таблицы, нет ключей.

- [ ] **Step 3: Добавить DDL в `fam/db.py`**

В `init_db`, рядом с остальными `_ensure_column` вызовами:

```python
    # Med gating (spec 2026-07-29): why a still-pending dose is being
    # held back by tick._meds_series. NULL = not held. Written only on
    # transition into/out of a hold, never on the 10-minute recheck --
    # audit_log already carries 22k+ tick.reminders rows and a
    # per-recheck audit row per dose would swamp it.
    _ensure_column(conn, "med_intakes", "gate_reason", "gate_reason TEXT")
```

Рядом с DDL `sent_messages` (после `idx_sent_messages_kind_ref`) добавить в ту же SQL-строку схемы:

```sql
CREATE TABLE IF NOT EXISTS sent_message_refs (
  id INTEGER PRIMARY KEY,
  sent_message_id INTEGER NOT NULL
    REFERENCES sent_messages(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('reminder','med')),
  ref_id INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_sent_message_refs_msg
  ON sent_message_refs(sent_message_id);
CREATE INDEX IF NOT EXISTS idx_sent_message_refs_ref
  ON sent_message_refs(kind, ref_id);
```

Почему отдельная таблица, а не список в `ref_id`: `sent_messages.wa_message_id` объявлен `UNIQUE`, а `ref_id` — скалярный `INTEGER NOT NULL`. Одно склеенное сообщение покрывает несколько доз, и другого места для этой связи нет.

- [ ] **Step 4: Добавить ключи в `fam/gate.py`**

В `CONFIG_DEFAULTS`, после блока `"med_repeat_min": 45,`:

```python
    # Med gating (spec 2026-07-29). Оба гейта откладывают ПЕРЕПРОВЕРКУ,
    # а не попытку доставки: удержанная доза получает
    # series_next_utc = now + med_gate_recheck_min и НЕ считается
    # отправленной. Это отделяет "попытку доставки" от "перепроверки
    # условия" -- разделения, которого в _meds_series раньше не было.
    "med_wake_gate_enabled": True,
    # Утренние дозы (плановое время раньше этого) удерживаются, пока нет
    # признака жизни. Тот же момент -- жёсткий бэкстоп: в med_wake_gate_until
    # гейт сдаётся и отправляет независимо от сигналов.
    "med_wake_gate_until": "12:00",
    "med_away_gate_enabled": True,
    # Away-гейт сдаётся здесь, чтобы доза не утекла молча в полуночный
    # missed-closeout.
    "med_away_gate_until": "21:00",
    "med_gate_recheck_min": 10,
    # ⏰-реакция на напоминании о лекарстве откладывает дозу на столько минут.
    "med_snooze_min": 60,
```

- [ ] **Step 5: Отзеркалить в `fam-config.example.json`**

Добавить те же шесть ключей плюс два, которые сегодня работают только на inline-фоллбэках (`gate.load_config` мержит лишь `gate.CONFIG_DEFAULTS`, а `whereami.CONFIG_DEFAULTS` не мержится, поэтому в живом конфиге они ненастраиваемы):

```json
  "med_wake_gate_enabled": true,
  "med_wake_gate_until": "12:00",
  "med_away_gate_enabled": true,
  "med_away_gate_until": "21:00",
  "med_gate_recheck_min": 10,
  "med_snooze_min": 60,
  "whereami_home_radius_km": 0.3,
  "whereami_car_fresh_min": 20
```

- [ ] **Step 6: Прогнать тесты**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_meds_gate.py tests/test_db_meta.py -q`
Expected: PASS.

- [ ] **Step 7: Коммит**

```bash
git add fam/db.py fam/gate.py fam-config.example.json tests/test_meds_gate.py
git commit -m "feat(amina/meds): schema and config for reminder gating"
```

---

### Task 2: `presence.awake_since`

**Files:**
- Create: `fam/presence.py`
- Test: `tests/test_meds_gate.py` (дополнить)

**Interfaces:**
- Produces: `presence.awake_since(conn, cfg, now_utc) -> str | None` — ISO UTC момента первого признака жизни за сегодняшний день Алматы, либо `None`.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_meds_gate.py`:

```python
from fam import audit, presence

# 2026-07-20, Алматы = UTC+5. 10:00 UTC = 15:00 Алматы.
NOW = "2026-07-20T10:00:00+00:00"
# 02:00 UTC = 07:00 Алматы того же дня.
EARLY = "2026-07-20T02:00:00+00:00"
# Вчера по Алматы: 18:00 UTC 19-го = 23:00 Алматы 19-го.
YESTERDAY = "2026-07-19T18:00:00+00:00"

CFG = {
    "target": "whatsapp:+77782110625",
    "state_db_path": "/nonexistent/state.db",
    "med_wake_gate_enabled": True,
    "med_wake_gate_until": "12:00",
    "med_away_gate_enabled": True,
    "med_away_gate_until": "21:00",
    "med_gate_recheck_min": 10,
    "med_snooze_min": 60,
    "med_repeat_min": 45,
    "road_home_lat": 43.197391,
    "road_home_lon": 76.872737,
    "whereami_home_radius_km": 0.3,
    "whereami_car_fresh_min": 20,
}


def _audit_at(db, kind, ts_utc):
    db.execute(
        "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
        (ts_utc, kind, "agent", "{}"))
    db.commit()


def test_awake_none_without_any_signal(db):
    assert presence.awake_since(db, CFG, NOW) is None


@pytest.mark.parametrize("kind", [
    "meds.take", "meds.skip", "meds.defer", "rem.ack_chain",
    "react.handle", "cal.add", "shopping.add", "goals.add",
])
def test_awake_from_each_signal_kind(db, kind):
    _audit_at(db, kind, EARLY)
    assert presence.awake_since(db, CFG, NOW) == EARLY


def test_awake_ignores_yesterday(db):
    _audit_at(db, "meds.take", YESTERDAY)
    assert presence.awake_since(db, CFG, NOW) is None


def test_awake_ignores_tick_and_gate_kinds(db):
    # Эти пишет сам тик -- они не признак того, что Амина проснулась.
    for kind in ("tick.reminders", "tick.med", "gate.sent", "tick.digest"):
        _audit_at(db, kind, EARLY)
    assert presence.awake_since(db, CFG, NOW) is None


def test_awake_returns_earliest_signal(db):
    _audit_at(db, "cal.add", "2026-07-20T04:00:00+00:00")
    _audit_at(db, "meds.take", EARLY)
    assert presence.awake_since(db, CFG, NOW) == EARLY


def test_awake_survives_missing_state_db(db):
    # state_db_path указывает в никуда -- источник "входящее сообщение"
    # обязан деградировать в "нет сигнала", а не бросить.
    assert presence.awake_since(db, CFG, NOW) is None
```

- [ ] **Step 2: Прогнать, убедиться что падает**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_meds_gate.py -q -k awake`
Expected: FAIL — `ImportError: cannot import name 'presence'`.

- [ ] **Step 3: Написать `fam/presence.py`**

```python
"""Присутствие Амины: спит ли она ещё и не вне ли дома.

Спека: docs/2026-07-29-med-reminder-gating-design.md

Два предиката для tick._meds_series. Ничего не пишет в БД -- только
читает. Оба намеренно консервативны: при недостатке данных отвечают
так, чтобы напоминание УШЛО. Ложное "спит" или "вне дома" означает
молчание там, где надо было напомнить, а все лекарства дома и
пропущенная доза дороже лишнего сообщения.

Почему не whereami.resolve_origin: та лестница отвечает на другой
вопрос -- "откуда она поедет на событие" -- имеет горизонт
предсказания whereami_predict_horizon_min и трактует "машина у дома"
как ОТСУТСТВИЕ доказательств, а не как "она дома". Здесь нужен прямой
предикат "прямо сейчас не дома".
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fam.road import _haversine_km

ALMATY = ZoneInfo("Asia/Almaty")

# Виды audit_log, означающие, что Амина что-то сделала: ack/реакция
# либо просьба, которую агент выполнил через fam. Виды, которые пишет
# сам тик (tick.*, gate.*, road.*), сюда не входят -- они не признак
# её активности.
AWAKE_AUDIT_KINDS = (
    "meds.take", "meds.skip", "meds.defer",
    "rem.ack_chain", "rem.cancel_chain",
    "react.handle",
    "cal.add", "cal.update", "cal.cancel",
    "plans.add", "plans.done",
    "shopping.add", "shopping.bought",
    "goals.add", "goals.done", "goals.decline",
    "people.add", "places.add",
)


def _parse(ts):
    return datetime.fromisoformat(ts)


def _almaty_day_bounds_utc(now_utc):
    """(начало, конец) текущих суток Алматы в виде ISO UTC строк."""
    local = _parse(now_utc).astimezone(ALMATY)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return (start_local.astimezone(timezone.utc).isoformat(timespec="seconds"),
            end_local.astimezone(timezone.utc).isoformat(timespec="seconds"))


def _inbound_message_ts(cfg, day_start, day_end):
    """Самое раннее входящее сообщение Амины за сутки, из state.db гейтвея.

    Строго защищённо: отсутствующий файл, таблица, routing-строка или
    любая sqlite3.Error означают "нет сигнала". Этот тик не имеет права
    падать из-за чужой БД.

    На 2026-07-29 источник дремлющий: routing-строки
    agent:main:whatsapp:dm:<номер> в state.db ещё нет, потому что Амина
    отвечает реакциями, а не текстом. Оживёт сам, когда она напишет.
    """
    import json

    path = cfg.get("state_db_path")
    if not path:
        return None
    target = str(cfg.get("target", ""))
    number = target.split(":")[-1].lstrip("+")
    if not number:
        return None
    session_key = f"agent:main:whatsapp:dm:{number}"

    conn = None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        row = conn.execute(
            "SELECT entry_json FROM gateway_routing WHERE session_key=?",
            (session_key,)).fetchone()
        if row is None:
            return None
        session_id = json.loads(row[0]).get("session_id")
        if not session_id:
            return None
        lo = _parse(day_start).timestamp()
        hi = _parse(day_end).timestamp()
        got = conn.execute(
            "SELECT MIN(timestamp) FROM messages "
            "WHERE session_id=? AND role='user' "
            "AND timestamp >= ? AND timestamp < ?",
            (session_id, lo, hi)).fetchone()
        if got is None or got[0] is None:
            return None
        return datetime.fromtimestamp(got[0], timezone.utc).isoformat(
            timespec="seconds")
    except (sqlite3.Error, ValueError, TypeError, OSError):
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def awake_since(conn, cfg, now_utc):
    """Момент первого признака жизни Амины за сегодняшние сутки Алматы,
    ISO UTC, либо None.

    Источники (берётся самый ранний):
      * строка audit_log вида из AWAKE_AUDIT_KINDS -- ack, реакция или
        просьба, выполненная агентом через fam;
      * входящее сообщение в state.db гейтвея (_inbound_message_ts).
    """
    day_start, day_end = _almaty_day_bounds_utc(now_utc)

    placeholders = ",".join("?" * len(AWAKE_AUDIT_KINDS))
    row = conn.execute(
        f"SELECT MIN(ts_utc) FROM audit_log "
        f"WHERE kind IN ({placeholders}) AND ts_utc >= ? AND ts_utc < ?",
        (*AWAKE_AUDIT_KINDS, day_start, day_end)).fetchone()
    candidates = [row[0]] if row and row[0] else []

    inbound = _inbound_message_ts(cfg, day_start, day_end)
    if inbound:
        candidates.append(inbound)

    return min(candidates) if candidates else None
```

- [ ] **Step 4: Прогнать тесты**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_meds_gate.py -q -k awake`
Expected: PASS (все `awake`-тесты).

- [ ] **Step 5: Коммит**

```bash
git add fam/presence.py tests/test_meds_gate.py
git commit -m "feat(amina/meds): presence.awake_since -- признак пробуждения"
```

---

### Task 3: `presence.is_away`

**Files:**
- Modify: `fam/presence.py`
- Test: `tests/test_meds_gate.py` (дополнить)

**Interfaces:**
- Consumes: `presence.ALMATY`, `presence._parse`, `road._haversine_km` (Task 2).
- Produces: `presence.is_away(conn, cfg, now_utc) -> (bool, str, str | None)` — `(away, reason, expected_home_utc)`. `reason` ∈ `{"event", "car_gps", "shared_location", "home_or_unknown"}`.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_meds_gate.py`:

```python
HOME_LAT, HOME_LON = 43.197391, 76.872737
# ~6 км от дома.
AWAY_LAT, AWAY_LON = 43.250000, 76.900000


def _add_place(db, name, lat, lon):
    cur = db.execute(
        "INSERT INTO places(name, address, lat, lon, created_at) "
        "VALUES(?,?,?,?,?)", (name, "", lat, lon, NOW))
    return cur.lastrowid


def _add_event(db, title, start_utc, end_utc, place_id=None, travel_min=None):
    cur = db.execute(
        "INSERT INTO events(title, start_utc, end_utc, place_id, status, "
        "travel_min, created_at) VALUES(?,?,?,?,'active',?,?)",
        (title, start_utc, end_utc, place_id, travel_min, NOW))
    return cur.lastrowid


def _add_car_metric(db, ts_utc, lat, lon, gps_ts=None):
    db.execute(
        "INSERT INTO car_metrics(ts_utc, gps_lat, gps_lon, gps_ts) "
        "VALUES(?,?,?,?)", (ts_utc, lat, lon, gps_ts or ts_utc))
    db.commit()


def test_away_false_with_no_evidence(db):
    away, reason, _ = presence.is_away(db, CFG, NOW)
    assert away is False
    assert reason == "home_or_unknown"


def test_away_true_during_event_at_remote_place(db):
    pid = _add_place(db, "Зал", AWAY_LAT, AWAY_LON)
    _add_event(db, "Тренировка", "2026-07-20T09:30:00+00:00",
               "2026-07-20T10:30:00+00:00", place_id=pid, travel_min=20)
    db.commit()

    away, reason, expected_home = presence.is_away(db, CFG, NOW)
    assert away is True
    assert reason == "event"
    # конец 10:30 UTC + 20 минут дороги
    assert expected_home == "2026-07-20T10:50:00+00:00"


def test_away_false_during_event_at_home_place(db):
    pid = _add_place(db, "Дом", HOME_LAT, HOME_LON)
    _add_event(db, "Уборка", "2026-07-20T09:30:00+00:00",
               "2026-07-20T10:30:00+00:00", place_id=pid)
    db.commit()
    assert presence.is_away(db, CFG, NOW)[0] is False


def test_away_false_for_event_without_place(db):
    # Событие без места отлучкой не считается -- выбор Дениса 2026-07-29.
    _add_event(db, "Созвон", "2026-07-20T09:30:00+00:00",
               "2026-07-20T10:30:00+00:00", place_id=None)
    db.commit()
    assert presence.is_away(db, CFG, NOW)[0] is False


def test_away_false_for_event_already_over(db):
    pid = _add_place(db, "Зал", AWAY_LAT, AWAY_LON)
    _add_event(db, "Тренировка", "2026-07-20T07:00:00+00:00",
               "2026-07-20T08:00:00+00:00", place_id=pid)
    db.commit()
    assert presence.is_away(db, CFG, NOW)[0] is False


def test_away_true_on_fresh_car_gps_far_from_home(db):
    _add_car_metric(db, "2026-07-20T09:55:00+00:00", AWAY_LAT, AWAY_LON)
    away, reason, expected_home = presence.is_away(db, CFG, NOW)
    assert away is True
    assert reason == "car_gps"
    assert expected_home is None


def test_away_false_on_stale_car_gps(db):
    # 09:00 при NOW=10:00 -- старше whereami_car_fresh_min (20 мин).
    _add_car_metric(db, "2026-07-20T09:00:00+00:00", AWAY_LAT, AWAY_LON)
    assert presence.is_away(db, CFG, NOW)[0] is False


def test_away_false_when_car_is_at_home(db):
    _add_car_metric(db, "2026-07-20T09:55:00+00:00", HOME_LAT, HOME_LON)
    assert presence.is_away(db, CFG, NOW)[0] is False


def test_away_true_on_unexpired_shared_location(db):
    db.execute(
        "INSERT INTO location_hints(source, lat, lon, label, ts_utc, "
        "expires_utc) VALUES('shared',?,?,'',?,?)",
        (AWAY_LAT, AWAY_LON, "2026-07-20T09:30:00+00:00",
         "2026-07-20T11:00:00+00:00"))
    db.commit()
    away, reason, _ = presence.is_away(db, CFG, NOW)
    assert away is True
    assert reason == "shared_location"


def test_away_false_on_expired_shared_location(db):
    db.execute(
        "INSERT INTO location_hints(source, lat, lon, label, ts_utc, "
        "expires_utc) VALUES('shared',?,?,'',?,?)",
        (AWAY_LAT, AWAY_LON, "2026-07-20T07:00:00+00:00",
         "2026-07-20T09:00:00+00:00"))
    db.commit()
    assert presence.is_away(db, CFG, NOW)[0] is False
```

- [ ] **Step 2: Прогнать, убедиться что падает**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_meds_gate.py -q -k away`
Expected: FAIL — `AttributeError: module 'fam.presence' has no attribute 'is_away'`.

- [ ] **Step 3: Дописать `is_away` в `fam/presence.py`**

```python
def _far_from_home(cfg, lat, lon):
    """True, если точка дальше whereami_home_radius_km от дома.

    Отсутствующие координаты (None) -- не доказательство отлучки.
    """
    if lat is None or lon is None:
        return False
    home_lat = cfg.get("road_home_lat")
    home_lon = cfg.get("road_home_lon")
    if home_lat is None or home_lon is None:
        return False
    radius = float(cfg.get("whereami_home_radius_km", 0.3))
    return _haversine_km(lat, lon, home_lat, home_lon) > radius


def is_away(conn, cfg, now_utc):
    """(away, reason, expected_home_utc) -- уверенно ли Амина не дома.

    Только уверенные признаки, в порядке проверки:
      1. "event" -- идёт событие с place_id, чьи координаты дальше
         радиуса. expected_home = end_utc + travel_min.
      2. "car_gps" -- свежая (не старше whereami_car_fresh_min) строка
         car_metrics с координатами дальше радиуса. expected_home
         неизвестен.
      3. "shared_location" -- неистёкшая строка location_hints дальше
         радиуса.
    Иначе (False, "home_or_unknown", None).

    Событие БЕЗ места отлучкой не считается: слишком много домашних дел
    попадает в календарь, а ложное "вне дома" стоит дороже лишнего
    сообщения (все лекарства дома).
    """
    now = _parse(now_utc)

    row = conn.execute(
        "SELECT e.end_utc AS end_utc, e.travel_min AS travel_min, "
        "       p.lat AS lat, p.lon AS lon "
        "FROM events e JOIN places p ON p.id = e.place_id "
        "WHERE e.status='active' AND e.place_id IS NOT NULL "
        "  AND e.start_utc <= ? AND e.end_utc >= ? "
        "ORDER BY e.start_utc LIMIT 1",
        (now_utc, now_utc)).fetchone()
    if row is not None and _far_from_home(cfg, row["lat"], row["lon"]):
        expected = None
        if row["end_utc"]:
            back = _parse(row["end_utc"]) + timedelta(
                minutes=int(row["travel_min"] or 0))
            expected = back.astimezone(timezone.utc).isoformat(
                timespec="seconds")
        return (True, "event", expected)

    fresh_min = int(cfg.get("whereami_car_fresh_min", 20))
    cutoff = (now - timedelta(minutes=fresh_min)).isoformat(timespec="seconds")
    car = conn.execute(
        "SELECT gps_lat, gps_lon FROM car_metrics "
        "WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL "
        "  AND COALESCE(gps_ts, ts_utc) >= ? "
        "ORDER BY COALESCE(gps_ts, ts_utc) DESC LIMIT 1",
        (cutoff,)).fetchone()
    if car is not None and _far_from_home(cfg, car["gps_lat"], car["gps_lon"]):
        return (True, "car_gps", None)

    hint = conn.execute(
        "SELECT lat, lon FROM location_hints "
        "WHERE expires_utc > ? ORDER BY ts_utc DESC LIMIT 1",
        (now_utc,)).fetchone()
    if hint is not None and _far_from_home(cfg, hint["lat"], hint["lon"]):
        return (True, "shared_location", None)

    return (False, "home_or_unknown", None)
```

- [ ] **Step 4: Прогнать тесты**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_meds_gate.py -q`
Expected: PASS.

Если тесты падают на именах колонок в `events` / `location_hints` / `car_metrics` — сверить фактическую схему командой `PRAGMA table_info(...)` и поправить SQL, а не тест.

- [ ] **Step 5: Коммит**

```bash
git add fam/presence.py tests/test_meds_gate.py
git commit -m "feat(amina/meds): presence.is_away -- уверенное определение отлучки"
```

---

### Task 4: Sleep-гейт в `_meds_series`

**Files:**
- Modify: `fam/tick.py` (ветка `else:` «take» на строках ~1003-1021)
- Test: `tests/test_meds_gate.py` (дополнить)

**Interfaces:**
- Consumes: `presence.awake_since` (Task 2); колонка `gate_reason`, ключи конфига (Task 1).
- Produces: приватный `tick._gate_hold(conn, intake_id, reason, now_dt, cfg) -> str` — ставит `series_next_utc = now + med_gate_recheck_min`, пишет `gate_reason`, аудитит `med.gate_hold` только при смене причины, возвращает новое `series_next_utc`. Используется и Task 5.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_meds_gate.py`:

```python
from fam import meds, tick


class FakeDeliver:
    def __init__(self):
        self.calls = []
        self.responses = []

    def __call__(self, conn, kind, raw, human_fallback, cfg, force=False,
                 now_utc=None, sent_ref=None):
        self.calls.append({"kind": kind, "raw": raw,
                           "human_fallback": human_fallback,
                           "sent_ref": sent_ref})
        return self.responses.pop(0) if self.responses else "sent"


@pytest.fixture()
def fake_deliver(monkeypatch):
    fd = FakeDeliver()
    monkeypatch.setattr(gate, "deliver", fd)
    return fd


def _pending_intake(db, name="Эутирокс", plan="2026-07-20T04:00:00+00:00",
                    times=("09:00",)):
    """Доза на 09:00 Алматы = 04:00 UTC, готовая к отправке."""
    med_id = meds.add(db, name, list(times), remaining=10)
    cur = db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, status, "
        "series_next_utc, created_at) VALUES(?,?,'pending',?,?)",
        (med_id, plan, plan, plan))
    db.commit()
    return cur.lastrowid


def _intake(db, intake_id):
    return db.execute("SELECT * FROM med_intakes WHERE id=?",
                      (intake_id,)).fetchone()


# 05:00 UTC = 10:00 Алматы -- утро, доза на 09:00 уже просрочена.
MORNING = "2026-07-20T05:00:00+00:00"
# 07:00 UTC = 12:00 Алматы ровно -- момент сдачи sleep-гейта.
NOON = "2026-07-20T07:00:00+00:00"


def test_sleep_gate_holds_morning_dose_without_signal(db, fake_deliver):
    intake_id = _pending_intake(db)
    tick._meds_series(db, MORNING, CFG)

    assert fake_deliver.calls == []
    row = _intake(db, intake_id)
    assert row["status"] == "pending"
    assert row["gate_reason"] == "asleep"
    # перепроверка через 10 минут, а не через 45
    assert row["series_next_utc"] == "2026-07-20T05:10:00+00:00"


def test_sleep_gate_releases_after_signal(db, fake_deliver):
    intake_id = _pending_intake(db)
    tick._meds_series(db, MORNING, CFG)
    assert fake_deliver.calls == []

    _audit_at(db, "cal.add", "2026-07-20T05:05:00+00:00")
    tick._meds_series(db, "2026-07-20T05:10:00+00:00", CFG)

    assert len(fake_deliver.calls) == 1
    row = _intake(db, intake_id)
    assert row["gate_reason"] is None
    # после реальной отправки -- обычные 45 минут
    assert row["series_next_utc"] == "2026-07-20T05:55:00+00:00"


def test_sleep_gate_gives_up_at_med_wake_gate_until(db, fake_deliver):
    _pending_intake(db)
    tick._meds_series(db, NOON, CFG)
    assert len(fake_deliver.calls) == 1


def test_sleep_gate_ignores_afternoon_dose(db, fake_deliver):
    # Доза на 14:00 Алматы = 09:00 UTC; гейт её не касается.
    _pending_intake(db, name="Магний", plan="2026-07-20T09:00:00+00:00",
                    times=("14:00",))
    tick._meds_series(db, "2026-07-20T09:00:00+00:00", CFG)
    assert len(fake_deliver.calls) == 1


def test_sleep_gate_disabled_by_config(db, fake_deliver):
    cfg = {**CFG, "med_wake_gate_enabled": False}
    _pending_intake(db)
    tick._meds_series(db, MORNING, cfg)
    assert len(fake_deliver.calls) == 1


def test_hold_does_not_spend_budget_or_audit_twice(db, fake_deliver):
    _pending_intake(db)
    tick._meds_series(db, MORNING, CFG)
    tick._meds_series(db, "2026-07-20T05:10:00+00:00", CFG)
    tick._meds_series(db, "2026-07-20T05:20:00+00:00", CFG)

    holds = db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='med.gate_hold'"
    ).fetchone()[0]
    assert holds == 1, "аудит только на переходе, не на каждой перепроверке"
    sent = db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='gate.sent'").fetchone()[0]
    assert sent == 0
```

- [ ] **Step 2: Прогнать, убедиться что падает**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_meds_gate.py -q -k sleep_gate`
Expected: FAIL — доза отправляется, `gate_reason` отсутствует.

- [ ] **Step 3: Добавить хелперы в `fam/tick.py`**

Рядом с `_today_almaty`:

```python
def _local_hhmm_before(ts_utc, hhmm):
    """True, если локальное время Алматы у ts_utc строго раньше "HH:MM"."""
    local = _parse_utc(ts_utc).astimezone(ALMATY)
    hh, mm = (int(p) for p in hhmm.split(":"))
    return (local.hour, local.minute) < (hh, mm)


def _gate_hold(conn, intake_id, reason, now_dt, cfg, prev_reason):
    """Удержать дозу: перепроверить через med_gate_recheck_min, НЕ отправляя.

    Это ключевое отличие от обычного цикла: 45-минутный шаг означает
    "мы отправили и ждём"; здесь мы не отправляли, поэтому шаг короткий
    -- иначе доза, отпущенная в 12:00:01, ждала бы до 12:45.

    med.gate_hold аудитится ТОЛЬКО при смене причины. audit_log уже
    несёт 22k+ строк tick.reminders; запись на каждой десятиминутной
    перепроверке по каждой дозе утопила бы его.
    """
    recheck = int(cfg.get("med_gate_recheck_min", 10))
    next_utc = (now_dt + timedelta(minutes=recheck)).isoformat(
        timespec="seconds")
    conn.execute(
        "UPDATE med_intakes SET series_next_utc=?, gate_reason=? "
        "WHERE id=? AND status='pending'",
        (next_utc, reason, intake_id))
    if prev_reason != reason:
        audit.log(conn, "med.gate_hold",
                  {"intake_id": intake_id, "reason": reason,
                   "recheck_at": next_utc})
    return next_utc
```

- [ ] **Step 4: Вставить гейт в ветку «take»**

В `_meds_series`, в `else:`-ветке (та, что сейчас начинается с `raw = {"mode": "take", ...}`), ПЕРЕД построением `raw`:

```python
            else:
                prev_reason = row["gate_reason"]
                hold_reason = None

                # Sleep-гейт: утренняя доза ждёт признака жизни, но не
                # дольше med_wake_gate_until -- это одновременно граница
                # "утренних" доз и жёсткий бэкстоп, чтобы доза не утекла
                # молча в полуночный missed-closeout.
                if (cfg.get("med_wake_gate_enabled", True)
                        and _local_hhmm_before(row["plan_ts_utc"],
                                               cfg.get("med_wake_gate_until",
                                                       "12:00"))
                        and _local_hhmm_before(now_utc,
                                               cfg.get("med_wake_gate_until",
                                                       "12:00"))
                        and presence.awake_since(conn, cfg, now_utc) is None):
                    hold_reason = "asleep"

                if hold_reason is not None:
                    _gate_hold(conn, intake_id, hold_reason, now_dt, cfg,
                               prev_reason)
                    conn.commit()
                    continue

                if prev_reason is not None:
                    conn.execute(
                        "UPDATE med_intakes SET gate_reason=NULL WHERE id=?",
                        (intake_id,))
                    audit.log(conn, "med.gate_release",
                              {"intake_id": intake_id, "was": prev_reason})

                raw = {"mode": "take", "name": name, "dose": dose}
                ...  # существующий код без изменений
```

Добавить `from fam import presence` в импорты `tick.py`.

- [ ] **Step 5: Прогнать тесты**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_meds_gate.py tests/test_tick_meds_series.py -q`
Expected: PASS. `test_tick_meds_series.py` обязан остаться зелёным — его `CFG` не содержит новых ключей, поэтому `cfg.get(..., default)` должен давать рабочее поведение. Если он покраснел, значит гейт применился там, где не должен.

- [ ] **Step 6: Коммит**

```bash
git add fam/tick.py tests/test_meds_gate.py
git commit -m "feat(amina/meds): sleep-гейт -- не будить напоминаниями"
```

---

### Task 5: Away-гейт в `_meds_series`

**Files:**
- Modify: `fam/tick.py` (та же ветка «take», сразу после sleep-гейта)
- Test: `tests/test_meds_gate.py` (дополнить)

**Interfaces:**
- Consumes: `presence.is_away` (Task 3), `tick._gate_hold`, `tick._local_hhmm_before` (Task 4).
- Produces: значение `gate_reason == "away"`.

- [ ] **Step 1: Написать падающие тесты**

```python
# 16:00 UTC = 21:00 Алматы ровно -- момент сдачи away-гейта.
AWAY_GIVEUP = "2026-07-20T16:00:00+00:00"
# 11:00 UTC = 16:00 Алматы -- день, sleep-гейт неприменим.
AFTERNOON = "2026-07-20T11:00:00+00:00"


def test_away_gate_holds_during_remote_event(db, fake_deliver):
    pid = _add_place(db, "Зал", AWAY_LAT, AWAY_LON)
    _add_event(db, "Тренировка", "2026-07-20T10:30:00+00:00",
               "2026-07-20T11:30:00+00:00", place_id=pid, travel_min=20)
    intake_id = _pending_intake(db, name="Магний",
                                plan="2026-07-20T11:00:00+00:00",
                                times=("16:00",))
    tick._meds_series(db, AFTERNOON, CFG)

    assert fake_deliver.calls == []
    row = _intake(db, intake_id)
    assert row["gate_reason"] == "away"
    assert row["series_next_utc"] == "2026-07-20T11:10:00+00:00"


def test_away_gate_releases_when_event_over(db, fake_deliver):
    pid = _add_place(db, "Зал", AWAY_LAT, AWAY_LON)
    _add_event(db, "Тренировка", "2026-07-20T10:30:00+00:00",
               "2026-07-20T11:30:00+00:00", place_id=pid, travel_min=20)
    _pending_intake(db, name="Магний", plan="2026-07-20T11:00:00+00:00",
                    times=("16:00",))
    tick._meds_series(db, AFTERNOON, CFG)
    assert fake_deliver.calls == []

    # 11:40 UTC -- событие кончилось в 11:30
    tick._meds_series(db, "2026-07-20T11:40:00+00:00", CFG)
    assert len(fake_deliver.calls) == 1


def test_away_gate_releases_when_car_returns_home(db, fake_deliver):
    _add_car_metric(db, "2026-07-20T10:55:00+00:00", AWAY_LAT, AWAY_LON)
    _pending_intake(db, name="Магний", plan="2026-07-20T11:00:00+00:00",
                    times=("16:00",))
    tick._meds_series(db, AFTERNOON, CFG)
    assert fake_deliver.calls == []

    _add_car_metric(db, "2026-07-20T11:05:00+00:00", HOME_LAT, HOME_LON)
    tick._meds_series(db, "2026-07-20T11:10:00+00:00", CFG)
    assert len(fake_deliver.calls) == 1


def test_away_gate_gives_up_at_med_away_gate_until(db, fake_deliver):
    pid = _add_place(db, "Зал", AWAY_LAT, AWAY_LON)
    _add_event(db, "Долгая встреча", "2026-07-20T10:00:00+00:00",
               "2026-07-20T18:00:00+00:00", place_id=pid)
    _pending_intake(db, name="Магний", plan="2026-07-20T11:00:00+00:00",
                    times=("16:00",))
    tick._meds_series(db, AWAY_GIVEUP, CFG)
    assert len(fake_deliver.calls) == 1, "в 21:00 гейт обязан сдаться"


def test_away_gate_disabled_by_config(db, fake_deliver):
    cfg = {**CFG, "med_away_gate_enabled": False}
    pid = _add_place(db, "Зал", AWAY_LAT, AWAY_LON)
    _add_event(db, "Тренировка", "2026-07-20T10:30:00+00:00",
               "2026-07-20T11:30:00+00:00", place_id=pid)
    _pending_intake(db, name="Магний", plan="2026-07-20T11:00:00+00:00",
                    times=("16:00",))
    tick._meds_series(db, AFTERNOON, cfg)
    assert len(fake_deliver.calls) == 1
```

- [ ] **Step 2: Прогнать, убедиться что падает**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_meds_gate.py -q -k away_gate`
Expected: FAIL — доза отправляется несмотря на отлучку.

- [ ] **Step 3: Дописать away-гейт в `_meds_series`**

Сразу после блока sleep-гейта, до `if hold_reason is not None:`:

```python
                # Away-гейт: все лекарства дома, поэтому напоминание в
                # чужом месте -- чистый шум. Сдаётся в med_away_gate_until,
                # чтобы доза не утекла молча в полуночный closeout.
                if (hold_reason is None
                        and cfg.get("med_away_gate_enabled", True)
                        and _local_hhmm_before(now_utc,
                                               cfg.get("med_away_gate_until",
                                                       "21:00"))
                        and presence.is_away(conn, cfg, now_utc)[0]):
                    hold_reason = "away"
```

- [ ] **Step 4: Прогнать тесты**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_meds_gate.py tests/test_tick_meds_series.py tests/test_tick.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add fam/tick.py tests/test_meds_gate.py
git commit -m "feat(amina/meds): away-гейт -- не напоминать, когда лекарств рядом нет"
```

---

### Task 6: Склейка при отпускании и групповой ack

Без этого отпускание в 12:00 при трёх лекарствах даёт три сообщения подряд — ровно ту «кучу», ради устранения которой всё затевалось.

**Files:**
- Modify: `fam/tick.py` (`_meds_series`), `fam/react.py` (`record_sent`, `handle`), `fam/gate.py` (`deliver`, проброс `ref_ids`)
- Test: `tests/test_meds_gate.py` (дополнить)

**Interfaces:**
- Consumes: `sent_message_refs` (Task 1), `gate_reason` (Task 4/5).
- Produces: `react.record_sent(conn, wa_message_id, kind, ref_id, event_id=None, chat_jid="", now_utc=None, ref_ids=None)`; `sent_ref` теперь может нести `"ref_ids": [int, ...]`.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_released_doses_merge_into_one_message(db, fake_deliver):
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    tick._meds_series(db, MORNING, CFG)
    assert fake_deliver.calls == []

    _audit_at(db, "cal.add", "2026-07-20T05:05:00+00:00")
    tick._meds_series(db, "2026-07-20T05:10:00+00:00", CFG)

    assert len(fake_deliver.calls) == 1
    call = fake_deliver.calls[0]
    assert call["raw"]["mode"] == "take_group"
    assert {i["name"] for i in call["raw"]["items"]} == {"Эутирокс", "Магний"}
    assert sorted(call["sent_ref"]["ref_ids"]) == sorted([a, b])
    assert "Эутирокс" in call["human_fallback"]
    assert "Магний" in call["human_fallback"]


def test_ordinary_repeats_stay_separate(db, fake_deliver):
    _pending_intake(db, name="Эутирокс", plan="2026-07-20T11:00:00+00:00",
                    times=("16:00",))
    _pending_intake(db, name="Магний", plan="2026-07-20T11:00:00+00:00",
                    times=("16:00",))
    tick._meds_series(db, AFTERNOON, CFG)
    # обе дозы не удерживались -- значит два отдельных сообщения
    assert len(fake_deliver.calls) == 2
    assert all(c["raw"]["mode"] == "take" for c in fake_deliver.calls)


def test_group_reaction_acks_every_dose(db):
    from fam import react
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    react.record_sent(db, "wa1", "med", a, ref_ids=[a, b])
    db.commit()

    out = react.handle(db, "wa1", "\U0001F44D")

    assert out["result"] == "confirmed"
    assert _intake(db, a)["status"] == "taken"
    assert _intake(db, b)["status"] == "taken"


def test_group_reaction_skips_already_acked_member(db):
    from fam import react
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    meds.take(db, a, now_utc=MORNING)
    react.record_sent(db, "wa1", "med", a, ref_ids=[a, b])
    db.commit()

    out = react.handle(db, "wa1", "\U0001F44D")

    assert out["result"] == "confirmed"
    assert _intake(db, b)["status"] == "taken"
```

- [ ] **Step 2: Прогнать, убедиться что падает**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_meds_gate.py -q -k "merge or group or separate"`
Expected: FAIL.

- [ ] **Step 3: Научить `react.record_sent` писать групповые ссылки**

```python
def record_sent(conn, wa_message_id, kind, ref_id, event_id=None,
                chat_jid=None, now_utc=None, ref_ids=None):
    """... (существующий докстринг) ...

    ref_ids: полный список целей, если это ОДНО сообщение, покрывающее
    несколько доз (склейка при отпускании гейта, tick._meds_series).
    sent_messages.ref_id хранит первую цель ради совместимости; полный
    список ложится в sent_message_refs, потому что wa_message_id
    объявлен UNIQUE, а ref_id -- скалярный.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO sent_messages("
        "  wa_message_id, chat_jid, kind, ref_id, event_id, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (wa_message_id, chat_jid or "", kind, ref_id, event_id,
         now_utc or _now()))
    if not ref_ids or len(ref_ids) <= 1:
        return
    row = conn.execute("SELECT id FROM sent_messages WHERE wa_message_id=?",
                       (wa_message_id,)).fetchone()
    if row is None:
        return
    for rid in ref_ids:
        conn.execute(
            "INSERT INTO sent_message_refs(sent_message_id, kind, ref_id) "
            "VALUES(?,?,?)", (row["id"], kind, rid))
```

- [ ] **Step 4: Научить `react.handle` работать с группой**

В ветке `else:  # med` заменить одиночный `row["ref_id"]` на список целей:

```python
    else:  # med
        targets = [r["ref_id"] for r in conn.execute(
            "SELECT ref_id FROM sent_message_refs "
            "WHERE sent_message_id=? AND kind='med' ORDER BY ref_id",
            (row["id"],))] or [row["ref_id"]]

        applied = 0
        for rid in targets:
            try:
                if action == "confirm":
                    take_out = meds.take(conn, rid, now_utc=now_utc)
                    detail.update({k: take_out[k]
                                   for k in ("remaining", "restock")
                                   if isinstance(take_out, dict)
                                   and k in take_out})
                else:
                    meds.skip(conn, rid)
                applied += 1
            except ValueError:
                # Эта доза уже ушла из 'pending' другой дверью
                # (устная "выпила", полуночный closeout). Для группы это
                # нормально: остальные её члены всё ещё ждут.
                continue

        if applied == 0:
            conn.execute(
                "UPDATE sent_messages SET ack_status='confirmed' "
                "WHERE kind=? AND ref_id=? AND ack_status='none'",
                (row["kind"], row["ref_id"]))
            out = {**base, "result": "already_acked", "reason": "not_pending"}
            audit.log(conn, "react.handle", out)
            conn.commit()
            return out

        detail["applied"] = applied
        result = "confirmed" if action == "confirm" else "skipped"
        new_status = result
```

Затем в блоке «пометить все записанные сообщения» ветка для `med` должна пройтись по всем `targets`:

```python
    else:
        for rid in targets:
            conn.execute(
                "UPDATE sent_messages SET ack_status=? "
                "WHERE kind='med' AND ref_id=? AND ack_status='none'",
                (new_status, rid))
```

- [ ] **Step 5: Пробросить `ref_ids` через `gate.deliver`**

В `deliver`, в блоке `if sent_ref:`:

```python
            react.record_sent(
                conn, message_id, sent_ref["kind"], sent_ref["ref_id"],
                event_id=sent_ref.get("event_id"),
                chat_jid=cfg.get("target", ""), now_utc=now,
                ref_ids=sent_ref.get("ref_ids"))
```

- [ ] **Step 6: Собрать группу в `_meds_series`**

Заменить немедленную отправку освобождённой дозы на накопление. В ветке «take», там где сейчас снимается `gate_reason`:

```python
                if prev_reason is not None:
                    conn.execute(
                        "UPDATE med_intakes SET gate_reason=NULL WHERE id=?",
                        (intake_id,))
                    audit.log(conn, "med.gate_release",
                              {"intake_id": intake_id, "was": prev_reason})
                    released.append({"intake_id": intake_id, "name": name,
                                     "dose": dose,
                                     "plan_local": _parse_utc(
                                         row["plan_ts_utc"]).astimezone(
                                         ALMATY).strftime("%H:%M")})
                    conn.commit()
                    continue
```

Перед циклом `for row in due:` объявить `released = []`. После цикла — одна отправка со своим `try/except`:

```python
    if released:
        # Дозы, освобождённые ОДНИМ тиком, уходят одним сообщением:
        # иначе отпускание sleep-гейта в 12:00 при трёх лекарствах
        # воспроизводит ровно ту "кучу", ради устранения которой гейт и
        # делался. Обычные 45-минутные повторы остаются раздельными.
        try:
            items = [{"name": r["name"], "dose": r["dose"],
                      "plan_local": r["plan_local"]} for r in released]
            ids = [r["intake_id"] for r in released]
            if len(items) == 1:
                raw = {"mode": "take", "name": items[0]["name"],
                       "dose": items[0]["dose"],
                       "intake_id": ids[0],
                       "plan_local": items[0]["plan_local"], "late": True}
                human_fallback = (
                    f"{items[0]['name']} за {items[0]['plan_local']} "
                    f"ещё не отмечено.")
            else:
                raw = {"mode": "take_group", "items": items, "late": True}
                listed = ", ".join(
                    f"{i['name']} ({i['plan_local']})" for i in items)
                human_fallback = f"Ещё не отмечено: {listed}."
            gate.deliver(conn, "med", raw, human_fallback, cfg,
                         force=True, now_utc=now_utc,
                         sent_ref={"kind": "med", "ref_id": ids[0],
                                   "ref_ids": ids})
            repeat_min = cfg.get("med_repeat_min", 45)
            next_utc = (now_dt + timedelta(minutes=repeat_min)).isoformat(
                timespec="seconds")
            for iid in ids:
                conn.execute(
                    "UPDATE med_intakes SET series_next_utc=? "
                    "WHERE id=? AND status='pending'", (next_utc, iid))
            audit.log(conn, "tick.med",
                      {"mode": "release_group", "intake_ids": ids})
            conn.commit()
        except Exception as e:
            conn.rollback()
            audit.log(conn, "tick.error",
                      {"where": "meds_release", "error": str(e)[:200]})
            conn.commit()
```

- [ ] **Step 7: Прогнать тесты**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_meds_gate.py tests/test_reactions.py tests/test_tick_meds_series.py -q`
Expected: PASS. Если файл тестов реакций называется иначе — найти его через `ls tests | grep -i react`.

- [ ] **Step 8: Коммит**

```bash
git add fam/tick.py fam/react.py fam/gate.py tests/test_meds_gate.py
git commit -m "feat(amina/meds): склейка освобождённых доз в одно сообщение"
```

---

### Task 7: Реакция ⏰ — отложить на час

**Files:**
- Modify: `fam/react.py`, `plugins/platforms/whatsapp/reactions.py` (в корне репозитория, не в `custom/fam`)
- Test: `tests/test_meds_snooze.py` (создать)

**Interfaces:**
- Consumes: `cfg["med_snooze_min"]` (Task 1), `sent_message_refs` (Task 6).
- Produces: `react.EMOJI_SNOOZE`; `handle(...)` возвращает `{"result": "snoozed", "until_utc": ...}`.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_meds_snooze.py`:

```python
"""⏰-реакция на напоминании о лекарстве -- отложить дозу на час.

Мотив (спека 2026-07-29): "принято" и "пропускаю" доступны одним тапом,
а "позже" требует набрать текст -- и именно в этом сценарии чаще всего
возникает раздражение.
"""
import pytest

from fam import meds, react

NOW = "2026-07-20T05:00:00+00:00"   # 10:00 Алматы


def _intake(db, name="Эутирокс"):
    med_id = meds.add(db, name, ["09:00"], remaining=10)
    cur = db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, status, "
        "series_next_utc, created_at) VALUES(?,?,'pending',?,?)",
        (med_id, "2026-07-20T04:00:00+00:00", "2026-07-20T04:00:00+00:00",
         "2026-07-20T04:00:00+00:00"))
    db.commit()
    return cur.lastrowid


def test_snooze_defers_by_an_hour(db):
    iid = _intake(db)
    react.record_sent(db, "wa1", "med", iid)
    db.commit()

    out = react.handle(db, "wa1", "⏰", now_utc=NOW)

    assert out["result"] == "snoozed"
    row = db.execute("SELECT * FROM med_intakes WHERE id=?", (iid,)).fetchone()
    assert row["status"] == "pending", "snooze не закрывает дозу"
    assert row["series_next_utc"] == "2026-07-20T06:00:00+00:00"
    assert row["deferred_until_utc"] == "2026-07-20T06:00:00+00:00"


def test_snooze_on_reminder_kind_is_ignored(db):
    # ⏰ имеет смысл только для лекарств; на событийном напоминании
    # ack-семантики нет.
    react.record_sent(db, "wa2", "reminder", 1, event_id=1)
    db.commit()
    out = react.handle(db, "wa2", "⏰", now_utc=NOW)
    assert out["result"] == "ignored"


def test_snooze_clamped_before_almaty_midnight(db):
    # 18:30 UTC = 23:30 Алматы; +60 минут ушло бы за полночь, что
    # meds.defer запрещает (столкновение с завтрашней дозой).
    iid = _intake(db)
    react.record_sent(db, "wa3", "med", iid)
    db.commit()

    out = react.handle(db, "wa3", "⏰", now_utc="2026-07-20T18:30:00+00:00")

    assert out["result"] == "snoozed"
    row = db.execute("SELECT * FROM med_intakes WHERE id=?", (iid,)).fetchone()
    # прижато к 23:59 Алматы = 18:59 UTC
    assert row["series_next_utc"] == "2026-07-20T18:59:00+00:00"


def test_snooze_group_defers_every_member(db):
    a = _intake(db, "Эутирокс")
    b = _intake(db, "Магний")
    react.record_sent(db, "wa4", "med", a, ref_ids=[a, b])
    db.commit()

    react.handle(db, "wa4", "⏰", now_utc=NOW)

    for iid in (a, b):
        row = db.execute("SELECT series_next_utc FROM med_intakes WHERE id=?",
                         (iid,)).fetchone()
        assert row["series_next_utc"] == "2026-07-20T06:00:00+00:00"


def test_snooze_emoji_is_in_dialogue_whitelist():
    # ИНВАРИАНТ: фильтр DIALOGUE_EMOJI срабатывает ДО react-hook, поэтому
    # эмодзи, добавленный только сюда, молча никогда не доедет.
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    src = (root / "plugins/platforms/whatsapp/reactions.py").read_text()
    for emoji in react.EMOJI_SNOOZE:
        assert emoji in src, f"{emoji!r} отсутствует в DIALOGUE_EMOJI"
```

- [ ] **Step 2: Прогнать, убедиться что падает**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_meds_snooze.py -q`
Expected: FAIL — `AttributeError: EMOJI_SNOOZE`.

- [ ] **Step 3: Добавить `EMOJI_SNOOZE` в `fam/react.py`**

Рядом с `EMOJI_CONFIRM` / `EMOJI_SKIP`:

```python
# ⏰ 🕐 ⏳ -- "не сейчас, напомни позже". Только для kind='med': у
# событийного напоминания нет примитива отсрочки, там уместны только
# ack и cancel.
EMOJI_SNOOZE = {"⏰", "\U0001F550", "⏳"}
```

Расширить докстринг модуля и инвариантный комментарий: `EMOJI_CONFIRM | EMOJI_SKIP | EMOJI_SNOOZE` должно быть подмножеством `DIALOGUE_EMOJI`.

- [ ] **Step 4: Обработать ⏰ в `handle`**

В месте, где сейчас вычисляется `action`, добавить третью ветку до проверки `row["ack_status"] != "none"`:

```python
    if norm in EMOJI_SNOOZE:
        if row["kind"] != "med":
            out = {**base, "result": "ignored", "reason": "snooze_not_med"}
            audit.log(conn, "react.handle", out)
            conn.commit()
            return out
        return _snooze(conn, row, base, now_utc)
```

И включить `EMOJI_SNOOZE` в проверку «неизвестный эмодзи» выше:

```python
    if removal or (norm not in EMOJI_CONFIRM and norm not in EMOJI_SKIP
                   and norm not in EMOJI_SNOOZE):
```

Сама функция:

```python
def _snooze(conn, row, base, now_utc):
    """⏰ -> meds.defer на med_snooze_min минут для каждой дозы сообщения.

    Доза остаётся 'pending' -- это не ack, а перенос. Если +N минут
    уходит за полночь Алматы (meds.defer это запрещает: завтрашняя доза
    генерируется своим расписанием), цель прижимается к 23:59 локально.
    """
    from datetime import datetime, timedelta
    from fam import gate

    cfg = gate.load_config()
    minutes = int(cfg.get("med_snooze_min", 60))
    now = datetime.fromisoformat(now_utc or _now())
    until = now + timedelta(minutes=minutes)

    local = until.astimezone(meds._ALMATY)
    end_of_day = local.replace(hour=23, minute=59, second=0, microsecond=0)
    if local > end_of_day:
        until = end_of_day.astimezone(until.tzinfo)
    until_str = until.isoformat(timespec="seconds")

    targets = [r["ref_id"] for r in conn.execute(
        "SELECT ref_id FROM sent_message_refs "
        "WHERE sent_message_id=? AND kind='med' ORDER BY ref_id",
        (row["id"],))] or [row["ref_id"]]

    deferred = 0
    for rid in targets:
        try:
            meds.defer(conn, rid, until_str, now_utc=now_utc)
            deferred += 1
        except ValueError:
            continue

    out = {**base, "result": "snoozed" if deferred else "already_acked",
           "until_utc": until_str, "deferred": deferred}
    audit.log(conn, "react.handle", out)
    conn.commit()
    return out
```

Обратите внимание: `ack_status` НЕ меняется — отложенная доза всё ещё ждёт ответа, и последующее 👍 обязано работать.

- [ ] **Step 5: Добавить эмодзи в `DIALOGUE_EMOJI`**

В `plugins/platforms/whatsapp/reactions.py` дописать `⏰`, `🕐`, `⏳` в `DIALOGUE_EMOJI`.

- [ ] **Step 6: Прогнать тесты**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_meds_snooze.py -q` затем из корня репозитория `venv/bin/python -m pytest tests/gateway/test_whatsapp_reactions_normalize.py -q`
Expected: PASS обоих.

- [ ] **Step 7: Коммит**

```bash
git add fam/react.py tests/test_meds_snooze.py ../../plugins/platforms/whatsapp/reactions.py
git commit -m "feat(amina/meds): ⏰-реакция откладывает дозу на час"
```

---

### Task 8: Разные формулировки повторов

**Files:**
- Modify: `fam/gate.py` (`_build_prompt`, новая константа), `fam/tick.py` (обогащение `raw`)
- Test: `tests/test_gate_med_variation.py` (создать)

**Interfaces:**
- Consumes: `raw["intake_id"]` из `_meds_series`.
- Produces: `gate.GATE_MED_VARIATION_INSTRUCTION`; `gate.med_fallback(name, dose, attempt_no)`; `raw` для `kind="med"` несёт `intake_id`, `attempt_no`, `minutes_late`.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_gate_med_variation.py`:

```python
"""Повторные напоминания об одной дозе не должны быть слово в слово
одинаковыми. Разнообразие обеспечивается двумя путями: инструкцией
переписывающему LLM и -- когда он недоступен -- пулом детерминированных
формулировок. Второй путь важнее: падения _call_rewrite тихие и штатные
(gate.py возвращается к human_fallback), так что без пула однообразие
наступало бы ровно в худший момент.
"""
from fam import gate


def test_fallback_pool_varies_by_attempt():
    texts = {gate.med_fallback("Эутирокс", None, n) for n in range(1, 5)}
    assert len(texts) == 4, "четыре попытки -- четыре разные формулировки"


def test_fallback_includes_name_and_dose():
    text = gate.med_fallback("Эутирокс", "50 мкг", 1)
    assert "Эутирокс" in text
    assert "50 мкг" in text


def test_fallback_wraps_around_beyond_pool():
    assert gate.med_fallback("X", None, 1) == gate.med_fallback(
        "X", None, 1 + len(gate.MED_FALLBACKS))


def test_variation_instruction_only_for_repeats():
    first = gate._build_prompt(
        {"mode": "take", "name": "X", "attempt_no": 1}, kind="med")
    repeat = gate._build_prompt(
        {"mode": "take", "name": "X", "attempt_no": 3}, kind="med")
    assert gate.GATE_MED_VARIATION_INSTRUCTION not in first
    assert gate.GATE_MED_VARIATION_INSTRUCTION in repeat


def test_variation_instruction_absent_for_other_kinds():
    prompt = gate._build_prompt({"attempt_no": 3}, kind="reminder")
    assert gate.GATE_MED_VARIATION_INSTRUCTION not in prompt
```

- [ ] **Step 2: Прогнать, убедиться что падает**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_gate_med_variation.py -q`
Expected: FAIL — нет `med_fallback`, нет `GATE_MED_VARIATION_INSTRUCTION`.

- [ ] **Step 3: Добавить пул и инструкцию в `fam/gate.py`**

Рядом с остальными `GATE_*_INSTRUCTION`:

```python
GATE_MED_VARIATION_INSTRUCTION = (
    "Это повторное напоминание про то же лекарство. Сформулируй иначе, "
    "чем в прошлый раз: короче, мягче, без упрёка и без слов «опять», "
    "«снова», «уже». Не выдумывай новых фактов."
)

# Детерминированный пул на случай, когда переписывающий LLM недоступен.
# Индексируется номером попытки: без него однообразие наступало бы ровно
# тогда, когда LLM упал -- а его падения тихие и штатные.
MED_FALLBACKS = (
    "Пора принять {name}{dose}.",
    "{name}{dose} — ещё не отмечено.",
    "Напоминаю про {name}{dose}.",
    "{name}{dose} всё ещё ждёт.",
)


def med_fallback(name, dose, attempt_no):
    """Детерминированная формулировка напоминания для попытки attempt_no
    (нумерация с 1). Циклится по MED_FALLBACKS."""
    template = MED_FALLBACKS[(max(1, attempt_no) - 1) % len(MED_FALLBACKS)]
    return template.format(name=name, dose=f" ({dose})" if dose else "")
```

В `_build_prompt`, рядом с ветками для `digest` и `reminder`:

```python
    elif kind == "med" and int(raw.get("attempt_no") or 1) > 1:
        instruction = f"{instruction} {GATE_MED_VARIATION_INSTRUCTION}"
```

- [ ] **Step 4: Обогатить `raw` в `_meds_series`**

В ветке «take» заменить построение `raw` и `human_fallback`:

```python
                attempt_no = conn.execute(
                    "SELECT COUNT(*) FROM sent_messages "
                    "WHERE kind='med' AND ref_id=?", (intake_id,)
                ).fetchone()[0] + 1
                minutes_late = int(
                    (now_dt - _parse_utc(row["plan_ts_utc"])).total_seconds()
                    // 60)
                raw = {"mode": "take", "name": name, "dose": dose,
                       "intake_id": intake_id, "attempt_no": attempt_no,
                       "minutes_late": minutes_late}
                human_fallback = gate.med_fallback(name, dose, attempt_no)
```

`attempt_no` считается по `sent_messages`, а не по новому счётчику: там уже лежит по строке на каждую реально доставленную отправку этой дозы, и удержания гейтом в него не попадают — ровно то, что нужно.

- [ ] **Step 5: Прогнать тесты**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_gate_med_variation.py tests/test_gate.py tests/test_tick_meds_series.py -q`
Expected: PASS. `test_tick_meds_series.py` мог проверять точный текст `human_fallback` («Пора принять X») — для первой попытки пул даёт ровно эту строку, так что тест обязан остаться зелёным. Если нет — сверить, какая попытка ожидается.

- [ ] **Step 6: Коммит**

```bash
git add fam/gate.py fam/tick.py tests/test_gate_med_variation.py
git commit -m "feat(amina/meds): повторы формулируются по-разному"
```

---

### Task 9: Наблюдаемость

Чтобы агент мог честно ответить «почему не напомнил», а удержанные дозы не терялись бесследно.

**Files:**
- Modify: `fam/meds.py` (`list_pending`), `fam/tick.py` (`_followup`)
- Test: `tests/test_meds_gate.py` (дополнить)

**Interfaces:**
- Consumes: `gate_reason` (Task 1).
- Produces: `list_pending()` возвращает ключ `gate_reason`; `_followup` упоминает удержанные дозы.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_list_pending_exposes_gate_reason(db, fake_deliver):
    _pending_intake(db)
    tick._meds_series(db, MORNING, CFG)

    rows = meds.list_pending(db)
    assert len(rows) == 1
    assert rows[0]["gate_reason"] == "asleep"


def test_list_pending_gate_reason_none_when_not_held(db):
    _pending_intake(db)
    rows = meds.list_pending(db)
    assert rows[0]["gate_reason"] is None
```

- [ ] **Step 2: Прогнать, убедиться что падает**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_meds_gate.py -q -k list_pending`
Expected: FAIL — `KeyError: 'gate_reason'`.

- [ ] **Step 3: Отдать `gate_reason` из `list_pending`**

В `fam/meds.py:117`, в SELECT:

```python
        "SELECT m.id AS intake_id, m.med_id AS med_id, d.name AS name, "
        "m.plan_ts_utc AS plan_ts_utc, m.status AS status, "
        "m.deferred_until_utc AS deferred_until_utc, "
        "m.gate_reason AS gate_reason "
        "FROM med_intakes m JOIN meds d ON d.id = m.med_id "
        "WHERE m.status='pending' ORDER BY m.plan_ts_utc"
```

Дописать в докстринг: `gate_reason` — почему доза удерживается тиком (`asleep` / `away` / `None`); нужен, чтобы агент отвечал на «почему не напомнил» фактом, а не догадкой.

- [ ] **Step 4: Упомянуть удержанные дозы в вечернем follow-up**

Отдельного сообщения не создавать — `_followup` уже существует, его бюджет уже учтён, и лишний проактивный канал противоречит всей задаче.

Тест (дописать в `tests/test_meds_gate.py`):

```python
def test_followup_mentions_held_doses(db, fake_deliver, monkeypatch):
    fake_deliver.responses = ["sent"]
    _pending_intake(db)
    tick._meds_series(db, MORNING, CFG)          # доза удержана как asleep

    # 20:00 Алматы = 15:00 UTC
    tick._followup(db, "2026-07-20T15:00:00+00:00", CFG)

    calls = [c for c in fake_deliver.calls if c["kind"] == "followup"]
    assert len(calls) == 1
    assert calls[0]["raw"]["held_meds"] == [
        {"name": "Эутирокс", "plan_local": "09:00", "reason": "asleep"}]
    assert "Эутирокс" in calls[0]["human_fallback"]
```

В `_followup`, перед строкой `has_recap = bool(outbound_events and related_plans)`:

```python
    held_meds = [
        {"name": r["name"],
         "plan_local": _parse_utc(r["plan_ts_utc"]).astimezone(
             ALMATY).strftime("%H:%M"),
         "reason": r["gate_reason"]}
        for r in conn.execute(
            "SELECT d.name AS name, m.plan_ts_utc AS plan_ts_utc, "
            "       m.gate_reason AS gate_reason "
            "FROM med_intakes m JOIN meds d ON d.id = m.med_id "
            "WHERE m.status='pending' AND m.gate_reason IS NOT NULL "
            "ORDER BY m.plan_ts_utc")
    ]
```

Заменить условие «нечего сказать», чтобы удержанные дозы сами по себе были поводом для follow-up:

```python
    has_recap = bool(outbound_events and related_plans)
    if not has_recap and prep_candidate is None and not held_meds:
        status = "no_events" if not outbound_events else "no_plans"
```

Внутри `else:` добавить `held_meds` в `raw` и строку в `human_fallback` — сразу после блока `if related_plans:` и до `prep_candidate`:

```python
        if held_meds:
            raw["held_meds"] = held_meds
            listed = ", ".join(
                f"{h['name']} ({h['plan_local']})" for h in held_meds)
            lines.append(f"Сегодня не отмечено: {listed}.")
```

Наконец, дописать `"n_held_meds": len(held_meds)` в payload финального `audit.log(conn, "tick.followup", ...)`.

- [ ] **Step 5: Прогнать полный набор**

Run: `~/.hermes/hermes-agent/venv/bin/python -m pytest tests -q`
Expected: `3 failed` — ровно те же три в `tests/test_whereami_recompute.py`, что и в baseline. Любое другое падение — регрессия этого плана.

- [ ] **Step 6: Обновить skill-документацию**

В `~/.hermes/skills/amina-fam/SKILL.md`, раздел «Medication Verbs», дописать:
- ⏰ на напоминании о лекарстве — это отсрочка на час, применённая КОДОМ до того, как агент что-то увидит (та же логика, что для 👍/👎 в «Reminder Reactions»); никогда не вызывать `med defer` «на всякий случай» после неё;
- напоминания могут молчать, пока Амина спит (до первого признака активности, максимум до 12:00) или пока она вне дома (максимум до 21:00); причина видна в `fam med list --pending --json` как `gate_reason`;
- одно сообщение может покрывать несколько доз (склейка при отпускании) — реакция на него отмечает их все.

- [ ] **Step 7: Коммит**

```bash
git add fam/meds.py fam/tick.py tests/test_meds_gate.py
git commit -m "feat(amina/meds): gate_reason виден агенту и вечернему follow-up"
```

---

## Порядок и зависимости

```
Task 1 (схема + конфиг)
  ├─> Task 2 (awake_since) ─┐
  ├─> Task 3 (is_away) ─────┤
  │                          ├─> Task 4 (sleep-гейт) ─> Task 5 (away-гейт) ─> Task 6 (склейка)
  │                          │                                                     ├─> Task 7 (⏰)
  └──────────────────────────┘                                                     ├─> Task 8 (формулировки)
                                                                                    └─> Task 9 (наблюдаемость)
```

Tasks 2 и 3 независимы и могут идти параллельно. Tasks 7, 8, 9 независимы между собой после Task 6.

## Перед мержем

1. Дождаться коммита чужой WIP-работы в `fam/whereami.py`, перебазировать `local/med-gating` на неё, прогнать полный набор — он обязан стать полностью зелёным.
2. Прописать шесть новых ключей плюс `whereami_home_radius_km` / `whereami_car_fresh_min` в живой `/home/denis/.hermes/private/amina/fam-config.json` (сделав `.bak` рядом, как принято в этом каталоге).
3. Первые сутки после выката проверить `fam log --kind med.gate_hold --last-hours 24 --json`: удержаний должно быть немного, и каждое должно иметь парный `med.gate_release` либо сработавший бэкстоп.
