"""Turn-error alerts to Denis's Telegram admin channel (spec 2026-07-16).

Detection is text/flag-based on the FINAL user-facing reply plus agent_result
flags — the stub texts are produced in the same fork (gateway/run.py:
_gateway_provider_error_reply, _normalize_empty_agent_response, "(empty)"
conversion), so matching their stable prefixes is reliable here.
"""
import gateway.turn_error_alerts as tea


# --- get_alert_config -------------------------------------------------------

def test_config_absent_or_empty_channel_disables():
    assert tea.get_alert_config(None) is None
    assert tea.get_alert_config({}) is None
    assert tea.get_alert_config({"gateway": {}}) is None
    assert tea.get_alert_config({"gateway": {"error_alerts": {}}}) is None
    assert tea.get_alert_config({"gateway": {"error_alerts": {"channel": "  "}}}) is None


def test_config_defaults_and_overrides():
    cfg = {"gateway": {"error_alerts": {"channel": "telegram:79564752"}}}
    parsed = tea.get_alert_config(cfg)
    assert parsed == {
        "channel": "telegram:79564752",
        "dedup_minutes": 15,
        "include_user_message": True,
    }
    cfg["gateway"]["error_alerts"].update(
        {"dedup_minutes": 5, "include_user_message": False}
    )
    parsed = tea.get_alert_config(cfg)
    assert parsed["dedup_minutes"] == 5
    assert parsed["include_user_message"] is False


def test_config_malformed_fails_closed():
    # ломаный блок не должен ни падать, ни включать фичу
    assert tea.get_alert_config({"gateway": {"error_alerts": 123}}) is None
    assert tea.get_alert_config({"gateway": "oops"}) is None


# --- detect_turn_degradation -------------------------------------------------

def test_detect_failed_and_partial_flags():
    assert tea.detect_turn_degradation(
        {"failed": True, "error": "boom"}, "The request failed: boom\nTry again..."
    ) == "failed"
    assert tea.detect_turn_degradation(
        {"partial": True, "error": "half"}, "⚠️ Processing stopped: half. Try again."
    ) == "partial"


def test_detect_provider_stub_categories():
    cases = {
        "⚠️ Provider authentication failed. Check the configured credentials; ...": "provider-auth",
        "⚠️ The model provider rejected the request. I kept the raw provider ...": "provider-policy",
        "⏱️ The model provider is rate-limiting requests. Please wait a moment and try again.": "rate-limit",
        "⚠️ The model provider failed after retries. I kept raw provider details ...": "provider-fail",
    }
    for text, cat in cases.items():
        assert tea.detect_turn_degradation({}, text) == cat, cat


def test_detect_empty_and_no_response_and_context():
    assert tea.detect_turn_degradation(
        {}, "⚠️ The model returned no response after processing tool results. ..."
    ) == "empty"
    assert tea.detect_turn_degradation(
        {"api_calls": 3}, "⚠️ Processing completed but no response was generated. ..."
    ) == "no-response"
    assert tea.detect_turn_degradation(
        {"failed": True, "error": "context window exceeded"},
        "⚠️ Session too large for the model's context window.\nUse /compact ...",
    ) == "context-overflow"


def test_detect_none_for_normal_and_interrupted():
    assert tea.detect_turn_degradation({}, "Готово, напомню завтра в 9:00!") is None
    assert tea.detect_turn_degradation({}, "") is None
    # осознанный /stop (interrupted, работа была) — не ошибка
    assert tea.detect_turn_degradation(
        {"interrupted": True, "api_calls": 4}, ""
    ) is None
    # interrupted-drop (0 api_calls) — вызвано /stop пользователя, не алертим
    assert tea.detect_turn_degradation(
        {"interrupted": True, "api_calls": 0},
        "⚠️ Your message was interrupted before processing started (likely by a recent /stop). Please send it again.",
    ) is None


