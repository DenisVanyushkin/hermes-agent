from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from job_intel.linkedin_otp_recovery import AttemptLedger
from job_intel.otp_mail import MailOtpConfig

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
CONFIG_STUB = MailOtpConfig(address="a@example.com", password="x", host="imap.example.com", port=993)


def test_first_attempt_is_allowed(tmp_path: Path) -> None:
    ledger = AttemptLedger(tmp_path / "attempts.json")

    assert ledger.may_attempt(now=NOW) is True


def test_second_attempt_within_the_day_is_refused(tmp_path: Path) -> None:
    """Молчаливый цикл перелогинов — это и есть эскалация, которой фаза C
    пытается избежать: частота входов с одного адреса сама является сигналом."""
    ledger = AttemptLedger(tmp_path / "attempts.json")
    ledger.record(now=NOW)

    assert ledger.may_attempt(now=NOW + timedelta(hours=3)) is False


def test_attempt_is_allowed_again_after_the_window(tmp_path: Path) -> None:
    ledger = AttemptLedger(tmp_path / "attempts.json")
    ledger.record(now=NOW)

    assert ledger.may_attempt(now=NOW + timedelta(hours=25)) is True


def test_corrupt_ledger_fails_closed(tmp_path: Path) -> None:
    """Нечитаемый журнал означает «не знаю, сколько попыток было». Считать это
    за ноль — значит снять предохранитель ровно тогда, когда состояние потеряно."""
    path = tmp_path / "attempts.json"
    path.write_text("{ not json", encoding="utf-8")
    ledger = AttemptLedger(path)

    assert ledger.may_attempt(now=NOW) is False


def test_record_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "attempts.json"
    AttemptLedger(path).record(now=NOW)

    assert AttemptLedger(path).may_attempt(now=NOW + timedelta(minutes=5)) is False


# --- Задача 11: восстановление сессии ------------------------------------

from job_intel.linkedin_otp_recovery import RecoveryOutcome, recover_session
from job_intel.linkedin_session import CHALLENGE_EMAIL_OTP, CHALLENGE_HARD, SESSION_OK, SessionVerdict
from job_intel.otp_mail import OtpResult


class FakePage:
    def __init__(self) -> None:
        self.filled: list[str] = []
        self.submitted = False

    def fill_code(self, code: str) -> None:
        self.filled.append(code)

    def submit(self) -> None:
        self.submitted = True


def _reader(result: OtpResult):
    def read(**kwargs: object) -> OtpResult:
        return result

    return read


def _notifier(sink: list[str]):
    def notify(message: str, **kwargs: object) -> dict[str, object]:
        sink.append(message)
        return {"posted": True}

    return notify


def test_hard_challenge_is_never_attempted(tmp_path: Path) -> None:
    """Капчу и запрос документа автоматика не проходит ни разу: повторное
    прохождение таких проверок и есть эскалация, ставка в которой — основной
    профиль владельца."""
    page = FakePage()
    sink: list[str] = []

    outcome = recover_session(
        verdict=SessionVerdict(state=CHALLENGE_HARD),
        page=page,
        ledger=AttemptLedger(tmp_path / "a.json"),
        config=None,
        now=NOW,
        otp_reader=_reader(OtpResult("483920", "ok")),
        notifier=_notifier(sink),
    )

    assert outcome == RecoveryOutcome(attempted=False, resolved=False, reason="hard_challenge_stop")
    assert page.filled == []
    assert sink and "hard_challenge_stop" in sink[0]


def test_authenticated_session_is_left_alone(tmp_path: Path) -> None:
    page = FakePage()

    outcome = recover_session(
        verdict=SessionVerdict(state=SESSION_OK),
        page=page,
        ledger=AttemptLedger(tmp_path / "a.json"),
        config=None,
        now=NOW,
        otp_reader=_reader(OtpResult("483920", "ok")),
        notifier=_notifier([]),
    )

    assert outcome.attempted is False
    assert outcome.reason == "nothing_to_recover"


def test_email_challenge_fills_and_submits_the_fresh_code(tmp_path: Path) -> None:
    page = FakePage()
    sink: list[str] = []

    outcome = recover_session(
        verdict=SessionVerdict(state=CHALLENGE_EMAIL_OTP),
        page=page,
        ledger=AttemptLedger(tmp_path / "a.json"),
        config=CONFIG_STUB,
        now=NOW,
        otp_reader=_reader(OtpResult("483920", "ok")),
        notifier=_notifier(sink),
    )

    assert outcome == RecoveryOutcome(attempted=True, resolved=True, reason="ok")
    assert page.filled == ["483920"]
    assert page.submitted is True
    assert sink, "оператор обязан узнать о каждом срабатывании"


def test_exhausted_limit_blocks_the_attempt(tmp_path: Path) -> None:
    ledger = AttemptLedger(tmp_path / "a.json")
    ledger.record(now=NOW)
    page = FakePage()

    outcome = recover_session(
        verdict=SessionVerdict(state=CHALLENGE_EMAIL_OTP),
        page=page,
        ledger=ledger,
        config=CONFIG_STUB,
        now=NOW,
        otp_reader=_reader(OtpResult("483920", "ok")),
        notifier=_notifier([]),
    )

    assert outcome.reason == "attempt_limit"
    assert page.filled == []


def test_missing_credentials_are_surfaced_as_their_own_reason(tmp_path: Path) -> None:
    page = FakePage()

    outcome = recover_session(
        verdict=SessionVerdict(state=CHALLENGE_EMAIL_OTP),
        page=page,
        ledger=AttemptLedger(tmp_path / "a.json"),
        config=None,
        now=NOW,
        otp_reader=_reader(OtpResult(None, "not_configured")),
        notifier=_notifier([]),
    )

    assert outcome.reason == "not_configured"
    assert outcome.resolved is False
