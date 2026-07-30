"""Повторные напоминания об одной дозе не должны быть слово в слово
одинаковыми. Разнообразие обеспечивается двумя путями: инструкцией
переписывающему LLM и -- когда он недоступен -- пулом детерминированных
формулировок. Второй путь важнее: падения _call_rewrite тихие и штатные
(gate.py возвращается к human_fallback), так что без пула однообразие
наступало бы ровно в худший момент.

Fix round 1 (reviewer finding 3): everything above this line unit-tests
med_fallback/_build_prompt in isolation, and every existing tick test
replaces gate.deliver wholesale (FakeDeliver), so nothing proved the
headline promise end to end -- that attempt_no is computed correctly by
tick._meds_series and that two REAL, consecutively delivered sends of
the same dose actually read differently. The tests below patch one level
lower (gate._call_rewrite / gate._call_send, the same seam
test_meds_gate.py's *_end_to_end_through_real_deliver tests use) so the
real gate.deliver -> react.record_sent -> sent_messages/sent_message_refs
path runs for real.
"""
import json

from fam import gate, meds, tick

# 2026-07-20, Asia/Almaty = UTC+5 (no DST). 11:00 UTC = 16:00 Almaty --
# ordinary afternoon: past the med_wake_gate_until backstop (12:00) and
# not in quiet hours, so neither gate holds and the ordinary "take"
# branch runs on the very first tick.
AFTERNOON = "2026-07-20T11:00:00+00:00"

# 09:00 Almaty = 04:00 UTC -- before the wake-gate backstop, so a dose
# planned here is held ("asleep") until a wake signal arrives.
MORNING_PLAN = "2026-07-20T04:00:00+00:00"
MORNING = "2026-07-20T05:00:00+00:00"          # 10:00 Almaty

_LIVE_CFG = {
    **gate.CONFIG_DEFAULTS,
    "target": "whatsapp:+77782110625",
    "state_db_path": "/nonexistent/state.db",
    "quiet_start": "21:30",
    "quiet_end": "07:30",
    "daily_budget": 8,
    "gate_model": "gpt-5.4-mini",
    "gate_provider": "openai-codex",
    "max_len_reminder": 300,
    "max_len_digest": 900,
    "med_repeat_min": 45,
}


def _pending_intake(db, name, plan):
    med_id = meds.add(db, name, ["09:00"], remaining=10)
    cur = db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, status, "
        "series_next_utc, created_at) VALUES(?,?,'pending',?,?)",
        (med_id, plan, plan, plan))
    db.commit()
    return cur.lastrowid


def _audit_at(db, kind, ts_utc):
    db.execute(
        "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
        (ts_utc, kind, "agent", "{}"))
    db.commit()


def _take_payloads(db):
    """Every gate.sent audit row for kind='med', mode='take' (the
    ordinary escalation branch this task changes), in send order."""
    rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='gate.sent' ORDER BY id"
    ).fetchall()
    payloads = [json.loads(r["payload"]) for r in rows]
    return [p for p in payloads
            if p["kind"] == "med" and p["raw"].get("mode") == "take"]


def test_repeat_reminder_texts_differ_across_real_ticks(db, monkeypatch):
    """No FakeDeliver here: gate._call_rewrite is forced to fail (so the
    fallback pool is what's under test -- the guaranteed path per this
    task's own docstring above), gate._call_send is stubbed to succeed
    with a distinct message id each time. Two ordinary 45-minute-apart
    ticks on the same pending intake must read attempt_no 1 then 2, and
    the two delivered texts must differ."""
    intake_id = _pending_intake(db, "Эутирокс", AFTERNOON)
    monkeypatch.setattr(gate, "_call_rewrite", lambda *a, **k: None)
    sent = []

    def _send(text, _cfg):
        sent.append(text)
        return True, f"WAMID{len(sent)}"

    monkeypatch.setattr(gate, "_call_send", _send)

    tick._meds_series(db, AFTERNOON, _LIVE_CFG)
    tick._meds_series(db, "2026-07-20T11:45:00+00:00", _LIVE_CFG)

    payloads = _take_payloads(db)
    assert [p["raw"]["attempt_no"] for p in payloads] == [1, 2]
    assert len(sent) == 2
    assert sent[0] != sent[1], "повтор не должен звучать слово в слово так же"
    # First attempt's fallback is preserved by construction (med_fallback's
    # first template) and by this task's own unit test
    # (test_fallback_pool_varies_by_attempt et al.) -- NOT by any
    # pre-existing exact-string assertion elsewhere in the repo (fix
    # round 1 finding 2: no such assertion exists; a prior version of
    # this report incorrectly claimed test_tick_meds_series.py had one).
    assert sent[0] == "Пора принять Эутирокс."

    row = db.execute("SELECT * FROM med_intakes WHERE id=?",
                     (intake_id,)).fetchone()
    assert row["series_next_utc"] == "2026-07-20T12:30:00+00:00"


