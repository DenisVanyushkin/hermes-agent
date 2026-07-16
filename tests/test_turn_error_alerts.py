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
