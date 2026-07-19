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
