"""A travel figure is meaningless without the point it was measured from.

"≈25 минут" used to be unambiguous: it was always from home. With a
dynamic origin the same number means different trips, and Hermes can now
be wrong about the premise rather than just the minutes. So the reminder
names the origin, and -- when the origin was inferred rather than stated
-- invites her to correct it with a pin.

Deliberately a piggyback on an existing reminder rather than a question
of its own: a separate message would cost one of the eight daily budget
slots and demand an answer, while this costs nothing and only gets a
reply when Hermes actually got it wrong.
"""
from fam import gate


def test_names_the_origin_when_the_rewrite_dropped_it():
    out = gate._append_piggyback_if_missing(
        "Через полчаса выезжать в театр, ≈25 минут.",
        {"origin": {"label": "от дома", "confidence": "high"}})
    assert "от дома" in out


def test_does_not_repeat_an_origin_the_rewrite_already_mentioned():
    text = "Выезжать через полчаса, ≈25 минут от дома."
    out = gate._append_piggyback_if_missing(
        text, {"origin": {"label": "от дома", "confidence": "high"}})
    assert out == text


def test_named_place_origin_survives():
    out = gate._append_piggyback_if_missing(
        "Пора выезжать, ≈15 минут.",
        {"origin": {"label": "от «Спортзала»", "confidence": "high"}})
    assert "Спортзал" in out


def test_low_confidence_adds_the_invitation():
    out = gate._append_piggyback_if_missing(
        "Пора выезжать, ≈25 минут от дома.",
        {"origin": {"label": "от дома", "confidence": "low"}})
    assert "скинь точку" in out.casefold()


def test_high_confidence_does_not_nag():
    """She just sent a pin -- asking for another one would be absurd."""
    out = gate._append_piggyback_if_missing(
        "Пора выезжать, ≈25 минут от дома.",
        {"origin": {"label": "от дома", "confidence": "high"}})
    assert "скинь точку" not in out.casefold()


def test_invitation_is_not_duplicated():
    text = "Пора выезжать, ≈25 минут от дома. Если ты не там — скинь точку."
    out = gate._append_piggyback_if_missing(
        text, {"origin": {"label": "от дома", "confidence": "medium"}})
    assert out.casefold().count("скинь точку") == 1


def test_malformed_origin_is_a_no_op():
    text = "Пора выезжать."
    for bad in ({"origin": None}, {"origin": {}}, {"origin": "дома"},
                {"origin": {"label": ""}}, {"origin": {"confidence": "low"}}):
        gate._append_piggyback_if_missing(text, bad)


def test_origin_note_coexists_with_the_enroute_piggyback():
    """Both hooks append; neither may swallow the other."""
    out = gate._append_piggyback_if_missing(
        "Пора выезжать.",
        {"origin": {"label": "от дома", "confidence": "low"},
         "enroute": "По пути: Отдать кастрюлю Аишке"})
    assert "кастрюлю" in out
    assert "от дома" in out
    assert "скинь точку" in out.casefold()
