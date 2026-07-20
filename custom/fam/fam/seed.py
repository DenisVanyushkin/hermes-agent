"""Data seeding round-trip: единый словарь листов/колонок xlsx ↔ fam.

Комментарии колонок (Col.comment) — контракт спеки §3: что за данные и на
какое поведение Гермеса влияют; seed_xlsx вешает их на заголовки.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from .cal import ALMATY
from . import audit, geo2gis
from .textnorm import fold

_LOCAL_FMT = "%Y-%m-%d %H:%M"


def local_to_utc_iso(s):
    try:
        dt = datetime.strptime(s.strip(), _LOCAL_FMT)
    except (ValueError, AttributeError):
        raise ValueError(f"время не в формате 'YYYY-MM-DD HH:MM': {s!r}")
    return dt.replace(tzinfo=ALMATY).astimezone(timezone.utc).isoformat()


def utc_iso_to_local(s):
    return datetime.fromisoformat(s).astimezone(ALMATY).strftime(_LOCAL_FMT)


LABELS = {
    "transport": {"car": "машина", "walk": "пешком", "public": "общественный", "unknown": ""},
    "kind": {"person": "человек", "group": "группа"},
    "category": {"grocery": "продукты", "pharmacy": "аптека"},
    "weekday": {"mon": "пн", "tue": "вт", "wed": "ср", "thu": "чт", "fri": "пт", "sat": "сб", "sun": "вс"},
    "enabled": {1: "да", 0: "нет"},
}
_CANON = {d: {str(v).casefold(): k for k, v in m.items() if str(v).strip()} for d, m in LABELS.items()}
_ALLOW_EMPTY = {"transport": (None,), "category": (None,)}


def label(domain, canon_value):
    return LABELS[domain][canon_value]


def canon(domain, lbl):
    key = str(lbl).strip().casefold()
    if not key and None in _ALLOW_EMPTY.get(domain, ()):  # пустое → None там, где допустимо
        return None
    try:
        return _CANON[domain][key]
    except KeyError:
        raise ValueError(f"{domain}: неизвестное значение {lbl!r}, ожидалось одно из "
                          f"{sorted(LABELS[domain].values())}")


@dataclass(frozen=True)
class Col:
    header: str
    key: str
    comment: str
    to_xlsx: object = None      # canon -> ячейка (None = как есть)
    from_xlsx: object = None    # ячейка -> canon
    readonly: bool = False      # экспортируется, импортом игнорируется


@dataclass(frozen=True)
class SheetSpec:
    sheet: str
    columns: tuple


_ID = Col("id", "id", "Служебный ключ записи. НЕ МЕНЯТЬ и не копировать в другие "
          "строки. У новых строк оставить пустым.", readonly=False)


def _csv(xs):
    return ", ".join(xs) if xs else ""


def _uncsv(s):
    return [p.strip() for p in str(s or "").split(",") if p.strip()]


def _travel_min_from_xlsx(v):
    """Cell -> int minutes. Only called on non-empty cells (normalize_row
    treats an empty/blank cell as 0 before reaching from_xlsx, since the
    column is NOT NULL DEFAULT 0 -- 'не задано' means 0, not unset). A
    non-numeric value here becomes a normal ValueError conflict, never an
    uncaught crash."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        raise ValueError(f"время в пути (мин): не число: {v!r}")


SHEETS = {
 "Люди": SheetSpec("Люди", (_ID,
   Col("имя", "name", "Имя, по которому Гермес узнаёт человека в разговоре. Уникально."),
   Col("тип", "kind", "«человек» или «группа». Группа (например «татешки») разворачивается "
       "в участников события по составу.", lambda v: label("kind", v), lambda v: canon("kind", v)),
   Col("slug", "slug", "Служебная метка для правил напоминаний (например, taya = ранние стадии "
       "«Тая собирается»). Менять только осознанно — завязаны цепочки напоминаний."),
   Col("дом (место)", "home", "Название места из листа «Места», где человек живёт. Влияет на "
       "резолв «к <имя>» (поездка к человеку = поездка к его дому) и подсказки «по пути»."),
   Col("алиасы", "aliases", "Другие имена через запятую («Аишка, Аиша»). По любому из них Гермес "
       "найдёт человека.", _csv, _uncsv),
   Col("состав группы", "members", "Только для типа «группа»: имена участников через запятую. "
       "Участники должны существовать на этом листе.", _csv, _uncsv),
   Col("заметки", "notes", "Свободный текст-контекст: Гермес видит его, когда работает с человеком."))),
 "Места": SheetSpec("Места", (_ID,
   Col("название", "name", "Название места. Уникально; по нему место указывается в событиях/планах."),
   Col("адрес", "address", "Адрес текстом — для человека и для контекста Гермеса."),
   Col("категория", "category", "«продукты» или «аптека» — включает подсказки «по пути» "
       "(купить продукты/лекарства по дороге мимо этого места). Пусто = обычное место.",
       lambda v: label("category", v) if v else "", lambda v: canon("category", v)),
   Col("lat", "lat", "Широта. Вместе с lon включает расчёт реальной дороги (пробки TomTom), "
       "leave_at-напоминания «пора выходить» и матчи «по пути»."),
   Col("lon", "lon", "Долгота. См. lat."),
   Col("2ГИС-ссылка", "gis_url", "ТОЛЬКО ДЛЯ ИМПОРТА: вставь ссылку 2ГИС (можно короткую "
       "go.2gis.com/…) — координаты подтянутся сами. Если lat/lon заполнены, они в приоритете."),
   Col("время в пути (мин)", "travel_min", "Ручная оценка времени в пути, минут. Используется для "
       "«пора выходить», пока нет координат/дороги. 0 = не задано.", None, _travel_min_from_xlsx),
   Col("алиасы", "aliases", "Другие названия через запятую («зал, Инвиктус»).", _csv, _uncsv),
   Col("заметки", "notes", "Свободный текст-контекст про место."))),
 "События": SheetSpec("События", (_ID,
   Col("название", "title", "Что за событие. Попадает в напоминания и утренний дайджест."),
   Col("начало", "start", "Дата-время начала, Алматы, формат ГГГГ-ММ-ДД ЧЧ:ММ. От него строится "
       "цепочка напоминаний (D/D-5/… и «пора выходить»).", None, None),
   Col("конец", "end", "Дата-время конца (можно пусто).", None, None),
   Col("место", "place", "Название с листа «Места». Даёт расчёт дороги, leave_at и «по пути»."),
   Col("транспорт", "transport", "«машина|пешком|общественный». ОБЯЗАТЕЛЕН, если задано место. "
       "машина → перед выездом проверка топлива (заправься) и предложение прогрева.",
       lambda v: label("transport", v), lambda v: canon("transport", v)),
   Col("участники", "participants", "Имена/группы через запятую. Для Таи включаются ранние стадии "
       "сборов; участники видны в напоминаниях.", _csv, _uncsv),
   Col("сборы (мин)", "prep_min", "Сколько минут нужно на сборы. Переопределяет стандартную "
       "цепочку напоминаний этого события (prepare-стадия за столько минут)."),
   Col("заметки", "notes", "Свободный текст: попадает в контекст напоминаний."))),
 "Серии": SheetSpec("Серии", (_ID,
   Col("название", "title", "Повторяющееся событие (например «Тренировка в Invictus»)."),
   Col("дни", "weekdays", "Дни недели через запятую: пн,ср,пт. По ним генерятся конкретные "
       "события на 8 недель вперёд.", lambda v: _csv([label("weekday", d) for d in _uncsv(v)]),
       lambda v: ",".join(canon("weekday", d) for d in _uncsv(v))),
   Col("начало", "start_time", "ЧЧ:ММ, Алматы."),
   Col("конец", "end_time", "ЧЧ:ММ (можно пусто)."),
   Col("место", "place", "Название с листа «Места» (см. комментарий там)."),
   Col("транспорт", "transport", "Как у событий: обязателен при месте; наследуется каждым "
       "вхождением серии.", lambda v: label("transport", v), lambda v: canon("transport", v)),
   Col("участники", "participants", "Имена/группы через запятую; попадают в каждое вхождение.",
       _csv, _uncsv),
   Col("до (дата)", "until_local", "ГГГГ-ММ-ДД, до какой даты повторять. Пусто = бессрочно."),
   Col("сборы (мин)", "prep_min", "Минуты на сборы, копируются в каждое вхождение."),
   Col("заметки", "notes", "Свободный текст."))),
 "Планы": SheetSpec("Планы", (_ID,
   Col("название", "title", "Дело без жёсткого времени («отдать кастрюлю Аишке»). Живёт в "
       "дайджесте (горящие планы), вечернем follow-up и подсказках «по пути»."),
   Col("срок", "deadline", "ГГГГ-ММ-ДД (можно пусто). Ближе к сроку план «горит» в дайджесте."),
   Col("место", "place", "Где сделать: включает «по пути» — Гермес напомнит, когда поедешь мимо."),
   Col("человек", "person", "К кому относится: план матчится «по пути» к дому человека."),
   Col("заметки", "notes", "Свободный текст."),
   Col("связь с событием", "link", "Служебное: план-подготовка привязан к событию (создаёт и "
       "гасит Гермес). Только для чтения.", readonly=True))),
 "Лекарства": SheetSpec("Лекарства", (_ID,
   Col("название", "name", "Название лекарства — по нему ack «выпила <название>»."),
   Col("доза", "dose", "Текст дозы («1 таблетка», «5 мл») — попадает в напоминание."),
   Col("времена", "times", "ЧЧ:ММ через запятую (08:00, 20:00) — в эти времена ежедневные "
       "напоминания с повтором каждые 45 мин до «выпила».", _csv, _uncsv),
   Col("остаток", "remaining", "Сколько осталось единиц. Минус 1 на каждый приём; пусто = не считать."),
   Col("порог", "threshold", "При остатке ≤ порога лекарство автоматически попадает в «Покупки» "
       "и в дайджест. 0 = выключено."),
   Col("включено", "enabled", "«да|нет». «нет» — напоминания не создаются, определение хранится.",
       lambda v: label("enabled", v), lambda v: canon("enabled", v)))),
 "Покупки": SheetSpec("Покупки", (_ID,
   Col("название", "name", "Что купить. Гермес подскажет «по пути», когда маршрут пройдёт мимо "
       "продуктового/аптеки (по категории места)."),
   Col("кол-во", "qty", "Количество текстом («2 шт», «1 кг»)."),
   Col("источник", "source", "Служебное: manual = добавлено руками, meds = авто-добавлено по "
       "порогу лекарства. Только для чтения.", readonly=True),
   Col("добавил", "added_by", "Кто добавил (свободный текст)."))),
}


