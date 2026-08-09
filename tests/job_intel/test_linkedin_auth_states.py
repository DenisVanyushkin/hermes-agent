from __future__ import annotations

from job_intel.browser_sourcing import BrowserSessionHealth, apply_linkedin_verdict
from job_intel.linkedin_session import (
    CHALLENGE_EMAIL_OTP,
    CHALLENGE_HARD,
    SESSION_MISSING,
    SESSION_OK,
    SessionVerdict,
)


def test_verdict_lands_in_the_health_snapshot() -> None:
    health = BrowserSessionHealth(source="linkedin")

    apply_linkedin_verdict(health, SessionVerdict(state=CHALLENGE_EMAIL_OTP))

    assert health.session_state == CHALLENGE_EMAIL_OTP
    assert health.snapshot()["session_state"] == CHALLENGE_EMAIL_OTP


def test_authenticated_verdict_does_not_raise_the_login_wall_counter() -> None:
    """Счётчик login_walls остаётся ради совместимости дашбордов, но перестаёт
    быть основанием для вывода: решение принимается по session_state."""
    health = BrowserSessionHealth(source="linkedin")

    apply_linkedin_verdict(health, SessionVerdict(state=SESSION_OK))

    assert health.login_walls == 0
    assert health.session_state == SESSION_OK


def test_missing_cookie_marks_the_session_and_not_a_challenge() -> None:
    health = BrowserSessionHealth(source="linkedin")

    apply_linkedin_verdict(health, SessionVerdict(state=SESSION_MISSING))

    assert health.session_state == SESSION_MISSING
    assert health.snapshot()["cookie_mismatch"] is False


def test_hard_challenge_is_recorded_distinctly_from_the_email_one() -> None:
    hard = BrowserSessionHealth(source="linkedin")
    email = BrowserSessionHealth(source="linkedin")

    apply_linkedin_verdict(hard, SessionVerdict(state=CHALLENGE_HARD))
    apply_linkedin_verdict(email, SessionVerdict(state=CHALLENGE_EMAIL_OTP))

    assert hard.session_state != email.session_state


def test_cookie_mismatch_flag_is_carried_through() -> None:
    health = BrowserSessionHealth(source="linkedin")

    apply_linkedin_verdict(health, SessionVerdict(state=SESSION_OK, cookie_mismatch=True))

    assert health.snapshot()["cookie_mismatch"] is True
