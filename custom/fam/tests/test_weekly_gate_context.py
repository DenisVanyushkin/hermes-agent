"""The week's context must survive the LLM rewrite.

Production incident 2026-09-20 20:00 Almaty: the ritual's first live
send arrived as "\\n\\nЧто запланируем на неделю?" -- the whole week
summary gone. gate.deliver builds the message from raw when the rewrite
succeeds and only falls back to human_fallback when it fails, so context
that exists solely in human_fallback is invisible on the happy path.

Every test here asserts against raw / the built prompt, never against a
stubbed deliver -- stubbing deliver is what hid the bug for five dry
runs.
"""
import json

import pytest

from fam import gate, plans, tick, weekly


class FakeDeliver:
    def __init__(self):
        self.calls = []
        self.responses = []

    def __call__(self, conn, kind, raw, human_fallback, cfg, force=False,
                 now_utc=None, sent_ref=None):
        self.calls.append({"kind": kind, "raw": raw,
                           "human_fallback": human_fallback})
        return self.responses.pop(0) if self.responses else "sent"


@pytest.fixture()
def fake_deliver(monkeypatch):
    fd = FakeDeliver()
    monkeypatch.setattr(gate, "deliver", fd)
    return fd


CFG = {
    "target": "whatsapp:+77782110625",
    "quiet_start": "21:30", "quiet_end": "07:30", "daily_budget": 8,
    "gate_model": "gpt-5.4-mini", "gate_provider": "openai-codex",
    "max_len_reminder": 300, "max_len_digest": 900,
    "reminder_max_age_min": 120,
}

SUNDAY_AT_FOLLOWUP = "2026-07-19T15:00:00+00:00"


def _followups(fd):
    return [c for c in fd.calls if c["kind"] == "followup"]


def _seed(db):
    from fam import cal
    cal.add(db, "Тренировка", "2026-07-22T05:00:00+00:00")   # Wed of W30
    plans.add(db, "Забрать куртку", deadline="2026-07-17")
    db.commit()


# --- the context reaches raw, not just the fallback -------------------

def test_raw_carries_the_week_context(db, fake_deliver):
    _seed(db)

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    payload = _followups(fake_deliver)[0]["raw"]["weekly_plan"]
    assert payload["label"]
    assert payload["days"] == [{"day": "ср", "titles": ["Тренировка"]}]
    assert "Забрать куртку" in [t["title"] for t in payload["tails"]]


def test_raw_names_the_free_days(db, fake_deliver):
    _seed(db)

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    payload = _followups(fake_deliver)[0]["raw"]["weekly_plan"]
    assert "сб" in payload["free_days"] and "вс" in payload["free_days"]


def test_an_empty_week_still_carries_the_label(db, fake_deliver):
    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    payload = _followups(fake_deliver)[0]["raw"]["weekly_plan"]
    assert payload["days"] == []
    assert payload["label"]


# --- the prompt the rewrite actually receives -------------------------

def test_prompt_tells_the_rewrite_to_keep_the_week_listed(db):
    raw = {"kind": "followup", "weekly_plan": {"label": "x", "days": [],
                                               "free_days": [], "tails": []},
           "question": "Что запланируем на неделю?"}

    prompt = gate._build_prompt(raw, kind="followup")

    assert "weekly_plan" in prompt
    assert gate.GATE_WEEKLY_PLAN_INSTRUCTION in prompt


def test_the_question_is_kept_out_of_the_weekly_prompt(db):
    """deliver() appends raw["question"] itself; leaving it in <data>
    invites the rewrite to paraphrase it mid-text, producing a duplicate
    the verbatim-only stripper cannot catch (the digest path already
    learned this)."""
    raw = {"kind": "followup", "weekly_plan": {"label": "x", "days": [],
                                               "free_days": [], "tails": []},
           "question": "Что запланируем на неделю?"}

    prompt = gate._build_prompt(raw, kind="followup")

    assert "Что запланируем на неделю?" not in prompt


def test_an_ordinary_followup_prompt_is_untouched(db):
    """No weekly_plan -> the plain evening recap behaves exactly as
    before, including keeping its own question in the payload."""
    raw = {"kind": "followup", "events": [], "plans": [],
           "question": tick.FOLLOWUP_QUESTION}

    prompt = gate._build_prompt(raw, kind="followup")

    assert gate.GATE_WEEKLY_PLAN_INSTRUCTION not in prompt


# --- the regression test the production bug needed --------------------

def test_the_week_survives_a_successful_rewrite(db, monkeypatch):
    """Vertical: real gate.deliver, real prompt building, real question
    appending. Only the two external edges are stubbed -- the model call
    and the transport.

    The stub rewrite does what a cooperative model does: it reflects the
    data it was given. So if the week never reaches raw, the stub has
    nothing to reflect and the message comes back as the bare question --
    which is precisely how this failed in production on 2026-09-20. No
    amount of stubbing gate.deliver itself would have caught that.
    """
    import json as _json
    from fam import cal, gate as gate_mod

    cal.add(db, "Тренировка", "2026-07-22T05:00:00+00:00")   # Wed of W30
    db.commit()

    def reflecting_rewrite(prompt, cfg):
        # The instruction text mentions the tag before the block itself,
        # so take the LAST opener.
        block = prompt.rsplit("<data>", 1)[1].split("</data>")[0]
        payload = _json.loads(block)
        week = payload.get("weekly_plan")
        if week is None:
            return "Как прошёл день?"
        lines = [week["label"]]
        lines += [f"{d['day']}: " + ", ".join(d["titles"]) for d in week["days"]]
        return "\n".join(lines)

    sent = {}

    def capture_send(text, cfg):
        sent["text"] = text
        return True, "stub-id"

    monkeypatch.setattr(gate_mod, "_call_rewrite", reflecting_rewrite)
    monkeypatch.setattr(gate_mod, "_call_send", capture_send)

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    text = sent.get("text") or ""
    assert "Тренировка" in text, "the week never reached the rewrite"
    assert text.rstrip().endswith("Что запланируем на неделю?")
    assert text.count("Что запланируем на неделю?") == 1


def test_an_ordinary_followup_still_reaches_the_user(db, monkeypatch):
    """The same vertical path, without a weekly plan: the plain evening
    recap must keep working exactly as before."""
    from fam import cal, gate as gate_mod, places, plans as plans_mod

    places.add(db, "Клиника", lat=43.2260, lon=76.8670)
    db.commit()
    ev = cal.add(db, "Врач", "2026-07-20T09:00:00+00:00", place="Клиника")
    db.commit()
    pid = plans_mod.add(db, "Забрать справку")
    db.commit()
    plans_mod.attach(db, pid, ev["id"] if isinstance(ev, dict) else ev)
    db.commit()

    sent = {}
    monkeypatch.setattr(gate_mod, "_call_rewrite", lambda p, c: "Вечерняя сводка.")
    monkeypatch.setattr(gate_mod, "_call_send",
                        lambda text, cfg: (sent.update(text=text), (True, "id"))[1])

    tick.reminders(db, now_utc="2026-07-20T15:00:00+00:00", cfg=CFG)

    text = sent.get("text") or ""
    assert "Вечерняя сводка." in text
    assert text.rstrip().endswith(tick.FOLLOWUP_QUESTION)
