"""Phase 5 Task 7: today's planned med intakes + yesterday's missed +
low-stock in the morning digest. Mirrors test_digest_plans.py's fixtures
(CFG/NOW/_fetch_wx/fake_deliver/db) -- kept in its own file per the task
brief rather than appended to the already-large test_tick.py/
test_digest_plans.py.
"""
import pytest

from fam import gate, meds, tick

CFG = {
    "target": "whatsapp:+77782110625",
    "quiet_start": "21:30",
    "quiet_end": "07:30",
    "daily_budget": 8,
    "gate_model": "gpt-5.4-mini",
    "gate_provider": "openai-codex",
    "max_len_reminder": 300,
    "max_len_digest": 900,
    "reminder_max_age_min": 120,
    "plan_deadline_horizon_days": 3,
}

# 2026-07-20T09:30 Almaty -- same instant test_digest_plans.py uses, so
# "today" is 2026-07-20 and "yesterday" is 2026-07-19.
NOW = "2026-07-20T04:30:00+00:00"


def _fetch_wx(wx=None):
    return lambda: wx


class FakeDeliver:
    def __init__(self):
        self.calls = []
        self.responses = []

    def __call__(self, conn, kind, raw, human_fallback, cfg, force=False,
                 now_utc=None):
        self.calls.append({
            "kind": kind, "raw": raw, "human_fallback": human_fallback,
            "force": force, "now_utc": now_utc,
        })
        return self.responses.pop(0)


@pytest.fixture()
def fake_deliver(monkeypatch):
    fd = FakeDeliver()
    monkeypatch.setattr(gate, "deliver", fd)
    return fd


def _insert_intake(db, med_id, plan_ts_utc="2026-07-20T03:00:00+00:00",
                    series_next_utc=None, status="pending"):
    cur = db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, taken_ts_utc, status, "
        "series_next_utc, created_at) VALUES (?,?,?,?,?,?)",
        (med_id, plan_ts_utc, None, status, series_next_utc, plan_ts_utc),
    )
    return cur.lastrowid


# ---- today's planned intakes ----

def test_today_intake_in_raw(db, fake_deliver):
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    _insert_intake(db, med_id, plan_ts_utc="2026-07-20T03:00:00+00:00")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    today = fake_deliver.calls[0]["raw"]["meds"]["today"]
    assert today == [{"name": "Магний", "time_local": "08:00"}]


def test_today_intake_in_fallback(db, fake_deliver):
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    _insert_intake(db, med_id, plan_ts_utc="2026-07-20T03:00:00+00:00")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "Лекарства:" in fallback
    assert "08:00 Магний" in fallback


def test_intake_outside_today_bounds_excluded(db, fake_deliver):
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    # Just before today's Almaty-day lower bound (2026-07-19T19:00:00+00:00).
    _insert_intake(db, med_id, plan_ts_utc="2026-07-19T18:59:00+00:00")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["meds"]["today"] == []


# ---- yesterday's missed ----

def test_yesterday_missed_in_raw(db, fake_deliver):
    med_id = meds.add(db, "Аспирин", ["08:00"])
    db.commit()
    _insert_intake(db, med_id, plan_ts_utc="2026-07-19T03:00:00+00:00",
                    status="missed")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    missed = fake_deliver.calls[0]["raw"]["meds"]["missed_yesterday"]
    assert missed == [{"name": "Аспирин"}]


def test_yesterday_missed_in_fallback(db, fake_deliver):
    med_id = meds.add(db, "Аспирин", ["08:00"])
    db.commit()
    _insert_intake(db, med_id, plan_ts_utc="2026-07-19T03:00:00+00:00",
                    status="missed")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "Аспирин" in fallback
    assert "пропущено" in fallback.lower()


def test_pending_yesterday_intake_is_not_missed(db, fake_deliver):
    # Only status='missed' rows count -- a still-pending row from
    # yesterday (shouldn't normally exist once meds_gen's midnight
    # closeout has run, but this helper bypasses that) must not be
    # reported as missed.
    med_id = meds.add(db, "Аспирин", ["08:00"])
    db.commit()
    _insert_intake(db, med_id, plan_ts_utc="2026-07-19T03:00:00+00:00",
                    status="pending")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["meds"]["missed_yesterday"] == []


def test_missed_two_days_ago_excluded(db, fake_deliver):
    med_id = meds.add(db, "Аспирин", ["08:00"])
    db.commit()
    _insert_intake(db, med_id, plan_ts_utc="2026-07-18T03:00:00+00:00",
                    status="missed")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["meds"]["missed_yesterday"] == []


# ---- low stock ----

def test_low_stock_at_threshold_in_raw_and_pora_kupit_in_fallback(db, fake_deliver):
    meds.add(db, "Витамин D", ["08:00"], remaining=2, threshold=2)
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    low_stock = fake_deliver.calls[0]["raw"]["meds"]["low_stock"]
    assert low_stock == [{"name": "Витамин D", "remaining": 2}]
    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "пора купить" in fallback
    assert "Витамин D" in fallback


def test_low_stock_zero_threshold_only_fires_at_zero(db, fake_deliver):
    # Same formula as meds.take's restock trigger (T5): threshold=0 only
    # counts as low stock once remaining actually hits 0, not merely
    # remaining<=threshold for some nonzero remaining above the (default)
    # threshold.
    meds.add(db, "Ибупрофен", ["08:00"], remaining=1, threshold=0)
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["meds"]["low_stock"] == []


def test_low_stock_zero_threshold_zero_remaining_fires(db, fake_deliver):
    meds.add(db, "Ибупрофен", ["08:00"], remaining=0, threshold=0)
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["meds"]["low_stock"] == [
        {"name": "Ибупрофен", "remaining": 0}
    ]


def test_untracked_remaining_never_low_stock(db, fake_deliver):
    meds.add(db, "Без учёта", ["08:00"], remaining=None, threshold=0)
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["meds"]["low_stock"] == []


# ---- fully empty: section omitted ----

def test_meds_section_omitted_when_all_empty(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    raw_meds = fake_deliver.calls[0]["raw"]["meds"]
    assert raw_meds == {"today": [], "missed_yesterday": [], "low_stock": []}
    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "Лекарства" not in fallback