def test_detect_prose_mentioning_warning_glyph_is_not_error():
    assert tea.detect_turn_degradation(
        {}, "⚠️ Осторожно: завтра гололёд. Выезжай на 10 минут раньше."
    ) is None


# --- dedup_decision -----------------------------------------------------------

def test_dedup_window():
    tea._ALERT_STATE.clear()
    sig = ("provider-fail", "boom")
    now = 1_000_000.0
    send, repeats = tea.dedup_decision(sig, now, 15)
    assert (send, repeats) == (True, 0)
    # повтор внутри окна — молчим и копим
    assert tea.dedup_decision(sig, now + 60, 15) == (False, 1)
    assert tea.dedup_decision(sig, now + 120, 15) == (False, 2)
    # выход за окно — шлём, отдаём накопленное
    send, repeats = tea.dedup_decision(sig, now + 16 * 60, 15)
    assert (send, repeats) == (True, 2)
    # счётчик сброшен
    assert tea.dedup_decision(sig, now + 16 * 60 + 30, 15) == (False, 1)


def test_dedup_signatures_independent():
    tea._ALERT_STATE.clear()
    now = 2_000_000.0
    assert tea.dedup_decision(("failed", "a"), now, 15)[0] is True
    assert tea.dedup_decision(("failed", "b"), now, 15)[0] is True


# --- format_alert --------------------------------------------------------------

def test_format_alert_full():
    text = tea.format_alert(
        category="provider-fail",
        platform="whatsapp",
        chat_label="77011102626",
        user_message="напомни про тренировку завтра",
        error_detail="API call failed: HTTP 500 upstream",
        repeats=4,
        now_utc=1752649920.0,  # 07:12 UTC = 12:12 Алматы (UTC+5)
    )
    assert text.startswith("⚠️ Гермес: ошибка хода")
    assert "whatsapp / 77011102626" in text
    assert "12:12 Алматы" in text
    assert "Категория: provider-fail" in text
    assert "«напомни про тренировку завтра»" in text
    assert "HTTP 500 upstream" in text
    assert "повторилась 4 раза" in text


def test_format_alert_truncates_and_omits_optional():
    text = tea.format_alert(
        category="failed",
        platform="whatsapp",
        chat_label="x",
        user_message="а" * 500,
        error_detail="e" * 2000,
        repeats=0,
        now_utc=1752652320.0,
    )
    assert "а" * 201 not in text          # сообщение обрезано до 200
    assert "e" * 601 not in text          # деталь обрезана до 600
    assert "повторилась" not in text      # repeats=0 — строки нет
    text2 = tea.format_alert(
        category="failed", platform="whatsapp", chat_label="x",
        user_message=None, error_detail=None, repeats=0, now_utc=1752652320.0,
    )
    assert "Сообщение:" not in text2
    assert "Ошибка:" not in text2


# --- maybe_alert_turn_error ----------------------------------------------------

CFG = {"gateway": {"error_alerts": {"channel": "telegram:79564752"}}}


def _fail_result():
    return {"failed": True, "error": "API call failed: HTTP 500"}


def test_maybe_alert_sends_on_degraded_turn(monkeypatch):
    tea._ALERT_STATE.clear()
    sent = []
    monkeypatch.setattr(tea, "_send_alert", lambda ch, txt: sent.append((ch, txt)))
    tea.maybe_alert_turn_error(
        CFG, platform="whatsapp", chat_id="77011102626",
        user_message="привет", agent_result=_fail_result(),
        final_response="The request failed: API call failed: HTTP 500\nTry again...",
    )
    assert len(sent) == 1
    ch, txt = sent[0]
    assert ch == "telegram:79564752"
    assert "Категория: failed" in txt and "«привет»" in txt


