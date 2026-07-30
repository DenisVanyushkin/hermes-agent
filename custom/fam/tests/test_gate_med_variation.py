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
