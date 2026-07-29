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
