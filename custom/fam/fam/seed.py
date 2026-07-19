"""Data seeding round-trip: единый словарь листов/колонок xlsx ↔ fam.

Комментарии колонок (Col.comment) — контракт спеки §3: что за данные и на
какое поведение Гермеса влияют; seed_xlsx вешает их на заголовки.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from .cal import ALMATY

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
       "«пора выходить», пока нет координат/дороги. 0 = не задано."),
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
        if isinstance(v, str):
            v = v.strip() or None
        if col.key == "id":
            v = int(v) if v not in (None, "") else None
        elif v is not None and col.from_xlsx:
            v = col.from_xlsx(v)
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


def _to_canonical(sheet, row):
    """Bridge Task 1's normalize_row (built for xlsx header/label input)
    so it also accepts canonical-key rows (export_rows/snapshot shape),
    per the Task 4 brief's input contract. A row is treated as header-form
    if it carries any of the sheet's Russian column headers; otherwise
    it's assumed canonical and round-tripped through
    _canon_row_to_header_form (canon -> label) first, exactly like
    make_snapshot does for export_rows output.
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
    return normalize_row(sheet, row)


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
    if sheet == "События":
        for key in ("start", "end"):
            v = row.get(key)
            if v:
                try:
                    local_to_utc_iso(v)
                except ValueError as exc:
                    return str(exc)
    elif sheet == "Серии":
        for key in ("start_time", "end_time"):
            v = row.get(key)
            if v and not _HHMM_RE.match(v):
                return f"{key}: время не в формате ЧЧ:ММ: {v!r}"
        v = row.get("until_local")
        if v and not _YMD_RE.match(v):
            return f"до (дата): не в формате ГГГГ-ММ-ДД: {v!r}"
    elif sheet == "Планы":
        v = row.get("deadline")
        if v and not _YMD_RE.match(v):
            return f"срок: не в формате ГГГГ-ММ-ДД: {v!r}"
    elif sheet == "Лекарства":
        for t in row.get("times") or []:
            if not _HHMM_RE.match(t):
                return f"времена: не в формате ЧЧ:ММ: {t!r}"
    return None


def _in_file_place_names(file_rows_by_sheet):
    """Casefolded names of places inserted (no id) in this same file, so a
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
            names.add(str(name).casefold())
    return names


def _in_file_person_names(file_rows_by_sheet):
    """Casefolded names+aliases of people inserted (no id) in this same
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
            names.add(str(name).casefold())
        for alias in canon_row.get("aliases") or []:
            names.add(str(alias).casefold())
    return names


def _check_refs(conn, sheet, row, in_file_places=(), in_file_people=()):
    """Returns a combined reason string (place/person unresolvable, or a
    place with no transport -- mirrors cli._check_trip_has_transport) or
    None. Issues are joined so a row with multiple problems reports all
    of them in one conflict entry.

    A place/person ref resolves against (live DB) union (in_file_places /
    in_file_people -- casefolded names of insert rows elsewhere in the same
    file), so a file that both inserts a new place/person and references it
    from another row isn't wrongly flagged as a conflict.
    """
    from fam import people, places

    def place_ok(name):
        return places.resolve(conn, name) is not None or name.casefold() in in_file_places

    def person_ok(name):
        return people.resolve(conn, name) is not None or name.casefold() in in_file_people

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
    for table, col in (("plans", "place_id"), ("events", "place_id"),
                        ("event_series", "place_id")):
        if conn.execute(f"SELECT 1 FROM {table} WHERE {col}=? LIMIT 1", (place_id,)).fetchone():
            return True
    if conn.execute("SELECT 1 FROM people WHERE home_place_id=? LIMIT 1", (place_id,)).fetchone():
        return True
    return False


def _person_referenced_outside_file(conn, person_id):
    for table, col in (("plans", "person_id"), ("event_participants", "person_id"),
                        ("event_series_participants", "person_id"),
                        ("group_members", "person_id")):
        if conn.execute(f"SELECT 1 FROM {table} WHERE {col}=? LIMIT 1", (person_id,)).fetchone():
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

    inserts, updates, deletes, conflicts = {}, {}, {}, {}

    for sheet in SHEETS:
        file_rows = file_rows_by_sheet.get(sheet, [])
        snap_sheet = snap.get("sheets", {}).get(sheet, {})
        live_sheet = live_snap["sheets"].get(sheet, {})

        ins, upd, conf = [], [], []
        file_ids_seen = set()

        for idx, row in enumerate(file_rows):
            row_ref = f"{sheet}#{idx + 1}"
            try:
                canon_row = _to_canonical(sheet, row)
            except ValueError as exc:
                conf.append({"sheet": sheet, "row_ref": row_ref, "reason": str(exc)})
                continue

            rid = canon_row.get("id")
            issues = []

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
                                     in_file_people=in_file_people)
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
