"""Policy: a cron job read by a non-technical end user is silent on
technical failure — the operator gets the detail instead. Operator-audience
jobs keep the historical in-chat summary."""

from __future__ import annotations

from cron.scheduler import plan_cron_failure_delivery, resolve_cron_audience

AMINA_JOB = {
    "id": "150d115fe905",
    "name": "Вечерний короткий прогноз погоды Алматы — Амина",
    "deliver": "whatsapp:+77011102626",
    "audience": "end_user",
}
OPERATOR_JOB = {
    "id": "d3865bc0d8cb",
    "name": "hermes-fallback-refresh-daily-0230",
    "deliver": "telegram:79564752",
}
UNFLAGGED_AMINA_JOB = {
    "id": "8b751dbfd5d6",
    "name": "Утренний короткий прогноз погоды Алматы — Амина",
    "deliver": "whatsapp:+77011102626",
}
ERROR = (
    "ImportError: cannot import name 'ReviewGateState' from "
    "'hermes_cli.review_gate' "
    "(/home/denis/.hermes/hermes-agent/hermes_cli/review_gate.py)"
)
CFG = {"cron": {"end_user_targets": ["whatsapp:+77011102626"]}}


def test_explicit_flag_wins() -> None:
    assert resolve_cron_audience(AMINA_JOB) == "end_user"


def test_default_is_operator() -> None:
    assert resolve_cron_audience(OPERATOR_JOB) == "operator"


def test_config_target_list_catches_unflagged_jobs() -> None:
    assert resolve_cron_audience(UNFLAGGED_AMINA_JOB, CFG) == "end_user"
    assert resolve_cron_audience(OPERATOR_JOB, CFG) == "operator"


def test_end_user_job_delivers_nothing_to_the_chat() -> None:
    chat_text, alert_text = plan_cron_failure_delivery(AMINA_JOB, ERROR)

    assert chat_text is None
    assert alert_text is not None


def test_operator_alert_names_the_job_and_the_reason() -> None:
    _, alert_text = plan_cron_failure_delivery(AMINA_JOB, ERROR)

    assert "Вечерний короткий прогноз" in alert_text
    assert "whatsapp:+77011102626" in alert_text
    assert "withheld" in alert_text.lower()
    assert "cannot import name" in alert_text


def test_operator_alert_is_redacted_too() -> None:
    _, alert_text = plan_cron_failure_delivery(AMINA_JOB, ERROR)

    assert "/home/denis" not in alert_text


def test_operator_job_keeps_the_in_chat_summary() -> None:
    chat_text, alert_text = plan_cron_failure_delivery(OPERATOR_JOB, ERROR)

    assert chat_text is not None
    assert "hermes-fallback-refresh-daily-0230" in chat_text
    assert alert_text is None  # the chat target IS the operator; no duplicate


def test_missing_error_still_yields_an_alert() -> None:
    chat_text, alert_text = plan_cron_failure_delivery(AMINA_JOB, None)

    assert chat_text is None
    assert alert_text is not None


def test_typo_audience_with_matching_config_degrades_to_end_user() -> None:
    job = {**UNFLAGGED_AMINA_JOB, "audience": "enduser"}

    assert resolve_cron_audience(job, CFG) == "end_user"


def test_typo_audience_without_config_falls_back_to_operator() -> None:
    job = {**UNFLAGGED_AMINA_JOB, "audience": "enduser"}

    assert resolve_cron_audience(job) == "operator"


def test_non_string_audience_does_not_raise_and_falls_back_to_operator() -> None:
    for bogus in (123, []):
        job = {**UNFLAGGED_AMINA_JOB, "audience": bogus}

        assert resolve_cron_audience(job) == "operator"


def test_malformed_cron_config_does_not_raise_and_falls_back_to_operator() -> None:
    """A hand-edited config.yaml can make `cron:` a non-dict (a string, a
    list). resolve_cron_audience's docstring promises it never raises;
    previously `((cfg or {}).get("cron", {}) or {}).get(...)` raised
    AttributeError here, which escaped past this function's promise and
    (before the caller wrapped it) reached an operator job's failure
    delivery, silently dropping it."""
    job = {**UNFLAGGED_AMINA_JOB}  # no explicit audience field

    assert resolve_cron_audience(job, {"cron": "not-a-dict"}) == "operator"


def test_bare_string_end_user_targets_still_protects() -> None:
    """A hand-edited config.yaml can easily drop the list dash, leaving
    `end_user_targets: whatsapp:+77011102626` as a bare string instead of a
    one-item list. A string is iterable, so treating it as the target list
    without normalizing would silently iterate its characters and match
    nothing -- disabling the safety net without any error. It must instead
    be treated as a single target."""
    cfg = {"cron": {"end_user_targets": "whatsapp:+77011102626"}}

    assert resolve_cron_audience(UNFLAGGED_AMINA_JOB, cfg) == "end_user"


