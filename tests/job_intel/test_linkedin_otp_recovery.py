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