# Excel (openpyxl, data_only=True) may hand back datetime/date/time objects
# for cells it auto-coerced, and lat/lon may arrive as strings (possibly with
# a Russian decimal comma). Coerce those into the canonical string/float form
# BEFORE any regex/float parsing, so a coerced-but-valid cell is accepted and
# a truly malformed one becomes a normal conflict, never an uncaught
# TypeError escaping diff().
_DATETIME_KEYS = {"start", "end"}
_DATE_KEYS = {"deadline", "until_local"}
_TIME_KEYS = {"start_time", "end_time", "times"}
_FLOAT_KEYS = {"lat", "lon"}


def _coerce_cell(key, v):
    if isinstance(v, datetime):
        if key in _DATETIME_KEYS:
            return v.strftime("%Y-%m-%d %H:%M")
        if key in _DATE_KEYS:
            return v.strftime("%Y-%m-%d")
        if key in _TIME_KEYS:
            return v.strftime("%H:%M")
        return str(v)
    if isinstance(v, time):
        return v.strftime("%H:%M") if key in _TIME_KEYS else str(v)
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d") if key in _DATE_KEYS else str(v)
    if key in _FLOAT_KEYS and isinstance(v, str):
        s = v.strip().replace(",", ".")  # русская десятичная запятая
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            raise ValueError(f"{key}: не число: {v!r}")
    return v


# List-valued columns where the operator's typed order and the DB's
# export order (whatever ORDER BY the domain module happens to use) are
# not meaningfully different data -- a pure reordering must diff/verify as
# a no-op. Sorted canonically (casefold) right here so every caller of
# normalize_row (file-side AND DB/export-side, via make_snapshot and
# _to_canonical) agrees on one order, instead of trying to keep diff()'s
# comparison and verify_roundtrip's comparison in sync separately.
_ORDER_INSENSITIVE_KEYS = {"aliases", "members", "participants", "times"}


def _canon_list_order(v):
    if not isinstance(v, list):
        return v
    return sorted(v, key=lambda x: str(x).casefold())


def normalize_row(sheet, row):
    """Канонический dict для diff-сравнений: строки strip, пустое → None.

    row может быть ключами по русским заголовкам (col.header) ИЛИ по
    каноническим ключам (col.key) — обе формы поддерживаются, чтобы
    normalize_row можно было применить и к строке из xlsx, и к строке,
    уже прочитанной из БД в каноническом виде.
    """
    spec = SHEETS[sheet]
    out = {}
    for col in spec.columns:
        if col.readonly and col.key != "id":
            continue
        if col.header in row:
            v = row[col.header]
        else:
            v = row.get(col.key)
        v = _coerce_cell(col.key, v)
        if isinstance(v, str):
            v = v.strip() or None
        if col.key == "id":
            v = int(v) if v not in (None, "") else None
        elif col.key == "travel_min":
            # places.travel_min is NOT NULL DEFAULT 0 -- an empty/blank cell
            # means "не задано", i.e. 0, never None (else apply() would hit
            # a NOT NULL IntegrityError and roll back).
            v = col.from_xlsx(v) if v is not None else 0
        elif v is not None and col.from_xlsx:
            v = col.from_xlsx(v)
        if col.key in _ORDER_INSENSITIVE_KEYS:
            v = _canon_list_order(v)
        out[col.key] = v
    return out


def _person_aliases(conn, person_id):
    rows = conn.execute(
        "SELECT alias FROM people_aliases WHERE person_id=? ORDER BY alias COLLATE NOCASE",
        (person_id,),
    ).fetchall()
    return [r["alias"] for r in rows]


