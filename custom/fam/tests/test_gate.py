import json
import subprocess

import pytest

from fam import audit, gate, tick

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
    "email_enabled": True,
    "email_from": "germes@vanyushk.in",
    "email_to": "hermes@vanyushk.in",
    "enroute_max_items": 2,
    "plan_deadline_horizon_days": 3,
    "followup_local_time": "20:00",
}


def _insert_audit(db, kind, ts_utc, payload=None):
    db.execute(
        "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
        (ts_utc, kind, "test", json.dumps(payload or {}, ensure_ascii=False)),
    )


# Frozen "now" reused by the phase-2c chain-budget tests below --
# 15:00 Almaty, 11 Jul 2026, same day used by the pre-existing
# budget_spent_today tests above.
NOW = "2026-07-11T10:00:00+00:00"

# Frozen "now" inside the quiet window (21:30-07:30 Almaty) -- 22:00
# Almaty, 11 Jul 2026, same instant the pre-existing quiet-hours tests
# already used as a literal. Reused by the phase-2c night-fire tests
# below (task 2c-6: reminders ignore quiet hours).
QUIET_NOW = "2026-07-11T22:00:00+05:00"


def _seed_gate_sent(db, kind, event_id=None, n=1, now_utc=None, final=None):
    """Insert n gate.sent audit rows with payload {"kind": kind, "raw":
    {"event_id": event_id}[, "final": final]}, timestamped inside today's
    Almaty day relative to now_utc (defaults to the module's frozen NOW)
    -- same window convention as _insert_audit's callers above (see
    test_budget_spent_today_counts_only_todays_gate_sent for the
    Almaty-day boundary math this relies on).

    final (phase 2c, task 7): the delivered text, for
    prior_texts_today's payload["final"] lookup -- omitted from the
    payload entirely when None, matching how the pre-existing budget/
    chain tests (which don't care about "final") stay unchanged.
    """
    ts_utc = now_utc or NOW
    for _ in range(n):
        payload = {"kind": kind, "raw": {"event_id": event_id}}
        if final is not None:
            payload["final"] = final
        _insert_audit(db, "gate.sent", ts_utc, payload)


class FakeRun:
    """Records every subprocess.run() call and dispatches a canned
    CompletedProcess-like response based on whether the call is the
    rewrite ("-z") or the send ("send") invocation. Tests configure
    .rewrite_responses (a list consumed in order, one per "-z" call) and
    .send_response.
    """

    def __init__(self):
        self.calls = []
        self.rewrite_responses = []
        self.send_response = _completed(0, "")

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if "-z" in args:
            resp = self.rewrite_responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp
        if "send" in args:
            resp = self.send_response
            if isinstance(resp, Exception):
                raise resp
            return resp
        raise AssertionError(f"unexpected hermes invocation: {args}")