def test_maybe_alert_noop_paths(monkeypatch):
    tea._ALERT_STATE.clear()
    sent = []
    monkeypatch.setattr(tea, "_send_alert", lambda ch, txt: sent.append(txt))
    ok = dict(platform="whatsapp", chat_id="1", user_message="hi")
    # выключено конфигом
    tea.maybe_alert_turn_error({}, **ok, agent_result=_fail_result(),
                               final_response="The request failed: x")
    # здоровый ответ
    tea.maybe_alert_turn_error(CFG, **ok, agent_result={},
                               final_response="Готово!")
    # сам админ-канал (петля/дубль)
    tea.maybe_alert_turn_error(
        CFG, platform="telegram", chat_id="79564752", user_message="hi",
        agent_result=_fail_result(), final_response="The request failed: x",
    )
    assert sent == []


def test_maybe_alert_dedup_and_repeat_note(monkeypatch):
    tea._ALERT_STATE.clear()
    sent = []
    monkeypatch.setattr(tea, "_send_alert", lambda ch, txt: sent.append(txt))
    kw = dict(platform="whatsapp", chat_id="1", user_message="hi",
              agent_result=_fail_result(),
              final_response="The request failed: API call failed: HTTP 500")
    t0 = 1_000_000.0
    tea.maybe_alert_turn_error(CFG, now=t0, **kw)
    tea.maybe_alert_turn_error(CFG, now=t0 + 60, **kw)      # молчит
    tea.maybe_alert_turn_error(CFG, now=t0 + 120, **kw)     # молчит
    tea.maybe_alert_turn_error(CFG, now=t0 + 16 * 60, **kw) # шлёт + N
    assert len(sent) == 2
    assert "повторилась 2 раза" in sent[1]


def test_maybe_alert_respects_include_user_message(monkeypatch):
    tea._ALERT_STATE.clear()
    sent = []
    monkeypatch.setattr(tea, "_send_alert", lambda ch, txt: sent.append(txt))
    cfg = {"gateway": {"error_alerts": {
        "channel": "telegram:79564752", "include_user_message": False}}}
    tea.maybe_alert_turn_error(
        cfg, platform="whatsapp", chat_id="1", user_message="секретик",
        agent_result=_fail_result(), final_response="The request failed: x",
    )
    assert "секретик" not in sent[0]


def test_maybe_alert_never_raises(monkeypatch):
    tea._ALERT_STATE.clear()
    def boom(ch, txt):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(tea, "_send_alert", boom)
    tea.maybe_alert_turn_error(
        CFG, platform="whatsapp", chat_id="1", user_message="hi",
        agent_result=_fail_result(), final_response="The request failed: x",
    )  # не бросило — тест пройден


def test_send_alert_spawns_daemon_thread_with_hermes_send(monkeypatch):
    calls = {}
    class FakeThread:
        def __init__(self, target=None, daemon=None, name=None):
            calls["daemon"] = daemon
            self._target = target
        def start(self):
            self._target()
    monkeypatch.setattr(tea.threading, "Thread", FakeThread)
    monkeypatch.setattr(tea, "_resolve_hermes_argv", lambda: ["/usr/bin/hermes"])
    def fake_run(argv, **kw):
        calls["argv"] = argv
        calls["input"] = kw.get("input")
        class R: returncode = 0
        return R()
    monkeypatch.setattr(tea.subprocess, "run", fake_run)
    tea._send_alert("telegram:79564752", "текст алерта")
    assert calls["daemon"] is True
    assert calls["argv"] == ["/usr/bin/hermes", "send", "-t", "telegram:79564752"]
    assert calls["input"] == "текст алерта"


def test_run_py_hook_wired():
    # run.py содержит вызов и все обязательные kwargs — дешёвая защита от
    # рассинхрона сигнатуры при будущих правках гигантского run.py
    import inspect
    import gateway.run as run
    src = inspect.getsource(run)
    assert "maybe_alert_turn_error(" in src
    tail = src.split("maybe_alert_turn_error(", 1)[1][:600]
    for kw in ("platform=", "chat_id=", "user_message=", "agent_result=", "final_response="):
        assert kw in tail, kw
