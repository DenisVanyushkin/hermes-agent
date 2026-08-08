"""Аудитория задания доезжает до конца хода — и не переживает его.

Наблюдение 2026-08-01: задание `150d115fe905` («Вечерний короткий прогноз
погоды Алматы — Амина», `deliver: whatsapp:+7…`) доставило Амине блок «Где
выполнялись проверки». Гейт инженерных футеров смотрел на платформу ХОДА,
а у крона она пуста (`platform=""` в контексте сессии, `platform="cron"`
у агента) — адресат ДОСТАВКИ живёт отдельно, в `deliver:` задания.

Признак передаётся строкой через контекстную переменную, а не импортом:
обратная зависимость `hermes_cli/run_evidence.py -> cron/scheduler.py`
втянула бы весь планировщик в турн-путь.
"""

import pytest

from cron.scheduler import (
    CRON_AUDIENCE_CONTEXT_VAR,
    bind_cron_audience_context,
    clear_cron_audience_context,
)
from gateway.session_context import _VAR_MAP, _UNSET, get_session_env
from hermes_cli.run_evidence import cron_end_user_turn, engineering_footers_suppressed


@pytest.fixture(autouse=True)
def _pristine_audience(monkeypatch):
    monkeypatch.delenv("HERMES_CRON_AUDIENCE", raising=False)
    yield
    _VAR_MAP[CRON_AUDIENCE_CONTEXT_VAR].set(_UNSET)


END_USER_JOB = {
    "id": "150d115fe905",
    "name": "Вечерний короткий прогноз погоды Алматы — Амина",
    "deliver": "whatsapp:+77011102626",
    "audience": "end_user",
}

OPERATOR_JOB = {"id": "op1", "name": "nightly repo audit", "deliver": "telegram:79564752"}

CFG = {"cron": {"end_user_targets": ["whatsapp:+77011102626"]}}


def test_an_end_user_job_marks_the_turn():
    assert bind_cron_audience_context(END_USER_JOB, CFG) == "end_user"
    assert get_session_env(CRON_AUDIENCE_CONTEXT_VAR) == "end_user"
    assert cron_end_user_turn() is True


def test_the_safety_net_marks_a_job_without_the_field():
    """`cron.end_user_targets` — сеть безопасности для заданий, созданных
    позже без явного поля. Гейт футеров обязан ловиться и ею."""
    job = dict(END_USER_JOB)
    job.pop("audience")

    assert bind_cron_audience_context(job, CFG) == "end_user"
    assert cron_end_user_turn() is True


def test_an_operator_job_leaves_the_turn_engineering():
    assert bind_cron_audience_context(OPERATOR_JOB, CFG) == "operator"
    assert cron_end_user_turn() is False
    assert engineering_footers_suppressed(fallback_platform="cron") is False


def test_the_mark_is_dropped_on_the_way_out():
    """Иначе признак протечёт в соседнее задание того же процесса: пул
    переиспользует потоки, а `clear_session_vars` эту переменную не трогает —
    она не `HERMES_SESSION_*`."""
    bind_cron_audience_context(END_USER_JOB, CFG)
    assert cron_end_user_turn() is True

    clear_cron_audience_context()

    assert get_session_env(CRON_AUDIENCE_CONTEXT_VAR) == ""
    assert cron_end_user_turn() is False


def test_a_broken_resolver_marks_nothing(monkeypatch):
    """Отказ везде означает «не подавлять»: сломанная политика аудитории обязана
    оставить оператору лишние подробности, а не тихо выключить анти-оверклейм."""
    import cron.scheduler as sched

    monkeypatch.setattr(
        sched, "resolve_cron_audience", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    assert bind_cron_audience_context(END_USER_JOB, CFG) == ""
    assert cron_end_user_turn() is False


def test_run_job_binds_and_clears_the_audience_around_the_agent():
    """Проводка в `run_job`, а не только сами хелперы: bind стоит внутри try,
    чей finally зовёт clear, поэтому оба обязаны быть в исходнике рядом."""
    import inspect

    import cron.scheduler as sched

    source = inspect.getsource(sched.run_job)
    assert "bind_cron_audience_context(job, _cfg)" in source
    assert "clear_cron_audience_context()" in source
    # clear обязан стоять после bind и в finally — иначе признак живёт дольше
    # задания.
    assert source.index("bind_cron_audience_context(job, _cfg)") < source.index(
        "clear_cron_audience_context()"
    )