def test_group_release_then_repeat_reads_correct_attempt_no(db, monkeypatch):
    """Regression guard for fix round 1 finding 1: a multi-dose group
    release records ONE sent_messages row (keyed to the first dose) and
    fans every dose -- including that first one -- into
    sent_message_refs (react.record_sent). The naive
    'COUNT(sent_messages) WHERE ref_id=?' query this task originally
    shipped would therefore see 0 rows for the SECOND dose forever and
    read every one of its later repeats back as attempt_no=1. Drive a
    real group release through gate.deliver, then one ordinary repeat of
    the second dose, and assert its attempt_no is 2."""
    a = _pending_intake(db, "Эутирокс", MORNING_PLAN)
    b = _pending_intake(db, "Магний", MORNING_PLAN)
    monkeypatch.setattr(gate, "_call_rewrite", lambda *a, **k: None)
    sent = []

    def _send(text, _cfg):
        sent.append(text)
        return True, f"WAMID{len(sent)}"

    monkeypatch.setattr(gate, "_call_send", _send)

    # Sleep-gate holds both doses (no wake signal yet, plan before 12:00
    # Almaty, now also before 12:00 Almaty).
    tick._meds_series(db, MORNING, _LIVE_CFG)
    assert sent == []

    # A wake signal arrives; the next tick releases both doses as ONE
    # group message.
    _audit_at(db, "cal.add", "2026-07-20T05:05:00+00:00")
    tick._meds_series(db, "2026-07-20T05:10:00+00:00", _LIVE_CFG)
    assert len(sent) == 1, "одно сообщение на обе освобождённые дозы"

    msg = db.execute("SELECT id FROM sent_messages").fetchall()
    assert len(msg) == 1
    refs = {r["ref_id"] for r in db.execute(
        "SELECT ref_id FROM sent_message_refs WHERE sent_message_id=?",
        (msg[0]["id"],))}
    assert refs == {a, b}

    # Both doses were advanced to the same next_utc by the release path
    # (05:10 + med_repeat_min). The next tick at that time is an
    # ORDINARY repeat for each -- not another release (gate_reason was
    # cleared).
    tick._meds_series(db, "2026-07-20T05:55:00+00:00", _LIVE_CFG)

    payloads = _take_payloads(db)
    by_name = {p["raw"]["name"]: p["raw"]["attempt_no"] for p in payloads}
    assert by_name["Эутирокс"] == 2, (
        "первая доза группы: одна строка в sent_messages, одна в "
        "sent_message_refs -- должны дедуплицироваться в 1, не в 2 "
        "исходно и стать 2 после этого повтора")
    assert by_name["Магний"] == 2, (
        "вторая доза группы видна ТОЛЬКО в sent_message_refs -- без "
        "объединения таблиц это было бы 1, воспроизводя дословно первую "
        "формулировку")


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


# ---- design spec S5: raw["previous"] makes the variation instruction
# concrete instead of a blind "word it differently" the model has no
# basis for following ----

def test_prior_instruction_used_when_previous_present():
    prompt = gate._build_prompt(
        {"mode": "take", "name": "X", "attempt_no": 2,
         "previous": ["Пора принять X."]}, kind="med")
    assert gate.GATE_MED_PRIOR_VARIATION_INSTRUCTION in prompt
    assert gate.GATE_MED_VARIATION_INSTRUCTION not in prompt


