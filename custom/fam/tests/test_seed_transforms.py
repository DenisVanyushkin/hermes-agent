import pytest
from fam import seed


def test_local_to_utc_roundtrip():
    assert seed.local_to_utc_iso("2026-08-01 10:00") == "2026-08-01T05:00:00+00:00"  # Алматы = UTC+5
    assert seed.utc_iso_to_local("2026-08-01T05:00:00+00:00") == "2026-08-01 10:00"


def test_local_to_utc_rejects_garbage():
    with pytest.raises(ValueError):
        seed.local_to_utc_iso("завтра в 10")


def test_labels_bidirectional():
    assert seed.label("transport", "car") == "машина"
    assert seed.canon("transport", "Машина") == "car"       # регистронезависимо
    assert seed.canon("weekday", "пн") == "mon"
    assert seed.label("kind", "group") == "группа"
    with pytest.raises(ValueError):
        seed.canon("transport", "такси")


def test_canon_enabled_with_bad_value():
    """Bad «включено» value should raise descriptive ValueError, not bare KeyError."""
    assert seed.canon("enabled", "Да") == 1
    assert seed.canon("enabled", "да") == 1
    assert seed.canon("enabled", "нет") == 0
    with pytest.raises(ValueError, match="enabled: неизвестное значение .*, ожидалось одно из"):
        seed.canon("enabled", "maybe")


def test_sheets_declared():
    assert set(seed.SHEETS) == {"Люди", "Места", "События", "Серии", "Планы", "Лекарства", "Покупки"}
    for spec in seed.SHEETS.values():
        assert spec.columns[0].key == "id"
        for col in spec.columns:
            assert col.comment.strip()          # спека: комментарий у КАЖДОЙ колонки


def test_normalize_row_strips_and_nulls():
    n = seed.normalize_row("Планы", {"id": "3", "название": "  Пироги ", "срок": "", "место": None,
                                     "человек": "", "заметки": "", "связь с событием": "x"})
    assert n == {"id": 3, "title": "Пироги", "deadline": None, "place": None, "person": None, "notes": None}
    # readonly-колонка ("связь с событием") в нормализацию не попадает


def test_normalize_row_accepts_canonical_keys():
    n = seed.normalize_row("Планы", {"id": 3, "title": "  Пироги ", "deadline": "", "place": None,
                                     "person": "", "notes": "", "link": "x"})
    assert n == {"id": 3, "title": "Пироги", "deadline": None, "place": None, "person": None, "notes": None}
