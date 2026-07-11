import json
import subprocess

import pytest

from fam import audit, gate

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
}


def _insert_audit(db, kind, ts_utc, payload=None):
    db.execute(
        "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
        (ts_utc, kind, "test", json.dumps(payload or {}, ensure_ascii=False)),
    )


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


# ---- deliver: quiet hours ----

def test_deliver_quiet_hours_skips_and_audits(db, fake_run):
    status = gate.deliver(
        db, "reminder", {"label": "тест"}, "fallback text", CFG,
        now_utc="2026-07-11T22:00:00+05:00",
    )
    db.commit()

    assert status == "quiet"
    assert fake_run.calls == []
    rows = audit.query(db, None, "gate.", None)
    assert rows[0]["kind"] == "gate.skip"
    assert rows[0]["payload"]["reason"] == "quiet"


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