def test_generic_instruction_used_when_attempt_no_repeat_and_no_previous():
    prompt = gate._build_prompt(
        {"mode": "take", "name": "X", "attempt_no": 2}, kind="med")
    assert gate.GATE_MED_VARIATION_INSTRUCTION in prompt
    assert gate.GATE_MED_PRIOR_VARIATION_INSTRUCTION not in prompt


def test_neither_instruction_on_first_attempt_without_previous():
    prompt = gate._build_prompt(
        {"mode": "take", "name": "X", "attempt_no": 1}, kind="med")
    assert gate.GATE_MED_VARIATION_INSTRUCTION not in prompt
    assert gate.GATE_MED_PRIOR_VARIATION_INSTRUCTION not in prompt


def test_prior_instruction_wins_even_when_previous_present_and_empty_list_does_not():
    # An empty list must behave exactly like an absent key -- only a
    # non-empty `previous` switches the branch.
    prompt = gate._build_prompt(
        {"mode": "take", "name": "X", "attempt_no": 2, "previous": []},
        kind="med")
    assert gate.GATE_MED_PRIOR_VARIATION_INSTRUCTION not in prompt
    assert gate.GATE_MED_VARIATION_INSTRUCTION in prompt


def test_prior_variation_instruction_absent_for_other_kinds():
    prompt = gate._build_prompt(
        {"attempt_no": 3, "previous": ["Что-то."]}, kind="reminder")
    assert gate.GATE_MED_PRIOR_VARIATION_INSTRUCTION not in prompt


# ---- late-dose wording (production incident 2026-07-29): a release-path
# raw carries late=True; the rewrite must not phrase the dose as missed
# or skipped -- see GATE_MED_LATE_INSTRUCTION's docstring in gate.py.
# This is a composing addition (separate `if`, not another elif in the
# kind=="med" chain) because a released dose can be BOTH late AND a
# repeat with previous texts, and both constraints must reach the model
# together.

def test_late_instruction_present_when_late_true():
    prompt = gate._build_prompt(
        {"mode": "take", "name": "X", "attempt_no": 1, "late": True},
        kind="med")
    assert gate.GATE_MED_LATE_INSTRUCTION in prompt


def test_late_instruction_absent_when_late_falsy_or_missing():
    prompt_missing = gate._build_prompt(
        {"mode": "take", "name": "X", "attempt_no": 1}, kind="med")
    prompt_false = gate._build_prompt(
        {"mode": "take", "name": "X", "attempt_no": 1, "late": False},
        kind="med")
    assert gate.GATE_MED_LATE_INSTRUCTION not in prompt_missing
    assert gate.GATE_MED_LATE_INSTRUCTION not in prompt_false


def test_late_instruction_composes_with_prior_variation_instruction():
    prompt = gate._build_prompt(
        {"mode": "take", "name": "X", "attempt_no": 2, "late": True,
         "previous": ["Пора принять X."]}, kind="med")
    assert gate.GATE_MED_LATE_INSTRUCTION in prompt
    assert gate.GATE_MED_PRIOR_VARIATION_INSTRUCTION in prompt
    assert gate.GATE_MED_VARIATION_INSTRUCTION not in prompt


def test_late_instruction_composes_with_generic_variation_instruction():
    prompt = gate._build_prompt(
        {"mode": "take", "name": "X", "attempt_no": 2, "late": True},
        kind="med")
    assert gate.GATE_MED_LATE_INSTRUCTION in prompt
    assert gate.GATE_MED_VARIATION_INSTRUCTION in prompt
    assert gate.GATE_MED_PRIOR_VARIATION_INSTRUCTION not in prompt


def test_late_instruction_absent_for_other_kinds():
    prompt = gate._build_prompt(
        {"attempt_no": 1, "late": True}, kind="reminder")
    assert gate.GATE_MED_LATE_INSTRUCTION not in prompt
    prompt_digest = gate._build_prompt(
        {"attempt_no": 1, "late": True}, kind="digest")
    assert gate.GATE_MED_LATE_INSTRUCTION not in prompt_digest