def _completed(returncode, stdout, stderr=""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


@pytest.fixture()
def fake_run(monkeypatch):
    fr = FakeRun()
    monkeypatch.setattr(gate.subprocess, "run", fr)
    return fr


# ---- load_config ----

def test_load_config_creates_from_example_when_absent(tmp_path):
    example = tmp_path / "fam-config.example.json"
    example.write_text(json.dumps(CFG, ensure_ascii=False), encoding="utf-8")
    target = tmp_path / "private" / "amina" / "fam-config.json"

    cfg = gate.load_config(config_path=target, example_path=example)

    assert target.exists()
    assert cfg == CFG


def test_load_config_reads_existing_without_overwriting(tmp_path):
    example = tmp_path / "fam-config.example.json"
    example.write_text(json.dumps(CFG, ensure_ascii=False), encoding="utf-8")
    target = tmp_path / "private" / "amina" / "fam-config.json"
    target.parent.mkdir(parents=True)
    custom = dict(CFG, target="whatsapp:+70000000000")
    target.write_text(json.dumps(custom, ensure_ascii=False), encoding="utf-8")

    cfg = gate.load_config(config_path=target, example_path=example)

    assert cfg["target"] == "whatsapp:+70000000000"


def test_load_config_default_merges_missing_reminder_max_age_min(tmp_path):
    # A live config predating the reminder_max_age_min key (Fix 1,
    # pre-live guards) must still load -- the key is merged in at its
    # default (120) in memory, without rewriting the admin's file on disk.
    example = tmp_path / "fam-config.example.json"
    example.write_text(json.dumps(CFG, ensure_ascii=False), encoding="utf-8")
    target = tmp_path / "private" / "amina" / "fam-config.json"
    target.parent.mkdir(parents=True)
    legacy = {k: v for k, v in CFG.items() if k != "reminder_max_age_min"}
    target.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    cfg = gate.load_config(config_path=target, example_path=example)

    assert cfg["reminder_max_age_min"] == 120
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert "reminder_max_age_min" not in on_disk


def test_load_config_default_merges_missing_email_keys(tmp_path):
    # Task 10: the live config on hermes-home predates email_enabled/
    # email_from/email_to -- same default-merge path as
    # reminder_max_age_min above, so `fam cal add`'s mail hook and
    # `fam mail test` work against it without a manual live-file edit.
    example = tmp_path / "fam-config.example.json"
    example.write_text(json.dumps(CFG, ensure_ascii=False), encoding="utf-8")
    target = tmp_path / "private" / "amina" / "fam-config.json"
    target.parent.mkdir(parents=True)
    legacy = {
        k: v for k, v in CFG.items()
        if k not in ("email_enabled", "email_from", "email_to")
    }
    target.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    cfg = gate.load_config(config_path=target, example_path=example)

    assert cfg["email_enabled"] is True
    assert cfg["email_from"] == "germes@vanyushk.in"
    assert cfg["email_to"] == "hermes@vanyushk.in"
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert "email_enabled" not in on_disk


# ---- in_quiet_hours: cross-midnight window (21:30-07:30 Almaty) ----

@pytest.mark.parametrize("local_time,expected", [
    ("21:29:00", False),
    ("21:30:00", True),   # carry-over T5->T6: exact start, inclusive
    ("21:31:00", True),
    ("07:29:00", True),
    ("07:30:00", False),  # carry-over T5->T6: exact end, exclusive
    ("07:31:00", False),
])
def test_in_quiet_hours_cross_midnight_edges(local_time, expected):
    now_utc = f"2026-07-11T{local_time}+05:00"  # Almaty is UTC+5, no DST
    assert gate.in_quiet_hours(now_utc, CFG) is expected


def test_in_quiet_hours_daytime_is_never_quiet():
    assert gate.in_quiet_hours("2026-07-11T12:00:00+05:00", CFG) is False


# ---- budget_spent_today ----

def test_budget_spent_today_counts_only_todays_gate_sent(db):
    now_utc = "2026-07-11T10:00:00+00:00"  # 15:00 Almaty, 11 Jul
    # inside today's Almaty window [2026-07-10T19:00Z, 2026-07-11T19:00Z)
    _insert_audit(db, "gate.sent", "2026-07-10T19:00:00+00:00")
    _insert_audit(db, "gate.sent", "2026-07-11T12:00:00+00:00")
    _insert_audit(db, "gate.sent", "2026-07-11T18:59:59+00:00")
    # just outside (previous Almaty day)
    _insert_audit(db, "gate.sent", "2026-07-10T18:59:59+00:00")
    # just outside (next Almaty day)
    _insert_audit(db, "gate.sent", "2026-07-11T19:00:00+00:00")
    # right kind window, wrong kind
    _insert_audit(db, "gate.skip", "2026-07-11T12:00:00+00:00")
    db.commit()

    assert gate.budget_spent_today(db, now_utc=now_utc) == 3


def test_budget_spent_today_zero_when_no_rows(db):
    db.commit()
    assert gate.budget_spent_today(db, now_utc="2026-07-11T10:00:00+00:00") == 0


# Carry-over T5->T6: the digest is delivered with force=True outside the
# daily budget -- its gate.sent row must not shrink the count reminders
# see. Only the payload's inner "kind" (not audit_log.kind, which is
# always "gate.sent") distinguishes a digest send from a reminder send.
def test_budget_spent_today_excludes_digest_kind(db):
    now_utc = "2026-07-11T10:00:00+00:00"
    _insert_audit(db, "gate.sent", "2026-07-11T12:00:00+00:00", {"kind": "digest"})
    for _ in range(3):
        _insert_audit(db, "gate.sent", "2026-07-11T12:00:00+00:00", {"kind": "reminder"})
    db.commit()

    assert gate.budget_spent_today(db, now_utc=now_utc) == 3


# Phase 2c: a reminder chain (all sends for the same event_id, same day)
# costs one budget unit, not one per send -- see gate.py's
# budget_spent_today docstring update and its "Цепочка = 1 единица
# бюджета" comment (decision: Денис, task 2c-5).
def test_budget_counts_chain_as_one(db):
    _seed_gate_sent(db, kind="reminder", event_id=7, n=3)
    _seed_gate_sent(db, kind="reminder", event_id=8, n=1)
    db.commit()

    assert gate.budget_spent_today(db, now_utc=NOW) == 2


def test_budget_digest_still_excluded(db):
    _seed_gate_sent(db, kind="digest", n=1)
    _seed_gate_sent(db, kind="reminder", event_id=7, n=2)
    db.commit()

    assert gate.budget_spent_today(db, now_utc=NOW) == 1


# ---- deliver: quiet hours ----

def test_deliver_quiet_hours_skips_and_audits(db, fake_run):
    # 2c night-fire: quiet hours no longer gate kind="reminder" (Denis
    # decision, 2026-07-12 -- "планы бывают и ночью, их не нужно
    # замалчивать"). This test now covers a non-reminder kind, which
    # keeps the pre-existing quiet-gate semantics; the reminder-specific
    # night-fire behavior is covered by
    # test_deliver_reminder_ignores_quiet_hours below.
    status = gate.deliver(
        db, "note", {"label": "тест"}, "fallback text", CFG,
        now_utc="2026-07-11T22:00:00+05:00",
    )
    db.commit()

    assert status == "quiet"
    assert fake_run.calls == []
    rows = audit.query(db, None, "gate.", None)
    assert rows[0]["kind"] == "gate.skip"
    assert rows[0]["payload"]["reason"] == "quiet"


def test_deliver_reminder_ignores_quiet_hours(db, fake_run):
    # 2c night-fire (Denis decision, 2026-07-12): a reminder chain fires
    # on schedule regardless of the quiet window -- "планы бывают и
    # ночью, их не нужно замалчивать". QUIET_NOW is inside 21:30-07:30
    # Almaty, same instant the pre-existing quiet-hours tests use.
    fake_run.rewrite_responses = [_completed(0, "Скоро событие.")]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(
        db, "reminder", {"event_id": 7, "label": "x"}, "fallback text", CFG,
        now_utc=QUIET_NOW,
    )

    assert status == "sent"


def test_deliver_non_reminder_still_quiet_gated(db, fake_run):
    # 2c night-fire (Denis decision, 2026-07-12): the quiet window still
    # applies to non-reminder kinds -- only reminders were carved out.
    status = gate.deliver(
        db, "note", {"x": 1}, "fallback text", CFG, now_utc=QUIET_NOW,
    )

    assert status == "quiet"
    assert fake_run.calls == []


def test_deliver_force_bypasses_quiet_hours(db, fake_run):
    fake_run.rewrite_responses = [_completed(0, "Скоро событие.")]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(
        db, "reminder", {"label": "тест"}, "fallback text", CFG,
        now_utc="2026-07-11T22:00:00+05:00", force=True,
    )

    assert status == "sent"
    assert len(fake_run.calls) == 2


# ---- deliver: budget ----

def test_deliver_budget_skip_after_daily_budget_reached(db, fake_run):
    now_utc = "2026-07-11T10:00:00+00:00"
    for _ in range(CFG["daily_budget"]):
        _insert_audit(db, "gate.sent", now_utc)
    db.commit()

    status = gate.deliver(
        db, "reminder", {"label": "тест"}, "fallback text", CFG,
        now_utc=now_utc,
    )
    db.commit()

    assert status == "budget"
    assert fake_run.calls == []
    rows = audit.query(db, None, "gate.skip", None)
    assert rows[0]["payload"]["reason"] == "budget"


def test_deliver_force_bypasses_budget(db, fake_run):
    now_utc = "2026-07-11T10:00:00+00:00"
    for _ in range(CFG["daily_budget"]):
        _insert_audit(db, "gate.sent", now_utc)
    db.commit()
    fake_run.rewrite_responses = [_completed(0, "Скоро событие.")]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(
        db, "reminder", {"label": "тест"}, "fallback text", CFG,
        now_utc=now_utc, force=True,
    )

    assert status == "sent"


# Phase 2c: chain continuation is free -- if event_id=7 already sent a
# reminder today, a later reminder for the same event passes even with
# the budget otherwise exhausted by other events (_reminder_sent_today).
def test_deliver_chain_continuation_free_at_budget_limit(db, fake_run):
    for eid in range(100, 100 + CFG["daily_budget"]):
        _seed_gate_sent(db, kind="reminder", event_id=eid, n=1)
    _seed_gate_sent(db, kind="reminder", event_id=7, n=1)
    db.commit()
    fake_run.rewrite_responses = [_completed(0, "Скоро событие.")]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(
        db, "reminder", {"event_id": 7, "label": "x"}, "fallback text", CFG,
        now_utc=NOW,
    )

    assert status == "sent"


# Phase 2c: a brand-new chain (no prior gate.sent today for its
# event_id) still gets budget-gated normally once the daily_budget of
# distinct chains/sends is reached.
def test_deliver_new_chain_blocked_at_budget_limit(db, fake_run):
    for eid in range(100, 100 + CFG["daily_budget"]):
        _seed_gate_sent(db, kind="reminder", event_id=eid, n=1)
    db.commit()

    status = gate.deliver(
        db, "reminder", {"event_id": 9, "label": "x"}, "fallback text", CFG,
        now_utc=NOW,
    )
    db.commit()

    assert status == "budget"
    assert fake_run.calls == []


# Phase 2c, task 7: the gate.skip audit row carries event_id when raw
# has one, so a skip caused by quiet hours or budget is traceable back
# to the reminder chain it belongs to, not just a bare kind+reason. Uses
# kind="note" + QUIET_NOW (quiet-hours skip path) as the brief specifies
# -- the budget-skip site gets the same skip_payload construction, not a
# second test, since both sites share one code path in the implementation.
def test_gate_skip_payload_carries_event_id(db):
    gate.deliver(db, "note", {"event_id": 7}, "fb", CFG, now_utc=QUIET_NOW)
    db.commit()
    row = db.execute(
        "SELECT payload FROM audit_log WHERE kind='gate.skip' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert json.loads(row["payload"])["event_id"] == 7


def test_gate_skip_payload_omits_event_id_when_raw_has_none(db):
    gate.deliver(db, "note", {"label": "x"}, "fb", CFG, now_utc=QUIET_NOW)
    db.commit()
    row = db.execute(
        "SELECT payload FROM audit_log WHERE kind='gate.skip' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert "event_id" not in json.loads(row["payload"])


# ---- prior_texts_today ----

# Phase 2c, task 7: prior_texts_today feeds the variation-rule instruction
# (GATE_REMINDER_TIME_SEMANTICS_INSTRUCTION) -- it must return only THIS
# event's already-sent final texts, today, in send order, so the rewrite
# knows what not to repeat.
def test_prior_texts_today_returns_this_events_finals(db):
    _seed_gate_sent(db, kind="reminder", event_id=7, final="Пора собираться.")
    _seed_gate_sent(db, kind="reminder", event_id=8, final="Другое событие.")
    db.commit()
    assert gate.prior_texts_today(db, 7, NOW) == ["Пора собираться."]


def test_prior_texts_today_empty_when_no_prior_sends(db):
    db.commit()
    assert gate.prior_texts_today(db, 7, NOW) == []


def test_prior_texts_today_ordered_by_send_order(db):
    _seed_gate_sent(db, kind="reminder", event_id=7, final="Первое.")
    _seed_gate_sent(db, kind="reminder", event_id=7, final="Второе.")
    db.commit()
    assert gate.prior_texts_today(db, 7, NOW) == ["Первое.", "Второе."]


# ---- deliver: rewrite success ----

def test_deliver_rewrite_success_sends_rewritten_text(db, fake_run):
    raw = {"label": "пора выходить", "event": {"title": "Врач"}}
    fake_run.rewrite_responses = [_completed(0, "  Пора выходить к врачу.  ")]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(
        db, "reminder", raw, "fallback text", CFG,
        now_utc="2026-07-11T12:00:00+05:00",
    )
    db.commit()

    assert status == "sent"
    rows = audit.query(db, None, "gate.sent", None)
    payload = rows[0]["payload"]
    assert payload["raw"] == raw
    assert payload["final"] == "Пора выходить к врачу."
    assert payload["attempt"] == "rewrite"
    assert payload["kind"] == "reminder"

    # rewrite call carries model/provider/prompt; send call carries target+stdin
    rewrite_args, rewrite_kwargs = fake_run.calls[0]
    assert "-z" in rewrite_args
    z_idx = rewrite_args.index("-z")
    prompt = rewrite_args[z_idx + 1]
    assert json.dumps(raw, ensure_ascii=False) in prompt
    # NB: "-m" also appears earlier in HERMES as `python -m hermes_cli.main`
    # -- search past the "-z PROMPT" pair for the gate's own -m flag.
    assert rewrite_args[rewrite_args.index("-m", z_idx) + 1] == CFG["gate_model"]
    assert rewrite_args[rewrite_args.index("--provider") + 1] == CFG["gate_provider"]
    # security pin (phase-2b final review): the rewrite invocation must
    # always carry "-t clarify" -- oneshot mode bypasses approvals
    # (HERMES_YOLO_MODE=1) and would otherwise load the default cli
    # toolsets, terminal included, while the prompt embeds user-authored
    # strings (event titles, place names). An explicit -t REPLACES the
    # configured toolsets; clarify is the minimal benign one.
    assert rewrite_args[rewrite_args.index("-t", z_idx) + 1] == "clarify"
    assert rewrite_kwargs["timeout"] == 90

    send_args, send_kwargs = fake_run.calls[1]
    assert "send" in send_args
    assert send_args[send_args.index("-t") + 1] == CFG["target"]
    assert send_kwargs["input"] == "Пора выходить к врачу."
    assert send_kwargs["timeout"] == 60


# ---- deliver: LLM failure -> human fallback ----

def test_deliver_llm_nonzero_exit_falls_back(db, fake_run):
    fake_run.rewrite_responses = [_completed(1, "", "boom")]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(
        db, "reminder", {"label": "тест"}, "человеческий фолбэк", CFG,
        now_utc="2026-07-11T12:00:00+05:00",
    )
    db.commit()

    assert status == "sent"
    rows = audit.query(db, None, "gate.sent", None)
    assert rows[0]["payload"]["final"] == "человеческий фолбэк"
    assert rows[0]["payload"]["attempt"] == "fallback"
    send_args, send_kwargs = fake_run.calls[-1]
    assert send_kwargs["input"] == "человеческий фолбэк"


def test_deliver_llm_timeout_falls_back(db, fake_run):
    fake_run.rewrite_responses = [subprocess.TimeoutExpired(cmd="hermes", timeout=90)]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(
        db, "reminder", {"label": "тест"}, "человеческий фолбэк", CFG,
        now_utc="2026-07-11T12:00:00+05:00",
    )

    assert status == "sent"
    send_args, send_kwargs = fake_run.calls[-1]
    assert send_kwargs["input"] == "человеческий фолбэк"


def test_deliver_llm_empty_stdout_falls_back(db, fake_run):
    fake_run.rewrite_responses = [_completed(0, "   ")]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(
        db, "reminder", {"label": "тест"}, "человеческий фолбэк", CFG,
        now_utc="2026-07-11T12:00:00+05:00",
    )
    db.commit()

    assert status == "sent"
    rows = audit.query(db, None, "gate.sent", None)
    assert rows[0]["payload"]["attempt"] == "fallback"


# ---- deliver: length ceiling ----

TIGHT_CFG = dict(CFG, max_len_reminder=10)


def test_deliver_over_ceiling_retries_and_shortens(db, fake_run):
    fake_run.rewrite_responses = [
        _completed(0, "это очень длинный текст сверх лимита"),
        _completed(0, "короче"),
    ]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(
        db, "reminder", {"label": "тест"}, "fallback", TIGHT_CFG,
        now_utc="2026-07-11T12:00:00+05:00",
    )
    db.commit()

    assert status == "sent"
    assert len(fake_run.calls) == 3  # rewrite, shorten retry, send
    shorten_args, _ = fake_run.calls[1]
    prompt = shorten_args[shorten_args.index("-z") + 1]
    assert "Сократи до 10 знаков" in prompt
    # the shorten retry goes through the same _call_rewrite, so it must
    # carry the same "-t clarify" security pin as the first rewrite call
    # (see test_deliver_rewrite_success_sends_rewritten_text).
    assert shorten_args[shorten_args.index("-t") + 1] == "clarify"

    rows = audit.query(db, None, "gate.sent", None)
    payload = rows[0]["payload"]
    assert payload["final"] == "короче"
    assert "long" not in payload

    send_args, send_kwargs = fake_run.calls[2]
    assert send_kwargs["input"] == "короче"


def test_deliver_over_ceiling_still_over_sends_as_is_with_long_flag(db, fake_run):
    long_text = "это очень длинный текст сверх лимита"
    fake_run.rewrite_responses = [
        _completed(0, long_text),
        _completed(0, "всё ещё длинновато для лимита"),
    ]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(
        db, "reminder", {"label": "тест"}, "fallback", TIGHT_CFG,
        now_utc="2026-07-11T12:00:00+05:00",
    )
    db.commit()

    assert status == "sent"
    rows = audit.query(db, None, "gate.sent", None)
    payload = rows[0]["payload"]
    assert payload["final"] == long_text
    assert payload["long"] is True

    send_args, send_kwargs = fake_run.calls[-1]
    assert send_kwargs["input"] == long_text


# ---- deliver: send failure ----

def test_deliver_send_failure_returns_error_and_audits(db, fake_run):
    fake_run.rewrite_responses = [_completed(0, "Короткий текст.")]
    fake_run.send_response = _completed(1, "", "delivery failed")

    status = gate.deliver(
        db, "reminder", {"label": "тест"}, "fallback", CFG,
        now_utc="2026-07-11T12:00:00+05:00",
    )
    db.commit()

    assert status == "error"
    rows = audit.query(db, None, "gate.", None)
    kinds = {r["kind"] for r in rows}
    assert "gate.error" in kinds
    assert "gate.sent" not in kinds
    error_row = [r for r in rows if r["kind"] == "gate.error"][0]
    assert error_row["payload"]["final"] == "Короткий текст."


def test_deliver_send_exception_returns_error(db, fake_run):
    fake_run.rewrite_responses = [_completed(0, "Короткий текст.")]
    fake_run.send_response = subprocess.TimeoutExpired(cmd="hermes", timeout=60)

    status = gate.deliver(
        db, "reminder", {"label": "тест"}, "fallback", CFG,
        now_utc="2026-07-11T12:00:00+05:00",
    )

    assert status == "error"


# ---- deliver: digest kind uses max_len_digest, not reminder ----

def test_deliver_digest_kind_uses_digest_ceiling(db, fake_run):
    cfg = dict(CFG, max_len_digest=10, max_len_reminder=1000)
    fake_run.rewrite_responses = [
        _completed(0, "погода и планы на сегодня, довольно длинный дайджест"),
        _completed(0, "коротко"),
    ]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(
        db, "digest", {"weather": None}, "fallback", cfg,
        now_utc="2026-07-11T12:00:00+05:00",
    )

    assert status == "sent"
    assert len(fake_run.calls) == 3


# ---- deliver: digest closing question is never dropped (live-found bug) ----
#
# Live evidence: a real digest went out with the weather/events summary but
# without its closing question (raw["question"] = tick.DIGEST_QUESTION) --
# the LLM rewrite's own brevity instructions won out over preserving it.
# The fix makes the question's presence deterministic and independent of
# the LLM: the rewrite prompt is told not to write its own question at all
# (GATE_DIGEST_NO_QUESTION_INSTRUCTION), and deliver() appends
# raw["question"] as the text's own last line itself, on both the rewrite
# and the fallback path, with an exactly-once dedupe (the fallback text
# already ends with the question by tick._build_digest_fallback's own
# construction).

def test_deliver_digest_rewrite_appends_question_exactly_once(db, fake_run):
    raw = {"kind": "digest", "date_local": "2026-07-11", "weather": None,
           "events": [], "question": tick.DIGEST_QUESTION}
    fake_run.rewrite_responses = [_completed(0, "Сегодня без осадков, планов нет.")]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(
        db, "digest", raw, "человеческий фолбэк\n\n" + tick.DIGEST_QUESTION, CFG,
        now_utc="2026-07-11T12:00:00+05:00", force=True,
    )
    db.commit()

    assert status == "sent"
    rows = audit.query(db, None, "gate.sent", None)
    final = rows[0]["payload"]["final"]
    assert rows[0]["payload"]["attempt"] == "rewrite"
    assert final.endswith(tick.DIGEST_QUESTION)
    assert final.count(tick.DIGEST_QUESTION) == 1


def test_deliver_digest_rewrite_prompt_forbids_own_question(db, fake_run):
    raw = {"kind": "digest", "question": tick.DIGEST_QUESTION}
    fake_run.rewrite_responses = [_completed(0, "Сводка коротко.")]
    fake_run.send_response = _completed(0, "")

    gate.deliver(db, "digest", raw, "fallback\n\n" + tick.DIGEST_QUESTION, CFG,
                  now_utc="2026-07-11T12:00:00+05:00", force=True)

    rewrite_args, _ = fake_run.calls[0]
    prompt = rewrite_args[rewrite_args.index("-z") + 1]
    assert "Не задавай вопросов" in prompt


def test_deliver_digest_prompt_includes_temperature_range_instruction(db, fake_run):
    raw = {"kind": "digest", "question": tick.DIGEST_QUESTION}
    fake_run.rewrite_responses = [_completed(0, "Сводка.")]
    fake_run.send_response = _completed(0, "")

    gate.deliver(db, "digest", raw, "fallback\n\n" + tick.DIGEST_QUESTION, CFG,
                  now_utc="2026-07-11T12:00:00+05:00", force=True)

    rewrite_args, _ = fake_run.calls[0]
    prompt = rewrite_args[rewrite_args.index("-z") + 1]
    assert "Если в сводке есть погода — обязательно укажи диапазон температур (минимум…максимум)" in prompt


def test_deliver_digest_prompt_includes_informativeness_instruction(db, fake_run):
    raw = {"kind": "digest", "question": tick.DIGEST_QUESTION}
    fake_run.rewrite_responses = [_completed(0, "Сводка.")]
    fake_run.send_response = _completed(0, "")

    gate.deliver(db, "digest", raw, "fallback\n\n" + tick.DIGEST_QUESTION, CFG,
                  now_utc="2026-07-11T12:00:00+05:00", force=True)

    rewrite_args, _ = fake_run.calls[0]
    prompt = rewrite_args[rewrite_args.index("-z") + 1]
    assert "Лаконичность не должна терять факты: каждое поле сводки должно быть отражено в тексте" in prompt


def test_deliver_reminder_prompt_has_no_digest_question_instruction(db, fake_run):
    fake_run.rewrite_responses = [_completed(0, "Скоро событие.")]
    fake_run.send_response = _completed(0, "")

    gate.deliver(db, "reminder", {"label": "тест"}, "fallback", CFG,
                  now_utc="2026-07-11T12:00:00+05:00")

    rewrite_args, _ = fake_run.calls[0]
    prompt = rewrite_args[rewrite_args.index("-z") + 1]
    assert "Не задавай вопросов" not in prompt


# ---- deliver: reminder time semantics + fabrication ban (Task 16,
# live-found bug -- "В 13:00 Тае пора собираться в поселок": the rewrite
# bound the label's action ("собираться", due at send time) to the
# event's own start_local (13:00) instead) ----

def test_deliver_reminder_prompt_includes_time_semantics_instruction(db, fake_run):
    fake_run.rewrite_responses = [_completed(0, "Скоро событие.")]
    fake_run.send_response = _completed(0, "")

    gate.deliver(
        db, "reminder",
        {"label": "Тае пора собираться", "start_local": "2026-07-12T13:00:00+05:00",
         "sent_now_local": "2026-07-12T12:15:00+05:00"},
        "fallback", CFG, now_utc="2026-07-12T12:15:00+05:00",
    )

    rewrite_args, _ = fake_run.calls[0]
    prompt = rewrite_args[rewrite_args.index("-z") + 1]
    assert gate.GATE_REMINDER_TIME_SEMANTICS_INSTRUCTION in prompt
    # the instruction must actually carry both requirements from the
    # brief, not just be present as an opaque blob:
    assert "sent_now_local" in gate.GATE_REMINDER_TIME_SEMANTICS_INSTRUCTION
    assert "start_local" in gate.GATE_REMINDER_TIME_SEMANTICS_INSTRUCTION


def test_gate_reminder_time_semantics_instruction_forbids_reassigning_label_actor():
    # Live-probe-found bug (Task 16, iteration 2): a rewrite of {"label":
    # "Тае пора собираться", "participants": ["Тая"], ...} once came back
    # as "Тебе пора собирать Таю" -- the label's own actor (Тая)
    # reassigned to the chat owner. Reminder-specific because it's
    # phrased in terms of raw's own label/participants fields.
    assert "participants" in gate.GATE_REMINDER_TIME_SEMANTICS_INSTRUCTION
    assert "переадресовывать" in gate.GATE_REMINDER_TIME_SEMANTICS_INSTRUCTION


def test_gate_reminder_time_semantics_instruction_attribution_follows_label():
    # Reviewer finding (Task 16 fix round): the anti-reassignment clause
    # above was designed and live-probed only against a self-naming label
    # ("Тае пора собираться") and overcorrected on the other two real
    # payload shapes. (a) Generic DEFAULT_STAGES labels ("пора выходить",
    # "скоро событие") with participants=["Тая"]: the action belongs to
    # the chat owner (who e.g. drives the participant) -- the old wording
    # ("действующее лицо -- тот участник, ... а не владелец чата")
    # plausibly forced the participant into the actor slot, the mirrored
    # form of the very misattribution this task exists to kill.
    # (b) participants=[] (a real case, see tick.py): the old wording
    # said "not the chat owner" with nobody else left to attribute to.
    # Attribution must follow the label's own meaning instead.
    instr = gate.GATE_REMINDER_TIME_SEMANTICS_INSTRUCTION
    assert "только по самому label" in instr
    assert "если label никого не называет" in instr
    assert "пустой participants" in instr


# Phase 2c, task 7: variation rule -- a reminder chain that has already
# sent earlier today must not repeat itself verbatim. prior_texts (built
# by tick.py from gate.prior_texts_today) is the input; this instruction
# is what tells the rewrite what to do with it.
def test_reminder_instruction_bans_verbatim_repeat():
    assert "prior_texts" in gate.GATE_REMINDER_TIME_SEMANTICS_INSTRUCTION


def test_deliver_digest_prompt_has_no_reminder_time_semantics_instruction(db, fake_run):
    raw = {"kind": "digest", "question": tick.DIGEST_QUESTION}
    fake_run.rewrite_responses = [_completed(0, "Сводка.")]
    fake_run.send_response = _completed(0, "")

    gate.deliver(db, "digest", raw, "fallback\n\n" + tick.DIGEST_QUESTION, CFG,
                  now_utc="2026-07-11T12:00:00+05:00", force=True)

    rewrite_args, _ = fake_run.calls[0]
    prompt = rewrite_args[rewrite_args.index("-z") + 1]
    assert gate.GATE_REMINDER_TIME_SEMANTICS_INSTRUCTION not in prompt


def test_gate_style_instruction_has_no_literal_reminder_example():
    # Live-found bug (few-shot bleed): a real rewrite once emitted the
    # literal sentence "Тае пора собираться" verbatim -- it existed only
    # as a copy-able example inside GATE_STYLE_INSTRUCTION, unrelated to
    # the actual raw data being rewritten. The addressing rule must be
    # expressed without a ready-made phrase the model can paste back.
    assert "Тае пора собираться" not in gate.GATE_STYLE_INSTRUCTION


def test_gate_style_instruction_forbids_attributing_participant_action_to_owner():
    # Live-probe-found bug (Task 16, iteration 1): after the copy-able
    # example was removed, a real rewrite of {"label": "Тае пора
    # собираться", ...} once came back as "Тебе пора собираться" -- the
    # participant's own action reassigned to the chat owner. The
    # addressing rule now explicitly forbids this.
    assert "не приписывай их действие" in gate.GATE_STYLE_INSTRUCTION


def test_deliver_digest_fallback_appends_question_exactly_once_no_duplicate(db, fake_run):
    # human_fallback mirrors tick._build_digest_fallback: it already ends
    # with the question by construction (a single "\n" separator, the
    # last line of the lines list) -- the dedupe must not double it when
    # the rewrite fails and this fallback text becomes final_text verbatim.
    raw = {"kind": "digest", "question": tick.DIGEST_QUESTION}
    human_fallback = (
        "Доброе утро! Сегодня 2026-07-11.\nСобытий нет.\n" + tick.DIGEST_QUESTION
    )
    fake_run.rewrite_responses = [_completed(1, "", "boom")]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(db, "digest", raw, human_fallback, CFG,
                           now_utc="2026-07-11T12:00:00+05:00", force=True)
    db.commit()

    assert status == "sent"
    rows = audit.query(db, None, "gate.sent", None)
    final = rows[0]["payload"]["final"]
    assert rows[0]["payload"]["attempt"] == "fallback"
    assert final.endswith(tick.DIGEST_QUESTION)
    assert final.count(tick.DIGEST_QUESTION) == 1


def test_deliver_reminder_kind_no_question_logic_even_if_raw_has_question(db, fake_run):
    # Guard: question handling is gated on kind=="digest" only -- a
    # reminder's raw is never inspected for a "question" key, even if one
    # happens to be present.
    fake_run.rewrite_responses = [_completed(0, "Скоро событие.")]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(
        db, "reminder", {"label": "тест", "question": "Куда идём?"}, "fallback",
        CFG, now_utc="2026-07-11T12:00:00+05:00",
    )
    db.commit()

    assert status == "sent"
    rows = audit.query(db, None, "gate.sent", None)
    assert rows[0]["payload"]["final"] == "Скоро событие."


DIGEST_TIGHT_CFG = dict(CFG, max_len_digest=30)


def test_deliver_digest_over_ceiling_shortens_informational_question_preserved(db, fake_run):
    question = "Что по планам?"
    raw = {"kind": "digest", "question": question}
    fake_run.rewrite_responses = [
        _completed(0, "очень длинный текст сверх лимита для дайджеста"),
        _completed(0, "коротко"),
    ]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(db, "digest", raw, "fallback\n\n" + question,
                           DIGEST_TIGHT_CFG,
                           now_utc="2026-07-11T12:00:00+05:00", force=True)
    db.commit()

    assert status == "sent"
    rows = audit.query(db, None, "gate.sent", None)
    payload = rows[0]["payload"]
    assert payload["final"] == "коротко\n\n" + question
    assert payload["final"].count(question) == 1
    assert "long" not in payload

    # the shorten-retry must target the informational part only, not the
    # combined (question-included) text -- the question is re-appended
    # after, never sent through the LLM's "shorten to N chars" instruction.
    shorten_args, _ = fake_run.calls[1]
    shorten_prompt = shorten_args[shorten_args.index("-z") + 1]
    assert question not in shorten_prompt


def test_deliver_digest_still_over_ceiling_sends_with_long_flag_question_preserved(db, fake_run):
    question = "Что по планам?"
    raw = {"kind": "digest", "question": question}
    long_text = "очень длинный текст сверх лимита для дайджеста"
    fake_run.rewrite_responses = [
        _completed(0, long_text),
        _completed(0, "всё ещё длинновато для лимита дайджеста"),
    ]
    fake_run.send_response = _completed(0, "")

    status = gate.deliver(db, "digest", raw, "fallback\n\n" + question,
                           DIGEST_TIGHT_CFG,
                           now_utc="2026-07-11T12:00:00+05:00", force=True)
    db.commit()

    assert status == "sent"
    rows = audit.query(db, None, "gate.sent", None)
    payload = rows[0]["payload"]
    # the ceiling is never allowed to truncate the question away, even
    # when the informational part is still too long after one shorten
    # retry -- long=True flags it instead, exactly like the reminder path.
    assert payload["final"].endswith(question)
    assert payload["final"].count(question) == 1
    assert payload["long"] is True
