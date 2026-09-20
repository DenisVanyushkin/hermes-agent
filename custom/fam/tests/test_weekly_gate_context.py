"""The week's context must survive the LLM rewrite.

Production incident, the ritual's first live send (2026-09-20): the
message arrived as "\\n\\nЧто запланируем на неделю?" with the whole week
missing. gate.deliver builds the text from `raw` whenever the rewrite
succeeds and only reads human_fallback when it fails, so context that
lives solely in the fallback is invisible on the happy path -- and five
dry runs that stubbed gate.deliver all showed a perfect message.

Nothing here stubs gate.deliver. The vertical cases stub only the two
external edges: the model call and the transport.
"""
import json as _json

import pytest

from fam import cal, gate, places, plans, tick, weekly


class FakeDeliver:
    def __init__(self):
        self.calls = []

    def __call__(self, conn, kind, raw, human_fallback, cfg, force=False,
                 now_utc=None, sent_ref=None):
        self.calls.append({"kind": kind, "raw": raw})
        return "sent"


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


# --- the payload reaches raw, not just the fallback -------------------

def test_raw_carries_the_week_context(db, fake_deliver):
    cal.add(db, "Тренировка", "2026-07-22T05:00:00+00:00")   # Wed of W30
    plans.add(db, "Забрать куртку", deadline="2026-07-17")
    db.commit()

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    payload = _followups(fake_deliver)[0]["raw"]["weekly_plan"]
    assert payload["label"]
    assert payload["days"] == [{"day": "ср", "titles": ["Тренировка"]}]
    assert [t["title"] for t in payload["tails"]] == ["Забрать куртку"]
    assert "сб" in payload["free_days"] and "вс" in payload["free_days"]


def test_an_empty_week_still_carries_the_label(db, fake_deliver):
    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    payload = _followups(fake_deliver)[0]["raw"]["weekly_plan"]
    assert payload["days"] == []
    assert payload["label"]


# --- the prompt the rewrite actually receives -------------------------

def test_the_weekly_prompt_adds_the_carve_out_and_hides_the_question(db):
    """The instruction exempts the plan from the generic 1-3 sentence
    rule; the question is withheld because deliver() appends the real
    one itself, and leaving it in <data> invites a paraphrased duplicate
    the verbatim-only stripper cannot catch."""
    raw = {"kind": "followup",
           "weekly_plan": {"label": "x", "days": [], "free_days": [],
                           "tails": []},
           "question": "Что запланируем на неделю?"}

    prompt = gate._build_prompt(raw, kind="followup")

    assert gate.GATE_WEEKLY_PLAN_INSTRUCTION in prompt
    assert "weekly_plan" in prompt
    assert "Что запланируем на неделю?" not in prompt


def test_an_ordinary_followup_prompt_is_untouched(db):
    """No weekly_plan -> the plain evening recap behaves exactly as
    before, carve-out absent and its own question still in the payload."""
    raw = {"kind": "followup", "events": [], "plans": [],
           "question": tick.FOLLOWUP_QUESTION}

    prompt = gate._build_prompt(raw, kind="followup")

    assert gate.GATE_WEEKLY_PLAN_INSTRUCTION not in prompt
    assert tick.FOLLOWUP_QUESTION in prompt


# --- vertical: the real gate, only the external edges stubbed ---------

def test_the_week_survives_a_successful_rewrite(db, monkeypatch):
    """The stub rewrite does what a cooperative model does: it reflects
    the data it was handed. So a week that never reaches raw cannot
    appear in the message -- precisely how this failed in production.
    """
    cal.add(db, "Тренировка", "2026-07-22T05:00:00+00:00")
    db.commit()

    def reflecting_rewrite(prompt, cfg):
        # The instruction text mentions the tag before the block itself,
        # so take the LAST opener.
        block = prompt.rsplit("<data>", 1)[1].split("</data>")[0]
        week = _json.loads(block).get("weekly_plan")
        if week is None:
            return "Как прошёл день?"
        return "\n".join(
            [week["label"]]
            + [f"{d['day']}: " + ", ".join(d["titles"]) for d in week["days"]])

    sent = {}
    monkeypatch.setattr(gate, "_call_rewrite", reflecting_rewrite)
    monkeypatch.setattr(gate, "_call_send",
                        lambda text, cfg: (sent.update(text=text),
                                           (True, "stub-id"))[1])

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    text = sent.get("text") or ""
    assert "Тренировка" in text, "the week never reached the rewrite"
    assert text.rstrip().endswith("Что запланируем на неделю?")
    assert text.count("Что запланируем на неделю?") == 1


def test_an_ordinary_followup_still_reaches_the_user(db, monkeypatch):
    """The same vertical path without a weekly plan: the plain evening
    recap must keep working exactly as before."""
    places.add(db, "Клиника", lat=43.2260, lon=76.8670)
    db.commit()
    ev = cal.add(db, "Врач", "2026-07-20T09:00:00+00:00", place="Клиника")
    db.commit()
    pid = plans.add(db, "Забрать справку")
    db.commit()
    plans.attach(db, pid, ev["id"] if isinstance(ev, dict) else ev)
    db.commit()

    sent = {}
    monkeypatch.setattr(gate, "_call_rewrite", lambda p, c: "Вечерняя сводка.")
    monkeypatch.setattr(gate, "_call_send",
                        lambda text, cfg: (sent.update(text=text),
                                           (True, "id"))[1])

    tick.reminders(db, now_utc="2026-07-20T15:00:00+00:00", cfg=CFG)

    text = sent.get("text") or ""
    assert "Вечерняя сводка." in text
    assert text.rstrip().endswith(tick.FOLLOWUP_QUESTION)