def test_unnormalized_valid_audience_is_stripped_and_lowercased() -> None:
    job = {**UNFLAGGED_AMINA_JOB, "audience": " END_USER "}

    assert resolve_cron_audience(job) == "end_user"


def test_unnormalized_valid_audience_withholds_chat_delivery_end_to_end() -> None:
    job = {**AMINA_JOB, "audience": " END_USER "}
    chat_text, alert_text = plan_cron_failure_delivery(job, ERROR)

    assert chat_text is None
    assert alert_text is not None


def test_operator_alert_is_sent_to_the_configured_channel(monkeypatch) -> None:
    from cron import scheduler

    sent = []
    monkeypatch.setattr(
        scheduler, "_deliver_result",
        lambda job, content, adapters=None, loop=None, wrap=None: sent.append((job, content)),
    )

    scheduler._send_cron_operator_alert(
        "⚠️ Cron 'x' failed", cfg={"gateway": {"error_alerts": {"channel": "telegram:79564752"}}}
    )

    assert len(sent) == 1
    synthetic_job, content = sent[0]
    assert synthetic_job["deliver"] == "telegram:79564752"
    assert content == "⚠️ Cron 'x' failed"


def test_no_alert_channel_configured_is_a_silent_no_op(monkeypatch, caplog) -> None:
    """The ``if not alert_cfg: return`` gate in _send_cron_operator_alert is
    what keeps this feature inert on any install that has no
    gateway.error_alerts.channel configured. Proving the gate held requires
    a stub that accepts the real call signature (``wrap`` included) — a
    stub missing ``wrap`` would make a bypassed gate raise TypeError instead
    of calling through, which the function's bare ``except Exception`` then
    swallows, leaving ``sent == []`` true either way and the test unable to
    fail if the gate were deleted. Asserting no warning was logged rules
    that out directly: a bypassed gate reaches ``alert_cfg["channel"]``
    with ``alert_cfg is None`` and raises, which the except-Exception logs
    as a warning."""
    import logging

    from cron import scheduler

    sent = []
    monkeypatch.setattr(
        scheduler, "_deliver_result",
        lambda job, content, adapters=None, loop=None, wrap=None: sent.append(content),
    )

    with caplog.at_level(logging.WARNING, logger="cron.scheduler"):
        scheduler._send_cron_operator_alert("⚠️ Cron 'x' failed", cfg={})

    assert sent == []
    assert not caplog.records, [r.getMessage() for r in caplog.records]


def test_alert_delivery_failure_never_raises(monkeypatch) -> None:
    from cron import scheduler

    def boom(job, content, adapters=None, loop=None, wrap=None):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr(scheduler, "_deliver_result", boom)

    scheduler._send_cron_operator_alert(
        "⚠️ Cron 'x' failed", cfg={"gateway": {"error_alerts": {"channel": "telegram:79564752"}}}
    )  # must not raise


def test_operator_alert_delivers_unwrapped(monkeypatch) -> None:
    """The alert must NOT go through _deliver_result's default cron-response
    wrapper: that header/footer names a job ("cron operator alert") that
    does not exist and tells the operator to "stop reminder cron operator
    alert" — a dead instruction. _send_cron_operator_alert passes
    wrap=False for exactly this reason; this test exercises the real
    (unmocked) _deliver_result to prove the alert text survives intact."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from cron import scheduler
    from gateway.config import Platform

    pconfig = MagicMock()
    pconfig.enabled = True
    mock_gw_cfg = MagicMock()
    mock_gw_cfg.platforms = {Platform.TELEGRAM: pconfig}

    with patch("gateway.config.load_gateway_config", return_value=mock_gw_cfg), \
         patch(
             "tools.send_message_tool._send_to_platform",
             new=AsyncMock(return_value={"success": True}),
         ) as send_mock, \
         patch(
             "cron.scheduler.load_config",
             return_value={"cron": {"wrap_response": True}},
         ):
        scheduler._send_cron_operator_alert(
            "⚠️ Cron 'x' failed",
            cfg={"gateway": {"error_alerts": {"channel": "telegram:79564752"}}},
        )

    send_mock.assert_called_once()
    sent_content = send_mock.call_args.kwargs.get("content") or send_mock.call_args[0][-1]
    assert sent_content == "⚠️ Cron 'x' failed"
    assert "Cronjob Response" not in sent_content
    assert "stop reminder" not in sent_content