def _group_members(conn, group_id):
    rows = conn.execute(
        "SELECT pe.name FROM group_members gm JOIN people pe ON pe.id = gm.person_id "
        "WHERE gm.group_id = ? ORDER BY pe.name COLLATE NOCASE",
        (group_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def _place_aliases(conn, place_id):
    rows = conn.execute(
        "SELECT alias FROM place_aliases WHERE place_id=? ORDER BY alias COLLATE NOCASE",
        (place_id,),
    ).fetchall()
    return [r["alias"] for r in rows]


def _series_participants(conn, series_id):
    rows = conn.execute(
        "SELECT pe.name FROM event_series_participants sp "
        "JOIN people pe ON pe.id = sp.person_id "
        "WHERE sp.series_id = ? ORDER BY pe.name COLLATE NOCASE",
        (series_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def _export_people(conn):
    from fam import people

    out = []
    for p in sorted(people.list_people(conn), key=lambda d: d["id"]):
        home = p.get("home_place")
        members = _group_members(conn, p["id"]) if p["kind"] == "group" else []
        out.append({
            "id": p["id"],
            "name": p["name"],
            "kind": p["kind"],
            "slug": p.get("slug"),
            "home": home["name"] if home else None,
            "aliases": _person_aliases(conn, p["id"]),
            "members": members,
            "notes": p.get("notes"),
        })
    return out


def _export_places(conn):
    from fam import places

    out = []
    for p in sorted(places.list_all(conn), key=lambda d: d["id"]):
        out.append({
            "id": p["id"],
            "name": p["name"],
            "address": p.get("address"),
            "category": p.get("category"),
            "lat": p.get("lat"),
            "lon": p.get("lon"),
            "gis_url": None,  # экспорт всегда пуст: только для импорта (см. Col.comment)
            "travel_min": p.get("travel_min"),
            "aliases": _place_aliases(conn, p["id"]),
            "notes": p.get("notes"),
        })
    return out


def _export_events(conn, now_iso):
    from fam import cal

    rows = cal.list_range(conn, now_iso, "9999-01-01")  # status="active" default
    out = []
    for ev in rows:
        if ev.get("series_id") is not None:
            continue
        if ev["start_utc"] <= now_iso:
            continue
        place = ev.get("place")
        out.append({
            "id": ev["id"],
            "title": ev["title"],
            "start": utc_iso_to_local(ev["start_utc"]),
            "end": utc_iso_to_local(ev["end_utc"]) if ev.get("end_utc") else None,
            "place": place["name"] if place else None,
            "transport": ev.get("transport"),
            "participants": [pp["name"] for pp in ev.get("participants", [])],
            "prep_min": ev.get("prep_min"),
            "notes": ev.get("notes"),
        })
    out.sort(key=lambda d: d["id"])
    return out


def _export_series(conn):
    from fam import series, places

    out = []
    for s in sorted(series.list_active(conn), key=lambda d: d["id"]):
        place = places.get(conn, s["place_id"]) if s.get("place_id") else None
        out.append({
            "id": s["id"],
            "title": s["title"],
            "weekdays": s["weekdays"],
            "start_time": s["start_time"],
            "end_time": s.get("end_time"),
            "place": place["name"] if place else None,
            "transport": s.get("transport"),
            "participants": _series_participants(conn, s["id"]),
            "until_local": s.get("until_local"),
            "prep_min": s.get("prep_min"),
            "notes": s.get("notes"),
        })
    return out


def _plan_link(conn, plan):
    from fam import cal

    event_id = plan.get("prep_for_event_id") or plan.get("attached_event_id")
    if event_id is None:
        return None
    ev = cal.get(conn, event_id)
    title = ev["title"] if ev else "?"
    if plan.get("prep_for_event_id") is not None:
        return f"подготовка к событию #{event_id} ({title})"
    return f"привязан к событию #{event_id} ({title})"


def _export_plans(conn):
    from fam import plans

    out = []
    for p in sorted(plans.list_open(conn), key=lambda d: d["id"]):
        place = p.get("place")
        person = p.get("person")
        out.append({
            "id": p["id"],
            "title": p["title"],
            "deadline": p.get("deadline"),
            "place": place["name"] if place else None,
            "person": person["name"] if person else None,
            "notes": p.get("notes"),
            "link": _plan_link(conn, p),
        })
    return out


def _export_meds(conn):
    from fam import meds

    out = []
    for m in sorted(meds.list(conn, include_disabled=True), key=lambda d: d["id"]):
        out.append({
            "id": m["id"],
            "name": m["name"],
            "dose": m.get("dose"),
            "times": m.get("times", []),
            "remaining": m.get("remaining"),
            "threshold": m.get("threshold"),
            "enabled": 1 if m.get("enabled") else 0,
        })
    return out


def _export_shopping(conn):
    from fam import shopping

    out = []
    for s in sorted(shopping.list_open(conn), key=lambda d: d["id"]):
        out.append({
            "id": s["id"],
            "name": s["name"],
            "qty": s.get("qty"),
            "source": s.get("source"),
            "added_by": s.get("added_by"),
        })
    return out


def export_rows(conn, now_utc=None):
    """Live-slice export: sheet name -> list of row dicts (canonical keys,
    canonical values -- times as local 'YYYY-MM-DD HH:MM' strings,
    weekdays/transport/etc. as canon codes; Russian labels are applied
    later by the xlsx layer's Col.to_xlsx).

    Slices: события = future active occurrences not belonging to a series
    (series_id IS NULL, start_utc > now); серии = active series; планы =
    open plans; лекарства = all meds (including disabled); покупки = open
    shopping items; люди/места = everything (no status concept there).

    now_utc: test seam, a UTC-aware datetime; defaults to wall-clock now.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    return {
        "Люди": _export_people(conn),
        "Места": _export_places(conn),
        "События": _export_events(conn, now_iso),
        "Серии": _export_series(conn),
        "Планы": _export_plans(conn),
        "Лекарства": _export_meds(conn),
        "Покупки": _export_shopping(conn),
    }


def _canon_row_to_header_form(sheet, row):
    """export_rows' row dicts already carry canonical values (col.key ->
    canon value, e.g. kind="person", weekdays="mon,wed,fri"). normalize_row
    (Task 1) is built for xlsx-shaped input -- it applies col.from_xlsx,
    which expects a RUSSIAN LABEL, not a canon value (from_xlsx("person")
    raises, it wants from_xlsx("человек")). Round-trip through col.to_xlsx
    first (canon -> label/csv, the same transform the xlsx layer applies
    when writing a sheet) into a header-keyed dict, so normalize_row's
    from_xlsx pass converts it straight back to the same canon value --
    exercising the exact xlsx round-trip contract instead of a bespoke one.
    """
    spec = SHEETS[sheet]
    out = {}
    for col in spec.columns:
        v = row.get(col.key)
        if v is not None and col.to_xlsx:
            v = col.to_xlsx(v)
        out[col.header] = v
    return out


def make_snapshot(rows_by_sheet):
    """{"exported_at_utc": iso, "sheets": {sheet: {str(id): normalized_row}}}
    -- normalized via normalize_row (after a to_xlsx/from_xlsx round-trip,
    see _canon_row_to_header_form) so a later diff against a fresh export
    compares like-for-like (readonly cols other than id dropped, strings
    stripped, empty -> None).
    """
    sheets = {}
    for sheet, rows in rows_by_sheet.items():
        sheets[sheet] = {
            str(row["id"]): normalize_row(sheet, _canon_row_to_header_form(sheet, row))
            for row in rows
        }
    return {
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "sheets": sheets,
    }


# ---------------------------------------------------------------------------
# Task 4: diff engine
# ---------------------------------------------------------------------------
import re
from dataclasses import dataclass, field

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_YMD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_SHEET_TABLE = {
    "Люди": "people",
    "Места": "places",
    "События": "events",
    "Серии": "event_series",
    "Планы": "plans",
    "Лекарства": "meds",
    "Покупки": "shopping",
}


@dataclass
class Diff:
    """Result of diff(): per-sheet classification of file rows vs live DB.

    inserts[sheet]: list of canonical row dicts (id is None) to create.
    updates[sheet]: list of {"id", "changes": {key: (was, now)}}.
    deletes[sheet]: list of canonical snapshot row dicts (carry "id") whose
      id was present at export time but is missing from the file.
    conflicts[sheet]: list of {"sheet", "row_ref", "reason"} -- rows that
      cannot be applied; diff() never raises for bad input data, it reports
      conflicts instead (approval gate blocks on has_conflicts).
    """

    inserts: dict = field(default_factory=dict)
    updates: dict = field(default_factory=dict)
    deletes: dict = field(default_factory=dict)
    conflicts: dict = field(default_factory=dict)

    @property
    def empty(self):
        return not (any(self.inserts.values()) or any(self.updates.values())
                    or any(self.deletes.values()))

    @property
    def has_conflicts(self):
        return any(self.conflicts.values())


# Reference-name fields: the operator types a place/person by id, name, OR
# alias (places.resolve/people.resolve accept all three); the DB only ever
# stores an id, and export always writes back the entity's canonical
# `name`. Comparing the file's raw typed text against that canonical name
# byte-for-byte means ANY case/alias variance the operator used (a normal,
# expected way to fill this cell -- see the Col.comment on "участники" etc.)
# reads as a perpetual, spurious divergence, exactly like an un-expanded
# gis_url would. So when a conn is available, resolve these fields to their
# canonical DB name for comparison purposes -- mirroring _expand_gis_url's
# resolve-at-comparison-time treatment of "2ГИС-ссылка". An unresolvable
# ref is left as-is: diff()'s _check_refs (or verify's plain mismatch)
# reports that, this is not the place to hide a real unknown reference.
_REF_SINGLE_FIELDS = {
    "Люди": ("home",),
    "События": ("place",),
    "Серии": ("place",),
    "Планы": ("place", "person"),
}
_REF_LIST_FIELDS = {
    "Люди": ("members",),
    "События": ("participants",),
    "Серии": ("participants",),
}
_PERSON_FIELDS = {"person", "members", "participants"}


def _canonicalize_refs(conn, sheet, canon_row):
    if conn is None:
        return canon_row
    from fam import people, places

    def resolve_one(key, name):
        if not name:
            return name
        if key in _PERSON_FIELDS:
            ent = people.resolve(conn, name)
        else:
            ent = places.resolve(conn, name)
        return ent["name"] if ent else name

    for key in _REF_SINGLE_FIELDS.get(sheet, ()):
        if canon_row.get(key):
            canon_row[key] = resolve_one(key, canon_row[key])
    for key in _REF_LIST_FIELDS.get(sheet, ()):
        if canon_row.get(key):
            canon_row[key] = _canon_list_order(
                [resolve_one(key, v) for v in canon_row[key]])
    return canon_row


def _to_canonical(sheet, row, conn=None):
    """Bridge Task 1's normalize_row (built for xlsx header/label input)
    so it also accepts canonical-key rows (export_rows/snapshot shape),
    per the Task 4 brief's input contract. A row is treated as header-form
    if it carries any of the sheet's Russian column headers; otherwise
    it's assumed canonical and round-tripped through
    _canon_row_to_header_form (canon -> label) first, exactly like
    make_snapshot does for export_rows output.

    conn: when given, reference-name fields (place/person/home/members/
    participants) are resolved to their canonical DB name -- see
    _canonicalize_refs. Callers comparing against a live DB (diff(),
    verify_roundtrip()/roundtrip_mismatches()) should pass conn on the
    file-side rows; callers that only need existence/membership sets
    (_in_file_*_names) leave it None, matching prior behaviour.
    """
    spec = SHEETS[sheet]
    # Columns where header and key differ are the only reliable signal:
    # some columns (slug, id, lat, lon...) happen to share header==key, so
    # their presence says nothing about which form the row is in. If any
    # differing column's raw canonical key is present, this is a
    # canonical-key row (export_rows/snapshot shape) and needs bridging
    # through _canon_row_to_header_form before normalize_row's from_xlsx
    # pass (which expects header-form label values).
    distinguishing = [col for col in spec.columns if col.key != "id" and col.header != col.key]
    is_canon_form = any(col.key in row for col in distinguishing)
    if is_canon_form:
        row = _canon_row_to_header_form(sheet, row)
    canon_row = normalize_row(sheet, row)
    return _canonicalize_refs(conn, sheet, canon_row)


def _expand_gis_url(canon_row, cache):
    """File-side pre-pass for «Места» rows (used by diff() AND
    verify_roundtrip()): the 2ГИС-ссылка is sugar for lat/lon, expanded at
    parse time. If gis_url is set and the effective lat/lon are not both
    filled, resolve the url (once per unique url per run, via `cache`) and
    substitute the (lat, lon) into the row; gis_url itself is dropped from
    the comparison entirely (set to None -- exports always carry None
    there). Filled lat/lon in the file win and the link is ignored.
    Returns a conflict reason string on resolution failure, else None.
    Mutates canon_row in place.
    """
    url = canon_row.get("gis_url")
    canon_row["gis_url"] = None
    if not url:
        return None
    if canon_row.get("lat") is not None and canon_row.get("lon") is not None:
        return None  # lat/lon в файле в приоритете (контракт Col.comment)
    if url not in cache:
        try:
            cache[url] = geo2gis.resolve_place_coords(url)
        except Exception:
            cache[url] = None
    resolved = cache[url]
    if resolved is None:
        return f"не удалось развернуть 2ГИС-ссылку: {url}"
    canon_row["lat"], canon_row["lon"] = resolved  # contract: (lat, lon)
    return None


def _id_exists(conn, sheet, rid):
    table = _SHEET_TABLE[sheet]
    return conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (rid,)).fetchone() is not None


def _check_time_date(sheet, row):
    """Returns a human reason string if a date/time/weekday field is
    malformed, else None. Col.from_xlsx already validates weekday/kind/
    transport/category/enabled labels (raised as ValueError earlier, in
    _to_canonical); this covers the free-text date/time fields that have
    no Col transform (start/end/deadline/until_local/start_time/end_time/
    meds times), reusing the exact 'YYYY-MM-DD HH:MM' parser cal/seed use.
    """
    def _s(v):
        # normalize_row's _coerce_cell already stringifies Excel-coerced
        # datetime/date/time cells; this is a last-resort belt so a regex
        # .match below can never raise TypeError on a non-str value.
        return v if isinstance(v, str) else str(v)

    if sheet == "События":
        for key in ("start", "end"):
            v = row.get(key)
            if v:
                try:
                    local_to_utc_iso(_s(v))
                except ValueError as exc:
                    return str(exc)
    elif sheet == "Серии":
        for key in ("start_time", "end_time"):
            v = row.get(key)
            if v and not _HHMM_RE.match(_s(v)):
                return f"{key}: время не в формате ЧЧ:ММ: {v!r}"
        v = row.get("until_local")
        if v and not _YMD_RE.match(_s(v)):
            return f"до (дата): не в формате ГГГГ-ММ-ДД: {v!r}"
    elif sheet == "Планы":
        v = row.get("deadline")
        if v and not _YMD_RE.match(_s(v)):
            return f"срок: не в формате ГГГГ-ММ-ДД: {v!r}"
    elif sheet == "Лекарства":
        for t in row.get("times") or []:
            if not _HHMM_RE.match(_s(t)):
                return f"времена: не в формате ЧЧ:ММ: {t!r}"
    return None


def _in_file_place_names(file_rows_by_sheet):
    """fam.textnorm.fold keys of places inserted (no id) in this same file, so a
    place/event referencing a brand-new place from the same seed file
    resolves without needing that place to already exist in the DB.
    """
    names = set()
    for row in file_rows_by_sheet.get("Места", []):
        try:
            canon_row = _to_canonical("Места", row)
        except ValueError:
            continue
        if canon_row.get("id") is not None:
            continue
        name = canon_row.get("name")
        if name:
            names.add(fold(str(name)))
    return names


def _in_file_person_names(file_rows_by_sheet):
    """fam.textnorm.fold keys (names+aliases) of people inserted (no id) in this same
    file -- same rationale as _in_file_place_names. Group expansion is not
    needed here: a referenced in-file group name counts as resolvable on
    its own.
    """
    names = set()
    for row in file_rows_by_sheet.get("Люди", []):
        try:
            canon_row = _to_canonical("Люди", row)
        except ValueError:
            continue
        if canon_row.get("id") is not None:
            continue
        name = canon_row.get("name")
        if name:
            names.add(fold(str(name)))
        for alias in canon_row.get("aliases") or []:
            names.add(fold(str(alias)))
    return names


def _in_file_group_names(file_rows_by_sheet):
    """fam.textnorm.fold keys (names+aliases) of GROUP rows in this file (тип «группа»),
    id or no id -- so a group name written into участники is caught even
    when the group itself is being inserted by this very file.
    """
    names = set()
    for row in file_rows_by_sheet.get("Люди", []):
        try:
            canon_row = _to_canonical("Люди", row)
        except ValueError:
            continue
        if canon_row.get("kind") != "group":
            continue
        name = canon_row.get("name")
        if name:
            names.add(fold(str(name)))
        for alias in canon_row.get("aliases") or []:
            names.add(fold(str(alias)))
    return names


def _check_refs(conn, sheet, row, in_file_places=(), in_file_people=(),
                in_file_groups=()):
    """Returns a combined reason string (place/person unresolvable, or a
    place with no transport -- mirrors cli._check_trip_has_transport) or
    None. Issues are joined so a row with multiple problems reports all
    of them in one conflict entry.

    A place/person ref resolves against (live DB) union (in_file_places /
    in_file_people -- fam.textnorm.fold keys of insert rows elsewhere in the same
    file), so a file that both inserts a new place/person and references it
    from another row isn't wrongly flagged as a conflict.
    """
    from fam import people, places

    def place_ok(name):
        return places.resolve(conn, name) is not None or fold(name) in in_file_places

    def person_ok(name):
        return people.resolve(conn, name) is not None or fold(name) in in_file_people

    issues = []
    if sheet in ("События", "Серии"):
        place_name = row.get("place")
        transport = row.get("transport")
        if place_name:
            if not place_ok(place_name):
                issues.append(f"место не найдено: {place_name!r}")
            if transport is None or transport == "unknown":
                issues.append("место задано, но не задан транспорт")
        for pname in row.get("participants") or []:
            if not person_ok(pname):
                issues.append(f"участник не найден: {pname!r}")
                continue
            # Экспорт разворачивает группы в имена людей; группа, вписанная
            # в участники руками, применилась бы криво (молчаливое удаление
            # участников) и никогда не проходит verify -- конфликт сразу.
            pe = people.resolve(conn, pname)
            if (pe and pe.get("kind") == "group") or fold(pname) in in_file_groups:
                issues.append(f"участники: укажи имена людей, не группу {pname!r}")
    elif sheet == "Планы":
        place_name = row.get("place")
        if place_name and not place_ok(place_name):
            issues.append(f"место не найдено: {place_name!r}")
        person_name = row.get("person")
        if person_name and not person_ok(person_name):
            issues.append(f"человек не найден: {person_name!r}")
    elif sheet == "Люди":
        home = row.get("home")
        if home and not place_ok(home):
            issues.append(f"место не найдено: {home!r}")
        if row.get("kind") == "group":
            for m in row.get("members") or []:
                if not person_ok(m):
                    issues.append(f"участник группы не найден: {m!r}")
    return "; ".join(issues) if issues else None


def _place_referenced_outside_file(conn, place_id):
    """Only LIVE references block a delete: cancelled events, cancelled
    series and dropped/done plans are dead rows and must not pin a place
    in place forever. people.home_place_id always counts (no status)."""
    if conn.execute("SELECT 1 FROM plans WHERE place_id=? AND status='open' LIMIT 1",
                     (place_id,)).fetchone():
        return True
    if conn.execute("SELECT 1 FROM events WHERE place_id=? AND status='active' LIMIT 1",
                     (place_id,)).fetchone():
        return True
    if conn.execute("SELECT 1 FROM event_series WHERE place_id=? AND status='active' LIMIT 1",
                     (place_id,)).fetchone():
        return True
    if conn.execute("SELECT 1 FROM people WHERE home_place_id=? LIMIT 1", (place_id,)).fetchone():
        return True
    return False


def _person_referenced_outside_file(conn, person_id):
    """Same live-only rule as _place_referenced_outside_file: participants
    of a cancelled event/series don't block a delete, since the event/series
    itself is dead. group_members has no status of its own -- membership
    always counts."""
    if conn.execute("SELECT 1 FROM plans WHERE person_id=? AND status='open' LIMIT 1",
                     (person_id,)).fetchone():
        return True
    if conn.execute(
        "SELECT 1 FROM event_participants ep JOIN events e ON e.id = ep.event_id "
        "WHERE ep.person_id=? AND e.status='active' LIMIT 1", (person_id,)
    ).fetchone():
        return True
    if conn.execute(
        "SELECT 1 FROM event_series_participants sp JOIN event_series es ON es.id = sp.series_id "
        "WHERE sp.person_id=? AND es.status='active' LIMIT 1", (person_id,)
    ).fetchone():
        return True
    if conn.execute("SELECT 1 FROM group_members WHERE person_id=? LIMIT 1",
                     (person_id,)).fetchone():
        return True
    return False


def diff(conn, file_rows_by_sheet, snap):
    """Compare file_rows_by_sheet (header/label form from seed_xlsx, or
    canonical-key form as produced by export_rows) against the live DB and
    the export snapshot `snap`. Read-only: never writes to conn.

    inserts/updates classification is against the CURRENT live DB state
    (a fresh export_rows/make_snapshot taken here); deletes are bounded by
    `snap` (ids present when the file was exported) so rows created in the
    DB after the export are never proposed for deletion, even if the file
    doesn't mention them.
    """
    live_snap = make_snapshot(export_rows(conn))
    in_file_places = _in_file_place_names(file_rows_by_sheet)
    in_file_people = _in_file_person_names(file_rows_by_sheet)
    in_file_groups = _in_file_group_names(file_rows_by_sheet)

    inserts, updates, deletes, conflicts = {}, {}, {}, {}
    gis_cache = {}

    for sheet in SHEETS:
        file_rows = file_rows_by_sheet.get(sheet, [])
        snap_sheet = snap.get("sheets", {}).get(sheet, {})
        live_sheet = live_snap["sheets"].get(sheet, {})

        ins, upd, conf = [], [], []
        file_ids_seen = set()

        for idx, row in enumerate(file_rows):
            row_ref = f"{sheet}#{idx + 1}"
            try:
                canon_row = _to_canonical(sheet, row, conn=conn)
            except ValueError as exc:
                conf.append({"sheet": sheet, "row_ref": row_ref, "reason": str(exc)})
                continue

            rid = canon_row.get("id")

            # Short-circuit: if this row (by id) is byte-for-byte identical
            # to the current live DB state, it's a no-op -- nothing will be
            # written for it, so none of the conflict/validation checks
            # below (transport guardrail, ref resolution, time parsing,
            # gis_url expansion, ...) should run against it. This matters
            # for legacy rows that predate a guardrail added later (e.g.
            # place set + transport='unknown'): round-tripping an untouched
            # export must yield an empty diff, never a spurious conflict.
            # Duplicate-id / dedup bookkeeping still applies to no-op rows
            # so a file that lists the same id twice is still caught.
            if rid is not None and rid not in file_ids_seen and _id_exists(conn, sheet, rid):
                cur_noop = live_sheet.get(str(rid))
                if cur_noop is not None:
                    unchanged = all(cur_noop.get(k) == v for k, v in canon_row.items()
                                     if k != "id")
                    if unchanged:
                        file_ids_seen.add(rid)
                        continue

            issues = []

            if sheet == "Места":
                gis_issue = _expand_gis_url(canon_row, gis_cache)
                if gis_issue:
                    issues.append(gis_issue)

            if rid is not None:
                if rid in file_ids_seen:
                    issues.append(f"дублирующийся id {rid} в файле")
                else:
                    file_ids_seen.add(rid)
                if not _id_exists(conn, sheet, rid):
                    issues.append(f"id {rid} не найден в БД")

            time_issue = _check_time_date(sheet, canon_row)
            if time_issue:
                issues.append(time_issue)
            ref_issue = _check_refs(conn, sheet, canon_row,
                                     in_file_places=in_file_places,
                                     in_file_people=in_file_people,
                                     in_file_groups=in_file_groups)
            if ref_issue:
                issues.append(ref_issue)

            if issues:
                conf.append({"sheet": sheet, "row_ref": row_ref, "reason": "; ".join(issues)})
                continue

            if rid is None:
                ins.append(canon_row)
            else:
                cur = live_sheet.get(str(rid))
                if cur is None:
                    # id exists in the DB (checked above) but outside the
                    # exported live slice (e.g. a done plan) -- nothing to
                    # diff against, leave untouched.
                    continue
                changes = {k: (cur.get(k), v) for k, v in canon_row.items()
                           if k != "id" and cur.get(k) != v}
                if changes:
                    upd.append({"id": rid, "changes": changes})

        dele = []
        for str_id, snap_row in snap_sheet.items():
            rid = int(str_id)
            if rid in file_ids_seen:
                continue
            if sheet == "Места" and _place_referenced_outside_file(conn, rid):
                conf.append({"sheet": sheet, "row_ref": f"{sheet}#id{rid}",
                             "reason": f"место #{rid} ({snap_row.get('name')}) удалить нельзя: "
                                       "на него ещё ссылаются планы/события/серии/дом человека"})
                continue
            if sheet == "Люди" and _person_referenced_outside_file(conn, rid):
                conf.append({"sheet": sheet, "row_ref": f"{sheet}#id{rid}",
                             "reason": f"человек #{rid} ({snap_row.get('name')}) удалить нельзя: "
                                       "на него ещё ссылаются планы/события/серии/группы"})
                continue
            dele.append(snap_row)

        inserts[sheet] = ins
        updates[sheet] = upd
        deletes[sheet] = dele
        conflicts[sheet] = conf

    return Diff(inserts=inserts, updates=updates, deletes=deletes, conflicts=conflicts)


def format_report(d):
    """Russian-language summary for Denis's approval gate: per sheet,
    ➕ inserts / ✏️ updates (field: was -> now) / 🗑 deletes, followed by a
    ⚠️ conflicts section (if any) listing sheet/row/reason. Sheets with no
    activity at all are omitted.
    """
    lines = []
    for sheet in SHEETS:
        ins = d.inserts.get(sheet, [])
        upd = d.updates.get(sheet, [])
        dele = d.deletes.get(sheet, [])
        conf = d.conflicts.get(sheet, [])
        if not (ins or upd or dele or conf):
            continue
        lines.append(f"## {sheet}")
        if ins:
            lines.append(f"➕ {len(ins)}")
        for u in upd:
            changes = ", ".join(f"{k}: {was!r} → {now!r}" for k, (was, now) in u["changes"].items())
            lines.append(f"✏️ #{u['id']} ({changes})")
        if dele:
            lines.append(f"🗑 {len(dele)}")
        if conf:
            lines.append("⚠️ конфликты:")
            for c in conf:
                lines.append(f"  - {c['row_ref']}: {c['reason']}")
        lines.append("")

    if not lines:
        return "Изменений нет."
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Task 5: applier + post-apply round-trip verify
# ---------------------------------------------------------------------------

_APPLY_ORDER = ["Места", "Люди", "Серии", "События", "Планы", "Лекарства", "Покупки"]


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- NOT NULL default coercion --------------------------------------------
#
# normalize_row (Task 1) maps every empty/blank cell to None -- that's the
# right canonical shape for diff/verify_roundtrip comparisons (an exported
# '' also normalizes to None on the DB side, see make_snapshot, so both
# sides agree and a cleared cell that was already empty stays a no-op).
# But a handful of columns are declared NOT NULL DEFAULT <x> in db.py's
# SCHEMA (e.g. places.address, events.transport) -- passing that bare None
# straight into an INSERT/UPDATE crashes with a NOT NULL IntegrityError and
# rolls back the whole apply (hit live: an operator cleared "адрес" and the
# import aborted). travel_min is the one exception -- normalize_row already
# folds its empty cell to 0 at parse time (see _travel_min_from_xlsx's call
# site above), so it never reaches here as None; it's still listed so a
# future reader doesn't wonder why it's missing.
#
# This table is the single place that maps sheet + column key -> schema
# default (kept in sync with db.py's init_db SCHEMA, the ground truth for
# NOT NULL columns) -- adding a new NOT NULL DEFAULT column to any sheet is
# a one-line addition here, applied uniformly to both insert rows and
# update changes below.
_NOT_NULL_DEFAULTS = {
    "Места": {"address": "", "notes": "", "source": "manual", "travel_min": 0},
    "Люди": {"notes": ""},
    "События": {"notes": "", "transport": "unknown"},
    "Серии": {"notes": "", "transport": "unknown"},
    "Планы": {"notes": ""},
    "Лекарства": {"dose": ""},
    "Покупки": {"qty": "", "added_by": "", "source": "manual"},
}


def _with_defaults(sheet, row):
    """Insert-row form: coerce any None value for a NOT NULL DEFAULT column
    (per _NOT_NULL_DEFAULTS[sheet]) to its schema default. Returns a new
    dict; row itself is not mutated. Columns absent from the row, or not
    listed for this sheet, pass through untouched."""
    defaults = _NOT_NULL_DEFAULTS.get(sheet)
    if not defaults:
        return row
    out = dict(row)
    for key, default in defaults.items():
        if key in out and out[key] is None:
            out[key] = default
    return out


def _with_defaults_in_changes(sheet, changes):
    """Update-changes form: same coercion as _with_defaults, but applied to
    the "now" side of a {key: (was, now)} changes dict (as produced by
    diff()). The "was" side is left as-is -- it only feeds the audit log
    and format_report, never a write."""
    defaults = _NOT_NULL_DEFAULTS.get(sheet)
    if not defaults:
        return changes
    out = dict(changes)
    for key, default in defaults.items():
        if key in out:
            was, now = out[key]
            if now is None:
                out[key] = (was, default)
    return out


# -- Места --------------------------------------------------------------

def _insert_place(conn, row):
    from fam import places

    # gis_url is already expanded into lat/lon at parse time (see
    # _expand_gis_url in diff()/verify_roundtrip) -- no special-casing here.
    lat, lon = row.get("lat"), row.get("lon")

    p = places.add(conn, row["name"], address=row.get("address") or "",
                    lat=lat, lon=lon, aliases=row.get("aliases") or ())

    extra = {}
    if row.get("category") is not None:
        extra["category"] = row["category"]
    if row.get("travel_min") is not None:
        extra["travel_min"] = row["travel_min"]
    if extra:
        places.update(conn, p["id"], **extra)

    audit.log(conn, "seed.Места.insert", {"id": p["id"], "name": row["name"]}, actor="admin")
    return p["id"]


def _update_place(conn, item):
    from fam import places

    rid, changes = item["id"], item["changes"]
    # gis_url is already expanded into lat/lon at parse time (see
    # _expand_gis_url in diff()) -- changes carry real coordinate diffs.
    fields = {k: v[1] for k, v in changes.items() if k in places._UPDATE_FIELDS}

    # name не входит в places._UPDATE_FIELDS -- переименование прямым
    # UPDATE, как _apply_people_update; audit-пейлоад ниже несёт changes
    # как есть, включая name.
    if "name" in changes:
        conn.execute("UPDATE places SET name=? WHERE id=?", (changes["name"][1], rid))

    if fields:
        places.update(conn, rid, **fields)

    if "aliases" in changes:
        was, now = changes["aliases"]
        was, now = set(was or []), set(now or [])
        for a in now - was:
            places.alias(conn, rid, a)
        for a in was - now:
            conn.execute("DELETE FROM place_aliases WHERE place_id=? AND alias=?", (rid, a))

    audit.log(conn, "seed.Места.update", {"id": rid, "changes": changes}, actor="admin")


def _detach_dead_place_refs(conn, place_id):
    """Mirror _place_referenced_outside_file's dead-row exception at DB
    level: cancelled/done events, cancelled series and dropped/done plans
    are allowed to reference a place that's about to be hard-deleted, but
    the plain (non-CASCADE) place_id FK still fires on DELETE unless those
    references are detached first. Only dead rows are touched -- a live
    reference is exactly what the diff guard is supposed to prevent from
    ever reaching here, so if one somehow slips through it's left alone
    and the subsequent DELETE fails loudly (FK violation) rather than
    silently detaching a live row. Returns a dict of counts for the audit
    log.
    """
    counts = {}
    cur = conn.execute(
        "UPDATE events SET place_id=NULL WHERE place_id=? AND status IN ('cancelled','done')",
        (place_id,))
    counts["events"] = cur.rowcount
    cur = conn.execute(
        "UPDATE event_series SET place_id=NULL WHERE place_id=? AND status='cancelled'",
        (place_id,))
    counts["event_series"] = cur.rowcount
    cur = conn.execute(
        "UPDATE plans SET place_id=NULL WHERE place_id=? AND status IN ('dropped','done')",
        (place_id,))
    counts["plans"] = cur.rowcount
    return counts


def _delete_place(conn, row):
    rid = row["id"]
    detached = _detach_dead_place_refs(conn, rid)
    conn.execute("DELETE FROM place_aliases WHERE place_id=?", (rid,))
    conn.execute("DELETE FROM places WHERE id=?", (rid,))
    audit.log(conn, "seed.Места.delete",
              {"id": rid, "name": row.get("name"), "detached": detached}, actor="admin")


# -- Люди -----------------------------------------------------------------

def _apply_people_inserts(conn, rows):
    from fam import people

    inserted = []
    for row in rows:
        p = people.add(conn, row["name"], kind=row.get("kind") or "person",
                        slug=row.get("slug"), aliases=row.get("aliases") or ())
        audit.log(conn, "seed.Люди.insert", {"id": p["id"], "name": row["name"]}, actor="admin")
        inserted.append((p["id"], row))

    # Second pass: home/members may reference entities inserted earlier in
    # this same file (places always precede people; a group's members may
    # be people inserted just above it in this pass).
    for pid, row in inserted:
        if row.get("home"):
            people.set_home(conn, pid, row["home"])
        for m in row.get("members") or []:
            people.add_member(conn, pid, m)


def _apply_people_update(conn, item):
    from fam import people

    rid, changes = item["id"], item["changes"]
    set_clauses, params = [], []
    for key in ("name", "slug", "notes"):
        if key in changes:
            set_clauses.append(f"{key}=?")
            params.append(changes[key][1])
    if set_clauses:
        params.append(rid)
        conn.execute(f"UPDATE people SET {', '.join(set_clauses)} WHERE id=?", params)

    if "home" in changes:
        people.set_home(conn, rid, changes["home"][1])

    if "aliases" in changes:
        was, now = changes["aliases"]
        was, now = set(was or []), set(now or [])
        for a in now - was:
            people.alias(conn, rid, a)
        for a in was - now:
            conn.execute("DELETE FROM people_aliases WHERE person_id=? AND alias=?", (rid, a))

    if "members" in changes:
        was, now = changes["members"]
        was, now = set(was or []), set(now or [])
        for m in now - was:
            people.add_member(conn, rid, m)
        for m in was - now:
            mp = people.get(conn, m)
            if mp:
                conn.execute("DELETE FROM group_members WHERE group_id=? AND person_id=?",
                             (rid, mp["id"]))

    audit.log(conn, "seed.Люди.update", {"id": rid, "changes": changes}, actor="admin")


def _detach_dead_person_refs(conn, person_id):
    """Mirror _person_referenced_outside_file's dead-row exception at DB
    level. event_participants and event_series_participants carry
    ON DELETE CASCADE (see fam/db.py init_db) so cancelled/done
    events/series referencing this person are cleaned up automatically by
    SQLite once `people` is deleted -- no manual handling needed there.
    plans.person_id is a plain FK, so a dropped/done plan referencing this
    person must be detached first or the hard delete below raises a FK
    violation. Only dead rows are touched; a live plan is exactly what the
    diff guard should have blocked, so leave it to fail loudly if present.
    """
    cur = conn.execute(
        "UPDATE plans SET person_id=NULL WHERE person_id=? AND status IN ('dropped','done')",
        (person_id,))
    return {"plans": cur.rowcount}


def _delete_person(conn, row):
    rid = row["id"]
    detached = _detach_dead_person_refs(conn, rid)
    conn.execute("DELETE FROM people_aliases WHERE person_id=?", (rid,))
    conn.execute("DELETE FROM group_members WHERE group_id=? OR person_id=?", (rid, rid))
    conn.execute("DELETE FROM people WHERE id=?", (rid,))
    audit.log(conn, "seed.Люди.delete",
              {"id": rid, "name": row.get("name"), "detached": detached}, actor="admin")


# -- Серии ------------------------------------------------------------------

def _apply_series_insert(conn, row):
    from fam import series

    s = series.add(conn, row["title"], row["weekdays"], row["start_time"],
                    end_time=row.get("end_time"), place=row.get("place"),
                    participants=row.get("participants") or (),
                    transport=row.get("transport") or "unknown",
                    notes=row.get("notes") or "", until_local=row.get("until_local"),
                    prep_min=row.get("prep_min"))
    audit.log(conn, "seed.Серии.insert", {"id": s["id"], "title": row["title"]}, actor="admin")
    return s["id"]


_SERIES_SCHEDULE_FIELDS = {"weekdays", "start_time", "end_time", "place", "transport",
                           "prep_min", "until_local"}


def _cancel_future_series_occurrences(conn, sid, now_iso, old_start_time):
    """Remove future active occurrences of series `sid` that are still ON
    the series grid (local HH:MM equals the series' OLD start_time, i.e.
    the value BEFORE this update was applied -- callers must capture it
    first), mirroring series.update_participants's "future untouched"
    filter. An occurrence individually rescheduled off-grid (cal.update
    leaves it with a different local start time) keeps its series_id and
    is left untouched here; series.generate() then only fills in the
    (series_id, new-grid-slot) pairs that are still empty.
    """
    from fam import cal

    rows = conn.execute(
        "SELECT id, start_utc FROM events WHERE series_id=? AND status='active' AND "
        "start_utc > ?", (sid, now_iso)).fetchall()
    for r in rows:
        local_hm = cal._to_local_iso(r["start_utc"])[11:16]
        if local_hm != old_start_time:
            continue  # rescheduled off the series grid -- leave alone
        event_id = r["id"]
        cal._prep_cascade_cancel(conn, event_id)
        conn.execute("UPDATE plans SET prep_for_event_id=NULL WHERE prep_for_event_id=?",
                     (event_id,))
        conn.execute("UPDATE plans SET attached_event_id=NULL WHERE attached_event_id=?",
                     (event_id,))
        conn.execute("DELETE FROM events WHERE id=?", (event_id,))


def _apply_series_update(conn, item, now_iso):
    from fam import places, series

    rid, changes = item["id"], item["changes"]
    # Capture the OLD start_time (the grid this series' occurrences were
    # generated on) before any mutation below -- _cancel_future_series_
    # occurrences must filter against it, not the just-applied new value.
    old_start_time = conn.execute(
        "SELECT start_time FROM event_series WHERE id=?", (rid,)).fetchone()["start_time"]

    set_clauses, params = [], []
    simple_map = {"title": "title", "weekdays": "weekdays", "start_time": "start_time",
                  "end_time": "end_time", "transport": "transport", "notes": "notes",
                  "until_local": "until_local", "prep_min": "prep_min"}
    for key, col in simple_map.items():
        if key in changes:
            set_clauses.append(f"{col}=?")
            params.append(changes[key][1])
    if "place" in changes:
        new_place_name = changes["place"][1]
        pl = places.resolve(conn, new_place_name) if new_place_name else None
        set_clauses.append("place_id=?")
        params.append(pl["id"] if pl else None)
    if set_clauses:
        set_clauses.append("updated_at=?")
        params.append(now_iso)
        params.append(rid)
        conn.execute(f"UPDATE event_series SET {', '.join(set_clauses)} WHERE id=?", params)

    if "participants" in changes:
        was, now = changes["participants"]
        was, now = set(was or []), set(now or [])
        series.update_participants(conn, rid, add=list(now - was), remove=list(was - now),
                                    now_utc=now_iso)

    if _SERIES_SCHEDULE_FIELDS & set(changes):
        _cancel_future_series_occurrences(conn, rid, now_iso, old_start_time)

    audit.log(conn, "seed.Серии.update", {"id": rid, "changes": changes}, actor="admin")


def _delete_series(conn, row):
    from fam import series

    series.cancel(conn, row["id"])
    audit.log(conn, "seed.Серии.delete", {"id": row["id"], "title": row.get("title")},
              actor="admin")


# -- События ------------------------------------------------------------------

def _apply_event_insert(conn, row):
    from fam import cal

    start_utc = local_to_utc_iso(row["start"])
    end_utc = local_to_utc_iso(row["end"]) if row.get("end") else None
    ev = cal.add(conn, row["title"], start_utc, end_utc=end_utc, place=row.get("place"),
                 participants=row.get("participants") or (),
                 transport=row.get("transport") or "unknown",
                 notes=row.get("notes") or "", prep_min=row.get("prep_min"))
    audit.log(conn, "seed.События.insert", {"id": ev["id"], "title": row["title"]}, actor="admin")
    return ev["id"]


def _apply_event_update(conn, item):
    from fam import cal

    rid, changes = item["id"], item["changes"]
    fields = {}
    if "title" in changes:
        fields["title"] = changes["title"][1]
    if "start" in changes:
        fields["start_utc"] = local_to_utc_iso(changes["start"][1])
    if "end" in changes:
        v = changes["end"][1]
        fields["end_utc"] = local_to_utc_iso(v) if v else None
    if "place" in changes:
        fields["place"] = changes["place"][1]
    if "transport" in changes:
        fields["transport"] = changes["transport"][1]
    if "notes" in changes:
        fields["notes"] = changes["notes"][1]
    if "prep_min" in changes:
        fields["prep_min"] = changes["prep_min"][1]
    if "participants" in changes:
        was, now = changes["participants"]
        was, now = set(was or []), set(now or [])
        add_p, rm_p = list(now - was), list(was - now)
        if add_p:
            fields["add_person"] = add_p
        if rm_p:
            fields["rm_person"] = rm_p

    if fields:
        cal.update(conn, rid, **fields)

    audit.log(conn, "seed.События.update", {"id": rid, "changes": changes}, actor="admin")


def _delete_event(conn, row):
    from fam import cal

    cal.cancel(conn, row["id"])
    audit.log(conn, "seed.События.delete", {"id": row["id"], "title": row.get("title")},
              actor="admin")


# -- Планы ------------------------------------------------------------------

def _apply_plan_insert(conn, row):
    from fam import plans

    pid = plans.add(conn, row["title"], place=row.get("place"), person=row.get("person"),
                     deadline=row.get("deadline"), notes=row.get("notes") or "")
    audit.log(conn, "seed.Планы.insert", {"id": pid, "title": row["title"]}, actor="admin")
    return pid


def _apply_plan_update(conn, item):
    from fam import people, places

    rid, changes = item["id"], item["changes"]
    set_clauses, params = [], []
    if "title" in changes:
        set_clauses.append("title=?")
        params.append(changes["title"][1])
    if "deadline" in changes:
        set_clauses.append("deadline=?")
        params.append(changes["deadline"][1])
    if "notes" in changes:
        set_clauses.append("notes=?")
        params.append(changes["notes"][1])
    if "place" in changes:
        v = changes["place"][1]
        pl = places.resolve(conn, v) if v else None
        set_clauses.append("place_id=?")
        params.append(pl["id"] if pl else None)
    if "person" in changes:
        v = changes["person"][1]
        pe = people.resolve(conn, v) if v else None
        set_clauses.append("person_id=?")
        params.append(pe["id"] if pe else None)
    if set_clauses:
        params.append(rid)
        conn.execute(f"UPDATE plans SET {', '.join(set_clauses)} WHERE id=?", params)

    audit.log(conn, "seed.Планы.update", {"id": rid, "changes": changes}, actor="admin")


def _delete_plan(conn, row):
    from fam import plans

    plans.mark(conn, row["id"], "dropped")
    audit.log(conn, "seed.Планы.delete", {"id": row["id"], "title": row.get("title")},
              actor="admin")


# -- Лекарства ------------------------------------------------------------

def _apply_med_insert(conn, row):
    from fam import meds

    mid = meds.add(conn, row["name"], row.get("times") or [], dose=row.get("dose") or "",
                    remaining=row.get("remaining"), threshold=row.get("threshold") or 0)
    if row.get("enabled") == 0:
        meds.edit(conn, mid, enabled=0)
    audit.log(conn, "seed.Лекарства.insert", {"id": mid, "name": row["name"]}, actor="admin")
    return mid


def _apply_med_update(conn, item):
    from fam import meds

    rid, changes = item["id"], item["changes"]
    fields = {k: v[1] for k, v in changes.items() if k in meds._EDIT_FIELDS}
    if fields:
        meds.edit(conn, rid, **fields)

    audit.log(conn, "seed.Лекарства.update", {"id": rid, "changes": changes}, actor="admin")


def _delete_med(conn, row):
    from fam import meds

    meds.remove(conn, row["id"])
    audit.log(conn, "seed.Лекарства.delete", {"id": row["id"], "name": row.get("name")},
              actor="admin")


# -- Покупки ------------------------------------------------------------------

def _apply_shopping_insert(conn, row):
    from fam import shopping

    sid = shopping.add(conn, row["name"], qty=row.get("qty") or "",
                        added_by=row.get("added_by") or "")
    audit.log(conn, "seed.Покупки.insert", {"id": sid, "name": row["name"]}, actor="admin")
    return sid


def _apply_shopping_update(conn, item):
    rid, changes = item["id"], item["changes"]
    set_clauses, params = [], []
    for key in ("name", "qty", "added_by"):
        if key in changes:
            set_clauses.append(f"{key}=?")
            params.append(changes[key][1])
    if set_clauses:
        params.append(rid)
        conn.execute(f"UPDATE shopping SET {', '.join(set_clauses)} WHERE id=?", params)

    audit.log(conn, "seed.Покупки.update", {"id": rid, "changes": changes}, actor="admin")


def _delete_shopping(conn, row):
    conn.execute("DELETE FROM shopping WHERE id=?", (row["id"],))
    audit.log(conn, "seed.Покупки.delete", {"id": row["id"], "name": row.get("name")},
              actor="admin")


_ROW_INSERT_FN = {
    "Места": _insert_place,
    "Серии": _apply_series_insert,
    "События": _apply_event_insert,
    "Планы": _apply_plan_insert,
    "Лекарства": _apply_med_insert,
    "Покупки": _apply_shopping_insert,
}

_UPDATE_FN = {
    "Места": _update_place,
    "Люди": _apply_people_update,
    "Серии": None,                     # needs now_iso, handled specially
    "События": _apply_event_update,
    "Планы": _apply_plan_update,
    "Лекарства": _apply_med_update,
    "Покупки": _apply_shopping_update,
}

_DELETE_FN = {
    "Места": _delete_place,
    "Люди": _delete_person,
    "Серии": _delete_series,
    "События": _delete_event,
    "Планы": _delete_plan,
    "Лекарства": _delete_med,
    "Покупки": _delete_shopping,
}


def apply_diff(conn, d, now_utc=None):
    """Apply an approved Diff to the DB through the fam domain modules.
    Never commits (the caller's transaction, mirroring every fam module).
    Raises ValueError if d.has_conflicts -- an approval gate must never
    apply a diff with unresolved conflicts.

    Order: Места -> Люди -> Серии -> События -> Планы -> Лекарства ->
    Покупки for inserts/updates (so a row referencing an entity inserted
    earlier in this same file resolves); deletes run afterwards, in the
    reverse sheet order (so a delete never trips over a live reference
    from a sheet processed earlier in this same apply). One
    series.generate() runs at the very end, after every series/event
    mutation has landed, so newly-updated schedules materialize their
    future occurrences in a single pass.

    Returns a dict of per-sheet {"inserts": n, "updates": n, "deletes": n}
    counts of what was applied.
    """
    if d.has_conflicts:
        raise ValueError("diff has conflicts, cannot apply")

    now_iso = now_utc.isoformat(timespec="seconds") if now_utc else _now_iso()

    counts = {"inserts": {}, "updates": {}, "deletes": {}}

    for sheet in _APPLY_ORDER:
        ins_rows = [_with_defaults(sheet, row) for row in d.inserts.get(sheet, [])]
        upd_items = [{**item, "changes": _with_defaults_in_changes(sheet, item["changes"])}
                     for item in d.updates.get(sheet, [])]

        if sheet == "Люди":
            _apply_people_inserts(conn, ins_rows)
        else:
            for row in ins_rows:
                _ROW_INSERT_FN[sheet](conn, row)

        if sheet == "Серии":
            for item in upd_items:
                _apply_series_update(conn, item, now_iso)
        else:
            for item in upd_items:
                _UPDATE_FN[sheet](conn, item)

        counts["inserts"][sheet] = len(ins_rows)
        counts["updates"][sheet] = len(upd_items)

    for sheet in reversed(_APPLY_ORDER):
        del_rows = d.deletes.get(sheet, [])
        for row in del_rows:
            _DELETE_FN[sheet](conn, row)
        counts["deletes"][sheet] = len(del_rows)

    from fam import series as series_mod

    series_mod.generate(conn, now_utc=now_iso)

    return counts


def roundtrip_mismatches(conn, file_rows_by_sheet):
    """[] iff a fresh export_rows(conn) matches file_rows_by_sheet, sheet by
    sheet: rows carrying an id must match the fresh export's row at that id
    exactly (normalized, and reference-name fields resolved to their
    canonical form -- see _canonicalize_refs); rows with no id
    (freshly-inserted, the file never learns the id apply_diff assigned)
    are matched by content alone, ignoring id, against whatever fresh rows
    are left after every id-carrying row has claimed its match. Every
    fresh row must be claimed by exactly one file row, in both directions
    -- an unmatched leftover on either side means the DB and the file have
    diverged.

    Returns a list of mismatch dicts (empty when clean), each one of:
      {"sheet", "id", "reason"}                         -- id/url issues
      {"sheet", "id", "reason", "fields", "file", "db"}  -- field diffs
      {"sheet", "id": None, "reason", "file", "db": None} -- unmatched file row
      {"sheet", "id", "reason", "file": None, "db"}       -- unmatched DB row

    verify_roundtrip() is a thin bool wrapper around this; this is the
    version scripts/data_roundtrip.py's exit-3 path uses to tell the
    operator WHAT diverged, not just that it did.
    """
    mismatches = []
    fresh = export_rows(conn)
    gis_cache = {}

    for sheet in SHEETS:
        file_list = [_to_canonical(sheet, r, conn=conn) for r in file_rows_by_sheet.get(sheet, [])]
        if sheet == "Места":
            for r in file_list:
                issue = _expand_gis_url(r, gis_cache)
                if issue:
                    mismatches.append({"sheet": sheet, "id": r.get("id"),
                                        "reason": issue, "file": r, "db": None})
        fresh_list = [_to_canonical(sheet, r) for r in fresh.get(sheet, [])]

        with_id = [r for r in file_list if r.get("id") is not None]
        without_id = [r for r in file_list if r.get("id") is None]

        fresh_by_id = {r["id"]: r for r in fresh_list}
        used_ids = set()
        for r in with_id:
            fr = fresh_by_id.get(r["id"])
            if fr is None:
                mismatches.append({"sheet": sheet, "id": r["id"],
                                    "reason": "id отсутствует в свежем экспорте",
                                    "file": r, "db": None})
                continue
            if fr != r:
                fields = {k: {"file": r.get(k), "db": fr.get(k)}
                          for k in sorted(set(r) | set(fr)) if r.get(k) != fr.get(k)}
                mismatches.append({"sheet": sheet, "id": r["id"], "reason": "поля не совпадают",
                                    "fields": fields, "file": r, "db": fr})
            used_ids.add(r["id"])

        remaining = [r for r in fresh_list if r["id"] not in used_ids]
        for r in without_id:
            r_no_id = {k: v for k, v in r.items() if k != "id"}
            match_idx = None
            for i, cand in enumerate(remaining):
                cand_no_id = {k: v for k, v in cand.items() if k != "id"}
                if cand_no_id == r_no_id:
                    match_idx = i
                    break
            if match_idx is None:
                mismatches.append({"sheet": sheet, "id": None,
                                    "reason": "новая строка файла не найдена в свежем экспорте",
                                    "file": r, "db": None})
            else:
                remaining.pop(match_idx)

        for r in remaining:
            mismatches.append({"sheet": sheet, "id": r.get("id"),
                                "reason": "запись есть в свежем экспорте, но не найдена в файле",
                                "file": None, "db": r})

    return mismatches


def format_mismatches(mismatches):
    """Human-readable, Russian-language rendering of roundtrip_mismatches()
    for the exit-3 operator path (scripts/data_roundtrip.py): sheet + row
    identity + differing fields, so an operator sees the cause instead of
    a bare warning."""
    if not mismatches:
        return "Расхождений нет."
    lines = []
    for m in mismatches:
        loc = f"{m['sheet']}#{m['id']}" if m.get("id") is not None else f"{m['sheet']} (новая строка)"
        lines.append(f"- {loc}: {m['reason']}")
        for key, fv in (m.get("fields") or {}).items():
            lines.append(f"    {key}: файл={fv['file']!r}  БД={fv['db']!r}")
    return "\n".join(lines)


def verify_roundtrip(conn, file_rows_by_sheet):
    """True iff a fresh export_rows(conn) matches file_rows_by_sheet.
    Thin bool wrapper around roundtrip_mismatches() -- see there for the
    matching contract; existing callers only need the bool, so this keeps
    working unchanged."""
    return not roundtrip_mismatches(conn, file_rows_by_sheet)
